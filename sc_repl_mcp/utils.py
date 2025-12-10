"""Utility functions for SC-REPL MCP Server."""

import math
import os
import signal
import subprocess
import time

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


def kill_process_on_port(port: int) -> bool:
    """Kill any process using the specified UDP port.

    Returns True if a process was killed.
    """
    try:
        # Use lsof to find process using the port (works on macOS and Linux)
        result = subprocess.run(
            ["lsof", "-t", "-i", f"UDP:{port}"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split('\n')
            my_pid = os.getpid()
            for pid_str in pids:
                try:
                    pid = int(pid_str)
                    if pid != my_pid:  # Don't kill ourselves
                        os.kill(pid, signal.SIGTERM)
                        # Give it a moment to die gracefully
                        time.sleep(0.1)
                except (ValueError, ProcessLookupError, PermissionError):
                    pass
            return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return False
