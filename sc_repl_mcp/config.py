"""Configuration constants for SC-REPL MCP Server."""

# Network configuration
SCSYNTH_HOST = "127.0.0.1"
SCSYNTH_PORT = 57110
REPLY_PORT = 57130  # Fixed port for OSC replies (orphaned processes are killed on connect)

# Execution limits
MAX_EVAL_TIMEOUT = 300.0  # Maximum allowed timeout (5 minutes)
VALIDATE_TIMEOUT = 10.0  # Timeout for syntax validation (seconds)

# Spectrum analyzer band frequencies (Hz) - logarithmic spacing from ~60Hz to ~16kHz
# These must match between Python (client.py) and SuperCollider (config.py, mcp_synthdefs.scd)
SPECTRUM_BAND_FREQUENCIES = [60, 100, 156, 244, 380, 594, 928, 1449, 2262, 3531, 5512, 8603, 13428, 16000]

# SuperCollider code to load SynthDefs and set up OSC forwarding
# This runs in a persistent sclang process started by the MCP server
SCLANG_INIT_CODE = r'''
// Connect to the existing scsynth server (running in SuperCollider.app)
// This ensures SynthDefs are added to the correct server
Server.default = Server.remote(\scsynth, NetAddr("127.0.0.1", 57110));
s = Server.default;
// Server.remote handles connection automatically

fork {
    0.5.wait;  // Give server connection time to establish

    // MCP Audio Analyzer SynthDefs

    // Full audio analyzer - pitch, timbre, amplitude, loudness, onset, spectrum
    SynthDef(\mcp_analyzer, {
        arg bus = 0, replyRate = 10, replyID = 1001;
        var in, mono, fft;
        var freq, hasFreq, centroid, flatness, rolloff;
        var peakL, peakR, rmsL, rmsR;
        var loudness;  // perceptual loudness in sones
        var onsetTrig;
        var spectrumBands;

        in = In.ar(bus, 2);
        mono = in.sum * 0.5;
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

        // Perceptual loudness (Moore-Glasberg model / ISO 532B via Loudness UGen)
        // Returns loudness in sones - more meaningful than RMS for human perception
        // Note: 2 sones = perceived twice as loud as 1 sone
        loudness = Loudness.kr(fft);

        // Onset detection
        onsetTrig = Onsets.kr(fft, threshold: 0.3, odftype: \rcomplex);

        // 14-band spectrum analyzer (logarithmic bands from ~60Hz to ~16kHz)
        spectrumBands = FFTSubbandPower.kr(fft, [60, 100, 156, 244, 380, 594, 928, 1449, 2262, 3531, 5512, 8603, 13428, 16000], square: 0);

        // Send main analysis data at regular intervals
        SendReply.kr(
            Impulse.kr(replyRate),
            '/mcp/analysis',
            [freq, hasFreq, centroid, flatness, rolloff, peakL, peakR, rmsL, rmsR, loudness],
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

    "MCP SynthDefs loaded".postln;
};  // end fork

// OSC forwarding: relay SendReply messages from scsynth to MCP Python server
// SendReply sends to sclang (port 57120), we forward to MCP (port 57130)
~mcpAddr = NetAddr("127.0.0.1", 57130);

OSCFunc({ |msg|
    ~mcpAddr.sendMsg(*msg);
}, '/mcp/analysis');

OSCFunc({ |msg|
    ~mcpAddr.sendMsg(*msg);
}, '/mcp/onset');

OSCFunc({ |msg|
    ~mcpAddr.sendMsg(*msg);
}, '/mcp/spectrum');

OSCFunc({ |msg|
    ~mcpAddr.sendMsg(*msg);
}, '/mcp/meter');

// Code execution responder - allows Python to execute SC code via OSC
// This avoids spawning fresh sclang processes (which require class library recompilation)
OSCFunc({ |msg|
    var requestId = msg[1].asInteger;
    var filePath = msg[2].asString;
    var code, result, success, output;

    try {
        // Read code from file
        code = File.readAllString(filePath);

        // Execute the code in the interpreter
        // Note: This returns the value of the last expression
        result = thisProcess.interpreter.interpret(code);

        success = 1;
        output = if(result.notNil) { result.asString } { "(nil)" };

        // Truncate long outputs (OSC has ~8KB practical limit)
        if(output.size > 7000) {
            output = output.keep(7000) ++ "... (truncated)";
        };
    } { |error|
        success = 0;
        output = error.errorString;
        if(output.size > 7000) {
            output = output.keep(7000) ++ "... (truncated)";
        };
    };

    // Send result back to Python
    ~mcpAddr.sendMsg('/mcp/eval/result', requestId, success, output);
}, '/mcp/eval');

"MCP sclang ready with OSC forwarding and code execution on port 57130".postln;

// Keep sclang running indefinitely
{ inf.wait }.defer;
'''

# Prefixes to filter from sclang stderr (startup noise)
SCLANG_STDERR_SKIP_PREFIXES = (
    'compiling class library',
    'NumPrimitives',
    'Welcome to SuperCollider',
    "type 'help'",
    'Found',
    'Compiling',
    'Read',
)
