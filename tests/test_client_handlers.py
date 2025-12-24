"""Tests for SCClient OSC handler methods.

These tests call handler methods directly without needing OSC infrastructure.
"""

import time
import pytest

from sc_repl_mcp.client import SCClient
from sc_repl_mcp.types import ServerStatus, AnalysisData, OnsetEvent, SpectrumData


class TestHandleStatusReply:
    """Tests for _handle_status_reply handler."""

    def test_parses_full_status(self, client):
        """Handler should parse all 9 args into ServerStatus."""
        # scsynth sends: [unused, num_ugens, num_synths, num_groups, num_synthdefs, avg_cpu, peak_cpu, nominal_sr, actual_sr]
        client._handle_status_reply(
            "/status.reply",
            1,      # unused
            100,    # num_ugens
            10,     # num_synths
            5,      # num_groups
            200,    # num_synthdefs
            15.5,   # avg_cpu
            25.3,   # peak_cpu
            48000,  # nominal_sample_rate (unused)
            48000.0,  # actual_sample_rate
        )

        assert client.status.running is True
        assert client.status.num_ugens == 100
        assert client.status.num_synths == 10
        assert client.status.num_groups == 5
        assert client.status.num_synthdefs == 200
        assert client.status.avg_cpu == 15.5
        assert client.status.peak_cpu == 25.3
        assert client.status.sample_rate == 48000.0

    def test_sets_status_event(self, client):
        """Handler should set the status event for waiting threads."""
        assert not client._status_event.is_set()

        client._handle_status_reply("/status.reply", 1, 0, 0, 0, 0, 0.0, 0.0, 48000, 48000.0)

        assert client._status_event.is_set()

    def test_ignores_short_args(self, client):
        """Handler should not crash with fewer than 9 args."""
        original_status = client.status

        client._handle_status_reply("/status.reply", 1, 2, 3)  # Only 3 args

        # Status unchanged, but event still set
        assert client.status == original_status
        assert client._status_event.is_set()


class TestHandleDone:
    """Tests for _handle_done handler."""

    def test_logs_simple_done(self, client):
        """Handler should log completed operation."""
        client._handle_done("/done", "/d_recv")

        logs = client.get_logs()
        assert len(logs) == 1
        assert logs[0].category == "done"
        assert "/d_recv completed" in logs[0].message

    def test_logs_done_with_extra_args(self, client):
        """Handler should include extra args in log message."""
        client._handle_done("/done", "/b_alloc", 10)

        logs = client.get_logs()
        assert len(logs) == 1
        assert "/b_alloc completed" in logs[0].message
        assert "10" in logs[0].message

    def test_handles_empty_args(self, client):
        """Handler should not crash with no args."""
        client._handle_done("/done")  # No args

        logs = client.get_logs()
        assert len(logs) == 0  # Nothing logged


class TestHandleFail:
    """Tests for _handle_fail handler."""

    def test_logs_fail_message(self, client):
        """Handler should log failure with all args."""
        client._handle_fail("/fail", "/s_new", "SynthDef not found")

        logs = client.get_logs(category="fail")
        assert len(logs) == 1
        assert "FAIL" in logs[0].message
        assert "/s_new" in logs[0].message
        assert "SynthDef not found" in logs[0].message


class TestHandleNodeGo:
    """Tests for _handle_node_go handler."""

    def test_logs_synth_start(self, client):
        """Handler should log synth start."""
        # args: node_id, parent, prev, next, is_group
        client._handle_node_go("/n_go", 1000, 0, -1, -1, 0)  # is_group=0 means synth

        logs = client.get_logs(category="node")
        assert len(logs) == 1
        assert "Node 1000" in logs[0].message
        assert "synth" in logs[0].message
        assert "started" in logs[0].message

    def test_logs_group_start(self, client):
        """Handler should identify groups."""
        client._handle_node_go("/n_go", 1001, 0, -1, -1, 1)  # is_group=1 means group

        logs = client.get_logs(category="node")
        assert len(logs) == 1
        assert "group" in logs[0].message

    def test_handles_short_args(self, client):
        """Handler should not crash with fewer args."""
        client._handle_node_go("/n_go", 1000, 0)  # Only 2 args

        logs = client.get_logs()
        assert len(logs) == 0  # Not logged due to insufficient args


class TestHandleNodeEnd:
    """Tests for _handle_node_end handler."""

    def test_logs_node_end(self, client):
        """Handler should log node end."""
        client._handle_node_end("/n_end", 1000, 0, -1, -1)

        logs = client.get_logs(category="node")
        assert len(logs) == 1
        assert "Node 1000" in logs[0].message
        assert "ended" in logs[0].message

    def test_clears_analyzer_node_id(self, client):
        """Handler should clear analyzer node ID when analyzer ends."""
        client._analyzer_node_id = 1000

        client._handle_node_end("/n_end", 1000)

        assert client._analyzer_node_id is None

    def test_preserves_analyzer_for_other_nodes(self, client):
        """Handler should not clear analyzer for other node IDs."""
        client._analyzer_node_id = 1000

        client._handle_node_end("/n_end", 9999)  # Different node

        assert client._analyzer_node_id == 1000


class TestHandleAnalysis:
    """Tests for _handle_analysis handler."""

    def test_parses_full_analysis(self, client):
        """Handler should parse all 11 args into AnalysisData."""
        # args: node_id, reply_id, freq, has_freq, centroid, flatness, rolloff, peak_l, peak_r, rms_l, rms_r
        client._handle_analysis(
            "/mcp/analysis",
            1000,   # node_id
            1001,   # reply_id
            440.0,  # freq
            0.95,   # has_freq
            880.0,  # centroid
            0.1,    # flatness
            4000.0, # rolloff
            0.8,    # peak_l
            0.75,   # peak_r
            0.3,    # rms_l
            0.28,   # rms_r
        )

        data = client._analysis_data
        assert data is not None
        assert data.freq == 440.0
        assert data.has_freq == 0.95
        assert data.centroid == 880.0
        assert data.flatness == 0.1
        assert data.rolloff == 4000.0
        assert data.peak_l == 0.8
        assert data.peak_r == 0.75
        assert data.rms_l == 0.3
        assert data.rms_r == 0.28
        assert data.timestamp > 0  # Should be set to current time

    def test_adds_to_history(self, client):
        """Handler should add analysis to history deque."""
        for i in range(5):
            client._handle_analysis(
                "/mcp/analysis",
                1000, 1001, float(440 + i), 0.9, 880.0, 0.1, 4000.0, 0.5, 0.5, 0.2, 0.2
            )

        assert len(client._analysis_history) == 5
        # Most recent should have highest freq
        assert client._analysis_history[-1].freq == 444.0

    def test_ignores_short_args(self, client):
        """Handler should not crash with fewer than 11 args."""
        client._handle_analysis("/mcp/analysis", 1000, 1001, 440.0)  # Only 4 args

        assert client._analysis_data is None


class TestHandleMeter:
    """Tests for _handle_meter handler."""

    def test_parses_meter_data(self, client):
        """Handler should parse meter data when analyzer not running."""
        assert client._analyzer_node_id is None  # No analyzer

        client._handle_meter(
            "/mcp/meter",
            1000,   # node_id
            1002,   # reply_id
            0.8,    # peak_l
            0.75,   # peak_r
            0.3,    # rms_l
            0.28,   # rms_r
        )

        data = client._analysis_data
        assert data is not None
        assert data.peak_l == 0.8
        assert data.peak_r == 0.75
        assert data.rms_l == 0.3
        assert data.rms_r == 0.28
        # Frequency fields should be default (meter doesn't provide them)
        assert data.freq == 0.0

    def test_ignored_when_analyzer_running(self, client):
        """Handler should not overwrite data when full analyzer is running."""
        client._analyzer_node_id = 1000  # Analyzer is running
        client._analysis_data = AnalysisData(freq=440.0, has_freq=0.95)

        client._handle_meter("/mcp/meter", 1000, 1002, 0.8, 0.75, 0.3, 0.28)

        # Data should be unchanged
        assert client._analysis_data.freq == 440.0

    def test_ignores_short_args(self, client):
        """Handler should not crash with fewer than 6 args."""
        client._handle_meter("/mcp/meter", 1000, 1002)  # Only 3 args

        assert client._analysis_data is None


class TestHandleOnset:
    """Tests for _handle_onset handler."""

    def test_parses_onset_event(self, client):
        """Handler should parse onset data into OnsetEvent."""
        # args: node_id, reply_id, freq, amplitude
        client._handle_onset(
            "/mcp/onset",
            1000,   # node_id
            1001,   # reply_id
            440.0,  # freq
            0.5,    # amplitude
        )

        assert len(client._onset_events) == 1
        event = client._onset_events[0]
        assert event.freq == 440.0
        assert event.amplitude == 0.5
        assert event.timestamp > 0

    def test_adds_multiple_events(self, client):
        """Handler should accumulate multiple onset events."""
        for i in range(5):
            client._handle_onset("/mcp/onset", 1000, 1001, float(440 + i * 100), 0.3 + i * 0.1)

        assert len(client._onset_events) == 5
        # Check last event
        assert client._onset_events[-1].freq == 840.0
        assert client._onset_events[-1].amplitude == 0.7

    def test_ignores_short_args(self, client):
        """Handler should not crash with fewer than 4 args."""
        client._handle_onset("/mcp/onset", 1000, 1001)  # Only 3 args

        assert len(client._onset_events) == 0

    def test_respects_max_buffer_size(self, client):
        """Onset buffer should not exceed maxlen."""
        # Buffer maxlen is 100
        for i in range(150):
            client._handle_onset("/mcp/onset", 1000, 1001, float(i), 0.5)

        assert len(client._onset_events) == 100
        # Oldest events should be dropped, newest kept
        assert client._onset_events[0].freq == 50.0  # First 50 were dropped
        assert client._onset_events[-1].freq == 149.0


class TestGetOnsets:
    """Tests for get_onsets method."""

    def test_returns_all_events(self, client):
        """get_onsets should return all events by default."""
        client._handle_onset("/mcp/onset", 1000, 1001, 440.0, 0.5)
        client._handle_onset("/mcp/onset", 1000, 1001, 880.0, 0.6)

        events = client.get_onsets()

        assert len(events) == 2
        assert events[0].freq == 440.0
        assert events[1].freq == 880.0

    def test_clears_events_by_default(self, client):
        """get_onsets should clear events after reading by default."""
        client._handle_onset("/mcp/onset", 1000, 1001, 440.0, 0.5)

        events1 = client.get_onsets()
        events2 = client.get_onsets()

        assert len(events1) == 1
        assert len(events2) == 0

    def test_preserves_events_when_clear_false(self, client):
        """get_onsets should preserve events when clear=False."""
        client._handle_onset("/mcp/onset", 1000, 1001, 440.0, 0.5)

        events1 = client.get_onsets(clear=False)
        events2 = client.get_onsets(clear=False)

        assert len(events1) == 1
        assert len(events2) == 1

    def test_filters_by_timestamp(self, client):
        """get_onsets should filter events by since parameter."""
        # Add event, record time, add another event
        client._handle_onset("/mcp/onset", 1000, 1001, 440.0, 0.5)
        cutoff_time = time.time()
        time.sleep(0.01)  # Small delay to ensure different timestamps
        client._handle_onset("/mcp/onset", 1000, 1001, 880.0, 0.6)

        events = client.get_onsets(since=cutoff_time)

        assert len(events) == 1
        assert events[0].freq == 880.0

    def test_returns_empty_list_when_no_events(self, client):
        """get_onsets should return empty list when no events."""
        events = client.get_onsets()
        assert events == []


class TestNodeId:
    """Tests for _next_node_id method."""

    def test_increments_monotonically(self, client):
        """Node IDs should increase with each call."""
        id1 = client._next_node_id()
        id2 = client._next_node_id()
        id3 = client._next_node_id()

        assert id2 == id1 + 1
        assert id3 == id2 + 1

    def test_starts_above_one_million(self, client):
        """Node IDs should start above 1,000,000 to avoid conflicts."""
        id1 = client._next_node_id()
        assert id1 > 1_000_000


class TestLogManagement:
    """Tests for log-related methods."""

    def test_get_logs_returns_recent(self, client):
        """get_logs should return most recent entries."""
        for i in range(10):
            client._add_log("info", f"Message {i}")

        logs = client.get_logs(limit=5)

        assert len(logs) == 5
        # Should be the last 5 messages
        assert "Message 5" in logs[0].message
        assert "Message 9" in logs[4].message

    def test_get_logs_filter_by_category(self, client):
        """get_logs should filter by category."""
        client._add_log("info", "Info message")
        client._add_log("fail", "Fail message")
        client._add_log("done", "Done message")
        client._add_log("fail", "Another fail")

        fail_logs = client.get_logs(category="fail")

        assert len(fail_logs) == 2
        assert all(log.category == "fail" for log in fail_logs)

    def test_clear_logs(self, client):
        """clear_logs should empty the buffer."""
        client._add_log("info", "Test message")
        assert len(client.get_logs()) == 1

        client.clear_logs()

        assert len(client.get_logs()) == 0

    def test_log_buffer_max_size(self, client):
        """Log buffer should respect maxlen."""
        # Buffer maxlen is 500
        for i in range(600):
            client._add_log("info", f"Message {i}")

        logs = client.get_logs(limit=1000)

        assert len(logs) == 500
        # Should have dropped the oldest messages
        assert "Message 100" in logs[0].message


class TestHandleSpectrum:
    """Tests for _handle_spectrum handler."""

    def test_parses_full_spectrum(self, client):
        """Handler should parse all 16 args (node_id, reply_id, 14 bands) into SpectrumData."""
        # args: node_id, reply_id, band0, band1, ..., band13
        bands = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 0.9, 0.8, 0.7, 0.6]
        client._handle_spectrum(
            "/mcp/spectrum",
            1000,   # node_id
            1001,   # reply_id
            *bands  # 14 band values
        )

        data = client._spectrum_data
        assert data is not None
        assert data.bands == tuple(bands)
        assert len(data.bands) == 14
        assert data.timestamp > 0  # Should be set to current time

    def test_ignores_short_args(self, client):
        """Handler should not crash with fewer than 16 args."""
        client._handle_spectrum("/mcp/spectrum", 1000, 1001, 0.1, 0.2, 0.3)  # Only 5 args

        assert client._spectrum_data is None

    def test_updates_spectrum_data(self, client):
        """Handler should update spectrum data on each call."""
        bands1 = [0.1] * 14
        bands2 = [0.9] * 14

        client._handle_spectrum("/mcp/spectrum", 1000, 1001, *bands1)
        assert client._spectrum_data.bands == tuple(bands1)

        client._handle_spectrum("/mcp/spectrum", 1000, 1001, *bands2)
        assert client._spectrum_data.bands == tuple(bands2)

    def test_bands_are_floats(self, client):
        """Handler should convert band values to floats."""
        # Pass integers
        bands = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]
        client._handle_spectrum("/mcp/spectrum", 1000, 1001, *bands)

        data = client._spectrum_data
        assert all(isinstance(b, float) for b in data.bands)


class TestGetSpectrum:
    """Tests for get_spectrum method."""

    def test_returns_error_when_analyzer_not_running(self, client):
        """get_spectrum should fail when analyzer not running."""
        assert client._analyzer_node_id is None

        success, message, data = client.get_spectrum()

        assert success is False
        assert "Analyzer not running" in message
        assert data is None

    def test_returns_error_when_no_data(self, client):
        """get_spectrum should fail when no data received yet."""
        client._analyzer_node_id = 1000  # Pretend analyzer is running

        success, message, data = client.get_spectrum()

        assert success is False
        assert "No spectrum data" in message
        assert data is None

    def test_returns_error_when_data_stale(self, client):
        """get_spectrum should fail when data is too old."""
        client._analyzer_node_id = 1000
        # Create data with old timestamp
        client._spectrum_data = SpectrumData(
            timestamp=time.time() - 2.0,  # 2 seconds old
            bands=(0.5,) * 14
        )

        success, message, data = client.get_spectrum()

        assert success is False
        assert "stale" in message
        assert data is None

    def test_returns_formatted_spectrum(self, client):
        """get_spectrum should return properly formatted data."""
        client._analyzer_node_id = 1000
        bands = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 0.9, 0.8, 0.7, 0.6)
        client._spectrum_data = SpectrumData(
            timestamp=time.time(),
            bands=bands
        )

        success, message, data = client.get_spectrum()

        assert success is True
        assert data is not None
        assert "bands" in data
        assert "band_frequencies" in data
        assert len(data["bands"]) == 14
        assert len(data["band_frequencies"]) == 14

    def test_bands_have_freq_power_db(self, client):
        """Each band should have freq, power, and db fields."""
        client._analyzer_node_id = 1000
        client._spectrum_data = SpectrumData(
            timestamp=time.time(),
            bands=(0.5,) * 14
        )

        success, _, data = client.get_spectrum()

        assert success is True
        for band in data["bands"]:
            assert "freq" in band
            assert "power" in band
            assert "db" in band

    def test_db_floored_at_minus_60(self, client):
        """dB values should be floored at -60."""
        client._analyzer_node_id = 1000
        # Very small power values
        client._spectrum_data = SpectrumData(
            timestamp=time.time(),
            bands=(0.00001,) * 14
        )

        success, _, data = client.get_spectrum()

        assert success is True
        for band in data["bands"]:
            assert band["db"] >= -60.0


class TestHandleAnalysisLoudness:
    """Tests for loudness field in analysis handler."""

    def test_parses_loudness_when_present(self, client):
        """Should parse loudness when included in OSC message."""
        # 12 args: node_id, reply_id, freq, has_freq, centroid, flatness, rolloff, peak_l, peak_r, rms_l, rms_r, loudness
        client._handle_analysis("/mcp/analysis", 1000, 1001, 440.0, 0.95, 880.0, 0.1, 4000.0, 0.8, 0.75, 0.3, 0.28, 15.5)

        assert client._analysis_data is not None
        assert client._analysis_data.loudness_sones == 15.5

    def test_defaults_loudness_when_missing(self, client):
        """Should default loudness to 0.0 when not in OSC message (backward compat)."""
        # 11 args: no loudness (old format)
        client._handle_analysis("/mcp/analysis", 1000, 1001, 440.0, 0.95, 880.0, 0.1, 4000.0, 0.8, 0.75, 0.3, 0.28)

        assert client._analysis_data is not None
        assert client._analysis_data.loudness_sones == 0.0


class TestGetAnalysisLoudness:
    """Tests for loudness in get_analysis output."""

    def test_includes_loudness_in_output(self, client):
        """get_analysis should include loudness in result dict."""
        client._analyzer_node_id = 1000
        client._analysis_data = AnalysisData(
            timestamp=time.time(),
            freq=440.0,
            has_freq=0.95,
            centroid=880.0,
            flatness=0.1,
            rolloff=4000.0,
            peak_l=0.8,
            peak_r=0.75,
            rms_l=0.3,
            rms_r=0.28,
            loudness_sones=12.5,
        )

        success, message, data = client.get_analysis()

        assert success is True
        assert "loudness" in data
        assert data["loudness"]["sones"] == 12.5


class TestReferenceCapture:
    """Tests for reference capture functionality."""

    def test_capture_reference_success(self, client):
        """Should capture current analysis as reference."""
        client._analyzer_node_id = 1000
        client._analysis_data = AnalysisData(
            timestamp=time.time(),
            freq=440.0,
            centroid=880.0,
            loudness_sones=10.0,
        )

        success, message = client.capture_reference("test_ref", "A test sound")

        assert success is True
        assert "captured" in message
        assert "test_ref" in client._references
        assert client._references["test_ref"].description == "A test sound"

    def test_capture_fails_without_analyzer(self, client):
        """Should fail if analyzer not running."""
        client._analyzer_node_id = None

        success, message = client.capture_reference("test")

        assert success is False
        assert "Analyzer not running" in message

    def test_capture_fails_without_data(self, client):
        """Should fail if no analysis data available."""
        client._analyzer_node_id = 1000
        client._analysis_data = None

        success, message = client.capture_reference("test")

        assert success is False
        assert "No analysis data" in message

    def test_capture_overwrites_existing(self, client):
        """Capturing same name should overwrite."""
        client._analyzer_node_id = 1000
        client._analysis_data = AnalysisData(
            timestamp=time.time(),
            freq=440.0,
        )

        client.capture_reference("test", "First")
        success, message = client.capture_reference("test", "Second")

        assert success is True
        assert "updated" in message
        assert client._references["test"].description == "Second"

    def test_list_references_empty(self, client):
        """Should return empty list when no references."""
        refs = client.list_references()
        assert refs == []

    def test_list_references_sorted_by_time(self, client):
        """References should be sorted by timestamp."""
        from sc_repl_mcp.types import ReferenceSnapshot

        client._analyzer_node_id = 1000

        # Directly add references with controlled timestamps
        client._references["second"] = ReferenceSnapshot(
            name="second",
            timestamp=200.0,
            analysis=AnalysisData(freq=880.0),
        )
        client._references["first"] = ReferenceSnapshot(
            name="first",
            timestamp=100.0,  # Earlier
            analysis=AnalysisData(freq=440.0),
        )

        refs = client.list_references()
        assert len(refs) == 2
        assert refs[0].name == "first"  # Earlier timestamp
        assert refs[1].name == "second"  # Later timestamp

    def test_delete_reference_success(self, client):
        """Should delete existing reference."""
        client._analyzer_node_id = 1000
        client._analysis_data = AnalysisData(timestamp=time.time(), freq=440.0)
        client.capture_reference("test")

        success, message = client.delete_reference("test")

        assert success is True
        assert "deleted" in message
        assert "test" not in client._references

    def test_delete_reference_not_found(self, client):
        """Should fail when reference doesn't exist."""
        success, message = client.delete_reference("nonexistent")

        assert success is False
        assert "not found" in message


class TestReferenceComparison:
    """Tests for reference comparison functionality."""

    def test_compare_to_reference_success(self, client):
        """Should compare current sound to reference."""
        client._analyzer_node_id = 1000

        # Capture reference
        client._analysis_data = AnalysisData(
            timestamp=time.time(),
            freq=440.0,
            centroid=880.0,
            loudness_sones=10.0,
            flatness=0.1,
            rms_l=0.3,
        )
        client.capture_reference("target")

        # Change current sound
        client._analysis_data = AnalysisData(
            timestamp=time.time(),
            freq=880.0,  # One octave higher
            centroid=1760.0,  # Brighter
            loudness_sones=15.0,  # Louder
            flatness=0.2,  # More noise
            rms_l=0.5,
        )

        success, message, data = client.compare_to_reference("target")

        assert success is True
        assert "pitch" in data
        assert "brightness" in data
        assert "loudness" in data
        assert "overall_score" in data

        # Check pitch difference (one octave = 12 semitones)
        assert abs(data["pitch"]["diff_semitones"] - 12.0) < 0.1

        # Check brightness ratio (1760/880 = 2.0)
        assert abs(data["brightness"]["ratio"] - 2.0) < 0.1

        # Check loudness difference (15 - 10 = 5 sones)
        assert abs(data["loudness"]["diff_sones"] - 5.0) < 0.1

    def test_compare_fails_without_reference(self, client):
        """Should fail when reference doesn't exist."""
        client._analyzer_node_id = 1000
        client._analysis_data = AnalysisData(timestamp=time.time(), freq=440.0)

        success, message, data = client.compare_to_reference("nonexistent")

        assert success is False
        assert "not found" in message
        assert data is None

    def test_compare_fails_without_analyzer(self, client):
        """Should fail if analyzer not running."""
        # First capture a reference
        client._analyzer_node_id = 1000
        client._analysis_data = AnalysisData(timestamp=time.time(), freq=440.0)
        client.capture_reference("target")

        # Stop analyzer
        client._analyzer_node_id = None

        success, message, data = client.compare_to_reference("target")

        assert success is False
        assert "Analyzer not running" in message

    def test_compare_overall_score_range(self, client):
        """Overall score should be between 0 and 100."""
        client._analyzer_node_id = 1000

        client._analysis_data = AnalysisData(
            timestamp=time.time(),
            freq=440.0,
            centroid=880.0,
            loudness_sones=10.0,
            flatness=0.1,
        )
        client.capture_reference("target")

        # Same sound should have high score
        success, _, data = client.compare_to_reference("target")
        assert 0 <= data["overall_score"] <= 100
        assert data["overall_score"] > 90  # Nearly identical

    def test_compare_silent_sounds_pitch_invalid(self, client):
        """Pitch should be marked invalid when one sound is silent."""
        client._analyzer_node_id = 1000

        # Reference with sound
        client._analysis_data = AnalysisData(
            timestamp=time.time(),
            freq=440.0,
            centroid=880.0,
        )
        client.capture_reference("target")

        # Current sound is silent (freq=0)
        client._analysis_data = AnalysisData(
            timestamp=time.time(),
            freq=0.0,  # Silent
            centroid=0.0,
        )

        success, _, data = client.compare_to_reference("target")
        assert success is True
        assert data["pitch"]["valid"] is False
        assert data["pitch"]["score"] == 0.0

    def test_compare_brightness_symmetric_scoring(self, client):
        """Brightness scoring should be symmetric (2x brighter = 0.5x darker in penalty)."""
        client._analyzer_node_id = 1000

        # Reference
        client._analysis_data = AnalysisData(
            timestamp=time.time(),
            freq=440.0,
            centroid=1000.0,  # Reference centroid
        )
        client.capture_reference("target")

        # 2x brighter
        client._analysis_data = AnalysisData(
            timestamp=time.time(),
            freq=440.0,
            centroid=2000.0,  # 2x brighter
        )
        _, _, data_brighter = client.compare_to_reference("target")

        # 0.5x darker (should have same score penalty)
        client._analysis_data = AnalysisData(
            timestamp=time.time(),
            freq=440.0,
            centroid=500.0,  # 0.5x darker
        )
        _, _, data_darker = client.compare_to_reference("target")

        # Scores should be equal (symmetric)
        assert abs(data_brighter["brightness"]["score"] - data_darker["brightness"]["score"]) < 1.0

    def test_compare_zero_centroid_both_silent(self, client):
        """Both sounds with zero centroid should match."""
        client._analyzer_node_id = 1000

        client._analysis_data = AnalysisData(
            timestamp=time.time(),
            freq=0.0,
            centroid=0.0,
        )
        client.capture_reference("target")

        success, _, data = client.compare_to_reference("target")
        assert success is True
        assert data["brightness"]["valid"] is True
        assert data["brightness"]["score"] == 100.0


class TestAnalyzeParameterImpact:
    """Tests for analyze_parameter_impact method."""

    def test_validates_empty_values(self, client):
        """Should reject empty values list."""
        client._analyzer_node_id = 1000

        success, message, _ = client.analyze_parameter_impact(
            "test", "freq", [], "pitch"
        )

        assert success is False
        assert "No values" in message

    def test_validates_unknown_metric(self, client):
        """Should reject unknown metric."""
        client._analyzer_node_id = 1000

        success, message, _ = client.analyze_parameter_impact(
            "test", "freq", [440], "unknown_metric"
        )

        assert success is False
        assert "Unknown metric" in message

    def test_validates_settle_time_less_than_dur(self, client):
        """Should reject settle_time >= dur."""
        client._analyzer_node_id = 1000

        success, message, _ = client.analyze_parameter_impact(
            "test", "freq", [440], "pitch",
            dur=0.3, settle_time=0.5  # Invalid: settle_time > dur
        )

        assert success is False
        assert "settle_time" in message
        assert "must be less than" in message

    def test_requires_analyzer_running(self, client):
        """Should fail if analyzer not running."""
        client._analyzer_node_id = None

        success, message, _ = client.analyze_parameter_impact(
            "test", "freq", [440], "pitch"
        )

        assert success is False
        assert "Analyzer not running" in message

    def test_detects_stale_data(self, client, mocker):
        """Should detect when analysis data is older than synth start time."""
        client._analyzer_node_id = 1000
        # Mock play_synth to succeed
        mocker.patch.object(client, 'play_synth', return_value=(True, "ok"))
        # Mock sleep to not actually wait
        mocker.patch('time.sleep')

        # Set data with old timestamp (before the synth would start)
        # The code records start_time = time.time() before play_synth,
        # so data from before that is considered stale
        client._analysis_data = AnalysisData(
            timestamp=time.time() - 1.0,
            freq=440.0,
        )

        success, message, results = client.analyze_parameter_impact(
            "test", "freq", [440], "pitch",
            dur=0.3, settle_time=0.1
        )

        assert success is True
        assert len(results) == 1
        assert results[0]["metric"] is None
        assert "fresh" in results[0].get("error", "").lower()

    def test_extracts_correct_metrics(self, client, mocker):
        """Should extract correct metric values."""
        import math
        client._analyzer_node_id = 1000
        mocker.patch('time.sleep')

        # Use side_effect to set fresh data when play_synth is called
        # This ensures data.timestamp > start_time
        def set_fresh_data(*args, **kwargs):
            client._analysis_data = AnalysisData(
                timestamp=time.time(),  # Fresh timestamp after start_time
                freq=440.0,
                centroid=880.0,
                loudness_sones=10.0,
                flatness=0.1,
                rms_l=0.3,
                rms_r=0.3,
            )
            return (True, "ok")

        mocker.patch.object(client, 'play_synth', side_effect=set_fresh_data)

        # Test each metric type
        # Note: RMS uses sqrt((0.3² + 0.3²) / 2) = 0.3
        for metric, expected in [
            ("pitch", 440.0),
            ("centroid", 880.0),
            ("loudness", 10.0),
            ("flatness", 0.1),
            ("rms", math.sqrt((0.3**2 + 0.3**2) / 2)),  # Correct RMS formula
        ]:
            success, _, results = client.analyze_parameter_impact(
                "test", "freq", [440], metric,
                dur=0.3, settle_time=0.1
            )
            assert success is True
            assert len(results) == 1
            assert abs(results[0]["metric"] - expected) < 0.01, f"Failed for {metric}"

    def test_continues_on_synth_failure(self, client, mocker):
        """Should continue collecting results when some synth plays fail."""
        client._analyzer_node_id = 1000
        mocker.patch('time.sleep')

        # First call fails, second succeeds
        call_count = [0]

        def mock_play(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return (False, "SynthDef not found")
            # Set fresh data for successful call
            client._analysis_data = AnalysisData(
                timestamp=time.time(),
                freq=880.0,
            )
            return (True, "ok")

        mocker.patch.object(client, 'play_synth', side_effect=mock_play)

        success, _, results = client.analyze_parameter_impact(
            "test", "freq", [440, 880], "pitch",
            dur=0.3, settle_time=0.1
        )

        assert success is True
        assert len(results) == 2
        # First failed
        assert results[0]["metric"] is None
        assert "Synth failed" in results[0].get("error", "")
        # Second succeeded
        assert results[1]["metric"] is not None


class TestReferenceComparisonEdgeCases:
    """Additional edge case tests for reference comparison."""

    def test_compare_brightness_one_zero_centroid(self, client):
        """Should handle comparison when only one sound has zero centroid."""
        client._analyzer_node_id = 1000

        # Capture reference with positive centroid
        ref_analysis = AnalysisData(
            timestamp=time.time(),
            freq=440.0,
            centroid=1000.0,
            flatness=0.1,
            loudness_sones=5.0,
        )
        from sc_repl_mcp.types import ReferenceSnapshot
        client._references["bright"] = ReferenceSnapshot(
            name="bright",
            timestamp=time.time(),
            analysis=ref_analysis,
        )

        # Current sound has zero centroid (silent/very dark)
        client._analysis_data = AnalysisData(
            timestamp=time.time(),
            freq=440.0,
            centroid=0.0,
            flatness=0.1,
            loudness_sones=5.0,
        )

        success, _, data = client.compare_to_reference("bright")

        assert success is True
        # Brightness should be marked invalid
        assert data["brightness"]["valid"] is False
        assert data["brightness"]["ratio"] is None  # or inf

    def test_compare_normalized_weights_when_pitch_invalid(self, client):
        """Overall score should normalize weights when pitch is invalid."""
        client._analyzer_node_id = 1000

        # Both sounds silent (freq=0), so pitch is invalid
        ref_analysis = AnalysisData(
            timestamp=time.time(),
            freq=0.0,  # Silent
            centroid=500.0,
            flatness=0.1,
            loudness_sones=5.0,
        )
        from sc_repl_mcp.types import ReferenceSnapshot
        client._references["silent"] = ReferenceSnapshot(
            name="silent",
            timestamp=time.time(),
            analysis=ref_analysis,
        )

        # Current sound also silent but otherwise identical
        client._analysis_data = AnalysisData(
            timestamp=time.time(),
            freq=0.0,
            centroid=500.0,  # Same brightness
            flatness=0.1,  # Same character
            loudness_sones=5.0,  # Same loudness
        )

        success, _, data = client.compare_to_reference("silent")

        assert success is True
        assert data["pitch"]["valid"] is False
        # With normalized weights, identical properties should give high score
        # Without normalization it would be ~70% (missing 30% from pitch)
        # With normalization it should be ~100% (weights redistributed)
        assert data["overall_score"] > 95.0, f"Expected >95%, got {data['overall_score']}"

    def test_capture_reference_stale_data(self, client):
        """Should fail when trying to capture with stale data."""
        client._analyzer_node_id = 1000

        # Set stale data (2 seconds old)
        client._analysis_data = AnalysisData(
            timestamp=time.time() - 2.0,
            freq=440.0,
        )

        success, message = client.capture_reference("test")

        assert success is False
        assert "stale" in message.lower()


class TestHandleEvalResult:
    """Tests for _handle_eval_result handler for persistent sclang."""

    def test_stores_result(self, client):
        """Handler should store result for matching request ID."""
        import threading

        # Set up a pending request
        event = threading.Event()
        client._eval_events[42] = event

        client._handle_eval_result("/mcp/eval/result", 42, 1, "Success!")

        assert 42 in client._eval_results
        success, output = client._eval_results[42]
        assert success is True
        assert output == "Success!"

    def test_signals_event(self, client):
        """Handler should signal the waiting event."""
        import threading

        event = threading.Event()
        client._eval_events[42] = event

        assert not event.is_set()
        client._handle_eval_result("/mcp/eval/result", 42, 1, "Done")
        assert event.is_set()

    def test_handles_error_result(self, client):
        """Handler should store error results correctly."""
        import threading

        event = threading.Event()
        client._eval_events[99] = event

        client._handle_eval_result("/mcp/eval/result", 99, 0, "ERROR: Parse error")

        success, output = client._eval_results[99]
        assert success is False
        assert "Parse error" in output

    def test_logs_malformed_args(self, client):
        """Handler should log malformed messages with fewer than 3 args."""
        # This should not raise, but should log
        client._handle_eval_result("/mcp/eval/result", 42, 1)

        # Verify it logged the error
        logs = client.get_logs(category="fail")
        assert len(logs) == 1
        assert "Malformed" in logs[0].message

    def test_logs_invalid_data_types(self, client):
        """Handler should log when data types are invalid."""
        # Pass a non-convertible value for request_id
        client._handle_eval_result("/mcp/eval/result", "not-an-int", 1, "output")

        logs = client.get_logs(category="fail")
        assert len(logs) == 1
        assert "Invalid eval result data" in logs[0].message

    def test_handles_none_output(self, client):
        """Handler should handle None output gracefully."""
        import threading

        event = threading.Event()
        client._eval_events[42] = event

        client._handle_eval_result("/mcp/eval/result", 42, 1, None)

        success, output = client._eval_results[42]
        assert output == ""

    def test_discards_orphaned_results(self, client):
        """Handler should not store results when no one is waiting."""
        # No event registered for this request ID
        client._handle_eval_result("/mcp/eval/result", 999, 1, "orphaned")

        # Result should not be stored (prevents memory leak)
        assert 999 not in client._eval_results


class TestIsSclangReady:
    """Tests for is_sclang_ready method."""

    def test_returns_false_when_no_process(self, client):
        """Should return False when no sclang process exists."""
        client._sclang_process = None
        assert client.is_sclang_ready() is False

    def test_returns_false_when_process_exited(self, client, mocker):
        """Should return False when sclang process has exited."""
        mock_proc = mocker.MagicMock()
        mock_proc.poll.return_value = 1  # Process exited with code 1
        client._sclang_process = mock_proc

        assert client.is_sclang_ready() is False

    def test_returns_true_when_process_running(self, client, mocker):
        """Should return True when sclang process is running."""
        mock_proc = mocker.MagicMock()
        mock_proc.poll.return_value = None  # Process still running
        client._sclang_process = mock_proc

        assert client.is_sclang_ready() is True


class TestEvalCode:
    """Tests for eval_code method (persistent sclang execution)."""

    def test_rejects_empty_code(self, client):
        """Should reject empty code."""
        success, message = client.eval_code("")
        assert success is False
        assert "No code provided" in message

    def test_rejects_whitespace_only(self, client):
        """Should reject whitespace-only code."""
        success, message = client.eval_code("   \n\t  ")
        assert success is False
        assert "No code provided" in message

    def test_requires_sclang_running(self, client):
        """Should fail when sclang not running."""
        client._sclang_process = None

        success, message = client.eval_code("1 + 1")
        assert success is False
        assert "not running" in message.lower()

    def test_requires_connection(self, client, mocker):
        """Should fail when not connected."""
        # Mock sclang as running
        mock_proc = mocker.MagicMock()
        mock_proc.poll.return_value = None
        client._sclang_process = mock_proc
        client._reply_server = None

        success, message = client.eval_code("1 + 1")
        assert success is False
        assert "Not connected" in message

    def test_successful_execution(self, client, mocker):
        """Should execute code and return result when everything works."""
        # Mock sclang as running
        mock_proc = mocker.MagicMock()
        mock_proc.poll.return_value = None
        client._sclang_process = mock_proc

        # Mock reply server
        mock_server = mocker.MagicMock()
        mock_server.socket = mocker.MagicMock()
        client._reply_server = mock_server

        # Simulate result arriving when OSC is sent
        def simulate_response(dgram, addr):
            request_id = client._eval_request_id
            client._handle_eval_result("/mcp/eval/result", request_id, 1, "42")

        mock_server.socket.sendto.side_effect = simulate_response

        success, output = client.eval_code("1 + 1", timeout=1.0)

        assert success is True
        assert "42" in output

    def test_timeout_returns_error_and_cleans_up(self, client, mocker):
        """Should return timeout error and clean up internal state."""
        mock_proc = mocker.MagicMock()
        mock_proc.poll.return_value = None
        client._sclang_process = mock_proc

        mock_server = mocker.MagicMock()
        mock_server.socket = mocker.MagicMock()
        client._reply_server = mock_server

        # Don't simulate any response - let it timeout
        success, message = client.eval_code("1+1", timeout=0.01)

        assert success is False
        assert "timed out" in message.lower()
        # Verify cleanup
        assert len(client._eval_events) == 0
        assert len(client._eval_results) == 0

    def test_cleans_up_event_on_send_failure(self, client, mocker):
        """Should clean up event when send fails."""
        mock_proc = mocker.MagicMock()
        mock_proc.poll.return_value = None
        client._sclang_process = mock_proc

        mock_server = mocker.MagicMock()
        mock_server.socket = mocker.MagicMock()
        mock_server.socket.sendto.side_effect = OSError("Network error")
        client._reply_server = mock_server

        success, message = client.eval_code("1+1")

        assert success is False
        assert "Failed to send" in message
        # Verify event was cleaned up
        assert len(client._eval_events) == 0


class TestRecording:
    """Tests for audio recording methods."""

    def test_start_recording_requires_sclang(self, client):
        """Should fail when sclang not running."""
        client._sclang_process = None

        success, message = client.start_recording()

        assert success is False
        assert "Not connected" in message

    def test_start_recording_rejects_already_recording(self, client, mocker):
        """Should fail when already recording."""
        mock_proc = mocker.MagicMock()
        mock_proc.poll.return_value = None
        client._sclang_process = mock_proc
        client._is_recording = True
        client._recording_path = "/tmp/existing.wav"

        success, message = client.start_recording()

        assert success is False
        assert "Already recording" in message
        assert "/tmp/existing.wav" in message

    def test_start_recording_validates_header_format(self, client, mocker):
        """Should reject invalid header formats."""
        mock_proc = mocker.MagicMock()
        mock_proc.poll.return_value = None
        client._sclang_process = mock_proc

        success, message = client.start_recording(header_format="mp3")

        assert success is False
        assert "Invalid header format" in message
        assert "wav" in message  # Should list valid formats

    def test_start_recording_validates_sample_format(self, client, mocker):
        """Should reject invalid sample formats."""
        mock_proc = mocker.MagicMock()
        mock_proc.poll.return_value = None
        client._sclang_process = mock_proc

        success, message = client.start_recording(sample_format="mp3")

        assert success is False
        assert "Invalid sample format" in message

    def test_start_recording_validates_channels(self, client, mocker):
        """Should reject invalid channel counts."""
        mock_proc = mocker.MagicMock()
        mock_proc.poll.return_value = None
        client._sclang_process = mock_proc

        success, message = client.start_recording(channels=0)
        assert success is False
        assert "Channels" in message

        success, message = client.start_recording(channels=100)
        assert success is False
        assert "Channels" in message

    def test_start_recording_validates_duration(self, client, mocker):
        """Should reject non-positive duration."""
        mock_proc = mocker.MagicMock()
        mock_proc.poll.return_value = None
        client._sclang_process = mock_proc

        success, message = client.start_recording(duration=0)
        assert success is False
        assert "Duration must be positive" in message

        success, message = client.start_recording(duration=-1)
        assert success is False
        assert "Duration must be positive" in message

    def test_start_recording_success(self, client, mocker):
        """Should start recording when sclang available."""
        mock_proc = mocker.MagicMock()
        mock_proc.poll.return_value = None
        client._sclang_process = mock_proc

        mock_server = mocker.MagicMock()
        mock_server.socket = mocker.MagicMock()
        client._reply_server = mock_server

        # Simulate successful response with path
        def simulate_response(dgram, addr):
            request_id = client._eval_request_id
            client._handle_eval_result(
                "/mcp/eval/result",
                request_id,
                1,
                "/Users/test/Music/SC_recording.wav"
            )

        mock_server.socket.sendto.side_effect = simulate_response

        success, message = client.start_recording()

        assert success is True
        assert "Recording started" in message
        assert client._is_recording is True
        assert client._recording_path == "/Users/test/Music/SC_recording.wav"

    def test_start_recording_expands_path(self, client, mocker):
        """Should expand ~ and make path absolute."""
        mock_proc = mocker.MagicMock()
        mock_proc.poll.return_value = None
        client._sclang_process = mock_proc

        mock_server = mocker.MagicMock()
        mock_server.socket = mocker.MagicMock()
        client._reply_server = mock_server

        # Capture the code that was sent
        sent_code = []

        def simulate_response(dgram, addr):
            request_id = client._eval_request_id
            # Just simulate success
            client._handle_eval_result(
                "/mcp/eval/result",
                request_id,
                1,
                "/Users/test/my_recording.wav"
            )

        mock_server.socket.sendto.side_effect = simulate_response

        success, _ = client.start_recording(path="~/my_recording.wav")

        assert success is True

    def test_stop_recording_when_not_recording(self, client):
        """Should fail when not currently recording."""
        client._is_recording = False

        success, message = client.stop_recording()

        assert success is False
        assert "Not currently recording" in message

    def test_stop_recording_success(self, client, mocker):
        """Should stop recording and clear state."""
        mock_proc = mocker.MagicMock()
        mock_proc.poll.return_value = None
        client._sclang_process = mock_proc

        mock_server = mocker.MagicMock()
        mock_server.socket = mocker.MagicMock()
        client._reply_server = mock_server

        # Set up recording state
        client._is_recording = True
        client._recording_path = "/tmp/test.wav"

        def simulate_response(dgram, addr):
            request_id = client._eval_request_id
            client._handle_eval_result("/mcp/eval/result", request_id, 1, "Recording stopped")

        mock_server.socket.sendto.side_effect = simulate_response

        success, message = client.stop_recording()

        assert success is True
        assert "Recording saved" in message
        assert "/tmp/test.wav" in message
        assert client._is_recording is False
        assert client._recording_path is None

    def test_stop_recording_without_sclang(self, client):
        """Should clear state even if sclang unavailable."""
        client._sclang_process = None
        client._is_recording = True
        client._recording_path = "/tmp/test.wav"

        success, message = client.stop_recording()

        assert success is False
        assert "sclang not available" in message
        # State should still be cleared
        assert client._is_recording is False
        assert client._recording_path is None

    def test_is_recording(self, client):
        """Should return recording state."""
        client._is_recording = False
        assert client.is_recording() is False

        client._is_recording = True
        assert client.is_recording() is True

    def test_get_recording_path(self, client):
        """Should return current recording path."""
        client._recording_path = None
        assert client.get_recording_path() is None

        client._recording_path = "/tmp/test.wav"
        assert client.get_recording_path() == "/tmp/test.wav"

    def test_disconnect_stops_recording(self, client, mocker):
        """Disconnect should stop recording if in progress."""
        mock_proc = mocker.MagicMock()
        mock_proc.poll.return_value = None
        client._sclang_process = mock_proc

        mock_server = mocker.MagicMock()
        mock_server.socket = mocker.MagicMock()
        client._reply_server = mock_server

        client._is_recording = True
        client._recording_path = "/tmp/test.wav"

        def simulate_response(dgram, addr):
            request_id = client._eval_request_id
            client._handle_eval_result("/mcp/eval/result", request_id, 1, "ok")

        mock_server.socket.sendto.side_effect = simulate_response

        client.disconnect()

        assert client._is_recording is False
