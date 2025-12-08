// MCP Audio Analyzer SynthDefs
// Run this in SuperCollider IDE to enable audio analysis

(
// Full audio analyzer - pitch, timbre, amplitude
SynthDef(\mcp_analyzer, {
    arg bus = 0, replyRate = 10, replyID = 1001;
    var in, mono, fft;
    var freq, hasFreq, centroid, flatness, rolloff;
    var peakL, peakR, rmsL, rmsR;

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

    // Send analysis data via OSC
    SendReply.kr(
        Impulse.kr(replyRate),
        '/mcp/analysis',
        [freq, hasFreq, centroid, flatness, rolloff, peakL, peakR, rmsL, rmsR],
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

~mcpMeterForwarder = OSCFunc({ |msg|
	~mcpAddr.sendMsg(*msg);
}, '/mcp/meter');
)
