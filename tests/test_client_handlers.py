"""Tests for SCClient OSC handler methods.

These tests call handler methods directly without needing OSC infrastructure.
"""

import time
import pytest

from sc_repl_mcp.client import SCClient
from sc_repl_mcp.types import ServerStatus, AnalysisData


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
