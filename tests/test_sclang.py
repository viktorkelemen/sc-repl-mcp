"""Tests for sc_repl_mcp.sclang subprocess functions."""

import os
import subprocess
import pytest

from sc_repl_mcp.sclang import find_sclang, eval_sclang
from sc_repl_mcp.config import MAX_EVAL_TIMEOUT


class TestFindSclang:
    """Tests for find_sclang function."""

    def test_finds_sclang_in_path(self, mocker):
        """Should return path from shutil.which if found."""
        mocker.patch("shutil.which", return_value="/usr/local/bin/sclang")

        result = find_sclang()

        assert result == "/usr/local/bin/sclang"

    def test_falls_back_to_macos_location(self, mocker):
        """Should check standard macOS location if not in PATH."""
        mocker.patch("shutil.which", return_value=None)
        mocker.patch("platform.system", return_value="Darwin")
        mocker.patch("os.path.isfile", side_effect=lambda p: p == "/Applications/SuperCollider.app/Contents/MacOS/sclang")

        result = find_sclang()

        assert result == "/Applications/SuperCollider.app/Contents/MacOS/sclang"

    def test_falls_back_to_linux_location(self, mocker):
        """Should check standard Linux location if not in PATH."""
        mocker.patch("shutil.which", return_value=None)
        mocker.patch("platform.system", return_value="Linux")
        mocker.patch("os.path.isfile", side_effect=lambda p: p == "/usr/bin/sclang")

        result = find_sclang()

        assert result == "/usr/bin/sclang"

    def test_returns_none_when_not_found(self, mocker):
        """Should return None if sclang not found anywhere."""
        mocker.patch("shutil.which", return_value=None)
        mocker.patch("platform.system", return_value="Darwin")
        mocker.patch("os.path.isfile", return_value=False)

        result = find_sclang()

        assert result is None

    def test_expands_user_path(self, mocker):
        """Should expand ~ in paths."""
        mocker.patch("shutil.which", return_value=None)
        mocker.patch("platform.system", return_value="Darwin")

        expanded_calls = []
        original_expanduser = os.path.expanduser

        def track_expanduser(path):
            result = original_expanduser(path)
            expanded_calls.append(path)
            return result

        mocker.patch("os.path.expanduser", side_effect=track_expanduser)
        mocker.patch("os.path.isfile", return_value=False)

        find_sclang()

        # Should have tried to expand the ~/Applications path
        assert any("~" in call for call in expanded_calls)


class TestEvalSclang:
    """Tests for eval_sclang function."""

    def test_rejects_empty_code(self):
        """Should return error for empty code."""
        success, output = eval_sclang("")
        assert success is False
        assert "No code provided" in output

    def test_rejects_whitespace_only(self):
        """Should return error for whitespace-only code."""
        success, output = eval_sclang("   \n\t  ")
        assert success is False
        assert "No code provided" in output

    def test_returns_error_when_sclang_not_found(self, mocker):
        """Should return error if sclang not found."""
        mocker.patch("sc_repl_mcp.sclang.find_sclang", return_value=None)

        success, output = eval_sclang("1 + 1")

        assert success is False
        assert "sclang not found" in output

    def test_successful_execution(self, mocker):
        """Should return success with stdout on successful execution."""
        mocker.patch("sc_repl_mcp.sclang.find_sclang", return_value="/usr/bin/sclang")

        mock_proc = mocker.Mock()
        mock_proc.communicate.return_value = ("Result: 42\n", "")
        mock_proc.returncode = 0

        mocker.patch("subprocess.Popen", return_value=mock_proc)
        mocker.patch("tempfile.NamedTemporaryFile", mocker.mock_open())
        mocker.patch("os.unlink")

        success, output = eval_sclang("1 + 1")

        assert success is True
        assert "42" in output

    def test_handles_timeout(self, mocker):
        """Should kill process and return error on timeout."""
        mocker.patch("sc_repl_mcp.sclang.find_sclang", return_value="/usr/bin/sclang")

        mock_proc = mocker.Mock()
        mock_proc.communicate.side_effect = subprocess.TimeoutExpired("sclang", 30)
        mock_proc.kill = mocker.Mock()
        mock_proc.wait = mocker.Mock()

        mocker.patch("subprocess.Popen", return_value=mock_proc)
        mocker.patch("tempfile.NamedTemporaryFile", mocker.mock_open())
        mocker.patch("os.unlink")

        success, output = eval_sclang("inf.wait", timeout=30)

        assert success is False
        assert "timed out" in output
        mock_proc.kill.assert_called_once()

    def test_handles_nonzero_exit_code(self, mocker):
        """Should return error for non-zero exit code."""
        mocker.patch("sc_repl_mcp.sclang.find_sclang", return_value="/usr/bin/sclang")

        mock_proc = mocker.Mock()
        mock_proc.communicate.return_value = ("", "ERROR: syntax error")
        mock_proc.returncode = 1

        mocker.patch("subprocess.Popen", return_value=mock_proc)
        mocker.patch("tempfile.NamedTemporaryFile", mocker.mock_open())
        mocker.patch("os.unlink")

        success, output = eval_sclang("invalid {{{")

        assert success is False
        assert "exited with code 1" in output

    def test_filters_stderr_noise(self, mocker):
        """Should filter common sclang startup noise from stderr."""
        mocker.patch("sc_repl_mcp.sclang.find_sclang", return_value="/usr/bin/sclang")

        # Simulate typical sclang startup noise
        stderr_noise = """compiling class library
NumPrimitives = 725
Welcome to SuperCollider 3.12.0
type 'help' for a list of commands
Compiling...
Found 123 primitives
Read file /some/path
ACTUAL ERROR: something went wrong"""

        mock_proc = mocker.Mock()
        mock_proc.communicate.return_value = ("Output", stderr_noise)
        mock_proc.returncode = 0

        mocker.patch("subprocess.Popen", return_value=mock_proc)
        mocker.patch("tempfile.NamedTemporaryFile", mocker.mock_open())
        mocker.patch("os.unlink")

        success, output = eval_sclang("1 + 1")

        assert success is True
        # Noise should be filtered
        assert "compiling class library" not in output
        assert "NumPrimitives" not in output
        assert "Welcome to SuperCollider" not in output
        # Actual error should remain
        assert "ACTUAL ERROR" in output

    def test_adds_semicolon_if_missing(self, mocker):
        """Should append semicolon to code if missing."""
        mocker.patch("sc_repl_mcp.sclang.find_sclang", return_value="/usr/bin/sclang")

        mock_file = mocker.mock_open()
        mocker.patch("tempfile.NamedTemporaryFile", mock_file)

        mock_proc = mocker.Mock()
        mock_proc.communicate.return_value = ("", "")
        mock_proc.returncode = 0
        mocker.patch("subprocess.Popen", return_value=mock_proc)
        mocker.patch("os.unlink")

        eval_sclang("1 + 1")

        # Check what was written to the temp file
        written_content = "".join(call.args[0] for call in mock_file().write.call_args_list)
        # Code should end with semicolon before the exit footer
        assert "1 + 1;" in written_content

    def test_caps_timeout_to_max(self, mocker):
        """Should cap timeout to MAX_EVAL_TIMEOUT."""
        mocker.patch("sc_repl_mcp.sclang.find_sclang", return_value="/usr/bin/sclang")

        mock_proc = mocker.Mock()
        mock_proc.communicate.return_value = ("", "")
        mock_proc.returncode = 0
        mocker.patch("subprocess.Popen", return_value=mock_proc)
        mocker.patch("tempfile.NamedTemporaryFile", mocker.mock_open())
        mocker.patch("os.unlink")

        eval_sclang("1 + 1", timeout=9999)  # Way over max

        # communicate should be called with capped timeout
        mock_proc.communicate.assert_called_once()
        call_kwargs = mock_proc.communicate.call_args
        assert call_kwargs[1]["timeout"] == MAX_EVAL_TIMEOUT

    def test_cleans_up_temp_file(self, mocker):
        """Should always clean up temp file, even on error."""
        mocker.patch("sc_repl_mcp.sclang.find_sclang", return_value="/usr/bin/sclang")

        mock_proc = mocker.Mock()
        mock_proc.communicate.side_effect = Exception("Unexpected error")
        mocker.patch("subprocess.Popen", return_value=mock_proc)

        temp_file_name = "/tmp/test_12345.scd"
        mock_file = mocker.mock_open()
        mock_file.return_value.name = temp_file_name
        mocker.patch("tempfile.NamedTemporaryFile", mock_file)

        mock_unlink = mocker.patch("os.unlink")

        eval_sclang("1 + 1")

        # unlink should be called even though exception occurred
        mock_unlink.assert_called_once_with(temp_file_name)

    def test_returns_no_output_message(self, mocker):
        """Should return '(no output)' when stdout/stderr are empty."""
        mocker.patch("sc_repl_mcp.sclang.find_sclang", return_value="/usr/bin/sclang")

        mock_proc = mocker.Mock()
        mock_proc.communicate.return_value = ("", "")
        mock_proc.returncode = 0

        mocker.patch("subprocess.Popen", return_value=mock_proc)
        mocker.patch("tempfile.NamedTemporaryFile", mocker.mock_open())
        mocker.patch("os.unlink")

        success, output = eval_sclang("nil")

        assert success is True
        assert output == "(no output)"
