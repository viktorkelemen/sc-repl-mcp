#!/usr/bin/env python3
"""
MCP server for SuperCollider REPL integration.
Uses OSC to communicate directly with scsynth.
"""

import atexit
import sys
import threading
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP
from pythonosc import udp_client, osc_server, dispatcher


SCSYNTH_HOST = "127.0.0.1"
SCSYNTH_PORT = 57110
REPLY_PORT = 57130  # Avoid 57120 (sclang's default)


@dataclass
class ServerStatus:
    running: bool = False
    num_ugens: int = 0
    num_synths: int = 0
    num_groups: int = 0
    num_synthdefs: int = 0
    avg_cpu: float = 0.0
    peak_cpu: float = 0.0
    sample_rate: float = 0.0


class SCClient:
    """Client for communicating with scsynth via OSC."""

    def __init__(self):
        self.client: udp_client.SimpleUDPClient | None = None
        self.status = ServerStatus()
        self._status_event = threading.Event()
        self._reply_server: osc_server.ThreadingOSCUDPServer | None = None
        self._node_id = 1000
        self._node_lock = threading.Lock()

    def _handle_status_reply(self, address: str, *args):
        """Handle /status.reply from scsynth."""
        if len(args) >= 9:
            self.status = ServerStatus(
                running=True,
                num_ugens=args[1],
                num_synths=args[2],
                num_groups=args[3],
                num_synthdefs=args[4],
                avg_cpu=args[5],
                peak_cpu=args[6],
                sample_rate=args[8],
            )
        self._status_event.set()

    def _handle_done(self, address: str, *args):
        """Handle /done messages."""
        pass

    def _handle_fail(self, address: str, *args):
        """Handle /fail messages."""
        sys.stderr.write(f"[SC] Fail: {args}\n")

    def connect(self) -> tuple[bool, str]:
        """Connect to scsynth server."""
        # Clean up existing connection first
        if self._reply_server:
            self._reply_server.shutdown()
            self._reply_server = None

        try:
            self.client = udp_client.SimpleUDPClient(SCSYNTH_HOST, SCSYNTH_PORT)

            # Set up OSC reply server
            disp = dispatcher.Dispatcher()
            disp.map("/status.reply", self._handle_status_reply)
            disp.map("/done", self._handle_done)
            disp.map("/fail", self._handle_fail)

            self._reply_server = osc_server.ThreadingOSCUDPServer(
                (SCSYNTH_HOST, REPLY_PORT), disp
            )
            thread = threading.Thread(target=self._reply_server.serve_forever, daemon=True)
            thread.start()

            # Query status to verify connection
            status = self.get_status()
            if status.running:
                return True, f"Connected to scsynth on port {SCSYNTH_PORT}"
            else:
                return False, "scsynth not responding. Make sure SuperCollider server is running."

        except Exception as e:
            return False, f"Failed to connect: {e}"

    def get_status(self) -> ServerStatus:
        """Query server status."""
        if not self.client:
            return ServerStatus(running=False)

        try:
            self._status_event.clear()
            self.client.send_message("/status", [])

            # Wait for reply with timeout
            if self._status_event.wait(timeout=1.0):
                return self.status
            return ServerStatus(running=False)

        except Exception:
            return ServerStatus(running=False)

    def _next_node_id(self) -> int:
        """Get next available node ID (thread-safe)."""
        with self._node_lock:
            self._node_id += 1
            return self._node_id

    def play_sine(self, freq: float = 440.0, amp: float = 0.1, dur: float = 1.0) -> tuple[bool, str]:
        """Play a sine wave using scsynth's default synthdef."""
        if not self.client:
            return False, "Not connected to scsynth. Call sc_connect first."

        try:
            node_id = self._next_node_id()

            # Use s_new to create a synth with the "default" synthdef
            self.client.send_message("/s_new", [
                "default",  # synthdef name
                node_id,    # node ID
                0,          # add action (0 = add to head)
                0,          # target group (0 = default group)
                "freq", freq,
                "amp", amp,
            ])

            # Schedule release in a background thread
            def release_later():
                import time
                time.sleep(dur)
                if self.client:
                    self.client.send_message("/n_set", [node_id, "gate", 0])

            threading.Thread(target=release_later, daemon=True).start()

            return True, f"Playing {freq}Hz sine wave for {dur}s"

        except Exception as e:
            return False, f"Failed to play sine: {e}"

    def free_all(self) -> tuple[bool, str]:
        """Free all synths."""
        if not self.client:
            return False, "Not connected to scsynth"

        try:
            self.client.send_message("/g_freeAll", [0])
            return True, "All synths freed"
        except Exception as e:
            return False, f"Failed to free synths: {e}"

    def disconnect(self):
        """Disconnect from server."""
        if self._reply_server:
            self._reply_server.shutdown()
            self._reply_server = None
        self.client = None


# Global client instance
sc_client = SCClient()

# Create MCP server
mcp = FastMCP("sc-repl")


@mcp.tool()
def sc_connect() -> str:
    """Connect to the SuperCollider server (scsynth). Make sure SuperCollider.app is running with the server booted."""
    _, message = sc_client.connect()
    return message


@mcp.tool()
def sc_status() -> str:
    """Get current SuperCollider server status (running, CPU, synths, groups)."""
    status = sc_client.get_status()
    if not status.running:
        return "SuperCollider server is not running. Use sc_connect first (and make sure SuperCollider.app server is booted)."

    return f"""SuperCollider Server Status:
- Running: {status.running}
- Sample Rate: {status.sample_rate} Hz
- UGens: {status.num_ugens}
- Synths: {status.num_synths}
- Groups: {status.num_groups}
- SynthDefs: {status.num_synthdefs}
- CPU (avg): {status.avg_cpu:.2f}%
- CPU (peak): {status.peak_cpu:.2f}%"""


@mcp.tool()
def sc_play_sine(freq: float = 440.0, amp: float = 0.1, dur: float = 1.0) -> str:
    """Play a sine wave tone.

    Args:
        freq: Frequency in Hz (default 440)
        amp: Amplitude 0-1 (default 0.1)
        dur: Duration in seconds (default 1)
    """
    _, message = sc_client.play_sine(freq=freq, amp=amp, dur=dur)
    return message


@mcp.tool()
def sc_free_all() -> str:
    """Free all running synths on the server."""
    _, message = sc_client.free_all()
    return message


# Cleanup on exit
atexit.register(sc_client.disconnect)


def main():
    """Main entry point."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
