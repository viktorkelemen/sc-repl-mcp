#!/usr/bin/env python3
"""
MCP server for SuperCollider REPL integration.
Uses OSC to communicate directly with scsynth.

This module re-exports from the sc_repl_mcp package for backwards compatibility.
"""

from sc_repl_mcp.main import main

if __name__ == "__main__":
    main()
