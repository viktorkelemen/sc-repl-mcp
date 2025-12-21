# SC-REPL MCP Server - Claude Usage Guide

This MCP server enables Claude to control SuperCollider for sound synthesis and music creation.

## Quick Start

1. **Connect**: `sc_connect` - connects to scsynth and starts sclang for SynthDef loading
2. **Play**: `sc_play_sine` or `sc_play_synth` to make sounds
3. **Analyze**: `sc_start_analyzer` then `sc_get_analysis` to monitor audio
4. **Debug**: `sc_get_logs` to see server messages and errors

## SuperCollider Verification

When writing SynthDefs or SC code, ALWAYS use WebSearch to verify:
- UGen argument names and order (they vary between UGens)
- Envelope specifications (Env.perc, Env.adsr, etc.)
- Filter/oscillator parameter ranges and defaults
- Trigger vs. gate behavior
- Any UGen you haven't used recently

Search with: `site:doc.sccode.org [UGen name]` to confirm syntax before writing code.

Do NOT rely on memory for SC specifics - the API has many subtle variations that are easy to get wrong.

## Tool Reference

### Basic Tools
- `sc_connect` - Connect to SuperCollider (required first step)
- `sc_status` - Check server status, CPU, synth count
- `sc_play_sine(freq, amp, dur)` - Quick test tone
- `sc_free_all` - Stop all sounds

### SynthDef Tools
- `sc_load_synthdef(name, code)` - **Recommended** way to define SynthDefs
- `sc_play_synth(synthdef, params, dur)` - Play any loaded SynthDef

### Analysis Tools
- `sc_start_analyzer` / `sc_stop_analyzer` - Audio monitoring
- `sc_get_analysis` - Get pitch, timbre, amplitude, **loudness** data
- `sc_get_spectrum` - 14-band frequency spectrum
- `sc_get_onsets` - Detect attack/transient events

### Sound Matching Tools
- `sc_capture_reference(name, description)` - Snapshot current sound for comparison
- `sc_compare_to_reference(name)` - Compare current sound to reference
- `sc_list_references()` - Show all captured references
- `sc_delete_reference(name)` - Remove a reference
- `sc_analyze_parameter(synthdef, param, values, metric)` - Measure how parameters affect sound

### Advanced Tools
- `sc_eval(code)` - Execute arbitrary SuperCollider code
- `sc_get_logs` / `sc_clear_logs` - Server log access

## Defining SynthDefs

**Always use `sc_load_synthdef`** instead of `sc_eval` with `.add`:

```python
sc_load_synthdef("ping", '''
    arg freq = 440, amp = 0.1, dur = 0.5;
    var sig = SinOsc.ar(freq) * EnvGen.kr(Env.perc(0.01, dur), doneAction: 2);
    Out.ar(0, sig ! 2 * amp);
''')
```

Then play it:
```python
sc_play_synth("ping", params={"freq": 880, "amp": 0.2})
```

## Playing Sequences with sc_eval

Use `s.sendBundle` with hardcoded timestamps for timed note sequences:

```supercollider
// Schedule notes at specific times (relative to now)
s.sendBundle(0.0, [\s_new, \ping, -1, 0, 0, \freq, 440, \amp, 0.2]);
s.sendBundle(0.2, [\s_new, \ping, -1, 0, 0, \freq, 550, \amp, 0.2]);
s.sendBundle(0.4, [\s_new, \ping, -1, 0, 0, \freq, 660, \amp, 0.2]);
"Sequence scheduled".postln;
```

## Common Pitfalls to Avoid

### 1. Don't use .add for SynthDefs
```supercollider
// WRONG - races with process exit
SynthDef(\foo, { ... }).add;

// RIGHT - use sc_load_synthdef tool instead
```

### 2. var declarations must come first
```supercollider
// WRONG - will timeout
s.sendBundle(0.1, ...);
var x = 1;

// RIGHT
var x = 1;
s.sendBundle(0.1, ...);
```

### 3. Don't use incrementing variables with sendBundle
```supercollider
// WRONG - var assignment issues cause timeout
var t = 0;
s.sendBundle(t, ...); t = t + 0.1;
s.sendBundle(t, ...); t = t + 0.1;

// RIGHT - hardcode the times
s.sendBundle(0.0, ...);
s.sendBundle(0.1, ...);
s.sendBundle(0.2, ...);
```

### 4. Avoid blocking constructs
```supercollider
// WRONG - will timeout
0.5.wait;
fork { ... s.sync; ... };
Condition.new.hang;
```

## Metallic/Inharmonic Sounds

For metallic sounds, use inharmonic partials with Klank:

```python
sc_load_synthdef("metallic", '''
    arg freq = 440, amp = 0.2, decay = 2, spread = 1;
    var freqs = [1, 2.32, 3.17, 4.53, 5.87] * freq * spread;
    var amps = [1, 0.6, 0.4, 0.25, 0.2];
    var decays = [1, 0.8, 0.7, 0.6, 0.5] * decay;
    var exciter = Impulse.ar(0) + (WhiteNoise.ar(0.5) * EnvGen.ar(Env.perc(0.001, 0.01)));
    var sig = Klank.ar(`[freqs, amps, decays], exciter);
    sig = sig * EnvGen.kr(Env.perc(0.001, decay), doneAction: 2) * amp;
    Out.ar(0, sig ! 2);
''')
```

## Sound Matching Workflow

Use reference capture and comparison to recreate target sounds:

### 1. Capture Your Target Sound
```python
# Play the sound you want to match, then:
sc_capture_reference("target", "bright metallic bell")
```

### 2. Create Your Synth and Compare
```python
# Load your synth
sc_load_synthdef("mybell", '''...''')

# Play it and compare
sc_play_synth("mybell", params={"freq": 440})
sc_compare_to_reference("target")
```

The comparison shows:
- **Pitch difference** in semitones (e.g., "+2.5 semitones sharper")
- **Brightness ratio** (e.g., "30% darker")
- **Loudness difference** in sones (perceptual loudness)
- **Character difference** (more tonal vs more noise-like)
- **Overall match score** (0-100%)

### 3. Analyze Parameter Impact
Find which parameter controls brightness:
```python
sc_analyze_parameter("mybell", "cutoff", [500, 1000, 2000, 4000], "centroid")
```

Returns a table showing how `cutoff` affects `centroid` (brightness).

### Analysis Metrics
- `pitch` - Detected frequency in Hz
- `centroid` - Spectral centroid (brightness) in Hz
- `loudness` - Perceptual loudness in sones
- `flatness` - 0 = tonal, 1 = noise-like
- `rms` - RMS amplitude

## Debugging

Check logs when things don't work:
```python
sc_get_logs()  # See recent server messages
sc_get_logs(category="fail")  # See only errors
```

Common errors:
- "SynthDef not found" - SynthDef wasn't loaded, use `sc_load_synthdef`
- Timeout - Check for var placement issues or blocking constructs
