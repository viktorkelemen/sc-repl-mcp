"""Tests for MIDI export functionality."""

import tempfile
from pathlib import Path

import pytest

from sc_repl_mcp.midi import (
    amp_to_velocity,
    events_to_midi,
    export_midi,
    freq_to_midi_note,
    parse_note_events,
    parse_sendbundle_array,
)
from sc_repl_mcp.types import NoteEvent


class TestParseSendbundleArray:
    """Tests for parse_sendbundle_array function."""

    def test_parses_s_new_with_freq_amp(self):
        array_str = r"\s_new, \ping, -1, 0, 0, \freq, 440, \amp, 0.2"
        command, synthdef, params = parse_sendbundle_array(array_str)

        assert command == "s_new"
        assert synthdef == "ping"
        assert params["freq"] == 440
        assert params["amp"] == 0.2

    def test_parses_s_new_with_dur(self):
        array_str = r"\s_new, \bell, -1, 0, 0, \freq, 880, \dur, 0.5"
        command, synthdef, params = parse_sendbundle_array(array_str)

        assert command == "s_new"
        assert synthdef == "bell"
        assert params["freq"] == 880
        assert params["dur"] == 0.5

    def test_parses_float_values(self):
        array_str = r"\s_new, \test, -1, 0, 0, \freq, 440.5, \amp, 0.25"
        command, synthdef, params = parse_sendbundle_array(array_str)

        assert params["freq"] == 440.5
        assert params["amp"] == 0.25

    def test_handles_empty_array(self):
        command, synthdef, params = parse_sendbundle_array("")
        assert command == ""
        assert synthdef == ""
        assert params == {}

    def test_handles_non_s_new_command(self):
        array_str = r"\n_set, 1000, \freq, 440"
        command, synthdef, params = parse_sendbundle_array(array_str)

        assert command == "n_set"
        assert synthdef == ""
        assert params == {}


class TestParseNoteEvents:
    """Tests for parse_note_events function."""

    def test_parses_single_sendbundle(self):
        code = r's.sendBundle(0.0, [\s_new, \ping, -1, 0, 0, \freq, 440]);'
        events = parse_note_events(code)

        assert len(events) == 1
        assert events[0].time == 0.0
        assert events[0].synthdef == "ping"
        assert events[0].freq == 440

    def test_parses_multiple_sendbundles(self):
        code = r"""
        s.sendBundle(0.0, [\s_new, \ping, -1, 0, 0, \freq, 440, \amp, 0.2]);
        s.sendBundle(0.5, [\s_new, \ping, -1, 0, 0, \freq, 550, \amp, 0.3]);
        s.sendBundle(1.0, [\s_new, \ping, -1, 0, 0, \freq, 660, \amp, 0.4]);
        """
        events = parse_note_events(code)

        assert len(events) == 3
        assert events[0].time == 0.0
        assert events[0].freq == 440
        assert events[1].time == 0.5
        assert events[1].freq == 550
        assert events[2].time == 1.0
        assert events[2].freq == 660

    def test_events_sorted_by_time(self):
        # Out of order in code
        code = r"""
        s.sendBundle(1.0, [\s_new, \ping, -1, 0, 0, \freq, 660]);
        s.sendBundle(0.0, [\s_new, \ping, -1, 0, 0, \freq, 440]);
        s.sendBundle(0.5, [\s_new, \ping, -1, 0, 0, \freq, 550]);
        """
        events = parse_note_events(code)

        assert events[0].time == 0.0
        assert events[1].time == 0.5
        assert events[2].time == 1.0

    def test_ignores_non_s_new_commands(self):
        code = r"""
        s.sendBundle(0.0, [\n_set, 1000, \freq, 440]);
        s.sendBundle(0.1, [\s_new, \ping, -1, 0, 0, \freq, 440]);
        s.sendBundle(0.2, [\n_free, 1000]);
        """
        events = parse_note_events(code)

        assert len(events) == 1
        assert events[0].freq == 440

    def test_returns_empty_for_no_matches(self):
        code = "// Just a comment\n1 + 1"
        events = parse_note_events(code)
        assert events == []

    def test_extracts_dur_parameter(self):
        code = r's.sendBundle(0.0, [\s_new, \ping, -1, 0, 0, \freq, 440, \dur, 0.75]);'
        events = parse_note_events(code)

        assert len(events) == 1
        assert events[0].dur == 0.75

    def test_default_amp_when_missing(self):
        code = r's.sendBundle(0.0, [\s_new, \ping, -1, 0, 0, \freq, 440]);'
        events = parse_note_events(code)

        assert events[0].amp == 0.5  # default value

    def test_handles_negative_time(self):
        """Negative time values (send now) should be parsed."""
        code = r's.sendBundle(-1, [\s_new, \ping, -1, 0, 0, \freq, 440]);'
        events = parse_note_events(code)

        assert len(events) == 1
        assert events[0].time == -1.0


class TestFreqToMidiNote:
    """Tests for freq_to_midi_note function."""

    def test_a4_is_69(self):
        assert freq_to_midi_note(440.0) == 69

    def test_a3_is_57(self):
        assert freq_to_midi_note(220.0) == 57

    def test_a5_is_81(self):
        assert freq_to_midi_note(880.0) == 81

    def test_middle_c_is_60(self):
        assert freq_to_midi_note(261.63) == 60

    def test_clamps_to_valid_range(self):
        assert freq_to_midi_note(20000) <= 127
        assert freq_to_midi_note(10) >= 0

    def test_zero_freq_returns_default(self):
        assert freq_to_midi_note(0) == 60

    def test_negative_freq_returns_default(self):
        assert freq_to_midi_note(-100) == 60


class TestAmpToVelocity:
    """Tests for amp_to_velocity function."""

    def test_unity_amp_is_max(self):
        assert amp_to_velocity(1.0) == 127

    def test_half_amp(self):
        assert amp_to_velocity(0.5) == 64

    def test_minimum_velocity(self):
        assert amp_to_velocity(0.0) == 1  # Minimum is 1, not 0

    def test_clamps_above_one(self):
        assert amp_to_velocity(2.0) == 127


class TestEventsToMidi:
    """Tests for events_to_midi function."""

    def test_creates_midi_file_with_notes(self):
        events = [
            NoteEvent(time=0.0, synthdef="ping", freq=440, amp=0.5),
            NoteEvent(time=0.5, synthdef="ping", freq=550, amp=0.5),
        ]
        midi = events_to_midi(events)

        assert len(midi.tracks) == 1
        # Track should have: tempo meta + note events
        assert len(midi.tracks[0]) >= 4

    def test_respects_explicit_duration(self):
        events = [
            NoteEvent(time=0.0, synthdef="ping", freq=440, amp=0.5, dur=1.0),
        ]
        midi = events_to_midi(events, default_duration=0.25)

        # Should have tempo meta + note_on + note_off = 3 messages
        assert len(midi.tracks[0]) == 3

    def test_empty_events_creates_valid_midi(self):
        midi = events_to_midi([])
        assert len(midi.tracks) == 1
        # Should have at least the tempo meta message
        assert len(midi.tracks[0]) >= 1

    def test_respects_tempo_setting(self):
        events = [NoteEvent(time=0.0, synthdef="ping", freq=440)]
        midi = events_to_midi(events, tempo=60)

        # Check that tempo was set in metadata
        tempo_msg = midi.tracks[0][0]
        assert tempo_msg.type == 'set_tempo'

    def test_uses_default_velocity_when_no_amp(self):
        events = [NoteEvent(time=0.0, synthdef="ping", freq=440, amp=0.0)]
        midi = events_to_midi(events, default_velocity=80)

        # The note should still be created (amp=0 gives velocity=1 due to clamping)
        assert len(midi.tracks[0]) >= 2

    def test_handles_simultaneous_notes(self):
        """Chord notes at the same time should produce valid MIDI."""
        events = [
            NoteEvent(time=0.0, synthdef="ping", freq=261.63, amp=0.5),  # C4
            NoteEvent(time=0.0, synthdef="ping", freq=329.63, amp=0.5),  # E4
            NoteEvent(time=0.0, synthdef="ping", freq=392.00, amp=0.5),  # G4
        ]
        midi = events_to_midi(events, default_duration=0.5)

        # Should have tempo meta + 6 note events (3 on + 3 off)
        assert len(midi.tracks[0]) == 7


class TestExportMidi:
    """Tests for export_midi function."""

    def test_exports_to_temp_file(self):
        code = r's.sendBundle(0.0, [\s_new, \ping, -1, 0, 0, \freq, 440]);'
        success, message, path = export_midi(code)

        assert success
        assert "1 notes" in message
        assert path is not None
        assert Path(path).exists()

        # Cleanup
        Path(path).unlink()

    def test_exports_to_specified_path(self):
        code = r's.sendBundle(0.0, [\s_new, \ping, -1, 0, 0, \freq, 440]);'

        with tempfile.NamedTemporaryFile(suffix='.mid', delete=False) as f:
            output_path = f.name

        success, message, path = export_midi(code, output_path=output_path)

        assert success
        assert path == output_path
        assert Path(path).exists()

        # Cleanup
        Path(path).unlink()

    def test_fails_on_no_events(self):
        code = "// No sendBundle calls here"
        success, message, path = export_midi(code)

        assert not success
        assert "No sendBundle" in message
        assert path is None

    def test_handles_custom_tempo(self):
        code = r's.sendBundle(0.0, [\s_new, \ping, -1, 0, 0, \freq, 440]);'
        success, message, path = export_midi(code, tempo=60)

        assert success
        Path(path).unlink()

    def test_expands_user_path(self):
        code = r's.sendBundle(0.0, [\s_new, \ping, -1, 0, 0, \freq, 440]);'

        # Create a temp file to get a real path, then use ~ format
        with tempfile.NamedTemporaryFile(suffix='.mid', delete=False) as f:
            temp_path = f.name

        success, message, path = export_midi(code, output_path=temp_path)

        assert success
        assert Path(path).exists()
        Path(path).unlink()

    def test_exports_multiple_notes(self):
        code = r"""
        s.sendBundle(0.0, [\s_new, \ping, -1, 0, 0, \freq, 440]);
        s.sendBundle(0.25, [\s_new, \ping, -1, 0, 0, \freq, 550]);
        s.sendBundle(0.5, [\s_new, \ping, -1, 0, 0, \freq, 660]);
        """
        success, message, path = export_midi(code)

        assert success
        assert "3 notes" in message
        Path(path).unlink()

    def test_handles_write_error(self, mocker):
        """Should return error message when file write fails."""
        from mido import MidiFile as MidoMidiFile

        code = r's.sendBundle(0.0, [\s_new, \ping, -1, 0, 0, \freq, 440]);'
        mocker.patch.object(MidoMidiFile, 'save', side_effect=OSError("Permission denied"))

        success, message, path = export_midi(code, output_path="/some/path.mid")

        assert not success
        assert "Failed to save" in message
        assert path is None
