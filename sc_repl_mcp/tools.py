"""MCP tool definitions for SC-REPL MCP Server."""

from datetime import datetime
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from .client import SCClient
from .sclang import eval_sclang

# Global client instance
sc_client = SCClient()

# Create MCP server
mcp = FastMCP("sc-repl")


@mcp.tool()
def sc_connect() -> str:
    """Connect to the SuperCollider server (scsynth). Make sure SuperCollider.app is running with the server booted."""
    _, message = sc_client.connect()
    return message


@mcp.tool()
def sc_status() -> str:
    """Get current SuperCollider server status (running, CPU, synths, groups)."""
    status = sc_client.get_status()
    if not status.running:
        return "SuperCollider server is not running. Use sc_connect first (and make sure SuperCollider.app server is booted)."

    return f"""SuperCollider Server Status:
- Running: {status.running}
- Sample Rate: {status.sample_rate} Hz
- UGens: {status.num_ugens}
- Synths: {status.num_synths}
- Groups: {status.num_groups}
- SynthDefs: {status.num_synthdefs}
- CPU (avg): {status.avg_cpu:.2f}%
- CPU (peak): {status.peak_cpu:.2f}%"""


@mcp.tool()
def sc_play_sine(freq: float = 440.0, amp: float = 0.1, dur: float = 1.0) -> str:
    """Play a sine wave tone.

    Args:
        freq: Frequency in Hz (default 440)
        amp: Amplitude 0-1 (default 0.1)
        dur: Duration in seconds (default 1)
    """
    _, message = sc_client.play_sine(freq=freq, amp=amp, dur=dur)
    return message


@mcp.tool()
def sc_free_all() -> str:
    """Free all running synths on the server."""
    _, message = sc_client.free_all()
    return message


@mcp.tool()
def sc_start_analyzer() -> str:
    """Start the audio analyzer to monitor pitch, timbre, and amplitude.

    Requires the mcp_synthdefs.scd file to be run in SuperCollider IDE first.
    The analyzer monitors the main output bus and provides real-time analysis.
    """
    _, message = sc_client.start_analyzer()
    return message


@mcp.tool()
def sc_stop_analyzer() -> str:
    """Stop the audio analyzer."""
    _, message = sc_client.stop_analyzer()
    return message


@mcp.tool()
def sc_get_analysis() -> str:
    """Get the latest audio analysis data.

    Returns pitch (frequency, note, cents deviation), timbre (spectral centroid,
    flatness, inferred waveform type), and amplitude (peak, RMS, dB) information.

    The analyzer must be running (call sc_start_analyzer first).
    """
    success, message, data = sc_client.get_analysis()
    if not success:
        return message

    # Format as readable string
    p = data["pitch"]
    t = data["timbre"]
    a = data["amplitude"]

    lines = [
        "Audio Analysis:",
        "",
        f"Pitch: {p['note']} ({p['freq']} Hz, {p['cents']:+.1f} cents)",
        f"  Confidence: {p['confidence']:.0%}",
        "",
        f"Timbre:",
        f"  Spectral centroid: {t['centroid']:.0f} Hz",
        f"  Flatness: {t['flatness']:.3f} (0=tonal, 1=noise)",
        f"  Rolloff (90%): {t['rolloff']:.0f} Hz",
        "",
        f"Amplitude:",
        f"  Peak: L={a['peak_l']:.4f} R={a['peak_r']:.4f}",
        f"  RMS:  L={a['rms_l']:.4f} R={a['rms_r']:.4f}",
        f"  dB:   L={a['db_l']:.1f} R={a['db_r']:.1f}",
        "",
        f"Silent: {data['is_silent']}",
        f"Clipping: {data['is_clipping']}",
    ]

    return "\n".join(lines)


@mcp.tool()
def sc_get_onsets() -> str:
    """Get recent onset (attack/transient) events detected by the analyzer.

    Returns a list of detected sound onsets with their timestamps, pitch, and amplitude.
    Useful for rhythm detection and understanding when sounds start.

    The analyzer must be running (call sc_start_analyzer first).
    Events are cleared after reading to avoid duplicates.
    """
    from .utils import freq_to_note

    events = sc_client.get_onsets()

    if not events:
        return "No onset events detected (or analyzer not running)"

    lines = [f"Onset Events ({len(events)} detected):", ""]

    for event in events:
        note, octave, _ = freq_to_note(event.freq)
        lines.append(
            f"  [{event.timestamp:.3f}] {note}{octave} ({event.freq:.0f} Hz) amp={event.amplitude:.3f}"
        )

    return "\n".join(lines)


@mcp.tool()
def sc_play_synth(
    synthdef: str,
    params: Optional[dict[str, Any]] = None,
    dur: Optional[float] = None,
    sustain: bool = True,
) -> str:
    """Play any SynthDef with custom parameters.

    Args:
        synthdef: Name of the SynthDef to play (must be loaded in scsynth)
        params: Dictionary of parameter name -> value pairs (e.g., {"freq": 440, "amp": 0.1})
        dur: Duration in seconds. If not provided, synth plays until freed with sc_free_all.
        sustain: If True (default), releases envelope after dur. If False, hard-frees the synth.

    Example:
        sc_play_synth("mySynth", params={"freq": 330, "amp": 0.2}, dur=2.0)
    """
    _, message = sc_client.play_synth(
        synthdef=synthdef,
        params=params,
        dur=dur,
        sustain=sustain,
    )
    return message


@mcp.tool()
def sc_load_synthdef(name: str, code: str, timeout: float = 15.0) -> str:
    """Load a SynthDef reliably by writing to disk and loading via OSC.

    This is the recommended way to define SynthDefs because it avoids async race
    conditions that occur with .add (which may not complete before sclang exits).

    Args:
        name: Name of the SynthDef (e.g., "metallic")
        code: The SynthDef body - everything inside the { } including args and Out.ar
        timeout: Maximum execution time in seconds (default 15)

    Example:
        sc_load_synthdef("ping", '''
            arg freq = 440, amp = 0.1, dur = 0.5;
            var sig = SinOsc.ar(freq) * EnvGen.kr(Env.perc(0.01, dur), doneAction: 2);
            Out.ar(0, sig ! 2 * amp);
        ''')

    After loading, play it with:
        sc_play_synth("ping", params={"freq": 880, "amp": 0.2})
    """
    # Wrap the code in a SynthDef that writes to disk and loads via OSC
    full_code = f"""
SynthDef(\\{name}, {{
{code}
}}).writeDefFile;
s.sendMsg(\\d_load, SynthDef.synthDefDir ++ "{name}.scsyndef");
"SynthDef '{name}' loaded".postln;
"""
    success, output = eval_sclang(full_code, timeout=timeout)
    if success:
        return f"SynthDef '{name}' loaded successfully"
    return f"Error loading SynthDef '{name}':\n{output}"


@mcp.tool()
def sc_eval(code: str, timeout: float = 30.0) -> str:
    """Execute arbitrary SuperCollider (sclang) code.

    This spawns a new sclang process to execute the code. Useful for:
    - Playing sequences with s.sendBundle()
    - Testing SuperCollider expressions
    - One-off synthesis with { }.play

    Args:
        code: SuperCollider code to execute
        timeout: Maximum execution time in seconds (default 30)

    IMPORTANT - Common pitfalls to avoid:

    1. For SynthDefs, use sc_load_synthdef instead of sc_eval with .add
       (async .add races with process exit)

    2. Put ALL var declarations at the START before any expressions:
       WRONG:  s.sendBundle(...); var x = 1;
       RIGHT:  var x = 1; s.sendBundle(...);

    3. Use hardcoded times for sendBundle, not incrementing variables:
       WRONG:  var t = 0; s.sendBundle(t, ...); t = t + 0.1;
       RIGHT:  s.sendBundle(0.0, ...); s.sendBundle(0.1, ...);

    4. Avoid .wait, fork with blocking, or Condition.hang (will timeout)

    Example - playing a sequence:
        s.sendBundle(0.0, [\\s_new, \\default, -1, 0, 0, \\freq, 440]);
        s.sendBundle(0.2, [\\s_new, \\default, -1, 0, 0, \\freq, 550]);
        s.sendBundle(0.4, [\\s_new, \\default, -1, 0, 0, \\freq, 660]);
        "Scheduled 3 notes".postln;

    Note: Each call spawns a fresh sclang process, so state doesn't persist.
    """
    success, output = eval_sclang(code, timeout=timeout)
    if success:
        return f"Executed successfully:\n{output}"
    return f"Error:\n{output}"


@mcp.tool()
def sc_get_logs(limit: int = 50, category: Optional[str] = None) -> str:
    """Get recent server log messages.

    Captures OSC messages from scsynth including:
    - /fail messages (errors)
    - /done messages (completed operations)
    - /n_go, /n_end messages (node lifecycle)

    Args:
        limit: Maximum number of entries to return (default 50, max 500)
        category: Filter by category: 'fail', 'done', 'node', or None for all

    Note: Logs are captured from OSC communication with scsynth.
    This does not include the SuperCollider IDE's Post Window output.
    """
    limit = min(limit, 500)
    entries = sc_client.get_logs(limit=limit, category=category)

    if not entries:
        return "No log entries" + (f" in category '{category}'" if category else "")

    lines = []
    for entry in entries:
        ts = datetime.fromtimestamp(entry.timestamp).strftime("%H:%M:%S.%f")[:-3]
        lines.append(f"[{ts}] [{entry.category.upper()}] {entry.message}")

    return f"Log entries ({len(entries)}):\n" + "\n".join(lines)


@mcp.tool()
def sc_clear_logs() -> str:
    """Clear the server log buffer."""
    sc_client.clear_logs()
    return "Log buffer cleared"
