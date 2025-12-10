# Audio Analysis Exploration

## Current Analysis (What We Have)

| Feature | UGen | What It Tells You |
|---------|------|-------------------|
| Pitch | `Pitch.kr` | Fundamental frequency |
| Spectral centroid | `SpecCentroid.kr` | "Center of mass" of spectrum (brightness) |
| Spectral flatness | `SpecFlatness.kr` | Tonal vs noisy (0=tone, 1=noise) |
| Spectral rolloff | `SpecPcile.kr` | Where 90% of energy is below |
| Peak amplitude | `PeakFollower.kr` | Maximum level |
| RMS amplitude | `RunningSum.rms` | Average power |

---

## Categories of Analysis We Could Add

### 1. Temporal/Event Detection

**Onset Detection** (`Onsets.kr`)
- Detects when sounds *start* - attacks, transients
- More useful than continuous monitoring for many tasks
- "A note began" vs "here's the current spectrum"
- Low CPU cost, high utility

**Beat Tracking** (`BeatTrack.kr`, `BeatTrack2.kr`)
- Estimates tempo (BPM) and beat positions
- Useful if generating rhythmic material
- Can be unreliable with non-standard rhythms

### 2. Perceptual Loudness

**Loudness** (`Loudness.kr`)
- ITU-R BS.1770 perceptual loudness in sones
- More meaningful than RMS - matches human perception
- A sound at 1000 Hz and 100 Hz with same RMS *feel* different loud

**Crest Factor** (Peak/RMS ratio)
- Indicates dynamic range, punchiness
- High crest = transient-heavy (drums), low = compressed (synth pad)

### 3. Spectral Shape (Beyond Centroid)

**Spectral Flux** (`FFTFlux`)
- Rate of spectral change over time
- High flux = evolving/moving sound
- Low flux = static/sustained sound
- Good for detecting "is something happening?"

**Spectral Spread** (`FFTSpread`)
- How spread out energy is around the centroid
- Narrow = pure tone, wide = complex/noisy

**Spectral Slope/Tilt** (`FFTSlope`)
- Overall shape: rising or falling with frequency
- Negative slope = typical (more bass), positive = unusual

**Spectral Crest** (`FFTCrest`)
- Peak-to-mean ratio in spectrum
- High = tonal (clear peaks), low = noisy (flat spectrum)

### 4. Timbre Fingerprinting

**MFCC** (`MFCC.kr`)
- Mel-Frequency Cepstral Coefficients (typically 13 values)
- Standard for timbre/voice recognition
- Problem: Abstract numbers, hard to interpret directly
- Useful for: "Does sound A match sound B?"

**Chromagram**
- Energy in each pitch class (C, C#, D, ... B)
- 12 values representing the harmonic content
- Useful for chord/key detection
- SC has `Chromagram` in some extensions

### 5. Stereo/Spatial

**Stereo Width**
- Correlation between L and R channels
- 1 = mono, 0 = completely different, -1 = out of phase

**Balance**
- Simple L vs R energy comparison
- Where is the sound positioned?

---

## Analysis Paradigms

### Continuous Stream (Current Approach)
```
Every 100ms: {pitch: 440, centroid: 2340, rms: 0.03, ...}
```
- Good for: Monitoring, real-time feedback
- Bad for: Understanding musical events, overwhelming data

### Event-Based (Alternative)
```
0.0s: Note onset detected (pitch ~440Hz, loud attack)
0.5s: Amplitude decaying
2.1s: Note ended
2.1s: New onset detected (pitch ~880Hz)
```
- Good for: Understanding what's happening musically
- Bad for: Continuous timbral monitoring

### Comparative/Relative
```
"Current sound is brighter than 2 seconds ago"
"Pitch is 12 cents sharp of target"
"Louder than reference by 3dB"
```
- Good for: Goal-oriented tasks ("make it brighter")
- Requires: Reference point or target

---

## What Would Actually Help Claude?

**High Value:**
1. **Onset detection** - Know when sounds start. Critical for timing, sequencing.
2. **Perceptual loudness** - "How loud does this feel?" not just raw amplitude.
3. **Spectral flux** - "Is the sound changing or static?"

**Medium Value:**
4. **Beat/tempo tracking** - If working with rhythmic material
5. **Simplified brightness indicator** - Centroid relative to fundamental (overtone richness)

**Questionable Value:**
- Raw MFCC coefficients (uninterpretable without comparison)
- Complex psychoacoustic measures (roughness, sharpness)
- Full chromagram (overkill unless doing harmony analysis)

---

## The Interpretation Problem

Raw numbers are hard to use:
```
centroid: 2847 Hz
```

What does that *mean*? Is it bright? Dark? Depends on:
- The fundamental frequency (2847 Hz is bright for a 100Hz bass, normal for a 1000Hz lead)
- The instrument/context (bright for a pad, dark for a cymbal)

**Possible solutions:**

1. **Relative to fundamental**: `centroid_ratio: 6.5` (centroid / pitch)
   - Removes pitch dependency
   - Higher = more overtones = brighter timbre

2. **Historical comparison**: "Brighter than 1 second ago"
   - Track changes, not absolutes

3. **Categorical with confidence**: "Likely bright (0.7 confidence)"
   - Fuzzy categories instead of precise numbers
   - But we removed this because it was unreliable...

---

## Recommendations

**Add these two, they're high-value and simple:**

1. **Onset detection** - Binary "attack detected" events
2. **Perceptual loudness** - Replace or supplement RMS with `Loudness.kr`

**Consider adding:**

3. **Spectral flux** - Single number indicating rate of change
4. **Centroid-to-pitch ratio** - More meaningful brightness measure (if pitch is detected)

**Avoid for now:**

- MFCC (too abstract)
- Beat tracking (specialized use case)
- Full chromagram (overkill)

---

## Alternative Architecture: Dual Analyzers

Instead of one analyzer doing everything:

**Continuous Analyzer** (current, 10Hz)
- Amplitude, pitch, basic spectrum
- "What is the current state?"

**Event Analyzer** (triggered)
- Onset detection → triggers detailed snapshot
- "What just happened?"
- Could capture: attack time, initial pitch, brightness at onset

This separates "monitoring" from "event detection" cleanly.
