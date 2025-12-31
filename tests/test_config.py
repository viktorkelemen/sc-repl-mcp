"""Tests for configuration and configurable ports."""

import os
import importlib
from unittest import mock

import pytest


@pytest.fixture(autouse=True)
def restore_config_module():
    """Restore config module to default state after each test.

    This prevents state leakage between tests when using importlib.reload().
    """
    yield
    # After test, restore to default ports by clearing env vars and reloading
    env_without_ports = {k: v for k, v in os.environ.items()
                         if k not in ("SC_REPLY_PORT", "SC_SCLANG_PORT")}
    with mock.patch.dict(os.environ, env_without_ports, clear=True):
        import sc_repl_mcp.config as config
        importlib.reload(config)


class TestConfigurablePorts:
    """Test environment variable configuration for ports."""

    def test_default_reply_port(self):
        """REPLY_PORT defaults to 57130 when env var not set."""
        with mock.patch.dict(os.environ, {}, clear=True):
            os.environ.pop("SC_REPLY_PORT", None)
            import sc_repl_mcp.config as config
            importlib.reload(config)
            assert config.REPLY_PORT == 57130

    def test_default_sclang_port(self):
        """SCLANG_OSC_PORT defaults to 57122 when env var not set."""
        with mock.patch.dict(os.environ, {}, clear=True):
            os.environ.pop("SC_SCLANG_PORT", None)
            import sc_repl_mcp.config as config
            importlib.reload(config)
            assert config.SCLANG_OSC_PORT == 57122

    def test_reply_port_from_env(self):
        """REPLY_PORT can be configured via SC_REPLY_PORT env var."""
        with mock.patch.dict(os.environ, {"SC_REPLY_PORT": "57131"}):
            import sc_repl_mcp.config as config
            importlib.reload(config)
            assert config.REPLY_PORT == 57131

    def test_sclang_port_from_env(self):
        """SCLANG_OSC_PORT can be configured via SC_SCLANG_PORT env var."""
        with mock.patch.dict(os.environ, {"SC_SCLANG_PORT": "57123"}):
            import sc_repl_mcp.config as config
            importlib.reload(config)
            assert config.SCLANG_OSC_PORT == 57123


class TestPortValidation:
    """Test port validation and error handling."""

    def test_non_numeric_port_exits(self, capsys):
        """Non-numeric port value causes sys.exit with error message."""
        with mock.patch.dict(os.environ, {"SC_REPLY_PORT": "not_a_number"}):
            import sc_repl_mcp.config as config
            with pytest.raises(SystemExit) as exc_info:
                importlib.reload(config)
            assert exc_info.value.code == 1
            captured = capsys.readouterr()
            assert "SC_REPLY_PORT='not_a_number'" in captured.err
            assert "not a valid integer" in captured.err

    def test_negative_port_exits(self, capsys):
        """Negative port number causes sys.exit with error message."""
        with mock.patch.dict(os.environ, {"SC_SCLANG_PORT": "-1"}):
            import sc_repl_mcp.config as config
            with pytest.raises(SystemExit) as exc_info:
                importlib.reload(config)
            assert exc_info.value.code == 1
            captured = capsys.readouterr()
            assert "SC_SCLANG_PORT=-1" in captured.err
            assert "not a valid port number" in captured.err

    def test_zero_port_exits(self, capsys):
        """Port 0 causes sys.exit with error message."""
        with mock.patch.dict(os.environ, {"SC_REPLY_PORT": "0"}):
            import sc_repl_mcp.config as config
            with pytest.raises(SystemExit) as exc_info:
                importlib.reload(config)
            assert exc_info.value.code == 1
            captured = capsys.readouterr()
            assert "Must be between 1 and 65535" in captured.err

    def test_port_above_65535_exits(self, capsys):
        """Port above 65535 causes sys.exit with error message."""
        with mock.patch.dict(os.environ, {"SC_REPLY_PORT": "70000"}):
            import sc_repl_mcp.config as config
            with pytest.raises(SystemExit) as exc_info:
                importlib.reload(config)
            assert exc_info.value.code == 1
            captured = capsys.readouterr()
            assert "SC_REPLY_PORT=70000" in captured.err
            assert "not a valid port number" in captured.err

    def test_valid_edge_port_1(self):
        """Port 1 (minimum valid) is accepted."""
        with mock.patch.dict(os.environ, {"SC_REPLY_PORT": "1"}):
            import sc_repl_mcp.config as config
            importlib.reload(config)
            assert config.REPLY_PORT == 1

    def test_valid_edge_port_65535(self):
        """Port 65535 (maximum valid) is accepted."""
        with mock.patch.dict(os.environ, {"SC_REPLY_PORT": "65535"}):
            import sc_repl_mcp.config as config
            importlib.reload(config)
            assert config.REPLY_PORT == 65535


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


class TestParsePortFunction:
    """Test the _parse_port helper function directly."""

    def test_returns_default_when_env_not_set(self):
        """Returns default when environment variable is not set."""
        from sc_repl_mcp.config import _parse_port

        with mock.patch.dict(os.environ, {}, clear=True):
            os.environ.pop("TEST_PORT", None)
            result = _parse_port("TEST_PORT", 12345)
            assert result == 12345

    def test_returns_env_value_when_set(self):
        """Returns parsed integer when environment variable is set."""
        from sc_repl_mcp.config import _parse_port

        with mock.patch.dict(os.environ, {"TEST_PORT": "9999"}):
            result = _parse_port("TEST_PORT", 12345)
            assert result == 9999
