# SC-REPL MCP Server

MCP (Model Context Protocol) server for SuperCollider integration with Claude Code.

## Features

- **Connect** to a running SuperCollider server (scsynth)
- **Query** server status (CPU, synths, groups)
- **Play** test tones
- **Free** all running synths

## Prerequisites

- [SuperCollider](https://supercollider.github.io/) installed
- SuperCollider.app running with server booted (`Server.local.boot`)
- [uv](https://docs.astral.sh/uv/) for Python package management

## Installation

```bash
cd sc-repl-mcp
uv sync
```

## Register with Claude Code

```bash
claude mcp add sc-repl -- uv run --directory /path/to/sc-repl-mcp python sc_repl_mcp.py
```

Or add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "sc-repl": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/sc-repl-mcp", "python", "sc_repl_mcp.py"]
    }
  }
}
```

## Available Tools

| Tool | Description |
|------|-------------|
| `sc_connect` | Connect to SuperCollider server (scsynth) |
| `sc_status` | Get server status (CPU, synths, groups) |
| `sc_play_sine` | Play a sine wave test tone |
| `sc_free_all` | Free all running synths |

## Usage

1. Start SuperCollider.app
2. Boot the server: `Server.local.boot`
3. Ask Claude: "Connect to SuperCollider and play a test tone"

## Architecture

```
Claude Code <--stdio/JSON-RPC--> sc-repl MCP <--OSC--> scsynth (port 57110)
```

The server uses OSC (Open Sound Control) to communicate directly with scsynth.
