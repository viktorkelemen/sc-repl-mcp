import { boot as bootLang } from "@supercollider/lang";

// Path to sclang executable (macOS default)
const SCLANG_PATH = process.env.SCLANG_PATH || "/Applications/SuperCollider.app/Contents/MacOS/sclang";

// Type for the SCLang instance returned by boot()
interface SCLangInstance {
  isReady(): boolean;
  interpret(
    code: string,
    nowExecutingPath?: string,
    asString?: boolean,
    postErrors?: boolean,
    getBacktrace?: boolean
  ): Promise<unknown>;
  quit(): Promise<unknown>;
}

export interface SCStatus {
  running: boolean;
  numUGens?: number;
  numSynths?: number;
  numGroups?: number;
  numSynthDefs?: number;
  avgCPU?: number;
  peakCPU?: number;
  sampleRate?: number;
}

export interface EvalResult {
  success: boolean;
  result?: string;
  error?: string;
}

class SCClient {
  private lang: SCLangInstance | null = null;
  private serverBooted: boolean = false;

  async boot(): Promise<{ success: boolean; message: string }> {
    if (this.lang && this.lang.isReady()) {
      return { success: true, message: "sclang already running" };
    }

    try {
      // Boot sclang interpreter with explicit path
      this.lang = await bootLang({ sclang: SCLANG_PATH });

      // Boot the audio server only if not already running
      const bootResult = await this.interpret(`
        if(s.serverRunning) {
          "SERVER_ALREADY_RUNNING"
        } {
          s.waitForBoot {
            s.sync;
            "SERVER_READY"
          };
        }
      `, 30000); // 30 second timeout for server boot

      if (bootResult.success) {
        this.serverBooted = true;
        const alreadyRunning = bootResult.result?.includes("ALREADY_RUNNING");
        return {
          success: true,
          message: alreadyRunning
            ? "Connected to existing SuperCollider server"
            : "SuperCollider server booted successfully"
        };
      } else {
        return { success: false, message: bootResult.error || "Failed to boot server" };
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      return { success: false, message: `Failed to start sclang: ${message}` };
    }
  }

  async interpret(code: string, timeout: number = 5000): Promise<EvalResult> {
    if (!this.lang || !this.lang.isReady()) {
      return {
        success: false,
        error: "sclang is not running. Call sc_boot first."
      };
    }

    try {
      const result = await Promise.race([
        this.lang.interpret(code, undefined, true, true, true),
        new Promise<never>((_, reject) =>
          setTimeout(() => reject(new Error("Evaluation timeout")), timeout)
        )
      ]);

      return {
        success: true,
        result: typeof result === "string" ? result : JSON.stringify(result)
      };
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);

      // Detect if sclang process has crashed
      if (message.includes("EPIPE") || message.includes("killed") || message.includes("ECONNRESET")) {
        this.handleCrash();
        return {
          success: false,
          error: "sclang process crashed. Call sc_boot to restart."
        };
      }

      return {
        success: false,
        error: message
      };
    }
  }

  private handleCrash(): void {
    this.lang = null;
    this.serverBooted = false;
  }

  async getStatus(): Promise<SCStatus> {
    if (!this.lang || !this.lang.isReady()) {
      return { running: false };
    }

    try {
      // Query server status via sclang
      const result = await this.interpret(`
        if(s.serverRunning) {
          "running:true,ugens:%,synths:%,groups:%,synthdefs:%,avgCPU:%,peakCPU:%,sampleRate:%".format(
            s.numUGens, s.numSynths, s.numGroups, s.numSynthDefs,
            s.avgCPU.round(0.01), s.peakCPU.round(0.01), s.sampleRate
          )
        } { "running:false" }
      `, 2000);

      if (result.success && result.result) {
        const cleaned = result.result.replace(/['"]/g, "");
        const pairs = cleaned.split(",");
        const status: SCStatus = { running: false };

        for (const pair of pairs) {
          const [key, value] = pair.split(":");
          if (key === "running") status.running = value === "true";
          else if (key === "ugens") status.numUGens = parseInt(value);
          else if (key === "synths") status.numSynths = parseInt(value);
          else if (key === "groups") status.numGroups = parseInt(value);
          else if (key === "synthdefs") status.numSynthDefs = parseInt(value);
          else if (key === "avgCPU") status.avgCPU = parseFloat(value);
          else if (key === "peakCPU") status.peakCPU = parseFloat(value);
          else if (key === "sampleRate") status.sampleRate = parseFloat(value);
        }

        return status;
      }

      return { running: this.serverBooted };
    } catch {
      return { running: false };
    }
  }

  async freeAll(): Promise<{ success: boolean; message: string }> {
    const result = await this.interpret("s.freeAll");
    if (result.success) {
      return { success: true, message: "All synths freed" };
    }
    return { success: false, message: result.error || "Failed to free synths" };
  }

  async listSynthDefs(): Promise<string[]> {
    const result = await this.interpret("SynthDescLib.global.synthDescs.keys.asArray.collect(_.asString)");
    if (result.success && result.result) {
      try {
        // Parse the SC array format
        const cleaned = result.result.replace(/^\[|\]$/g, "").replace(/'/g, "");
        return cleaned.split(",").map(s => s.trim()).filter(s => s.length > 0);
      } catch {
        return [];
      }
    }
    return [];
  }

  async getMidiDevices(): Promise<{ sources: string[]; destinations: string[] }> {
    const result = await this.interpret(`
      MIDIClient.init;
      "SOURCES:" ++ MIDIClient.sources.collect(_.name).asString ++
      "|DESTINATIONS:" ++ MIDIClient.destinations.collect(_.name).asString
    `);

    if (result.success && result.result) {
      const cleaned = result.result.replace(/['"]/g, "");
      const [sourcePart, destPart] = cleaned.split("|");

      const parseList = (str: string): string[] => {
        const match = str.match(/\[(.*)\]/);
        if (match) {
          return match[1].split(",").map(s => s.trim()).filter(s => s.length > 0);
        }
        return [];
      };

      return {
        sources: parseList(sourcePart || ""),
        destinations: parseList(destPart || "")
      };
    }

    return { sources: [], destinations: [] };
  }

  async quit(): Promise<void> {
    if (this.lang) {
      await this.interpret("s.quit");
      await this.lang.quit();
      this.lang = null;
      this.serverBooted = false;
    }
  }

  isRunning(): boolean {
    return this.lang !== null && this.lang.isReady();
  }
}

// Singleton instance
export const scClient = new SCClient();
