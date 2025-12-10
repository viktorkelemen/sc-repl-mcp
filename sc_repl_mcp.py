#!/usr/bin/env python3
"""
MCP server for SuperCollider REPL integration.
Uses OSC to communicate directly with scsynth.
"""

import atexit
import signal
import sys

from sc_repl_mcp.tools import mcp, sc_client


def _cleanup():
    """Clean up resources on exit."""
    sc_client.disconnect()


def _signal_handler(signum, frame):
    """Handle termination signals gracefully."""
    _cleanup()
    sys.exit(0)


# Register cleanup handlers
atexit.register(_cleanup)
signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


def main():
    """Main entry point."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
