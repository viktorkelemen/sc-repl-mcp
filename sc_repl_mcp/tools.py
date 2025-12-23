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

    The analyzer monitors the main output bus and provides real-time analysis.
    Requires sc_connect to be called first (which loads the analyzer SynthDefs).
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
    l = data["loudness"]

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
        f"Loudness: {l['sones']:.1f} sones (perceptual)",
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
def sc_get_spectrum() -> str:
    """Get the current spectrum analyzer data (14 frequency bands).

    Returns power levels across the frequency spectrum from ~60Hz to ~16kHz.
    Useful for understanding the frequency content of the current sound.

    The analyzer must be running (call sc_start_analyzer first).
    """
    success, message, data = sc_client.get_spectrum()
    if not success:
        return message

    lines = ["Spectrum Analysis (14 bands):", ""]

    # Create a simple ASCII visualization
    for band in data["bands"]:
        freq = band["freq"]
        db = band["db"]
        # Scale dB to bar length (0 to 40 chars, -60dB to 0dB)
        bar_len = int((db + 60) / 60 * 40)
        bar_len = max(0, min(40, bar_len))
        bar = "█" * bar_len

        # Format frequency label
        if freq >= 1000:
            freq_str = f"{freq/1000:.1f}k".rjust(5)
        else:
            freq_str = f"{freq}".rjust(5)

        lines.append(f"  {freq_str} Hz │{bar} {db:.0f} dB")

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
def sc_eval(code: str, timeout: float = 120.0) -> str:
    """Execute arbitrary SuperCollider (sclang) code.

    This spawns a new sclang process to execute the code. Useful for:
    - Playing sequences with s.sendBundle()
    - Testing SuperCollider expressions
    - One-off synthesis with { }.play

    Args:
        code: SuperCollider code to execute
        timeout: Maximum execution time in seconds (default 120)

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


# Reference capture and comparison tools for sound matching

@mcp.tool()
def sc_capture_reference(name: str, description: str = "") -> str:
    """Capture the current sound as a named reference for later comparison.

    This captures the current analysis data (pitch, timbre, loudness, spectrum)
    as a snapshot that can be compared against later. Essential for sound matching
    workflows where you want to recreate a target sound.

    Args:
        name: Unique name for this reference (e.g., "target_bell", "warm_pad")
        description: Optional description of the sound characteristics

    Example workflow:
        1. Play your target sound
        2. sc_capture_reference("target", "bright metallic bell")
        3. Adjust your synth parameters
        4. sc_compare_to_reference("target") to see how close you are
    """
    _, message = sc_client.capture_reference(name=name, description=description)
    return message


@mcp.tool()
def sc_compare_to_reference(name: str) -> str:
    """Compare the current sound to a stored reference.

    Returns detailed comparison showing differences in pitch, brightness,
    loudness, and tonal character, plus an overall similarity score.

    Args:
        name: Name of the reference to compare against

    Returns comparison with:
        - Pitch difference in semitones
        - Brightness ratio (centroid comparison)
        - Loudness difference in sones
        - Character difference (tonal vs noise)
        - Overall similarity score (0-100%)
    """
    success, message, data = sc_client.compare_to_reference(name)
    if not success:
        return message

    ref = data["reference"]
    p = data["pitch"]
    b = data["brightness"]
    l = data["loudness"]
    c = data["character"]
    a = data["amplitude"]

    # Format pitch difference (handle invalid/silent sounds)
    if not p.get("valid", True):
        pitch_desc = "N/A (one or both sounds silent)"
    elif p["diff_semitones"] > 0:
        pitch_desc = f"+{p['diff_semitones']:.1f} semitones (sharper)"
    elif p["diff_semitones"] < 0:
        pitch_desc = f"{p['diff_semitones']:.1f} semitones (flatter)"
    else:
        pitch_desc = "matched"

    # Format brightness (handle invalid/silent sounds)
    if not b.get("valid", True):
        bright_desc = "N/A (one sound has no spectral content)"
    elif b["ratio"] is None:
        bright_desc = "N/A"
    elif b["ratio"] > 1.1:
        bright_desc = f"{(b['ratio']-1)*100:.0f}% brighter"
    elif b["ratio"] < 0.9:
        bright_desc = f"{(1-b['ratio'])*100:.0f}% darker"
    else:
        bright_desc = "matched"

    # Format loudness
    if l["diff_sones"] > 0.5:
        loud_desc = f"+{l['diff_sones']:.1f} sones (louder)"
    elif l["diff_sones"] < -0.5:
        loud_desc = f"{l['diff_sones']:.1f} sones (quieter)"
    else:
        loud_desc = "matched"

    # Format character
    if c["diff"] > 0.1:
        char_desc = "more noise-like"
    elif c["diff"] < -0.1:
        char_desc = "more tonal"
    else:
        char_desc = "matched"

    lines = [
        f"Comparison to '{ref['name']}':",
        f"  {ref['description']}" if ref['description'] else "",
        "",
        f"Overall Match: {data['overall_score']:.0f}%",
        "",
        f"Pitch: {pitch_desc}",
        f"  Current: {p['current_freq']:.0f} Hz, Reference: {p['reference_freq']:.0f} Hz",
        f"  Score: {p['score']:.0f}%",
        "",
        f"Brightness: {bright_desc}",
        f"  Current centroid: {b['current_centroid']:.0f} Hz, Reference: {b['reference_centroid']:.0f} Hz",
        f"  Score: {b['score']:.0f}%",
        "",
        f"Loudness: {loud_desc}",
        f"  Current: {l['current_sones']:.1f} sones, Reference: {l['reference_sones']:.1f} sones",
        f"  Score: {l['score']:.0f}%",
        "",
        f"Character: {char_desc}",
        f"  Current flatness: {c['current_flatness']:.3f}, Reference: {c['reference_flatness']:.3f}",
        f"  Score: {c['score']:.0f}%",
        "",
        f"Amplitude: {a['diff_db']:+.1f} dB difference",
    ]

    # Filter out empty lines from missing description
    lines = [ln for ln in lines if ln != ""]

    return "\n".join(lines)


@mcp.tool()
def sc_list_references() -> str:
    """List all captured sound references.

    Shows all references available for comparison, with their capture time
    and description.
    """
    from .utils import freq_to_note

    refs = sc_client.list_references()

    if not refs:
        return "No references captured. Use sc_capture_reference to capture a sound."

    lines = [f"Captured References ({len(refs)}):", ""]

    for ref in refs:
        ts = datetime.fromtimestamp(ref.timestamp).strftime("%H:%M:%S")
        note, octave, _ = freq_to_note(ref.analysis.freq)
        desc = f" - {ref.description}" if ref.description else ""

        lines.append(f"  '{ref.name}'{desc}")
        lines.append(f"    Captured at {ts}")
        lines.append(f"    Pitch: {note}{octave} ({ref.analysis.freq:.0f} Hz)")
        lines.append(f"    Centroid: {ref.analysis.centroid:.0f} Hz")
        lines.append(f"    Loudness: {ref.analysis.loudness_sones:.1f} sones")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
def sc_delete_reference(name: str) -> str:
    """Delete a stored reference.

    Args:
        name: Name of the reference to delete
    """
    _, message = sc_client.delete_reference(name)
    return message


# Parameter analysis tools

@mcp.tool()
def sc_analyze_parameter(
    synthdef: str,
    param: str,
    values: list[float],
    metric: str = "centroid",
    base_params: Optional[dict[str, Any]] = None,
) -> str:
    """Analyze how a synth parameter affects a specific audio metric.

    Sweeps a parameter through different values and measures the result.
    Essential for understanding "which knob controls brightness?" type questions.

    Args:
        synthdef: Name of the SynthDef to test (must be loaded)
        param: Parameter name to sweep (e.g., "freq", "cutoff", "resonance")
        values: List of values to test (e.g., [200, 400, 800, 1600, 3200])
        metric: What to measure - "pitch", "centroid" (brightness), "loudness", "flatness", or "rms"
        base_params: Other fixed parameters (e.g., {"amp": 0.2, "dur": 0.5})

    Example:
        sc_analyze_parameter("mySynth", "cutoff", [500, 1000, 2000, 4000], "centroid", {"amp": 0.2})

    Returns a table showing parameter_value → metric_value mapping.
    """
    success, message, results = sc_client.analyze_parameter_impact(
        synthdef=synthdef,
        param=param,
        values=values,
        metric=metric,
        base_params=base_params,
    )

    if not success:
        return message

    if not results:
        return "No results collected"

    # Format as table
    lines = [
        f"Parameter Impact Analysis: {param} → {metric}",
        f"SynthDef: {synthdef}",
        "",
        f"{'Value':>12} │ {metric.capitalize():>12}",
        "─" * 12 + "─┼─" + "─" * 12,
    ]

    for r in results:
        val_str = f"{r['value']:>12.2f}"
        if r.get("metric") is not None:
            metric_str = f"{r['metric']:>12.4f}"
        else:
            metric_str = f"{'N/A':>12}"
        lines.append(f"{val_str} │ {metric_str}")

    # Add summary
    valid_results = [r for r in results if r.get("metric") is not None]
    if len(valid_results) >= 2:
        metrics = [r["metric"] for r in valid_results]
        min_val = min(metrics)
        max_val = max(metrics)
        lines.append("")
        lines.append(f"Range: {min_val:.4f} to {max_val:.4f}")

        # Check correlation direction
        first_metric = valid_results[0]["metric"]
        last_metric = valid_results[-1]["metric"]
        if last_metric > first_metric * 1.1:
            lines.append(f"Trend: {param} ↑ causes {metric} ↑")
        elif last_metric < first_metric * 0.9:
            lines.append(f"Trend: {param} ↑ causes {metric} ↓")
        else:
            lines.append(f"Trend: {param} has minimal effect on {metric}")

    return "\n".join(lines)


# Syntax validation tool

@mcp.tool()
def sc_validate_syntax(code: str) -> str:
    """Validate SuperCollider code syntax without executing it.

    Uses tree-sitter for fast validation (~5ms) with sclang compile() fallback
    for edge cases (~200ms). Does not execute the code or produce sound.

    Useful for:
    - Checking SynthDef code before loading
    - Validating code snippets
    - Finding syntax errors with line numbers

    Args:
        code: SuperCollider code to validate

    Returns:
        Validation result with any error details including line numbers.
        Shows which backend (tree-sitter or sclang) was used.

    Example:
        sc_validate_syntax("SinOsc.ar(440)")  # Valid
        sc_validate_syntax("{ SinOsc.ar(440 }")  # Error: mismatched brackets
    """
    from .syntax import get_validator

    validator = get_validator()
    is_valid, message, errors = validator.validate(code)

    if is_valid:
        return f"Syntax valid (checked with {validator.backend})"

    lines = [f"Syntax errors found (checked with {validator.backend}):"]
    for err in errors:
        line_info = f"Line {err['line']}"
        if err.get("column", 1) > 1:
            line_info += f", col {err['column']}"
        lines.append(f"  {line_info}: {err['message']}")

    return "\n".join(lines)
