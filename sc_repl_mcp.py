#!/usr/bin/env python3
"""
MCP server for SuperCollider REPL integration.
Uses OSC to communicate directly with scsynth.
"""

import atexit
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP
from pythonosc import osc_server, dispatcher, osc_message_builder


class ReuseAddrOSCUDPServer(osc_server.ThreadingOSCUDPServer):
    """OSC server that allows address reuse for faster reconnection."""
    allow_reuse_address = True


SCSYNTH_HOST = "127.0.0.1"
SCSYNTH_PORT = 57110
REPLY_PORT = 57130  # Avoid 57120 (sclang's default)

# SuperCollider code to load SynthDefs and set up OSC forwarding
# This runs in a persistent sclang process started by the MCP server
SCLANG_INIT_CODE = r'''
// MCP Audio Analyzer SynthDefs

// Full audio analyzer - pitch, timbre, amplitude
SynthDef(\mcp_analyzer, {
    arg bus = 0, replyRate = 10, replyID = 1001;
    var in, mono, fft;
    var freq, hasFreq, centroid, flatness, rolloff;
    var peakL, peakR, rmsL, rmsR;

    in = In.ar(bus, 2);
    mono = in.sum * 0.5;
    fft = FFT(LocalBuf(2048), mono);

    # freq, hasFreq = Pitch.kr(mono, ampThreshold: 0.01, median: 7);
    centroid = SpecCentroid.kr(fft);
    flatness = SpecFlatness.kr(fft);
    rolloff = SpecPcile.kr(fft, 0.9);

    peakL = PeakFollower.kr(in[0], 0.99);
    peakR = PeakFollower.kr(in[1], 0.99);
    rmsL = RunningSum.rms(in[0], 1024);
    rmsR = RunningSum.rms(in[1], 1024);

    SendReply.kr(
        Impulse.kr(replyRate),
        '/mcp/analysis',
        [freq, hasFreq, centroid, flatness, rolloff, peakL, peakR, rmsL, rmsR],
        replyID
    );
}).add;

// Simple peak/RMS meter only (lighter weight)
SynthDef(\mcp_meter, {
    arg bus = 0, replyRate = 20, replyID = 1002;
    var in, peakL, peakR, rmsL, rmsR;

    in = In.ar(bus, 2);
    peakL = PeakFollower.kr(in[0], 0.99);
    peakR = PeakFollower.kr(in[1], 0.99);
    rmsL = RunningSum.rms(in[0], 512);
    rmsR = RunningSum.rms(in[1], 512);

    SendReply.kr(
        Impulse.kr(replyRate),
        '/mcp/meter',
        [peakL, peakR, rmsL, rmsR],
        replyID
    );
}).add;

// OSC Forwarding to MCP server (port 57130)
~mcpAddr = NetAddr("127.0.0.1", 57130);
~mcpAnalysisForwarder = OSCFunc({ |msg| ~mcpAddr.sendMsg(*msg) }, '/mcp/analysis');
~mcpMeterForwarder = OSCFunc({ |msg| ~mcpAddr.sendMsg(*msg) }, '/mcp/meter');

"MCP SynthDefs loaded and OSC forwarding active".postln;

// Keep sclang running indefinitely
{ inf.wait }.defer;
'''


@dataclass
class ServerStatus:
    running: bool = False
    num_ugens: int = 0
    num_synths: int = 0
    num_groups: int = 0
    num_synthdefs: int = 0
    avg_cpu: float = 0.0
    peak_cpu: float = 0.0
    sample_rate: float = 0.0


@dataclass
class AnalysisData:
    """Audio analysis data from the mcp_analyzer SynthDef."""
    timestamp: float = 0.0
    # Pitch
    freq: float = 0.0
    has_freq: float = 0.0  # confidence 0-1
    # Timbre
    centroid: float = 0.0  # spectral centroid in Hz
    flatness: float = 0.0  # 0 = tonal, 1 = noise
    rolloff: float = 0.0   # 90% energy rolloff frequency
    # Amplitude
    peak_l: float = 0.0
    peak_r: float = 0.0
    rms_l: float = 0.0
    rms_r: float = 0.0


# Note names for pitch detection
NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


def freq_to_note(freq: float) -> tuple[str, int, float]:
    """Convert frequency to note name, octave, and cents deviation.

    Returns (note_name, octave, cents) e.g., ('A', 4, 0.0) for 440Hz
    """
    if freq <= 0:
        return ('?', 0, 0.0)

    # A4 = 440Hz = MIDI note 69
    midi_note = 12 * math.log2(freq / 440.0) + 69
    midi_rounded = round(midi_note)
    cents = (midi_note - midi_rounded) * 100

    note_index = midi_rounded % 12
    octave = (midi_rounded // 12) - 1

    return (NOTE_NAMES[note_index], octave, cents)


def amp_to_db(amp: float) -> float:
    """Convert linear amplitude to decibels."""
    if amp <= 0:
        return -float('inf')
    return 20 * math.log10(amp)


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
        pass

    def _handle_fail(self, address: str, *args):
        """Handle /fail messages."""
        sys.stderr.write(f"[SC] Fail: {args}\n")

    def _handle_analysis(self, address: str, *args):
        """Handle /mcp/analysis messages from the analyzer synth.

        Expected args: [node_id, reply_id, freq, has_freq, centroid, flatness, rolloff, peak_l, peak_r, rms_l, rms_r]
        """
        if len(args) >= 11:
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
        # Clean up existing connection first
        if self._reply_server:
            self._reply_server.shutdown()
            self._reply_server = None

        try:
            # Set up OSC reply server FIRST (it binds to REPLY_PORT)
            disp = dispatcher.Dispatcher()
            disp.map("/status.reply", self._handle_status_reply)
            disp.map("/done", self._handle_done)
            disp.map("/fail", self._handle_fail)
            disp.map("/n_end", self._handle_node_end)
            disp.map("/mcp/analysis", self._handle_analysis)
            disp.map("/mcp/meter", self._handle_meter)

            self._reply_server = ReuseAddrOSCUDPServer(
                (SCSYNTH_HOST, REPLY_PORT), disp
            )
            thread = threading.Thread(target=self._reply_server.serve_forever, daemon=True)
            thread.start()

            # Query status to verify connection (uses the same socket for send/receive)
            status = self.get_status()
            if not status.running:
                return False, "scsynth not responding. Make sure SuperCollider server is running."

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


MAX_EVAL_TIMEOUT = 300.0  # Maximum allowed timeout (5 minutes)


def find_sclang() -> Optional[str]:
    """Find the sclang executable path."""
    # Check if sclang is in PATH
    sclang_path = shutil.which("sclang")
    if sclang_path:
        return sclang_path

    # Platform-specific common locations
    import platform
    system = platform.system()

    if system == "Darwin":  # macOS
        paths = [
            "/Applications/SuperCollider.app/Contents/MacOS/sclang",
            "/Applications/SuperCollider/SuperCollider.app/Contents/MacOS/sclang",
            "~/Applications/SuperCollider.app/Contents/MacOS/sclang",
        ]
    elif system == "Linux":
        paths = [
            "/usr/bin/sclang",
            "/usr/local/bin/sclang",
            "/opt/SuperCollider/bin/sclang",
        ]
    elif system == "Windows":
        paths = [
            r"C:\Program Files\SuperCollider\sclang.exe",
            r"C:\Program Files (x86)\SuperCollider\sclang.exe",
        ]
    else:
        paths = []

    for path in paths:
        expanded = os.path.expanduser(path)
        if os.path.isfile(expanded):
            return expanded

    return None


# Prefixes to filter from sclang stderr (startup noise)
SCLANG_STDERR_SKIP_PREFIXES = (
    'compiling class library',
    'NumPrimitives',
    'Welcome to SuperCollider',
    "type 'help'",
    'Found',
    'Compiling',
    'Read',
)


def eval_sclang(code: str, timeout: float = 30.0) -> tuple[bool, str]:
    """Execute SuperCollider code via sclang subprocess.

    Args:
        code: SuperCollider code to execute
        timeout: Maximum execution time in seconds (default 30, max 300)

    Returns:
        (success, output) tuple
    """
    # Validate input
    if not code or not code.strip():
        return False, "No code provided"

    # Cap timeout to prevent excessive waits
    timeout = min(timeout, MAX_EVAL_TIMEOUT)

    sclang = find_sclang()
    if not sclang:
        return False, "sclang not found. Make sure SuperCollider is installed and sclang is in PATH or at standard location."

    # sclang doesn't support -e flag, so we write code to a temp file
    # Ensure code ends with semicolon, then append 0.exit to exit after execution
    code_stripped = code.rstrip()
    if not code_stripped.endswith(';'):
        code_stripped += ';'
    code_with_exit = code_stripped + "\n0.exit;\n"

    temp_path = None
    proc = None
    try:
        # Create a temporary .scd file
        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.scd',
            delete=False,
        ) as f:
            f.write(code_with_exit)
            temp_path = f.name

        proc = subprocess.Popen(
            [sclang, temp_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Kill the process and reap it
            proc.kill()
            proc.wait()
            return False, f"sclang execution timed out after {timeout}s"

        # Combine stdout and stderr
        output_parts = []
        if stdout and stdout.strip():
            output_parts.append(stdout.strip())
        if stderr and stderr.strip():
            # Filter out common sclang startup noise using prefix matching
            stderr_lines = []
            for line in stderr.strip().split('\n'):
                stripped = line.strip()
                # Skip lines that start with known noise prefixes
                if stripped.startswith(SCLANG_STDERR_SKIP_PREFIXES):
                    continue
                stderr_lines.append(line)
            if stderr_lines:
                output_parts.append("stderr: " + '\n'.join(stderr_lines))

        output = '\n'.join(output_parts) if output_parts else "(no output)"

        # Non-zero return code indicates error (but 0.exit returns 0)
        if proc.returncode != 0:
            return False, f"sclang exited with code {proc.returncode}\n{output}"

        return True, output

    except FileNotFoundError:
        return False, f"sclang not found at {sclang}"
    except Exception as e:
        return False, f"Error executing sclang: {e}"
    finally:
        # Always clean up temp file
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


# Global client instance
sc_client = SCClient()

# Create MCP server
mcp = FastMCP("sc-repl")


@mcp.tool()
def sc_connect() -> str:
    """Connect to the SuperCollider server (scsynth). Make sure SuperCollider.app is running with the server booted."""
    _, message = sc_client.connect()
    return message


@mcp.tool()
def sc_status() -> str:
    """Get current SuperCollider server status (running, CPU, synths, groups)."""
    status = sc_client.get_status()
    if not status.running:
        return "SuperCollider server is not running. Use sc_connect first (and make sure SuperCollider.app server is booted)."

    return f"""SuperCollider Server Status:
- Running: {status.running}
- Sample Rate: {status.sample_rate} Hz
- UGens: {status.num_ugens}
- Synths: {status.num_synths}
- Groups: {status.num_groups}
- SynthDefs: {status.num_synthdefs}
- CPU (avg): {status.avg_cpu:.2f}%
- CPU (peak): {status.peak_cpu:.2f}%"""


@mcp.tool()
def sc_play_sine(freq: float = 440.0, amp: float = 0.1, dur: float = 1.0) -> str:
    """Play a sine wave tone.

    Args:
        freq: Frequency in Hz (default 440)
        amp: Amplitude 0-1 (default 0.1)
        dur: Duration in seconds (default 1)
    """
    _, message = sc_client.play_sine(freq=freq, amp=amp, dur=dur)
    return message


@mcp.tool()
def sc_free_all() -> str:
    """Free all running synths on the server."""
    _, message = sc_client.free_all()
    return message


@mcp.tool()
def sc_start_analyzer() -> str:
    """Start the audio analyzer to monitor pitch, timbre, and amplitude.

    Requires the mcp_synthdefs.scd file to be run in SuperCollider IDE first.
    The analyzer monitors the main output bus and provides real-time analysis.
    """
    _, message = sc_client.start_analyzer()
    return message


@mcp.tool()
def sc_stop_analyzer() -> str:
    """Stop the audio analyzer."""
    _, message = sc_client.stop_analyzer()
    return message


@mcp.tool()
def sc_get_analysis() -> str:
    """Get the latest audio analysis data.

    Returns pitch (frequency, note, cents deviation), timbre (spectral centroid,
    flatness, inferred waveform type), and amplitude (peak, RMS, dB) information.

    The analyzer must be running (call sc_start_analyzer first).
    """
    success, message, data = sc_client.get_analysis()
    if not success:
        return message

    # Format as readable string
    p = data["pitch"]
    t = data["timbre"]
    a = data["amplitude"]

    lines = [
        "Audio Analysis:",
        "",
        f"Pitch: {p['note']} ({p['freq']} Hz, {p['cents']:+.1f} cents)",
        f"  Confidence: {p['confidence']:.0%}",
        "",
        f"Timbre: {t['type']}",
        f"  Spectral centroid: {t['centroid']:.0f} Hz",
        f"  Flatness: {t['flatness']:.3f} (0=tonal, 1=noise)",
        f"  Rolloff (90%): {t['rolloff']:.0f} Hz",
        "",
        f"Amplitude:",
        f"  Peak: L={a['peak_l']:.4f} R={a['peak_r']:.4f}",
        f"  RMS:  L={a['rms_l']:.4f} R={a['rms_r']:.4f}",
        f"  dB:   L={a['db_l']:.1f} R={a['db_r']:.1f}",
        "",
        f"Silent: {data['is_silent']}",
        f"Clipping: {data['is_clipping']}",
    ]

    return "\n".join(lines)


@mcp.tool()
def sc_play_synth(
    synthdef: str,
    params: Optional[dict[str, Any]] = None,
    dur: Optional[float] = None,
    sustain: bool = True,
) -> str:
    """Play any SynthDef with custom parameters.

    Args:
        synthdef: Name of the SynthDef to play (must be loaded in scsynth)
        params: Dictionary of parameter name -> value pairs (e.g., {"freq": 440, "amp": 0.1})
        dur: Duration in seconds. If not provided, synth plays until freed with sc_free_all.
        sustain: If True (default), releases envelope after dur. If False, hard-frees the synth.

    Example:
        sc_play_synth("mySynth", params={"freq": 330, "amp": 0.2}, dur=2.0)
    """
    _, message = sc_client.play_synth(
        synthdef=synthdef,
        params=params,
        dur=dur,
        sustain=sustain,
    )
    return message


@mcp.tool()
def sc_eval(code: str, timeout: float = 30.0) -> str:
    """Execute arbitrary SuperCollider (sclang) code.

    This spawns a new sclang process to execute the code. Useful for:
    - Defining and loading new SynthDefs
    - Testing SuperCollider expressions
    - Running one-off synthesis code

    Args:
        code: SuperCollider code to execute
        timeout: Maximum execution time in seconds (default 30)

    Example:
        sc_eval("SynthDef(\\\\test, { Out.ar(0, SinOsc.ar(440) * 0.1) }).add")
        sc_eval("{ SinOsc.ar(440) * 0.1 }.play")

    Note: Each call spawns a fresh sclang process, so state doesn't persist between calls.
    For persistent synths, define SynthDefs and use sc_play_synth to trigger them.
    """
    success, output = eval_sclang(code, timeout=timeout)
    if success:
        return f"Executed successfully:\n{output}"
    return f"Error:\n{output}"


def _cleanup():
    """Clean up resources on exit."""
    sc_client.disconnect()


def _signal_handler(signum, frame):
    """Handle termination signals gracefully."""
    _cleanup()
    sys.exit(0)


# Register cleanup handlers
atexit.register(_cleanup)
signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


def main():
    """Main entry point."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
