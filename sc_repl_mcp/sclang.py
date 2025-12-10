"""sclang subprocess execution for SC-REPL MCP Server."""

import os
import platform
import shutil
import subprocess
import tempfile
from typing import Optional

from .config import MAX_EVAL_TIMEOUT, SCLANG_STDERR_SKIP_PREFIXES


def find_sclang() -> Optional[str]:
    """Find the sclang executable path."""
    # Check if sclang is in PATH
    sclang_path = shutil.which("sclang")
    if sclang_path:
        return sclang_path

    # Platform-specific common locations
    system = platform.system()

    if system == "Darwin":  # macOS
        paths = [
            "/Applications/SuperCollider.app/Contents/MacOS/sclang",
            "/Applications/SuperCollider/SuperCollider.app/Contents/MacOS/sclang",
            "~/Applications/SuperCollider.app/Contents/MacOS/sclang",
        ]
    elif system == "Linux":
        paths = [
            "/usr/bin/sclang",
            "/usr/local/bin/sclang",
            "/opt/SuperCollider/bin/sclang",
        ]
    elif system == "Windows":
        paths = [
            r"C:\Program Files\SuperCollider\sclang.exe",
            r"C:\Program Files (x86)\SuperCollider\sclang.exe",
        ]
    else:
        paths = []

    for path in paths:
        expanded = os.path.expanduser(path)
        if os.path.isfile(expanded):
            return expanded

    return None


def eval_sclang(code: str, timeout: float = 30.0) -> tuple[bool, str]:
    """Execute SuperCollider code via sclang subprocess.

    Args:
        code: SuperCollider code to execute
        timeout: Maximum execution time in seconds (default 30, max 300)

    Returns:
        (success, output) tuple
    """
    # Validate input
    if not code or not code.strip():
        return False, "No code provided"

    # Cap timeout to prevent excessive waits
    timeout = min(timeout, MAX_EVAL_TIMEOUT)

    sclang = find_sclang()
    if not sclang:
        return False, "sclang not found. Make sure SuperCollider is installed and sclang is in PATH or at standard location."

    # sclang doesn't support -e flag, so we write code to a temp file
    # Prepend server connection code so SynthDefs are added to the correct server
    # Use fork with s.sync to ensure server is ready, then delay before exit
    server_connect = """
// Connect to the existing scsynth server (running in SuperCollider.app)
Server.default = Server.remote(\\scsynth, NetAddr("127.0.0.1", 57110));
s = Server.default;
"""
    code_footer = """
0.exit;
"""
    # Ensure code ends with semicolon
    code_stripped = code.rstrip()
    if not code_stripped.endswith(';'):
        code_stripped += ';'
    code_with_exit = server_connect + code_stripped + code_footer

    temp_path = None
    proc = None
    try:
        # Create a temporary .scd file
        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.scd',
            delete=False,
        ) as f:
            f.write(code_with_exit)
            temp_path = f.name

        proc = subprocess.Popen(
            [sclang, temp_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Kill the process and reap it
            proc.kill()
            proc.wait()
            return False, f"sclang execution timed out after {timeout}s"

        # Combine stdout and stderr
        output_parts = []
        if stdout and stdout.strip():
            output_parts.append(stdout.strip())
        if stderr and stderr.strip():
            # Filter out common sclang startup noise using prefix matching
            stderr_lines = []
            for line in stderr.strip().split('\n'):
                stripped = line.strip()
                # Skip lines that start with known noise prefixes
                if stripped.startswith(SCLANG_STDERR_SKIP_PREFIXES):
                    continue
                stderr_lines.append(line)
            if stderr_lines:
                output_parts.append("stderr: " + '\n'.join(stderr_lines))

        output = '\n'.join(output_parts) if output_parts else "(no output)"

        # Non-zero return code indicates error (but 0.exit returns 0)
        if proc.returncode != 0:
            return False, f"sclang exited with code {proc.returncode}\n{output}"

        return True, output

    except FileNotFoundError:
        return False, f"sclang not found at {sclang}"
    except Exception as e:
        return False, f"Error executing sclang: {e}"
    finally:
        # Always clean up temp file
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
