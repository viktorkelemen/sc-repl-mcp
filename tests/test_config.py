"""Tests for configuration and configurable ports."""

import os
from unittest import mock

import pytest


class TestConfigurablePorts:
    """Test environment variable configuration for ports."""

    def test_default_reply_port(self):
        """REPLY_PORT defaults to 57130 when env var not set."""
        # Clear env var if set and reimport
        with mock.patch.dict(os.environ, {}, clear=True):
            # Remove SC_REPLY_PORT if present
            os.environ.pop("SC_REPLY_PORT", None)
            # Force reimport to pick up new env
            import importlib
            import sc_repl_mcp.config as config
            importlib.reload(config)
            assert config.REPLY_PORT == 57130

    def test_default_sclang_port(self):
        """SCLANG_OSC_PORT defaults to 57122 when env var not set."""
        with mock.patch.dict(os.environ, {}, clear=True):
            os.environ.pop("SC_SCLANG_PORT", None)
            import importlib
            import sc_repl_mcp.config as config
            importlib.reload(config)
            assert config.SCLANG_OSC_PORT == 57122

    def test_reply_port_from_env(self):
        """REPLY_PORT can be configured via SC_REPLY_PORT env var."""
        with mock.patch.dict(os.environ, {"SC_REPLY_PORT": "57131"}):
            import importlib
            import sc_repl_mcp.config as config
            importlib.reload(config)
            assert config.REPLY_PORT == 57131

    def test_sclang_port_from_env(self):
        """SCLANG_OSC_PORT can be configured via SC_SCLANG_PORT env var."""
        with mock.patch.dict(os.environ, {"SC_SCLANG_PORT": "57123"}):
            import importlib
            import sc_repl_mcp.config as config
            importlib.reload(config)
            assert config.SCLANG_OSC_PORT == 57123


class TestSclangInitCode:
    """Test the get_sclang_init_code function."""

    def test_generates_code_with_custom_ports(self):
        """Init code contains the specified ports."""
        from sc_repl_mcp.config import get_sclang_init_code

        code = get_sclang_init_code(sclang_port=57123, reply_port=57131)

        # Check sclang port is used in multiple places
        assert "thisProcess.openUDPPort(57123)" in code
        assert "OSC port 57123" in code
        assert "recvPort: 57123" in code

        # Check reply port is used for forwarding
        assert 'NetAddr("127.0.0.1", 57131)' in code

    def test_generates_code_with_default_ports(self):
        """Init code works with default port values."""
        from sc_repl_mcp.config import get_sclang_init_code

        code = get_sclang_init_code(sclang_port=57122, reply_port=57130)

        assert "thisProcess.openUDPPort(57122)" in code
        assert 'NetAddr("127.0.0.1", 57130)' in code

    def test_sclang_init_code_constant_uses_configured_ports(self):
        """SCLANG_INIT_CODE constant uses current port values."""
        # Set custom ports via env vars
        with mock.patch.dict(os.environ, {
            "SC_REPLY_PORT": "57135",
            "SC_SCLANG_PORT": "57125"
        }):
            import importlib
            import sc_repl_mcp.config as config
            importlib.reload(config)

            # The constant should reflect the configured values
            assert "thisProcess.openUDPPort(57125)" in config.SCLANG_INIT_CODE
            assert 'NetAddr("127.0.0.1", 57135)' in config.SCLANG_INIT_CODE

    def test_init_code_contains_synthdef_definitions(self):
        """Init code contains the analyzer SynthDefs."""
        from sc_repl_mcp.config import get_sclang_init_code

        code = get_sclang_init_code(sclang_port=57122, reply_port=57130)

        assert "SynthDef(\\mcp_analyzer" in code
        assert "SynthDef(\\mcp_meter" in code

    def test_init_code_contains_osc_forwarding(self):
        """Init code sets up OSC forwarding for analysis messages."""
        from sc_repl_mcp.config import get_sclang_init_code

        code = get_sclang_init_code(sclang_port=57122, reply_port=57130)

        assert "'/mcp/analysis'" in code
        assert "'/mcp/onset'" in code
        assert "'/mcp/spectrum'" in code
        assert "'/mcp/meter'" in code

    def test_init_code_contains_eval_responder(self):
        """Init code sets up the code execution responder."""
        from sc_repl_mcp.config import get_sclang_init_code

        code = get_sclang_init_code(sclang_port=57122, reply_port=57130)

        assert "'/mcp/eval'" in code
        assert "'/mcp/eval/result'" in code
