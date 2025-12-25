"""Tests for MCP tool wrappers in sc_repl_mcp.tools.

These tests use the mock_sc_client fixture to patch the global singleton.
"""

import pytest
from unittest.mock import Mock

from sc_repl_mcp.types import ServerStatus, AnalysisData, LogEntry


class TestScConnect:
    """Tests for sc_connect tool."""

    def test_returns_client_message(self, mock_sc_client):
        mock_sc_client.connect.return_value = (True, "Connected to scsynth on port 57110")

        from sc_repl_mcp.tools import sc_connect
        result = sc_connect()

        assert result == "Connected to scsynth on port 57110"
        mock_sc_client.connect.assert_called_once()

    def test_returns_error_message(self, mock_sc_client):
        mock_sc_client.connect.return_value = (False, "scsynth not responding")

        from sc_repl_mcp.tools import sc_connect
        result = sc_connect()

        assert result == "scsynth not responding"


class TestScStatus:
    """Tests for sc_status tool."""

    def test_formats_running_status(self, mock_sc_client):
        mock_sc_client.get_status.return_value = ServerStatus(
            running=True,
            num_ugens=100,
            num_synths=10,
            num_groups=5,
            num_synthdefs=200,
            avg_cpu=15.5,
            peak_cpu=25.3,
            sample_rate=48000.0,
        )

        from sc_repl_mcp.tools import sc_status
        result = sc_status()

        assert "Running: True" in result
        assert "Sample Rate: 48000.0 Hz" in result
        assert "UGens: 100" in result
        assert "Synths: 10" in result
        assert "Groups: 5" in result
        assert "SynthDefs: 200" in result
        assert "CPU (avg): 15.50%" in result
        assert "CPU (peak): 25.30%" in result

    def test_returns_not_running_message(self, mock_sc_client):
        mock_sc_client.get_status.return_value = ServerStatus(running=False)

        from sc_repl_mcp.tools import sc_status
        result = sc_status()

        assert "not running" in result
        assert "sc_connect" in result


class TestScPlaySine:
    """Tests for sc_play_sine tool."""

    def test_passes_parameters(self, mock_sc_client):
        mock_sc_client.play_sine.return_value = (True, "Playing 440Hz sine wave for 1s")

        from sc_repl_mcp.tools import sc_play_sine
        result = sc_play_sine(freq=880.0, amp=0.2, dur=2.0)

        mock_sc_client.play_sine.assert_called_once_with(freq=880.0, amp=0.2, dur=2.0)

    def test_uses_defaults(self, mock_sc_client):
        mock_sc_client.play_sine.return_value = (True, "Playing")

        from sc_repl_mcp.tools import sc_play_sine
        sc_play_sine()

        mock_sc_client.play_sine.assert_called_once_with(freq=440.0, amp=0.1, dur=1.0)


class TestScFreeAll:
    """Tests for sc_free_all tool."""

    def test_returns_message(self, mock_sc_client):
        mock_sc_client.free_all.return_value = (True, "All synths freed")

        from sc_repl_mcp.tools import sc_free_all
        result = sc_free_all()

        assert result == "All synths freed"
        mock_sc_client.free_all.assert_called_once()


class TestScStartAnalyzer:
    """Tests for sc_start_analyzer tool."""

    def test_returns_message(self, mock_sc_client):
        mock_sc_client.start_analyzer.return_value = (True, "Analyzer started")

        from sc_repl_mcp.tools import sc_start_analyzer
        result = sc_start_analyzer()

        assert result == "Analyzer started"


class TestScStopAnalyzer:
    """Tests for sc_stop_analyzer tool."""

    def test_returns_message(self, mock_sc_client):
        mock_sc_client.stop_analyzer.return_value = (True, "Analyzer stopped")

        from sc_repl_mcp.tools import sc_stop_analyzer
        result = sc_stop_analyzer()

        assert result == "Analyzer stopped"


class TestScGetAnalysis:
    """Tests for sc_get_analysis tool."""

    def test_formats_analysis_data(self, mock_sc_client):
        mock_sc_client.get_analysis.return_value = (
            True,
            "Analysis data retrieved",
            {
                "pitch": {"freq": 440.0, "note": "A4", "cents": 0.0, "confidence": 0.95},
                "timbre": {"centroid": 880.0, "flatness": 0.1, "rolloff": 4000.0},
                "amplitude": {"peak_l": 0.8, "peak_r": 0.75, "rms_l": 0.3, "rms_r": 0.28, "db_l": -10.5, "db_r": -11.1},
                "loudness": {"sones": 12.5},
                "is_silent": False,
                "is_clipping": False,
            }
        )

        from sc_repl_mcp.tools import sc_get_analysis
        result = sc_get_analysis()

        assert "Audio Analysis:" in result
        assert "Pitch: A4 (440.0 Hz" in result
        assert "Confidence: 95%" in result
        assert "Timbre:" in result
        assert "Spectral centroid: 880 Hz" in result
        assert "Flatness: 0.100" in result
        assert "Peak: L=0.8000 R=0.7500" in result
        assert "RMS:  L=0.3000 R=0.2800" in result
        assert "Loudness: 12.5 sones" in result
        assert "Silent: False" in result
        assert "Clipping: False" in result

    def test_returns_error_when_not_running(self, mock_sc_client):
        mock_sc_client.get_analysis.return_value = (
            False,
            "Analyzer not running. Call sc_start_analyzer first.",
            None
        )

        from sc_repl_mcp.tools import sc_get_analysis
        result = sc_get_analysis()

        assert "Analyzer not running" in result


class TestScGetOnsets:
    """Tests for sc_get_onsets tool."""

    def test_formats_onset_events(self, mock_sc_client):
        from sc_repl_mcp.types import OnsetEvent

        mock_sc_client.get_onsets.return_value = [
            OnsetEvent(timestamp=1000.0, freq=440.0, amplitude=0.5),
            OnsetEvent(timestamp=1000.1, freq=880.0, amplitude=0.6),
        ]

        from sc_repl_mcp.tools import sc_get_onsets
        result = sc_get_onsets()

        assert "Onset Events (2 detected)" in result
        assert "440" in result
        assert "880" in result
        assert "A4" in result  # Note name for 440 Hz
        assert "A5" in result  # Note name for 880 Hz

    def test_returns_no_events_message(self, mock_sc_client):
        mock_sc_client.get_onsets.return_value = []

        from sc_repl_mcp.tools import sc_get_onsets
        result = sc_get_onsets()

        assert "No onset events detected" in result


class TestScGetSpectrum:
    """Tests for sc_get_spectrum tool."""

    def test_formats_spectrum_data(self, mock_sc_client):
        mock_sc_client.get_spectrum.return_value = (
            True,
            "Spectrum data retrieved",
            {
                "bands": [
                    {"freq": 60, "power": 0.1, "db": -20.0},
                    {"freq": 100, "power": 0.2, "db": -14.0},
                    {"freq": 156, "power": 0.5, "db": -6.0},
                    {"freq": 244, "power": 0.3, "db": -10.4},
                    {"freq": 380, "power": 0.1, "db": -20.0},
                    {"freq": 594, "power": 0.1, "db": -20.0},
                    {"freq": 928, "power": 0.1, "db": -20.0},
                    {"freq": 1449, "power": 0.1, "db": -20.0},
                    {"freq": 2262, "power": 0.1, "db": -20.0},
                    {"freq": 3531, "power": 0.1, "db": -20.0},
                    {"freq": 5512, "power": 0.1, "db": -20.0},
                    {"freq": 8603, "power": 0.1, "db": -20.0},
                    {"freq": 13428, "power": 0.05, "db": -26.0},
                    {"freq": 16000, "power": 0.02, "db": -34.0},
                ],
                "band_frequencies": [60, 100, 156, 244, 380, 594, 928, 1449, 2262, 3531, 5512, 8603, 13428, 16000],
            }
        )

        from sc_repl_mcp.tools import sc_get_spectrum
        result = sc_get_spectrum()

        assert "Spectrum Analysis (14 bands)" in result
        assert "60 Hz" in result
        assert "16k Hz" in result or "16.0k Hz" in result
        assert "dB" in result

    def test_returns_error_when_not_running(self, mock_sc_client):
        mock_sc_client.get_spectrum.return_value = (
            False,
            "Analyzer not running. Call sc_start_analyzer first.",
            None
        )

        from sc_repl_mcp.tools import sc_get_spectrum
        result = sc_get_spectrum()

        assert "Analyzer not running" in result


class TestScPlaySynth:
    """Tests for sc_play_synth tool."""

    def test_passes_all_parameters(self, mock_sc_client):
        mock_sc_client.play_synth.return_value = (True, "Playing 'ping' for 2s")

        from sc_repl_mcp.tools import sc_play_synth
        result = sc_play_synth(
            synthdef="ping",
            params={"freq": 880, "amp": 0.2},
            dur=2.0,
            sustain=False
        )

        mock_sc_client.play_synth.assert_called_once_with(
            synthdef="ping",
            params={"freq": 880, "amp": 0.2},
            dur=2.0,
            sustain=False
        )

    def test_uses_defaults(self, mock_sc_client):
        mock_sc_client.play_synth.return_value = (True, "Playing")

        from sc_repl_mcp.tools import sc_play_synth
        sc_play_synth(synthdef="test")

        mock_sc_client.play_synth.assert_called_once_with(
            synthdef="test",
            params=None,
            dur=None,
            sustain=True
        )


class TestScLoadSynthdef:
    """Tests for sc_load_synthdef tool."""

    def test_wraps_code_in_synthdef(self, mocker):
        mock_eval = mocker.patch("sc_repl_mcp.tools.eval_sclang")
        mock_eval.return_value = (True, "SynthDef 'ping' loaded")

        from sc_repl_mcp.tools import sc_load_synthdef
        result = sc_load_synthdef(
            name="ping",
            code="arg freq = 440; Out.ar(0, SinOsc.ar(freq));"
        )

        assert result == "SynthDef 'ping' loaded successfully"

        # Check the code passed to eval_sclang
        call_args = mock_eval.call_args
        code = call_args[0][0]
        assert "SynthDef(\\ping" in code
        assert "arg freq = 440" in code
        assert "writeDefFile" in code
        assert "d_load" in code

    def test_returns_error_on_failure(self, mocker):
        mock_eval = mocker.patch("sc_repl_mcp.tools.eval_sclang")
        mock_eval.return_value = (False, "ERROR: syntax error")

        from sc_repl_mcp.tools import sc_load_synthdef
        result = sc_load_synthdef(name="bad", code="invalid {{{")

        assert "Error loading SynthDef 'bad'" in result
        assert "syntax error" in result


class TestScEval:
    """Tests for sc_eval tool."""

    def test_returns_success_output(self, mocker):
        mock_eval = mocker.patch("sc_repl_mcp.tools.eval_sclang")
        mock_eval.return_value = (True, "Result: 42")

        from sc_repl_mcp.tools import sc_eval
        result = sc_eval(code="1 + 41")

        assert "Executed successfully" in result
        assert "42" in result

    def test_returns_error_output(self, mocker):
        mock_eval = mocker.patch("sc_repl_mcp.tools.eval_sclang")
        mock_eval.return_value = (False, "ERROR: Parse error")

        from sc_repl_mcp.tools import sc_eval
        result = sc_eval(code="bad code")

        assert "Error" in result
        assert "Parse error" in result

    def test_passes_timeout(self, mocker):
        mock_eval = mocker.patch("sc_repl_mcp.tools.eval_sclang")
        mock_eval.return_value = (True, "")

        from sc_repl_mcp.tools import sc_eval
        sc_eval(code="test", timeout=60.0)

        mock_eval.assert_called_once_with("test", timeout=60.0)

    def test_uses_persistent_sclang_when_ready(self, mocker):
        """Should use persistent sclang when available."""
        mock_client = mocker.patch("sc_repl_mcp.tools.sc_client")
        mock_client.is_sclang_ready.return_value = True
        mock_client.eval_code.return_value = (True, "42")

        from sc_repl_mcp.tools import sc_eval
        result = sc_eval(code="1 + 1")

        mock_client.eval_code.assert_called_once()
        assert "persistent" in result

    def test_falls_back_to_fresh_process_when_not_ready(self, mocker):
        """Should spawn fresh process when persistent sclang not available."""
        mock_client = mocker.patch("sc_repl_mcp.tools.sc_client")
        mock_client.is_sclang_ready.return_value = False
        mock_eval = mocker.patch("sc_repl_mcp.tools.eval_sclang")
        mock_eval.return_value = (True, "42")

        from sc_repl_mcp.tools import sc_eval
        result = sc_eval(code="1 + 1")

        mock_eval.assert_called_once()
        assert "fresh process" in result


class TestScGetLogs:
    """Tests for sc_get_logs tool."""

    def test_formats_log_entries(self, mock_sc_client):
        mock_sc_client.get_logs.return_value = [
            LogEntry(timestamp=1234567890.123, category="info", message="Connected"),
            LogEntry(timestamp=1234567891.456, category="fail", message="SynthDef not found"),
        ]

        from sc_repl_mcp.tools import sc_get_logs
        result = sc_get_logs()

        assert "Log entries (2)" in result
        assert "[INFO]" in result
        assert "Connected" in result
        assert "[FAIL]" in result
        assert "SynthDef not found" in result

    def test_returns_empty_message(self, mock_sc_client):
        mock_sc_client.get_logs.return_value = []

        from sc_repl_mcp.tools import sc_get_logs
        result = sc_get_logs()

        assert "No log entries" in result

    def test_passes_parameters(self, mock_sc_client):
        mock_sc_client.get_logs.return_value = []

        from sc_repl_mcp.tools import sc_get_logs
        sc_get_logs(limit=100, category="fail")

        mock_sc_client.get_logs.assert_called_once_with(limit=100, category="fail")

    def test_caps_limit_to_500(self, mock_sc_client):
        mock_sc_client.get_logs.return_value = []

        from sc_repl_mcp.tools import sc_get_logs
        sc_get_logs(limit=1000)  # Over max

        mock_sc_client.get_logs.assert_called_once_with(limit=500, category=None)


class TestScClearLogs:
    """Tests for sc_clear_logs tool."""

    def test_clears_and_returns_message(self, mock_sc_client):
        from sc_repl_mcp.tools import sc_clear_logs
        result = sc_clear_logs()

        assert result == "Log buffer cleared"
        mock_sc_client.clear_logs.assert_called_once()


class TestScStartRecording:
    """Tests for sc_start_recording tool."""

    def test_starts_recording_with_defaults(self, mock_sc_client):
        mock_sc_client.start_recording.return_value = (
            True, "Recording started: /Users/test/Music/SC_recording.wav"
        )

        from sc_repl_mcp.tools import sc_start_recording
        result = sc_start_recording()

        mock_sc_client.start_recording.assert_called_once_with(
            path=None,
            duration=None,
            header_format="wav",
            sample_format="int24",
            channels=2,
        )
        assert "Recording started" in result

    def test_passes_all_parameters(self, mock_sc_client):
        mock_sc_client.start_recording.return_value = (
            True, "Recording started: /tmp/test.aiff"
        )

        from sc_repl_mcp.tools import sc_start_recording
        result = sc_start_recording(
            path="/tmp/test.aiff",
            duration=10.0,
            format="aiff",
            sample_format="int16",
            channels=4,
        )

        mock_sc_client.start_recording.assert_called_once_with(
            path="/tmp/test.aiff",
            duration=10.0,
            header_format="aiff",
            sample_format="int16",
            channels=4,
        )

    def test_returns_error_when_already_recording(self, mock_sc_client):
        mock_sc_client.start_recording.return_value = (
            False, "Already recording to: /tmp/existing.wav"
        )

        from sc_repl_mcp.tools import sc_start_recording
        result = sc_start_recording()

        assert "Already recording" in result


class TestScStopRecording:
    """Tests for sc_stop_recording tool."""

    def test_stops_recording_and_returns_path(self, mock_sc_client):
        mock_sc_client.stop_recording.return_value = (
            True, "Recording saved: /Users/test/Music/SC_recording.wav"
        )

        from sc_repl_mcp.tools import sc_stop_recording
        result = sc_stop_recording()

        mock_sc_client.stop_recording.assert_called_once()
        assert "Recording saved" in result

    def test_returns_error_when_not_recording(self, mock_sc_client):
        mock_sc_client.stop_recording.return_value = (
            False, "Not currently recording"
        )

        from sc_repl_mcp.tools import sc_stop_recording
        result = sc_stop_recording()

        assert "Not currently recording" in result
