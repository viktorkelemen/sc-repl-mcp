# SC-REPL MCP Server

MCP (Model Context Protocol) server for SuperCollider integration with Claude Code.

## Features

- **Connect** to a running SuperCollider server (scsynth)
- **Load SynthDefs** reliably with automatic disk-write pattern
- **Play synths** with custom parameters and timing
- **Execute code** - run arbitrary SuperCollider code
- **Validate syntax** - check code for errors without executing (tree-sitter + sclang fallback)
- **Analyze audio** - real-time pitch, timbre, and amplitude monitoring
- **Compare sounds** - capture references and compare against them
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
| `sc_validate_syntax` | Check code syntax without executing |
| `sc_free_all` | Free all running synths |
| `sc_start_analyzer` | Start audio analysis |
| `sc_stop_analyzer` | Stop audio analysis |
| `sc_get_analysis` | Get pitch/timbre/amplitude data |
| `sc_get_onsets` | Get detected onset/attack events |
| `sc_get_spectrum` | Get frequency spectrum data |
| `sc_capture_reference` | Save current sound as reference |
| `sc_compare_to_reference` | Compare current sound to reference |
| `sc_list_references` | List saved sound references |
| `sc_delete_reference` | Delete a saved reference |
| `sc_analyze_parameter` | Analyze how a parameter affects sound |
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
                                     +--persistent--> sclang (code execution)
```

The server uses OSC (Open Sound Control) to communicate directly with scsynth.
A persistent sclang process handles code execution, SynthDef loading, and OSC forwarding.

### Performance

After `sc_connect`, a persistent sclang process stays running. This makes `sc_eval` and `sc_load_synthdef` **much faster** (~10ms vs 2-5s) by avoiding class library recompilation on each call. State persists within the session.

## Syntax Validation

The `sc_validate_syntax` tool uses a hybrid approach:

1. **tree-sitter** (fast, ~5ms) - Primary validation using a SuperCollider grammar
2. **sclang compile()** (accurate, ~200ms) - Fallback when tree-sitter unavailable

### Building the Grammar

The tree-sitter grammar must be compiled before first use:

```bash
uv run python scripts/build_grammar.py
```

Requires: git, C compiler (gcc/clang)

### Known Limitations

The tree-sitter grammar has some false positives with advanced SC syntax:

| Pattern | Status | Workaround |
|---------|--------|------------|
| `arr[i % 8]` | False positive | `var idx = i % 8; arr[idx]` |
| `Out.ar(0, sig ! 2)` | False positive | `Out.ar(0, sig.dup(2))` |
| `` `[freqs, amps] `` | False positive | Use variable: `var spec = [f,a]; Klank.ar(spec)` |

These patterns are valid SuperCollider and will execute correctly - the validator just can't parse them.

## Documentation

See `CLAUDE.md` for detailed usage guidance when working with Claude Code.
