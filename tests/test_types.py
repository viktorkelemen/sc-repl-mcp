"""Tests for sc_repl_mcp.types dataclasses."""

import pytest

from sc_repl_mcp.types import LogEntry, ServerStatus, AnalysisData, OnsetEvent, SpectrumData, ReferenceSnapshot


class TestLogEntry:
    """Tests for LogEntry dataclass."""

    def test_create_with_all_fields(self):
        entry = LogEntry(timestamp=1234567890.0, category="fail", message="Test error")
        assert entry.timestamp == 1234567890.0
        assert entry.category == "fail"
        assert entry.message == "Test error"

    def test_categories(self):
        """LogEntry should accept various category values."""
        for category in ["fail", "done", "node", "osc", "info"]:
            entry = LogEntry(timestamp=0.0, category=category, message="test")
            assert entry.category == category


class TestServerStatus:
    """Tests for ServerStatus dataclass."""

    def test_default_values(self):
        status = ServerStatus()
        assert status.running is False
        assert status.num_ugens == 0
        assert status.num_synths == 0
        assert status.num_groups == 0
        assert status.num_synthdefs == 0
        assert status.avg_cpu == 0.0
        assert status.peak_cpu == 0.0
        assert status.sample_rate == 0.0

    def test_create_with_values(self):
        status = ServerStatus(
            running=True,
            num_ugens=100,
            num_synths=10,
            num_groups=5,
            num_synthdefs=200,
            avg_cpu=15.5,
            peak_cpu=25.3,
            sample_rate=48000.0,
        )
        assert status.running is True
        assert status.num_ugens == 100
        assert status.num_synths == 10
        assert status.num_groups == 5
        assert status.num_synthdefs == 200
        assert status.avg_cpu == 15.5
        assert status.peak_cpu == 25.3
        assert status.sample_rate == 48000.0

    def test_partial_override(self):
        """Can override just some defaults."""
        status = ServerStatus(running=True, num_synths=5)
        assert status.running is True
        assert status.num_synths == 5
        assert status.num_ugens == 0  # Still default


class TestAnalysisData:
    """Tests for AnalysisData dataclass."""

    def test_default_values(self):
        data = AnalysisData()
        assert data.timestamp == 0.0
        assert data.freq == 0.0
        assert data.has_freq == 0.0
        assert data.centroid == 0.0
        assert data.flatness == 0.0
        assert data.rolloff == 0.0
        assert data.peak_l == 0.0
        assert data.peak_r == 0.0
        assert data.rms_l == 0.0
        assert data.rms_r == 0.0
        assert data.loudness_sones == 0.0

    def test_create_with_pitch_data(self):
        data = AnalysisData(
            timestamp=1234567890.0,
            freq=440.0,
            has_freq=0.95,
        )
        assert data.timestamp == 1234567890.0
        assert data.freq == 440.0
        assert data.has_freq == 0.95
        # Other fields still default
        assert data.centroid == 0.0

    def test_create_with_full_analysis(self):
        data = AnalysisData(
            timestamp=1234567890.0,
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
        assert data.freq == 440.0
        assert data.centroid == 880.0
        assert data.flatness == 0.1
        assert data.rolloff == 4000.0
        assert data.peak_l == 0.8
        assert data.peak_r == 0.75
        assert data.rms_l == 0.3
        assert data.rms_r == 0.28
        assert data.loudness_sones == 12.5


class TestOnsetEvent:
    """Tests for OnsetEvent dataclass."""

    def test_default_values(self):
        event = OnsetEvent()
        assert event.timestamp == 0.0
        assert event.freq == 0.0
        assert event.amplitude == 0.0

    def test_create_with_values(self):
        event = OnsetEvent(
            timestamp=1234567890.0,
            freq=440.0,
            amplitude=0.5,
        )
        assert event.timestamp == 1234567890.0
        assert event.freq == 440.0
        assert event.amplitude == 0.5

    def test_partial_override(self):
        """Can override just some defaults."""
        event = OnsetEvent(freq=880.0)
        assert event.freq == 880.0
        assert event.timestamp == 0.0  # Still default
        assert event.amplitude == 0.0  # Still default


class TestSpectrumData:
    """Tests for SpectrumData dataclass."""

    def test_default_values(self):
        data = SpectrumData()
        assert data.timestamp == 0.0
        assert data.bands == (0.0,) * 14
        assert len(data.bands) == 14

    def test_create_with_values(self):
        bands = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 0.9, 0.8, 0.7, 0.6)
        data = SpectrumData(
            timestamp=1234567890.0,
            bands=bands,
        )
        assert data.timestamp == 1234567890.0
        assert data.bands == bands
        assert len(data.bands) == 14

    def test_bands_is_tuple(self):
        """Bands should be a tuple (immutable) for thread safety."""
        data = SpectrumData()
        assert isinstance(data.bands, tuple)

    def test_partial_override(self):
        """Can override just timestamp."""
        data = SpectrumData(timestamp=100.0)
        assert data.timestamp == 100.0
        assert data.bands == (0.0,) * 14  # Still default


class TestReferenceSnapshot:
    """Tests for ReferenceSnapshot dataclass."""

    def test_create_with_required_fields(self):
        analysis = AnalysisData(freq=440.0, centroid=880.0, loudness_sones=10.0)
        ref = ReferenceSnapshot(
            name="test_ref",
            timestamp=1234567890.0,
            analysis=analysis,
        )
        assert ref.name == "test_ref"
        assert ref.timestamp == 1234567890.0
        assert ref.analysis == analysis
        assert ref.spectrum is None  # Default
        assert ref.description == ""  # Default

    def test_create_with_all_fields(self):
        analysis = AnalysisData(freq=440.0, centroid=880.0)
        spectrum = SpectrumData(timestamp=1234567890.0)
        ref = ReferenceSnapshot(
            name="my_bell",
            timestamp=1234567890.0,
            analysis=analysis,
            spectrum=spectrum,
            description="Bright metallic bell sound",
        )
        assert ref.name == "my_bell"
        assert ref.analysis == analysis
        assert ref.spectrum == spectrum
        assert ref.description == "Bright metallic bell sound"

    def test_analysis_contains_loudness(self):
        """ReferenceSnapshot should store analysis with loudness_sones."""
        analysis = AnalysisData(
            freq=440.0,
            centroid=880.0,
            loudness_sones=15.5,
        )
        ref = ReferenceSnapshot(
            name="test",
            timestamp=100.0,
            analysis=analysis,
        )
        assert ref.analysis.loudness_sones == 15.5
