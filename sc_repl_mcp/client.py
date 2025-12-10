"""SuperCollider OSC client for SC-REPL MCP Server."""

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
    SCLANG_INIT_CODE,
)
from .types import LogEntry, ServerStatus, AnalysisData
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

        # Audio analysis state
        self._analyzer_node_id: Optional[int] = None
        self._analysis_data: Optional[AnalysisData] = None
        self._analysis_history: deque[AnalysisData] = deque(maxlen=100)
        self._analysis_lock = threading.Lock()

        # Persistent sclang process for SynthDefs and OSC forwarding
        self._sclang_process: Optional[subprocess.Popen] = None
        self._sclang_init_file: Optional[str] = None  # Temp file for init code

        # Server log capture
        self._log_buffer: deque[LogEntry] = deque(maxlen=500)
        self._log_lock = threading.Lock()

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

        Expected args: [node_id, reply_id, freq, has_freq, centroid, flatness, rolloff, peak_l, peak_r, rms_l, rms_r]
        """
        # Debug: log message details
        if len(args) < 11:
            sys.stderr.write(f"[MCP] Analysis: unexpected arg count {len(args)}: {args}\n")
            return

        data = AnalysisData(
            timestamp=time.time(),
            freq=float(args[2]),
            has_freq=float(args[3]),
            centroid=float(args[4]),
            flatness=float(args[5]),
            rolloff=float(args[6]),
            peak_l=peak_l,
            peak_r=float(args[8]),
            rms_l=float(args[9]),
            rms_r=float(args[10]),
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

        Requires mcp_analyzer SynthDef to be loaded in SuperCollider.
        Run mcp_synthdefs.scd in SuperCollider IDE first.
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
            return False, "No analysis data received yet. Make sure mcp_synthdefs.scd was run in SuperCollider.", None

        # Check if data is stale (older than 1 second)
        age = time.time() - data.timestamp
        if age > 1.0:
            return False, f"Analysis data is stale ({age:.1f}s old). Analyzer may have stopped.", None

        # Convert to friendly format
        note, octave, cents = freq_to_note(data.freq)
        is_silent = data.rms_l < 0.001 and data.rms_r < 0.001

        # Infer waveform type from spectral characteristics
        waveform = "unknown"
        if is_silent:
            waveform = "silence"
        elif data.flatness > 0.5:
            waveform = "noise"
        elif data.has_freq > 0.8 and data.freq > 20:
            # Estimate based on centroid/freq ratio (freq > 20Hz = audible)
            ratio = data.centroid / data.freq
            if ratio < 1.5:
                waveform = "sine"
            elif ratio < 3:
                waveform = "triangle"
            elif ratio < 5:
                waveform = "square"
            else:
                waveform = "saw"

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
                "type": waveform,
            },
            "amplitude": {
                "peak_l": round(data.peak_l, 4),
                "peak_r": round(data.peak_r, 4),
                "rms_l": round(data.rms_l, 4),
                "rms_r": round(data.rms_r, 4),
                "db_l": round(amp_to_db(data.rms_l), 1),
                "db_r": round(amp_to_db(data.rms_r), 1),
            },
            "is_silent": is_silent,
            "is_clipping": data.peak_l > 1.0 or data.peak_r > 1.0,
        }

        return True, "Analysis data retrieved", result

    def disconnect(self):
        """Disconnect from server and stop sclang."""
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
