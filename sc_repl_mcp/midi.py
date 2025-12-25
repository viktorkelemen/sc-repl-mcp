"""MIDI export functionality for SC-REPL MCP Server.

Parses SuperCollider s.sendBundle() calls and exports to MIDI format.
"""

import math
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

from mido import Message, MetaMessage, MidiFile, MidiTrack, bpm2tempo, second2tick

from .types import NoteEvent


# Regex patterns for parsing sendBundle calls
SEND_BUNDLE_PATTERN = re.compile(
    r's\.sendBundle\s*\(\s*'
    r'(-?[0-9.]+)\s*,\s*'         # time argument (can be negative)
    r'\[([^\]]+)\]'               # array content
    r'\s*\)',
    re.MULTILINE
)

ARRAY_ELEMENT_PATTERN = re.compile(
    r'\\(\w+)|'           # Symbol like \freq
    r'(-?[0-9.]+)|'       # Number
    r'"([^"]*)"'          # String
)


def parse_sendbundle_array(array_str: str) -> tuple[str, str, dict]:
    """Parse the array content of a sendBundle call.

    Args:
        array_str: The content inside [...] of a sendBundle call

    Returns:
        Tuple of (command, synthdef, params_dict)
    """
    elements = []
    for match in ARRAY_ELEMENT_PATTERN.finditer(array_str):
        if match.group(1):  # Symbol
            elements.append(match.group(1))
        elif match.group(2):  # Number
            val = match.group(2)
            elements.append(float(val) if '.' in val else int(val))
        elif match.group(3):  # String
            elements.append(match.group(3))

    if not elements:
        return ("", "", {})

    command = elements[0] if elements else ""

    # For s_new: [command, synthdef, node_id, add_action, target, ...params]
    if command == "s_new" and len(elements) >= 2:
        synthdef = str(elements[1])
        # Parse key-value pairs after the first 5 elements
        params = {}
        i = 5
        while i + 1 < len(elements):
            key = str(elements[i])
            value = elements[i + 1]
            params[key] = value
            i += 2
        return (command, synthdef, params)

    return (command, "", {})


def parse_note_events(code: str) -> list[NoteEvent]:
    """Parse all sendBundle calls from SuperCollider code.

    Args:
        code: SuperCollider code containing s.sendBundle() calls

    Returns:
        List of NoteEvent objects sorted by time
    """
    events = []

    for match in SEND_BUNDLE_PATTERN.finditer(code):
        time = float(match.group(1))
        array_str = match.group(2)

        command, synthdef, params = parse_sendbundle_array(array_str)

        if command != "s_new":
            continue

        # Extract note-relevant parameters
        freq = float(params.get("freq", 440.0))
        amp = float(params.get("amp", 0.5))
        dur_val = params.get("dur")
        dur = float(dur_val) if dur_val is not None else None

        events.append(NoteEvent(
            time=time,
            synthdef=synthdef,
            freq=freq,
            amp=amp,
            dur=dur,
            params=params
        ))

    return sorted(events, key=lambda e: e.time)


def freq_to_midi_note(freq: float) -> int:
    """Convert frequency in Hz to MIDI note number.

    Uses A4 = 440 Hz = MIDI note 69 as reference.
    """
    if freq <= 0:
        return 60  # Default to middle C

    midi_note = 12 * math.log2(freq / 440.0) + 69
    return max(0, min(127, round(midi_note)))


def amp_to_velocity(amp: float) -> int:
    """Convert amplitude (0-1) to MIDI velocity (1-127)."""
    return max(1, min(127, round(amp * 127)))


def events_to_midi(
    events: list[NoteEvent],
    tempo: int = 120,
    ticks_per_beat: int = 480,
    default_duration: float = 0.25,
    default_velocity: int = 100,
) -> MidiFile:
    """Convert NoteEvent list to a MidiFile object.

    Args:
        events: List of NoteEvent objects
        tempo: Tempo in BPM
        ticks_per_beat: MIDI resolution (pulses per quarter note)
        default_duration: Default note duration in seconds
        default_velocity: Default MIDI velocity (1-127)

    Returns:
        MidiFile object ready to save
    """
    mid = MidiFile(ticks_per_beat=ticks_per_beat)
    track = MidiTrack()
    mid.tracks.append(track)

    # Add tempo meta message
    track.append(MetaMessage('set_tempo', tempo=bpm2tempo(tempo), time=0))

    if not events:
        return mid

    # Calculate duration for each note and build note data
    note_data = []
    for i, event in enumerate(events):
        midi_note = freq_to_midi_note(event.freq)
        velocity = amp_to_velocity(event.amp) if event.amp else default_velocity

        # Determine duration: explicit dur > gap to next note > default
        if event.dur is not None:
            duration = event.dur
        elif i + 1 < len(events):
            next_time = events[i + 1].time
            duration = min(next_time - event.time, default_duration * 4)
            if duration <= 0:
                duration = default_duration
        else:
            duration = default_duration

        note_data.append({
            'time': event.time,
            'note': midi_note,
            'velocity': velocity,
            'duration': duration,
        })

    # Build MIDI messages with proper delta times
    # Combine note_on and note_off events, sort by absolute time
    midi_events = []
    for nd in note_data:
        midi_events.append(('note_on', nd['time'], nd['note'], nd['velocity']))
        midi_events.append(('note_off', nd['time'] + nd['duration'], nd['note'], 0))

    # Sort by time (note_off before note_on for same time)
    midi_events.sort(key=lambda x: (x[1], 0 if x[0] == 'note_off' else 1))

    # Convert to delta times
    tempo_us = bpm2tempo(tempo)
    current_time = 0.0
    for event_type, abs_time, note, velocity in midi_events:
        delta_seconds = abs_time - current_time
        delta_ticks = int(second2tick(delta_seconds, ticks_per_beat, tempo_us))
        delta_ticks = max(0, delta_ticks)

        track.append(Message(event_type, note=note, velocity=velocity, time=delta_ticks))
        current_time = abs_time

    return mid


def export_midi(
    code: str,
    output_path: Optional[str] = None,
    tempo: int = 120,
    ticks_per_beat: int = 480,
    default_duration: float = 0.25,
    default_velocity: int = 100,
) -> tuple[bool, str, Optional[str]]:
    """Parse SC code and export to MIDI file.

    Args:
        code: SuperCollider code with sendBundle() calls
        output_path: Output path (optional, uses temp file if None)
        tempo: BPM (default 120)
        ticks_per_beat: MIDI resolution (default 480)
        default_duration: Default note duration in seconds
        default_velocity: Default MIDI velocity

    Returns:
        Tuple of (success, message, file_path)
    """
    # Parse events
    events = parse_note_events(code)

    if not events:
        return (False, "No sendBundle() s_new calls found in code", None)

    # Convert to MIDI
    midi_file = events_to_midi(
        events,
        tempo=tempo,
        ticks_per_beat=ticks_per_beat,
        default_duration=default_duration,
        default_velocity=default_velocity,
    )

    # Determine output path
    if output_path is None:
        fd, output_path = tempfile.mkstemp(suffix='.mid', prefix='sc_export_')
        os.close(fd)
    else:
        output_path = str(Path(output_path).expanduser())

    # Save
    try:
        midi_file.save(output_path)
    except OSError as e:
        return (False, f"Failed to save MIDI file: {e}", None)

    return (True, f"Exported {len(events)} notes to MIDI", output_path)
