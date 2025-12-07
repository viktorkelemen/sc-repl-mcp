# SC-REPL MCP Server

MCP (Model Context Protocol) server for SuperCollider REPL integration with Claude Code.

## Features

This MCP server allows Claude Code to:

- **Boot** the SuperCollider audio server
- **Evaluate** SuperCollider code and see results
- **Query** server status (CPU, synths, groups)
- **Free** all running synths
- **List** registered SynthDefs
- **List** MIDI devices

## Prerequisites

- [SuperCollider](https://supercollider.github.io/) installed and accessible via PATH
- Node.js 18+

## Installation

```bash
cd sc-repl-mcp
npm install
npm run build
```

## Register with Claude Code

```bash
claude mcp add sc-repl -- node /path/to/sc-repl-mcp/dist/index.js
```

## Available Tools

| Tool | Description |
|------|-------------|
| `sc_boot` | Boot SuperCollider (sclang + scsynth) |
| `sc_eval` | Evaluate SuperCollider code |
| `sc_status` | Get server status (CPU, synths, groups) |
| `sc_free_all` | Free all running synths |
| `sc_list_synthdefs` | List registered SynthDef names |
| `sc_midi_devices` | List MIDI input/output devices |
| `sc_quit` | Quit SuperCollider |

## Usage Examples

Once registered, you can ask Claude:

- "Boot SuperCollider and play a sine wave at 440Hz"
- "Show me the server status"
- "Create a SynthDef for a filtered sawtooth"
- "List all available MIDI devices"
- "Free all synths"

## Development

```bash
npm run dev    # Watch mode
npm run build  # Build once
npm start      # Run the server
```

## Architecture

```
Claude Code <--stdio/JSON-RPC--> sc-repl MCP <--supercolliderjs--> sclang/scsynth
```

The server uses [supercolliderjs](https://github.com/crucialfelix/supercolliderjs) to communicate with sclang via stdin/stdout.
