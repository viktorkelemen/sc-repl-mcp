"""Tests for sc_repl_mcp.utils pure functions."""

import math
import os
import subprocess
import pytest

from sc_repl_mcp.utils import freq_to_note, amp_to_db, kill_process_on_port, NOTE_NAMES


class TestFreqToNote:
    """Tests for freq_to_note function."""

    def test_a4_440hz(self):
        """A4 = 440Hz is the standard reference pitch."""
        note, octave, cents = freq_to_note(440.0)
        assert note == "A"
        assert octave == 4
        assert cents == pytest.approx(0.0, abs=0.1)

    def test_a3_220hz(self):
        """A3 = 220Hz, one octave below A4."""
        note, octave, cents = freq_to_note(220.0)
        assert note == "A"
        assert octave == 3
        assert cents == pytest.approx(0.0, abs=0.1)

    def test_a5_880hz(self):
        """A5 = 880Hz, one octave above A4."""
        note, octave, cents = freq_to_note(880.0)
        assert note == "A"
        assert octave == 5
        assert cents == pytest.approx(0.0, abs=0.1)

    def test_middle_c_261hz(self):
        """C4 (middle C) is approximately 261.63Hz."""
        note, octave, cents = freq_to_note(261.63)
        assert note == "C"
        assert octave == 4
        assert abs(cents) < 5  # Within 5 cents

    def test_e4_329hz(self):
        """E4 is approximately 329.63Hz."""
        note, octave, cents = freq_to_note(329.63)
        assert note == "E"
        assert octave == 4
        assert abs(cents) < 5

    def test_sharp_notes(self):
        """Test that sharp notes are detected correctly."""
        # C#4 is approximately 277.18Hz
        note, octave, cents = freq_to_note(277.18)
        assert note == "C#"
        assert octave == 4
        assert abs(cents) < 5

    def test_zero_frequency(self):
        """Zero frequency should return unknown note."""
        note, octave, cents = freq_to_note(0.0)
        assert note == "?"
        assert octave == 0
        assert cents == 0.0

    def test_negative_frequency(self):
        """Negative frequency should return unknown note."""
        note, octave, cents = freq_to_note(-100.0)
        assert note == "?"
        assert octave == 0
        assert cents == 0.0

    def test_very_low_frequency(self):
        """Very low audible frequency (sub-bass)."""
        note, octave, cents = freq_to_note(27.5)  # A0
        assert note == "A"
        assert octave == 0
        assert abs(cents) < 5

    def test_very_high_frequency(self):
        """Very high frequency near hearing limit."""
        note, octave, cents = freq_to_note(14080.0)  # A9
        assert note == "A"
        assert octave == 9
        assert abs(cents) < 5

    def test_cents_positive_deviation(self):
        """Frequency slightly sharp should give positive cents."""
        # 10 cents sharp of A4 is approximately 442.55Hz
        note, octave, cents = freq_to_note(442.55)
        assert note == "A"
        assert octave == 4
        assert cents > 0
        assert cents == pytest.approx(10.0, abs=1.0)

    def test_cents_negative_deviation(self):
        """Frequency slightly flat should give negative cents."""
        # 10 cents flat of A4 is approximately 437.47Hz
        note, octave, cents = freq_to_note(437.47)
        assert note == "A"
        assert octave == 4
        assert cents < 0
        assert cents == pytest.approx(-10.0, abs=1.0)

    def test_all_note_names_reachable(self):
        """All 12 note names should be reachable."""
        # Frequencies for all notes in octave 4 (equal temperament)
        a4 = 440.0
        notes_found = set()
        for semitone in range(12):
            freq = a4 * (2 ** (semitone / 12))
            note, _, _ = freq_to_note(freq)
            notes_found.add(note)

        assert notes_found == set(NOTE_NAMES)


class TestAmpToDb:
    """Tests for amp_to_db function."""

    def test_unity_gain(self):
        """Amplitude of 1.0 should be 0 dB."""
        assert amp_to_db(1.0) == pytest.approx(0.0)

    def test_half_amplitude(self):
        """Amplitude of 0.5 should be approximately -6 dB."""
        assert amp_to_db(0.5) == pytest.approx(-6.02, abs=0.1)

    def test_tenth_amplitude(self):
        """Amplitude of 0.1 should be -20 dB."""
        assert amp_to_db(0.1) == pytest.approx(-20.0)

    def test_hundredth_amplitude(self):
        """Amplitude of 0.01 should be -40 dB."""
        assert amp_to_db(0.01) == pytest.approx(-40.0)

    def test_double_amplitude(self):
        """Amplitude of 2.0 should be approximately +6 dB."""
        assert amp_to_db(2.0) == pytest.approx(6.02, abs=0.1)

    def test_zero_amplitude(self):
        """Amplitude of 0 should be negative infinity."""
        result = amp_to_db(0.0)
        assert result == float('-inf')

    def test_negative_amplitude(self):
        """Negative amplitude should be negative infinity."""
        result = amp_to_db(-0.5)
        assert result == float('-inf')

    def test_very_small_amplitude(self):
        """Very small amplitude should give very negative dB."""
        result = amp_to_db(0.000001)
        assert result == pytest.approx(-120.0)

    def test_typical_audio_levels(self):
        """Test typical audio amplitude values."""
        # Soft sound
        assert amp_to_db(0.05) == pytest.approx(-26.0, abs=0.5)
        # Medium sound
        assert amp_to_db(0.2) == pytest.approx(-14.0, abs=0.5)
        # Loud sound
        assert amp_to_db(0.7) == pytest.approx(-3.1, abs=0.5)


class TestKillProcessOnPort:
    """Tests for kill_process_on_port function."""

    def test_no_process_on_port(self, mocker):
        """Should return False when no process is using the port."""
        mock_run = mocker.patch("sc_repl_mcp.utils.subprocess.run")
        mock_run.return_value = mocker.MagicMock(
            returncode=1,  # lsof returns 1 when no process found
            stdout=""
        )

        result = kill_process_on_port(12345)

        assert result is False
        mock_run.assert_called_once()

    def test_kills_process_on_port(self, mocker):
        """Should kill process and return True when process found."""
        mock_run = mocker.patch("sc_repl_mcp.utils.subprocess.run")
        mock_run.return_value = mocker.MagicMock(
            returncode=0,
            stdout="12345\n"
        )
        mock_kill = mocker.patch("sc_repl_mcp.utils.os.kill")
        mock_getpid = mocker.patch("sc_repl_mcp.utils.os.getpid", return_value=99999)
        mock_sleep = mocker.patch("sc_repl_mcp.utils.time.sleep")

        result = kill_process_on_port(57130)

        assert result is True
        mock_kill.assert_called_once_with(12345, mocker.ANY)
        mock_sleep.assert_called_once_with(0.1)

    def test_skips_own_process(self, mocker):
        """Should not kill own process."""
        mock_run = mocker.patch("sc_repl_mcp.utils.subprocess.run")
        mock_run.return_value = mocker.MagicMock(
            returncode=0,
            stdout="12345\n"
        )
        mock_kill = mocker.patch("sc_repl_mcp.utils.os.kill")
        # Return same PID as in lsof output
        mock_getpid = mocker.patch("sc_repl_mcp.utils.os.getpid", return_value=12345)

        result = kill_process_on_port(57130)

        assert result is True
        mock_kill.assert_not_called()  # Should not kill itself

    def test_handles_multiple_pids(self, mocker):
        """Should handle multiple PIDs on same port."""
        mock_run = mocker.patch("sc_repl_mcp.utils.subprocess.run")
        mock_run.return_value = mocker.MagicMock(
            returncode=0,
            stdout="12345\n12346\n12347\n"
        )
        mock_kill = mocker.patch("sc_repl_mcp.utils.os.kill")
        mock_getpid = mocker.patch("sc_repl_mcp.utils.os.getpid", return_value=99999)
        mock_sleep = mocker.patch("sc_repl_mcp.utils.time.sleep")

        result = kill_process_on_port(57130)

        assert result is True
        assert mock_kill.call_count == 3

    def test_handles_invalid_pid(self, mocker):
        """Should handle non-numeric PID gracefully."""
        mock_run = mocker.patch("sc_repl_mcp.utils.subprocess.run")
        mock_run.return_value = mocker.MagicMock(
            returncode=0,
            stdout="not_a_pid\n12345\n"
        )
        mock_kill = mocker.patch("sc_repl_mcp.utils.os.kill")
        mock_getpid = mocker.patch("sc_repl_mcp.utils.os.getpid", return_value=99999)
        mock_sleep = mocker.patch("sc_repl_mcp.utils.time.sleep")

        result = kill_process_on_port(57130)

        assert result is True
        # Should only kill the valid PID
        mock_kill.assert_called_once_with(12345, mocker.ANY)

    def test_handles_process_not_found(self, mocker):
        """Should handle ProcessLookupError gracefully."""
        mock_run = mocker.patch("sc_repl_mcp.utils.subprocess.run")
        mock_run.return_value = mocker.MagicMock(
            returncode=0,
            stdout="12345\n"
        )
        mock_kill = mocker.patch("sc_repl_mcp.utils.os.kill")
        mock_kill.side_effect = ProcessLookupError("No such process")
        mock_getpid = mocker.patch("sc_repl_mcp.utils.os.getpid", return_value=99999)

        result = kill_process_on_port(57130)

        assert result is True  # Still returns True since we tried

    def test_handles_permission_error(self, mocker):
        """Should handle PermissionError gracefully."""
        mock_run = mocker.patch("sc_repl_mcp.utils.subprocess.run")
        mock_run.return_value = mocker.MagicMock(
            returncode=0,
            stdout="12345\n"
        )
        mock_kill = mocker.patch("sc_repl_mcp.utils.os.kill")
        mock_kill.side_effect = PermissionError("Operation not permitted")
        mock_getpid = mocker.patch("sc_repl_mcp.utils.os.getpid", return_value=99999)

        result = kill_process_on_port(57130)

        assert result is True

    def test_handles_timeout(self, mocker):
        """Should handle subprocess timeout gracefully."""
        mock_run = mocker.patch("sc_repl_mcp.utils.subprocess.run")
        mock_run.side_effect = subprocess.TimeoutExpired("lsof", 5)

        result = kill_process_on_port(57130)

        assert result is False

    def test_handles_lsof_not_found(self, mocker):
        """Should handle lsof not being available."""
        mock_run = mocker.patch("sc_repl_mcp.utils.subprocess.run")
        mock_run.side_effect = FileNotFoundError("lsof not found")

        result = kill_process_on_port(57130)

        assert result is False
