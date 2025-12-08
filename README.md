# SC-REPL MCP Server

MCP (Model Context Protocol) server for SuperCollider integration with Claude Code.

## Features

- **Connect** to a running SuperCollider server (scsynth)
- **Load SynthDefs** reliably with automatic disk-write pattern
- **Play synths** with custom parameters and timing
- **Execute code** - run arbitrary SuperCollider code
- **Analyze audio** - real-time pitch, timbre, and amplitude monitoring
- **Debug** - access server logs and error messages

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
| `sc_connect` | Connect to SuperCollider server |
| `sc_status` | Get server status (CPU, synths, groups) |
| `sc_play_sine` | Play a sine wave test tone |
| `sc_play_synth` | Play any SynthDef with parameters |
| `sc_load_synthdef` | Load a SynthDef reliably |
| `sc_eval` | Execute arbitrary SuperCollider code |
| `sc_free_all` | Free all running synths |
| `sc_start_analyzer` | Start audio analysis |
| `sc_stop_analyzer` | Stop audio analysis |
| `sc_get_analysis` | Get pitch/timbre/amplitude data |
| `sc_get_logs` | View server log messages |
| `sc_clear_logs` | Clear log buffer |

## Quick Example

```
You: Connect to SuperCollider and create a metallic bell sound

Claude: [uses sc_connect, sc_load_synthdef, sc_play_synth]
```

## Architecture

```
Claude Code <--stdio/JSON-RPC--> sc-repl MCP <--OSC--> scsynth (port 57110)
                                     |
                                     +--spawns--> sclang (for SynthDefs)
```

The server uses OSC (Open Sound Control) to communicate directly with scsynth.
A persistent sclang process handles SynthDef loading and OSC forwarding.

## Documentation

See `CLAUDE.md` for detailed usage guidance when working with Claude Code.
