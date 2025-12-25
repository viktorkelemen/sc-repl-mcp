"""Data types for SC-REPL MCP Server."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LogEntry:
    """A log entry from the SuperCollider server."""
    timestamp: float
    category: str  # 'fail', 'done', 'node', 'osc', 'info'
    message: str


@dataclass
class ServerStatus:
    """SuperCollider server status information."""
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
    # Perceptual loudness (ITU-R BS.1770)
    loudness_sones: float = 0.0  # perceptual loudness in sones


@dataclass
class OnsetEvent:
    """An onset (attack/transient) detection event."""
    timestamp: float = 0.0
    freq: float = 0.0      # pitch at onset
    amplitude: float = 0.0  # amplitude at onset


@dataclass
class SpectrumData:
    """14-band spectrum analyzer data."""
    timestamp: float = 0.0
    # Band powers (logarithmic spacing from ~60Hz to ~16kHz)
    # Frequencies: 60, 100, 156, 244, 380, 594, 928, 1449, 2262, 3531, 5512, 8603, 13428, 16000 Hz
    bands: tuple = (0.0,) * 14  # 14 bands


@dataclass
class ReferenceSnapshot:
    """A captured reference sound for comparison.

    Used for sound matching workflows - capture a target sound's analysis
    and compare subsequent sounds against it.
    """
    name: str
    timestamp: float
    analysis: AnalysisData
    spectrum: Optional[SpectrumData] = None
    description: str = ""


@dataclass
class NoteEvent:
    """A parsed note event from sendBundle() for MIDI export."""
    time: float           # Start time in seconds
    synthdef: str         # SynthDef name
    freq: float           # Frequency in Hz
    amp: float = 0.5      # Amplitude 0-1
    dur: Optional[float] = None  # Duration if specified
    params: dict = field(default_factory=dict)  # All other params
