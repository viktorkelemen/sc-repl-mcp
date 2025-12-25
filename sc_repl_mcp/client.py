"""SuperCollider OSC client for SC-REPL MCP Server."""

import math
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from typing import Any, Optional

from pythonosc import osc_server, dispatcher, osc_message_builder

from .config import (
    SCSYNTH_HOST,
    SCSYNTH_PORT,
    REPLY_PORT,
    SCLANG_OSC_PORT,
    SCLANG_INIT_CODE,
    SPECTRUM_BAND_FREQUENCIES,
)
from .types import LogEntry, ServerStatus, AnalysisData, OnsetEvent, SpectrumData, ReferenceSnapshot
from .utils import freq_to_note, amp_to_db, kill_process_on_port
from .sclang import find_sclang


class ReuseAddrOSCUDPServer(osc_server.ThreadingOSCUDPServer):
    """OSC server that allows address reuse for faster reconnection."""
    allow_reuse_address = True


class SCClient:
    """Client for communicating with scsynth via OSC."""

    def __init__(self):
        self.status = ServerStatus()
        self._status_event = threading.Event()
        self._reply_server: osc_server.ThreadingOSCUDPServer | None = None
        # Use time-based starting ID to avoid collision across restarts
        # Takes lower 20 bits of current time in ms, shifted to high range
        self._node_id = 1_000_000 + (int(time.time() * 1000) & 0xFFFFF) * 1000
        self._node_lock = threading.Lock()
        self._scsynth_addr = (SCSYNTH_HOST, SCSYNTH_PORT)
        # sclang address for code execution (dedicated MCP port, not IDE's 57120)
        self._sclang_addr = (SCSYNTH_HOST, SCLANG_OSC_PORT)

        # Audio analysis state
        self._analyzer_node_id: Optional[int] = None
        self._analysis_data: Optional[AnalysisData] = None
        self._analysis_history: deque[AnalysisData] = deque(maxlen=100)
        self._analysis_lock = threading.Lock()

        # Onset detection state
        self._onset_events: deque[OnsetEvent] = deque(maxlen=100)
        self._onset_lock = threading.Lock()

        # Spectrum analyzer state
        self._spectrum_data: Optional[SpectrumData] = None
        self._spectrum_lock = threading.Lock()

        # Reference snapshots for sound matching
        self._references: dict[str, ReferenceSnapshot] = {}
        self._reference_lock = threading.Lock()

        # Persistent sclang process for SynthDefs and OSC forwarding
        self._sclang_process: Optional[subprocess.Popen] = None
        self._sclang_init_file: Optional[str] = None  # Temp file for init code

        # Server log capture
        self._log_buffer: deque[LogEntry] = deque(maxlen=500)
        self._log_lock = threading.Lock()

        # Persistent sclang code execution state
        self._eval_request_id = 0
        self._eval_request_lock = threading.Lock()
        self._eval_results: dict[int, tuple[bool, str]] = {}  # request_id -> (success, output)
        self._eval_events: dict[int, threading.Event] = {}  # request_id -> event

        # Recording state
        self._is_recording = False
        self._recording_path: Optional[str] = None
        self._recording_session_id: int = 0  # Tracks current recording session for auto-stop
        self._recording_lock = threading.Lock()

    def _send_message(self, address: str, args: list) -> bool:
        """Send an OSC message to scsynth using the reply server's socket.

        Returns True if message was sent, False otherwise.
        """
        if not self._reply_server:
            return False
        try:
            builder = osc_message_builder.OscMessageBuilder(address=address)
            for arg in args:
                builder.add_arg(arg)
            msg = builder.build()
            self._reply_server.socket.sendto(msg.dgram, self._scsynth_addr)
            return True
        except Exception:
            return False

    def _send_to_sclang(self, address: str, args: list) -> bool:
        """Send an OSC message to sclang using the reply server's socket.

        Returns True if message was sent, False otherwise.
        """
        if not self._reply_server:
            return False
        try:
            builder = osc_message_builder.OscMessageBuilder(address=address)
            for arg in args:
                builder.add_arg(arg)
            msg = builder.build()
            self._reply_server.socket.sendto(msg.dgram, self._sclang_addr)
            return True
        except OSError as e:
            # Network/socket errors - sclang may not be listening
            sys.stderr.write(f"[SC] Failed to send OSC to sclang: {e}\n")
            return False
        except Exception as e:
            # Unexpected error - log with context for debugging
            sys.stderr.write(f"[SC] Unexpected error sending to sclang: {type(e).__name__}: {e}\n")
            return False

    def _add_log(self, category: str, message: str):
        """Add an entry to the log buffer (thread-safe)."""
        entry = LogEntry(timestamp=time.time(), category=category, message=message)
        with self._log_lock:
            self._log_buffer.append(entry)

    def _handle_status_reply(self, address: str, *args):
        """Handle /status.reply from scsynth."""
        if len(args) >= 9:
            self.status = ServerStatus(
                running=True,
                num_ugens=args[1],
                num_synths=args[2],
                num_groups=args[3],
                num_synthdefs=args[4],
                avg_cpu=args[5],
                peak_cpu=args[6],
                sample_rate=args[8],
            )
        self._status_event.set()

    def _handle_done(self, address: str, *args):
        """Handle /done messages."""
        if args:
            self._add_log("done", f"{args[0]} completed" + (f" {args[1:]}" if len(args) > 1 else ""))

    def _handle_fail(self, address: str, *args):
        """Handle /fail messages."""
        msg = f"FAIL: {' '.join(str(a) for a in args)}"
        self._add_log("fail", msg)
        sys.stderr.write(f"[SC] {msg}\n")

    def _handle_node_go(self, address: str, *args):
        """Handle /n_go messages (node started)."""
        if len(args) >= 4:
            node_id, parent, prev, next_node = args[:4]
            is_group = args[4] if len(args) > 4 else -1
            node_type = "group" if is_group == 1 else "synth"
            self._add_log("node", f"Node {node_id} ({node_type}) started in group {parent}")

    def _handle_node_info(self, address: str, *args):
        """Handle /n_info messages (node info reply)."""
        if len(args) >= 4:
            node_id, parent, prev, next_node = args[:4]
            self._add_log("node", f"Node {node_id}: parent={parent}, prev={prev}, next={next_node}")

    def _handle_analysis(self, address: str, *args):
        """Handle /mcp/analysis messages from the analyzer synth.

        Expected args: [node_id, reply_id, freq, has_freq, centroid, flatness, rolloff, peak_l, peak_r, rms_l, rms_r, loudness]
        Note: loudness field (index 11) was added later - handle gracefully if missing.
        """
        if len(args) < 11:
            return

        # Extract loudness if present (backward compatible)
        loudness = float(args[11]) if len(args) >= 12 else 0.0

        data = AnalysisData(
            timestamp=time.time(),
            freq=float(args[2]),
            has_freq=float(args[3]),
            centroid=float(args[4]),
            flatness=float(args[5]),
            rolloff=float(args[6]),
            peak_l=float(args[7]),
            peak_r=float(args[8]),
            rms_l=float(args[9]),
            rms_r=float(args[10]),
            loudness_sones=loudness,
        )
        with self._analysis_lock:
            self._analysis_data = data
            self._analysis_history.append(data)

    def _handle_meter(self, address: str, *args):
        """Handle /mcp/meter messages (lightweight metering only).

        Expected args: [node_id, reply_id, peak_l, peak_r, rms_l, rms_r]
        Only updates if the full analyzer is not running (to avoid overwriting).
        """
        # Don't overwrite full analysis data with meter-only data
        if self._analyzer_node_id is not None:
            return

        if len(args) >= 6:
            data = AnalysisData(
                timestamp=time.time(),
                peak_l=float(args[2]),
                peak_r=float(args[3]),
                rms_l=float(args[4]),
                rms_r=float(args[5]),
            )
            with self._analysis_lock:
                self._analysis_data = data
                self._analysis_history.append(data)

    def _handle_node_end(self, address: str, *args):
        """Handle /n_end messages (node freed notification).

        Used to detect when analyzer synth is freed externally.
        """
        if len(args) >= 1:
            node_id = int(args[0])
            self._add_log("node", f"Node {node_id} ended")
            if node_id == self._analyzer_node_id:
                self._analyzer_node_id = None

    def _handle_onset(self, address: str, *args):
        """Handle /mcp/onset messages (attack/transient detected).

        Expected args: [node_id, reply_id, freq, amplitude]
        """
        if len(args) < 4:
            return

        event = OnsetEvent(
            timestamp=time.time(),
            freq=float(args[2]),
            amplitude=float(args[3]),
        )
        with self._onset_lock:
            self._onset_events.append(event)

    def _handle_spectrum(self, address: str, *args):
        """Handle /mcp/spectrum messages (14-band spectrum analyzer).

        Expected args: [node_id, reply_id, band0, band1, ..., band13]
        """
        if len(args) < 16:  # node_id + reply_id + 14 bands
            return

        bands = tuple(float(args[i]) for i in range(2, 16))
        data = SpectrumData(
            timestamp=time.time(),
            bands=bands,
        )
        with self._spectrum_lock:
            self._spectrum_data = data

    def _handle_eval_result(self, address: str, *args):
        """Handle /mcp/eval/result messages from persistent sclang.

        Expected args: [request_id, success, output]
        """
        if len(args) < 3:
            self._add_log("fail", f"Malformed eval result: expected 3 args, got {len(args)}")
            return

        try:
            request_id = int(args[0])
            success = bool(args[1])
            output = str(args[2]) if args[2] is not None else ""
        except (ValueError, TypeError) as e:
            self._add_log("fail", f"Invalid eval result data: {e}")
            return

        with self._eval_request_lock:
            # Check if anyone is waiting for this result
            event = self._eval_events.get(request_id)
            if event:
                # Store the result and signal the waiting thread
                self._eval_results[request_id] = (success, output)
                event.set()
            else:
                # Orphaned result - no one waiting (likely timed out)
                # Don't store to prevent memory leak
                pass

    def _start_sclang(self) -> tuple[bool, str]:
        """Start persistent sclang process for SynthDefs and OSC forwarding."""
        # Stop any existing sclang process
        self._stop_sclang()

        sclang = find_sclang()
        if not sclang:
            return False, "sclang not found"

        try:
            # Write init code to a temp file (sclang doesn't support -e flag)
            with tempfile.NamedTemporaryFile(
                mode='w',
                suffix='.scd',
                delete=False,
            ) as f:
                f.write(SCLANG_INIT_CODE)
                self._sclang_init_file = f.name

            # Start sclang with the init file
            # Use DEVNULL to avoid pipe buffer deadlock (sclang output can exceed 64KB)
            self._sclang_process = subprocess.Popen(
                [sclang, self._sclang_init_file],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            # Give sclang time to compile and load SynthDefs
            time.sleep(2.0)

            # Check if process is still running
            if self._sclang_process.poll() is not None:
                # Process exited unexpectedly - ensure fully reaped
                exit_code = self._sclang_process.returncode
                try:
                    self._sclang_process.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    pass
                self._sclang_process = None
                self._cleanup_sclang_init_file()
                return False, f"sclang exited unexpectedly with code {exit_code}"

            return True, "sclang started with SynthDefs and OSC forwarding"

        except Exception as e:
            self._sclang_process = None
            self._cleanup_sclang_init_file()
            return False, f"Failed to start sclang: {e}"

    def _cleanup_sclang_init_file(self):
        """Remove the temporary init file."""
        if self._sclang_init_file:
            try:
                os.unlink(self._sclang_init_file)
            except OSError:
                pass
            self._sclang_init_file = None

    def _stop_sclang(self):
        """Stop the persistent sclang process."""
        # Capture reference locally to avoid race conditions
        proc = self._sclang_process
        self._sclang_process = None  # Clear reference immediately
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=1.0)  # Reap the killed process
                except subprocess.TimeoutExpired:
                    pass  # Process truly stuck, nothing more we can do
            except Exception:
                pass
        self._cleanup_sclang_init_file()

    def connect(self) -> tuple[bool, str]:
        """Connect to scsynth server and start sclang for SynthDefs."""
        # Check if already connected and working - reuse the connection
        if self._reply_server:
            try:
                status = self.get_status()
                if status.running:
                    return True, f"Already connected to scsynth on port {SCSYNTH_PORT}"
            except Exception:
                pass
            # Connection exists but not working - clean it up
            try:
                self._reply_server.shutdown()
            except Exception:
                pass
            self._reply_server = None

        try:
            # Set up OSC reply server FIRST (it binds to REPLY_PORT)
            disp = dispatcher.Dispatcher()
            disp.map("/status.reply", self._handle_status_reply)
            disp.map("/done", self._handle_done)
            disp.map("/fail", self._handle_fail)
            disp.map("/n_go", self._handle_node_go)
            disp.map("/n_end", self._handle_node_end)
            disp.map("/n_info", self._handle_node_info)
            disp.map("/mcp/analysis", self._handle_analysis)
            disp.map("/mcp/meter", self._handle_meter)
            disp.map("/mcp/onset", self._handle_onset)
            disp.map("/mcp/spectrum", self._handle_spectrum)
            disp.map("/mcp/eval/result", self._handle_eval_result)

            # Try to bind, killing orphaned processes if needed
            for attempt in range(2):
                try:
                    self._reply_server = ReuseAddrOSCUDPServer(
                        (SCSYNTH_HOST, REPLY_PORT), disp
                    )
                    break  # Success
                except OSError as e:
                    if e.errno == 48 and attempt == 0:  # Address already in use
                        kill_process_on_port(REPLY_PORT)
                        time.sleep(0.2)  # Give OS time to release the port
                    else:
                        raise

            thread = threading.Thread(target=self._reply_server.serve_forever, daemon=True)
            thread.start()

            # Query status to verify connection (uses the same socket for send/receive)
            status = self.get_status()
            if not status.running:
                return False, "scsynth not responding. Make sure SuperCollider server is running."

            # Enable notifications for node events (/n_go, /n_end, etc.)
            self._send_message("/notify", [1])
            self._add_log("info", f"Connected to scsynth on port {SCSYNTH_PORT}")

            # Start sclang for SynthDefs and OSC forwarding
            sclang_ok, sclang_msg = self._start_sclang()
            if sclang_ok:
                return True, f"Connected to scsynth on port {SCSYNTH_PORT}. {sclang_msg}"
            else:
                # Connection succeeded but sclang failed - still usable, just warn
                return True, f"Connected to scsynth on port {SCSYNTH_PORT}. Warning: {sclang_msg} (analyzer may not work)"

        except Exception as e:
            return False, f"Failed to connect: {e}"

    def get_status(self) -> ServerStatus:
        """Query server status."""
        if not self._reply_server:
            return ServerStatus(running=False)

        try:
            self._status_event.clear()
            self._send_message("/status", [])

            # Wait for reply with timeout
            if self._status_event.wait(timeout=1.0):
                return self.status
            return ServerStatus(running=False)

        except Exception:
            return ServerStatus(running=False)

    def _next_node_id(self) -> int:
        """Get next available node ID (thread-safe)."""
        with self._node_lock:
            self._node_id += 1
            return self._node_id

    def play_sine(self, freq: float = 440.0, amp: float = 0.1, dur: float = 1.0) -> tuple[bool, str]:
        """Play a sine wave using scsynth's default synthdef."""
        if not self._reply_server:
            return False, "Not connected to scsynth. Call sc_connect first."

        # Validate parameters
        if freq <= 0:
            return False, f"Frequency must be positive, got {freq}"
        if not 0 < amp <= 1.0:
            return False, f"Amplitude must be between 0 and 1, got {amp}"
        if dur <= 0:
            return False, f"Duration must be positive, got {dur}"

        node_id = self._next_node_id()

        # Use s_new to create a synth with the "default" synthdef
        if not self._send_message("/s_new", [
            "default",  # synthdef name
            node_id,    # node ID
            0,          # add action (0 = add to head)
            0,          # target group (0 = default group)
            "freq", freq,
            "amp", amp,
        ]):
            return False, "Failed to send OSC message to scsynth"

        # Schedule release in a background thread
        def release_later():
            time.sleep(dur)
            self._send_message("/n_set", [node_id, "gate", 0])

        threading.Thread(target=release_later, daemon=True).start()

        return True, f"Playing {freq}Hz sine wave for {dur}s"

    def play_synth(
        self,
        synthdef: str,
        params: Optional[dict[str, Any]] = None,
        dur: Optional[float] = None,
        sustain: bool = True,
    ) -> tuple[bool, str]:
        """Play any SynthDef with custom parameters.

        Args:
            synthdef: Name of the SynthDef to play (must be loaded in scsynth)
            params: Dictionary of parameter name -> value pairs
            dur: Duration in seconds. If None, synth plays until freed manually.
                 If provided, releases the synth after dur seconds (sets gate=0).
            sustain: If True, synth sustains until released. If False with dur,
                     the synth is freed immediately after dur (using n_free).

        Returns:
            (success, message) tuple
        """
        if not self._reply_server:
            return False, "Not connected to scsynth. Call sc_connect first."

        if not synthdef or not isinstance(synthdef, str):
            return False, "SynthDef name is required and must be a string"

        if dur is not None and dur <= 0:
            return False, f"Duration must be positive, got {dur}"

        node_id = self._next_node_id()

        # Build s_new arguments: synthdef name, node_id, add_action, target, then param pairs
        args: list[Any] = [synthdef, node_id, 0, 0]  # add to head of default group

        if params:
            for key, value in params.items():
                # Validate key
                if not isinstance(key, str):
                    return False, f"Parameter key must be string, got {type(key).__name__}"
                # Skip None values
                if value is None:
                    continue
                # Validate and convert value types
                if isinstance(value, bool):
                    args.append(key)
                    args.append(1 if value else 0)
                elif isinstance(value, (int, float)):
                    args.append(key)
                    args.append(float(value))
                elif isinstance(value, str):
                    args.append(key)
                    args.append(value)
                else:
                    return False, f"Parameter '{key}' has unsupported type {type(value).__name__} (use bool, int, float, or str)"

        if not self._send_message("/s_new", args):
            return False, "Failed to send OSC message to scsynth"

        # Schedule release if duration specified
        if dur is not None:
            def release_later():
                time.sleep(dur)
                if sustain:
                    # Release envelope (if synth has gate parameter)
                    self._send_message("/n_set", [node_id, "gate", 0])
                else:
                    # Hard free
                    self._send_message("/n_free", [node_id])

            threading.Thread(target=release_later, daemon=True).start()
            return True, f"Playing '{synthdef}' for {dur}s (node {node_id})"

        return True, f"Playing '{synthdef}' (node {node_id}) - use sc_free_all to stop"

    def free_all(self) -> tuple[bool, str]:
        """Free all synths."""
        if not self._reply_server:
            return False, "Not connected to scsynth"

        if self._send_message("/g_freeAll", [0]):
            self._analyzer_node_id = None  # Analyzer was freed too
            return True, "All synths freed"
        return False, "Failed to send OSC message to scsynth"

    def start_analyzer(self) -> tuple[bool, str]:
        """Start the audio analyzer synth.

        Requires sc_connect to be called first (which loads mcp_analyzer SynthDef).
        """
        if not self._reply_server:
            return False, "Not connected to scsynth. Call sc_connect first."

        if self._analyzer_node_id is not None:
            return True, "Analyzer already running"

        node_id = self._next_node_id()

        # Create analyzer synth monitoring bus 0 (main output)
        if not self._send_message("/s_new", [
            "mcp_analyzer",  # synthdef name
            node_id,         # node ID
            1,               # add action (1 = add to tail, so it runs after other synths)
            0,               # target group
            "bus", 0,        # monitor main output
            "replyRate", 10, # 10 updates per second
        ]):
            return False, "Failed to send OSC message to scsynth"

        self._analyzer_node_id = node_id

        # Clear old analysis data
        with self._analysis_lock:
            self._analysis_data = None
            self._analysis_history.clear()
        with self._onset_lock:
            self._onset_events.clear()
        with self._spectrum_lock:
            self._spectrum_data = None

        return True, "Analyzer started (monitoring output bus 0)"

    def stop_analyzer(self) -> tuple[bool, str]:
        """Stop the audio analyzer synth."""
        if not self._reply_server:
            return False, "Not connected to scsynth"

        if self._analyzer_node_id is None:
            return True, "Analyzer not running"

        if self._send_message("/n_free", [self._analyzer_node_id]):
            self._analyzer_node_id = None
            return True, "Analyzer stopped"
        return False, "Failed to send OSC message to scsynth"

    def get_analysis(self) -> tuple[bool, str, Optional[dict]]:
        """Get the latest audio analysis data.

        Returns (success, message, data_dict)
        """
        if self._analyzer_node_id is None:
            return False, "Analyzer not running. Call sc_start_analyzer first.", None

        with self._analysis_lock:
            data = self._analysis_data

        if data is None:
            return False, "No analysis data received yet. The analyzer SynthDef may have failed to load.", None

        # Check if data is stale (older than 1 second)
        age = time.time() - data.timestamp
        if age > 1.0:
            return False, f"Analysis data is stale ({age:.1f}s old). Analyzer may have stopped.", None

        # Convert to friendly format
        note, octave, cents = freq_to_note(data.freq)
        is_silent = data.rms_l < 0.001 and data.rms_r < 0.001

        result = {
            "pitch": {
                "freq": round(data.freq, 2),
                "note": f"{note}{octave}",
                "cents": round(cents, 1),
                "confidence": round(data.has_freq, 2),
            },
            "timbre": {
                "centroid": round(data.centroid, 1),
                "flatness": round(data.flatness, 3),
                "rolloff": round(data.rolloff, 1),
            },
            "amplitude": {
                "peak_l": round(data.peak_l, 4),
                "peak_r": round(data.peak_r, 4),
                "rms_l": round(data.rms_l, 4),
                "rms_r": round(data.rms_r, 4),
                "db_l": round(amp_to_db(data.rms_l), 1),
                "db_r": round(amp_to_db(data.rms_r), 1),
            },
            "loudness": {
                "sones": round(data.loudness_sones, 2),
            },
            "is_silent": is_silent,
            "is_clipping": data.peak_l > 1.0 or data.peak_r > 1.0,
        }

        return True, "Analysis data retrieved", result

    def get_onsets(self, since: Optional[float] = None, clear: bool = True) -> list[OnsetEvent]:
        """Get recent onset (attack/transient) events.

        Args:
            since: Only return events after this timestamp. If None, returns all.
            clear: If True, clears returned events from buffer. Default True.

        Returns:
            List of OnsetEvent objects, oldest first.
        """
        with self._onset_lock:
            if since is not None:
                events = [e for e in self._onset_events if e.timestamp > since]
            else:
                events = list(self._onset_events)

            if clear and events:
                # Remove returned events from buffer
                for event in events:
                    try:
                        self._onset_events.remove(event)
                    except ValueError:
                        pass  # Already removed

        return events

    def get_spectrum(self) -> tuple[bool, str, Optional[dict]]:
        """Get the latest spectrum analyzer data.

        Returns (success, message, data_dict) with 14 frequency bands.
        """
        if self._analyzer_node_id is None:
            return False, "Analyzer not running. Call sc_start_analyzer first.", None

        with self._spectrum_lock:
            data = self._spectrum_data

        if data is None:
            return False, "No spectrum data received yet.", None

        # Check if data is stale
        age = time.time() - data.timestamp
        if age > 1.0:
            return False, f"Spectrum data is stale ({age:.1f}s old).", None

        # Band center frequencies (Hz) - from config for consistency with SynthDef
        band_freqs = SPECTRUM_BAND_FREQUENCIES

        # Convert to dB and create labeled result
        bands_db = []
        for freq, power in zip(band_freqs, data.bands):
            db = amp_to_db(power) if power > 0 else -60.0
            bands_db.append({
                "freq": freq,
                "power": round(power, 6),
                "db": round(max(db, -60.0), 1),  # Floor at -60dB
            })

        result = {
            "bands": bands_db,
            "band_frequencies": band_freqs,
        }

        return True, "Spectrum data retrieved", result

    def disconnect(self):
        """Disconnect from server and stop sclang."""
        # Stop recording if in progress to avoid corrupted files
        if self.is_recording():
            success, message = self.stop_recording()
            if not success:
                self._add_log(
                    "fail",
                    f"Failed to stop recording during disconnect: {message}. "
                    f"Recording file may be incomplete."
                )
        self._stop_sclang()
        if self._reply_server:
            self._reply_server.shutdown()
            self._reply_server = None

    def get_logs(self, limit: int = 50, category: Optional[str] = None) -> list[LogEntry]:
        """Get recent log entries.

        Args:
            limit: Maximum number of entries to return (default 50)
            category: Filter by category ('fail', 'done', 'node', 'info') or None for all

        Returns:
            List of LogEntry objects, most recent last
        """
        with self._log_lock:
            entries = list(self._log_buffer)

        if category:
            entries = [e for e in entries if e.category == category]

        return entries[-limit:]

    def clear_logs(self):
        """Clear the log buffer."""
        with self._log_lock:
            self._log_buffer.clear()

    # Persistent sclang code execution

    def is_sclang_ready(self) -> bool:
        """Check if persistent sclang is running and ready for code execution."""
        return self._sclang_process is not None and self._sclang_process.poll() is None

    def eval_code(self, code: str, timeout: float = 30.0) -> tuple[bool, str]:
        """Execute SuperCollider code via the persistent sclang process.

        This is much faster than spawning a new sclang process because the
        class library is already compiled.

        Args:
            code: SuperCollider code to execute
            timeout: Maximum time to wait for result (default 30s)

        Returns:
            (success, output) tuple
        """
        if not code or not code.strip():
            return False, "No code provided"

        if not self.is_sclang_ready():
            return False, "Persistent sclang not running. Call sc_connect first."

        if not self._reply_server:
            return False, "Not connected. Call sc_connect first."

        # Generate unique request ID
        with self._eval_request_lock:
            self._eval_request_id += 1
            request_id = self._eval_request_id
            # Create event for this request
            event = threading.Event()
            self._eval_events[request_id] = event

        # Write code to temp file (OSC has size limits)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode='w',
                suffix='.scd',
                delete=False,
            ) as f:
                f.write(code)
                temp_path = f.name

            # Send execution request to sclang
            if not self._send_to_sclang("/mcp/eval", [request_id, temp_path]):
                # Clean up event on send failure
                with self._eval_request_lock:
                    self._eval_events.pop(request_id, None)
                return False, "Failed to send code to sclang"

            # Wait for result
            if not event.wait(timeout=timeout):
                # Timeout - clean up
                with self._eval_request_lock:
                    self._eval_events.pop(request_id, None)
                    self._eval_results.pop(request_id, None)
                return False, f"sclang execution timed out after {timeout}s"

            # Get result
            with self._eval_request_lock:
                result = self._eval_results.pop(request_id, None)
                self._eval_events.pop(request_id, None)

            if result is None:
                return False, "No result received from sclang"

            success, output = result
            return success, output

        except Exception as e:
            # Clean up event on any failure
            with self._eval_request_lock:
                self._eval_events.pop(request_id, None)
            return False, f"Error executing code: {e}"
        finally:
            # Clean up temp file
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    # Reference capture and comparison methods

    def capture_reference(self, name: str, description: str = "") -> tuple[bool, str]:
        """Capture current analysis and spectrum as a named reference.

        Args:
            name: Unique name for this reference
            description: Optional description of the sound

        Returns:
            (success, message) tuple
        """
        if not name or not isinstance(name, str):
            return False, "Reference name is required"

        if self._analyzer_node_id is None:
            return False, "Analyzer not running. Call sc_start_analyzer first."

        # Get current analysis data
        with self._analysis_lock:
            analysis = self._analysis_data

        if analysis is None:
            return False, "No analysis data available"

        # Check if data is stale
        age = time.time() - analysis.timestamp
        if age > 1.0:
            return False, f"Analysis data is stale ({age:.1f}s old). Make sure sound is playing."

        # Get current spectrum data
        with self._spectrum_lock:
            spectrum = self._spectrum_data

        # Create snapshot
        snapshot = ReferenceSnapshot(
            name=name,
            timestamp=time.time(),
            analysis=analysis,
            spectrum=spectrum,
            description=description,
        )

        # Store reference
        with self._reference_lock:
            overwriting = name in self._references
            self._references[name] = snapshot

        if overwriting:
            return True, f"Reference '{name}' updated"
        return True, f"Reference '{name}' captured"

    def get_reference(self, name: str) -> Optional[ReferenceSnapshot]:
        """Get a stored reference by name.

        Args:
            name: Name of the reference to retrieve

        Returns:
            ReferenceSnapshot if found, None otherwise
        """
        with self._reference_lock:
            return self._references.get(name)

    def list_references(self) -> list[ReferenceSnapshot]:
        """List all stored references.

        Returns:
            List of ReferenceSnapshot objects, sorted by timestamp
        """
        with self._reference_lock:
            refs = list(self._references.values())
        return sorted(refs, key=lambda r: r.timestamp)

    def delete_reference(self, name: str) -> tuple[bool, str]:
        """Delete a stored reference.

        Args:
            name: Name of the reference to delete

        Returns:
            (success, message) tuple
        """
        with self._reference_lock:
            if name not in self._references:
                return False, f"Reference '{name}' not found"
            del self._references[name]
        return True, f"Reference '{name}' deleted"

    def compare_to_reference(self, name: str) -> tuple[bool, str, Optional[dict]]:
        """Compare current sound to a stored reference.

        Args:
            name: Name of the reference to compare against

        Returns:
            (success, message, comparison_dict) tuple
        """
        # Get reference
        ref = self.get_reference(name)
        if ref is None:
            return False, f"Reference '{name}' not found", None

        # Get current analysis
        if self._analyzer_node_id is None:
            return False, "Analyzer not running. Call sc_start_analyzer first.", None

        with self._analysis_lock:
            current = self._analysis_data

        if current is None:
            return False, "No current analysis data available", None

        # Check if current data is stale
        age = time.time() - current.timestamp
        if age > 1.0:
            return False, f"Current analysis data is stale ({age:.1f}s old)", None

        ref_analysis = ref.analysis

        # Calculate pitch difference in semitones
        # Handle silent sounds (freq=0) explicitly
        if current.freq > 0 and ref_analysis.freq > 0:
            pitch_diff_semitones = 12 * math.log2(current.freq / ref_analysis.freq)
            pitch_valid = True
        else:
            pitch_diff_semitones = 0.0
            pitch_valid = False  # One or both sounds are silent

        # Calculate centroid ratio (brightness comparison)
        # Use log scale for symmetric scoring (2x brighter = 0.5x darker in score impact)
        if current.centroid > 0 and ref_analysis.centroid > 0:
            brightness_ratio = current.centroid / ref_analysis.centroid
            # Log scale: ratio of 2.0 or 0.5 both give same score penalty
            brightness_diff_octaves = abs(math.log2(brightness_ratio))
            brightness_valid = True
        elif current.centroid == 0 and ref_analysis.centroid == 0:
            brightness_ratio = 1.0
            brightness_diff_octaves = 0.0
            brightness_valid = True  # Both silent/dark
        else:
            brightness_ratio = 0.0 if current.centroid == 0 else float('inf')
            brightness_diff_octaves = 10.0  # Large penalty for mismatch
            brightness_valid = False

        # Calculate loudness difference
        loudness_diff = current.loudness_sones - ref_analysis.loudness_sones

        # Calculate RMS difference in dB (handle zero values)
        if current.rms_l > 0 and ref_analysis.rms_l > 0:
            rms_db_diff = amp_to_db(current.rms_l) - amp_to_db(ref_analysis.rms_l)
        elif current.rms_l == 0 and ref_analysis.rms_l == 0:
            rms_db_diff = 0.0  # Both silent
        else:
            rms_db_diff = -60.0 if current.rms_l == 0 else 60.0  # One silent

        # Flatness difference (tonal vs noise character)
        flatness_diff = current.flatness - ref_analysis.flatness

        # Calculate individual component scores (0-100%)
        # Scoring penalties per unit difference
        PITCH_PENALTY_PER_SEMITONE = 10  # 10% per semitone
        BRIGHTNESS_PENALTY_PER_OCTAVE = 50  # 50% per octave
        LOUDNESS_PENALTY_PER_SONE = 5  # 5% per sone
        FLATNESS_PENALTY = 200  # flatness is 0-1

        if pitch_valid:
            pitch_score = max(0, 100 - abs(pitch_diff_semitones) * PITCH_PENALTY_PER_SEMITONE)
        else:
            pitch_score = 0.0  # Can't compare pitch when one is silent

        if brightness_valid:
            brightness_score = max(0, 100 - brightness_diff_octaves * BRIGHTNESS_PENALTY_PER_OCTAVE)
        else:
            brightness_score = 0.0

        loudness_score = max(0, 100 - abs(loudness_diff) * LOUDNESS_PENALTY_PER_SONE)
        flatness_score = max(0, 100 - abs(flatness_diff) * FLATNESS_PENALTY)

        # Calculate overall score with normalized weights
        # Only include valid components in the weighted average
        components = []
        if pitch_valid:
            components.append((pitch_score, 0.3))  # 30% weight
        if brightness_valid:
            components.append((brightness_score, 0.3))  # 30% weight
        components.append((loudness_score, 0.2))  # 20% weight
        components.append((flatness_score, 0.2))  # 20% weight

        # Normalize weights to sum to 1.0
        total_weight = sum(weight for _, weight in components)
        overall_score = sum(score * (weight / total_weight) for score, weight in components)

        result = {
            "reference": {
                "name": ref.name,
                "description": ref.description,
                "captured_at": ref.timestamp,
            },
            "pitch": {
                "current_freq": round(current.freq, 2),
                "reference_freq": round(ref_analysis.freq, 2),
                "diff_semitones": round(pitch_diff_semitones, 2),
                "score": round(pitch_score, 1),
                "valid": pitch_valid,
            },
            "brightness": {
                "current_centroid": round(current.centroid, 1),
                "reference_centroid": round(ref_analysis.centroid, 1),
                "ratio": round(brightness_ratio, 2) if brightness_valid else None,
                "score": round(brightness_score, 1),
                "valid": brightness_valid,
            },
            "loudness": {
                "current_sones": round(current.loudness_sones, 2),
                "reference_sones": round(ref_analysis.loudness_sones, 2),
                "diff_sones": round(loudness_diff, 2),
                "score": round(loudness_score, 1),
            },
            "character": {
                "current_flatness": round(current.flatness, 3),
                "reference_flatness": round(ref_analysis.flatness, 3),
                "diff": round(flatness_diff, 3),
                "score": round(flatness_score, 1),
            },
            "amplitude": {
                "current_db": round(amp_to_db(current.rms_l), 1),
                "reference_db": round(amp_to_db(ref_analysis.rms_l), 1),
                "diff_db": round(rms_db_diff, 1),
            },
            "overall_score": round(overall_score, 1),
        }

        return True, "Comparison complete", result

    def analyze_parameter_impact(
        self,
        synthdef: str,
        param: str,
        values: list[float],
        metric: str,
        base_params: Optional[dict[str, Any]] = None,
        dur: float = 0.3,
        settle_time: float = 0.15,
    ) -> tuple[bool, str, Optional[list[dict]]]:
        """Analyze how a parameter affects a specific metric.

        Plays the synth with different parameter values and measures the result.

        Args:
            synthdef: Name of the SynthDef to test
            param: Parameter name to sweep (e.g., "freq", "cutoff")
            values: List of values to test
            metric: Metric to measure ("pitch", "centroid", "loudness", "flatness", "rms")
            base_params: Other fixed parameters for the synth
            dur: Duration to play each test tone (default 0.3s)
            settle_time: Time to wait before measuring (default 0.15s). Must be < dur.

        Returns:
            (success, message, results_list) where each result contains
            the parameter value and measured metric. Results may include
            an "error" key if measurement failed for that value.
        """
        if not values:
            return False, "No values provided to test", None

        if metric not in ("pitch", "centroid", "loudness", "flatness", "rms"):
            return False, f"Unknown metric '{metric}'. Use: pitch, centroid, loudness, flatness, rms", None

        if settle_time >= dur:
            return False, f"settle_time ({settle_time}s) must be less than dur ({dur}s)", None

        if self._analyzer_node_id is None:
            return False, "Analyzer not running. Call sc_start_analyzer first.", None

        results = []
        base = base_params or {}

        for value in values:
            # Build params with the test value
            test_params = dict(base)
            test_params[param] = value

            # Record time before playing so we can verify we got fresh data
            start_time = time.time()

            # Play the synth (continue on failure instead of aborting)
            success, msg = self.play_synth(synthdef, test_params, dur=dur)
            if not success:
                results.append({"value": value, "metric": None, "error": f"Synth failed: {msg}"})
                continue

            # Wait for sound to settle
            time.sleep(settle_time)

            # Measure the metric
            with self._analysis_lock:
                data = self._analysis_data

            # Check for missing data
            if data is None:
                results.append({"value": value, "metric": None, "error": "No analysis data"})
                time.sleep(dur - settle_time + 0.1)
                continue

            # Check that data is from AFTER synth started (fixes race condition)
            if data.timestamp < start_time:
                results.append({"value": value, "metric": None, "error": "No fresh data received"})
                time.sleep(dur - settle_time + 0.1)
                continue

            # Extract the requested metric
            if metric == "pitch":
                measured = data.freq
            elif metric == "centroid":
                measured = data.centroid
            elif metric == "loudness":
                measured = data.loudness_sones
            elif metric == "flatness":
                measured = data.flatness
            elif metric == "rms":
                # Correct RMS averaging: sqrt of mean of squares
                measured = math.sqrt((data.rms_l**2 + data.rms_r**2) / 2)
            else:
                measured = 0.0

            results.append({
                "value": value,
                "metric": round(measured, 4),
            })

            # Wait for synth to finish with larger gap to prevent overlap
            remaining = dur - settle_time
            if remaining > 0:
                time.sleep(remaining + 0.1)

        return True, f"Analyzed {len(results)} values", results

    # Audio recording methods

    def start_recording(
        self,
        path: Optional[str] = None,
        duration: Optional[float] = None,
        header_format: str = "wav",
        sample_format: str = "int24",
        channels: int = 2,
    ) -> tuple[bool, str]:
        """Start recording server output to an audio file.

        Args:
            path: Output file path. If None, uses SuperCollider's default
                  (~/Music/SuperCollider Recordings/SC_<timestamp>.<format>)
            duration: Optional auto-stop duration in seconds
            header_format: File format - "wav", "aiff", "caf" (default: wav)
            sample_format: Sample format - "int16", "int24", "float" (default: int24)
            channels: Number of channels to record (default: 2)

        Returns:
            (success, message) tuple
        """
        if not self.is_sclang_ready():
            return False, "Not connected. Call sc_connect first."

        # Validate parameters before acquiring lock
        valid_header_formats = ["wav", "aiff", "caf", "w64", "rf64"]
        if header_format not in valid_header_formats:
            return False, f"Invalid header format '{header_format}'. Use: {', '.join(valid_header_formats)}"

        valid_sample_formats = ["int16", "int24", "int32", "float"]
        if sample_format not in valid_sample_formats:
            return False, f"Invalid sample format '{sample_format}'. Use: {', '.join(valid_sample_formats)}"

        if channels < 1 or channels > 32:
            return False, f"Channels must be between 1 and 32, got {channels}"

        if duration is not None and duration <= 0:
            return False, f"Duration must be positive, got {duration}"

        # Acquire lock and set recording state atomically to prevent TOCTOU race
        with self._recording_lock:
            if self._is_recording:
                return False, f"Already recording to: {self._recording_path}"
            # Mark as recording immediately to prevent concurrent start attempts
            self._is_recording = True
            self._recording_session_id += 1
            session_id = self._recording_session_id

        # Build sclang code to start recording
        # Escape path for SuperCollider string if provided
        if path:
            # Expand ~ and make absolute
            expanded_path = os.path.expanduser(path)
            if not os.path.isabs(expanded_path):
                expanded_path = os.path.abspath(expanded_path)
            # Escape backslashes and quotes for SC string
            escaped_path = expanded_path.replace("\\", "\\\\").replace('"', '\\"')
            path_arg = f'"{escaped_path}"'
        else:
            path_arg = "nil"

        code = f"""
s.recChannels = {channels};
s.recHeaderFormat = "{header_format}";
s.recSampleFormat = "{sample_format}";
s.record({path_arg});
s.recorder.path;
"""

        success, output = self.eval_code(code, timeout=10.0)
        if not success:
            # Revert recording state on failure
            with self._recording_lock:
                self._is_recording = False
                self._recording_path = None
            return False, f"Failed to start recording: {output}"

        # Extract the actual recording path from sclang output
        # The last line of output should be the path
        actual_path = output.strip().split("\n")[-1].strip()

        # Validate extracted path
        if not actual_path or actual_path.startswith("ERROR") or actual_path.startswith("WARNING"):
            with self._recording_lock:
                self._is_recording = False
                self._recording_path = None
            return False, f"Could not determine recording path from sclang output: {output[:200]}"

        with self._recording_lock:
            self._recording_path = actual_path

        # Schedule auto-stop if duration specified
        if duration is not None:
            def auto_stop(expected_session_id: int):
                time.sleep(duration)
                # Check if this is still the same recording session
                with self._recording_lock:
                    if self._recording_session_id != expected_session_id:
                        return  # Different recording or stopped, abort
                    if not self._is_recording:
                        return  # Already stopped
                success, message = self.stop_recording()
                if not success:
                    self._add_log("fail", f"Auto-stop recording failed: {message}")

            threading.Thread(target=auto_stop, args=(session_id,), daemon=True).start()
            return True, f"Recording started: {actual_path} (auto-stop in {duration}s)"

        return True, f"Recording started: {actual_path}"

    def stop_recording(self) -> tuple[bool, str]:
        """Stop recording and finalize the audio file.

        Returns:
            (success, message) tuple with the path to the recorded file.
        """
        with self._recording_lock:
            if not self._is_recording:
                return False, "Not currently recording"
            recording_path = self._recording_path

        if not self.is_sclang_ready():
            # Clear state even if we can't stop properly
            with self._recording_lock:
                self._is_recording = False
                self._recording_path = None
            return False, "sclang not available. Recording state cleared but file may be incomplete."

        code = "s.stopRecording;"
        success, output = self.eval_code(code, timeout=10.0)

        with self._recording_lock:
            self._is_recording = False
            self._recording_path = None

        if not success:
            return False, f"Error stopping recording: {output}. File may be incomplete: {recording_path}"

        return True, f"Recording saved: {recording_path}"

    def is_recording(self) -> bool:
        """Check if currently recording.

        Returns:
            True if recording is in progress, False otherwise.
        """
        with self._recording_lock:
            return self._is_recording

    def get_recording_path(self) -> Optional[str]:
        """Get the path to the current recording.

        Returns:
            Path string if recording, None otherwise.
        """
        with self._recording_lock:
            return self._recording_path
