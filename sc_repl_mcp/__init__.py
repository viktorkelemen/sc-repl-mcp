"""SC-REPL MCP Server - SuperCollider integration via OSC."""

__all__ = ["main"]


def main():
    """Main entry point - lazy import to avoid eager dependency loading."""
    from .main import main as _main
    return _main()
