"""Main entry point for SC-REPL MCP Server."""

import atexit
import signal
import sys

from .tools import mcp, sc_client


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
