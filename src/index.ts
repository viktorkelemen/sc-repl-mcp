#!/usr/bin/env node

import { McpServer, ResourceTemplate } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { scClient } from "./sc-client.js";

// Create the MCP server
const server = new McpServer({
  name: "sc-repl",
  version: "1.0.0",
});

// Tool: sc_boot - Boot SuperCollider server
server.tool(
  "sc_boot",
  "Boot the SuperCollider audio server (sclang + scsynth)",
  {},
  async () => {
    const result = await scClient.boot();
    return {
      content: [
        {
          type: "text",
          text: result.success
            ? `SuperCollider booted successfully.\n${result.message}`
            : `Failed to boot SuperCollider: ${result.message}`,
        },
      ],
    };
  }
);

// Tool: sc_eval - Evaluate SuperCollider code
server.tool(
  "sc_eval",
  "Evaluate SuperCollider code and return the result",
  {
    code: z.string().describe("SuperCollider code to evaluate"),
    timeout: z.number().optional().describe("Timeout in milliseconds (default 5000)"),
  },
  async ({ code, timeout }) => {
    const result = await scClient.interpret(code, timeout ?? 5000);

    if (result.success) {
      return {
        content: [{ type: "text", text: `Result: ${result.result ?? "(no return value)"}` }],
      };
    } else {
      return {
        content: [
          {
            type: "text",
            text: `Error: ${result.error}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// Tool: sc_status - Get server status
server.tool(
  "sc_status",
  "Get current SuperCollider server status (running, CPU, synths, groups)",
  {},
  async () => {
    const status = await scClient.getStatus();

    if (!status.running) {
      return {
        content: [
          {
            type: "text",
            text: "SuperCollider server is not running. Use sc_boot to start it.",
          },
        ],
      };
    }

    const text = `SuperCollider Server Status:
- Running: ${status.running}
- Sample Rate: ${status.sampleRate ?? "unknown"} Hz
- UGens: ${status.numUGens ?? 0}
- Synths: ${status.numSynths ?? 0}
- Groups: ${status.numGroups ?? 0}
- SynthDefs: ${status.numSynthDefs ?? 0}
- CPU (avg): ${status.avgCPU?.toFixed(2) ?? "0"}%
- CPU (peak): ${status.peakCPU?.toFixed(2) ?? "0"}%`;

    return {
      content: [{ type: "text", text }],
    };
  }
);

// Tool: sc_free_all - Free all synths
server.tool(
  "sc_free_all",
  "Free all running synths on the server",
  {},
  async () => {
    const result = await scClient.freeAll();
    return {
      content: [
        {
          type: "text",
          text: result.success ? result.message : `Error: ${result.message}`,
        },
      ],
      isError: !result.success,
    };
  }
);

// Tool: sc_list_synthdefs - List available SynthDefs
server.tool(
  "sc_list_synthdefs",
  "List all registered SynthDef names",
  {},
  async () => {
    const synthDefs = await scClient.listSynthDefs();

    if (synthDefs.length === 0) {
      return {
        content: [
          {
            type: "text",
            text: "No SynthDefs registered (or server not running)",
          },
        ],
      };
    }

    return {
      content: [
        {
          type: "text",
          text: `Registered SynthDefs (${synthDefs.length}):\n${synthDefs.map(s => `  - ${s}`).join("\n")}`,
        },
      ],
    };
  }
);

// Tool: sc_midi_devices - List MIDI devices
server.tool(
  "sc_midi_devices",
  "List available MIDI input and output devices",
  {},
  async () => {
    const devices = await scClient.getMidiDevices();

    let text = "MIDI Devices:\n\n";
    text += `Sources (inputs):\n${devices.sources.length > 0 ? devices.sources.map(s => `  - ${s}`).join("\n") : "  (none found)"}\n\n`;
    text += `Destinations (outputs):\n${devices.destinations.length > 0 ? devices.destinations.map(s => `  - ${s}`).join("\n") : "  (none found)"}`;

    return {
      content: [{ type: "text", text }],
    };
  }
);

// Tool: sc_quit - Quit SuperCollider
server.tool(
  "sc_quit",
  "Quit the SuperCollider server and interpreter",
  {},
  async () => {
    await scClient.quit();
    return {
      content: [
        {
          type: "text",
          text: "SuperCollider has been shut down.",
        },
      ],
    };
  }
);

// Handle graceful shutdown
process.on("SIGINT", async () => {
  await scClient.quit();
  process.exit(0);
});

process.on("SIGTERM", async () => {
  await scClient.quit();
  process.exit(0);
});

// Start the server
async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);

  // Log to stderr (not stdout - that's for MCP protocol)
  console.error("SC-REPL MCP Server started");
}

main().catch((error) => {
  console.error("Failed to start server:", error);
  process.exit(1);
});
