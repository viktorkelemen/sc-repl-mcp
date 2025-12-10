// MCP Audio Analyzer SynthDefs
// Run this in SuperCollider IDE to enable audio analysis
// NOTE: This is now optional - sc_connect loads these automatically via sclang

(
// Full audio analyzer - pitch, timbre, amplitude, onset, spectrum
SynthDef(\mcp_analyzer, {
    arg bus = 0, replyRate = 10, replyID = 1001;
    var in, mono, fft;
    var freq, hasFreq, centroid, flatness, rolloff;
    var peakL, peakR, rmsL, rmsR;
    var onsetTrig;
    var spectrumBands;

    // Read stereo output
    in = In.ar(bus, 2);
    mono = in.sum * 0.5;

    // FFT for spectral analysis
    fft = FFT(LocalBuf(2048), mono);

    // Pitch detection
    # freq, hasFreq = Pitch.kr(mono, ampThreshold: 0.01, median: 7);

    // Timbral features
    centroid = SpecCentroid.kr(fft);
    flatness = SpecFlatness.kr(fft);
    rolloff = SpecPcile.kr(fft, 0.9);

    // Amplitude (stereo)
    peakL = PeakFollower.kr(in[0], 0.99);
    peakR = PeakFollower.kr(in[1], 0.99);
    rmsL = RunningSum.rms(in[0], 1024);
    rmsR = RunningSum.rms(in[1], 1024);

    // Onset detection
    onsetTrig = Onsets.kr(fft, threshold: 0.3, odftype: \rcomplex);

    // 14-band spectrum analyzer (logarithmic bands from ~60Hz to ~16kHz)
    spectrumBands = FFTSubbandPower.kr(fft, [60, 100, 156, 244, 380, 594, 928, 1449, 2262, 3531, 5512, 8603, 13428, 16000], square: 0);

    // Send main analysis data at regular intervals
    SendReply.kr(
        Impulse.kr(replyRate),
        '/mcp/analysis',
        [freq, hasFreq, centroid, flatness, rolloff, peakL, peakR, rmsL, rmsR],
        replyID
    );

    // Send onset trigger immediately when detected
    SendReply.kr(
        onsetTrig,
        '/mcp/onset',
        [freq, peakL + peakR * 0.5],  // pitch and amplitude at onset
        replyID
    );

    // Send spectrum data at regular intervals
    SendReply.kr(
        Impulse.kr(replyRate),
        '/mcp/spectrum',
        spectrumBands,
        replyID
    );
}).add;

// Simple peak/RMS meter only (lighter weight)
SynthDef(\mcp_meter, {
    arg bus = 0, replyRate = 20, replyID = 1002;
    var in, peakL, peakR, rmsL, rmsR;

    in = In.ar(bus, 2);
    peakL = PeakFollower.kr(in[0], 0.99);
    peakR = PeakFollower.kr(in[1], 0.99);
    rmsL = RunningSum.rms(in[0], 512);
    rmsR = RunningSum.rms(in[1], 512);

    SendReply.kr(
        Impulse.kr(replyRate),
        '/mcp/meter',
        [peakL, peakR, rmsL, rmsR],
        replyID
    );
}).add;

// OSC Forwarding to MCP server
// SendReply only sends to sclang (port 57120), but the MCP server
// listens on port 57130. These OSCFuncs bridge the gap.
~mcpAddr = NetAddr("127.0.0.1", 57130);

~mcpAnalysisForwarder = OSCFunc({ |msg|
    ~mcpAddr.sendMsg(*msg);
}, '/mcp/analysis');

~mcpOnsetForwarder = OSCFunc({ |msg|
    ~mcpAddr.sendMsg(*msg);
    // Log onsets to post window
    "ONSET detected: % Hz".format(msg[3].round(0.1)).postln;
}, '/mcp/onset');

~mcpSpectrumForwarder = OSCFunc({ |msg|
    ~mcpAddr.sendMsg(*msg);
}, '/mcp/spectrum');

~mcpMeterForwarder = OSCFunc({ |msg|
    ~mcpAddr.sendMsg(*msg);
}, '/mcp/meter');

"MCP SynthDefs loaded with OSC forwarding to port 57130".postln;
)
