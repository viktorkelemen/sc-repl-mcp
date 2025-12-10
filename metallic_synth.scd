// Metallic Synth - resonant inharmonic tones
// Run this in SuperCollider IDE to load the SynthDef

(
SynthDef(\metallic, {
	arg freq = 440, amp = 0.3, decay = 2, brightness = 0.5, gate = 1, out = 0;
	var exciter, resonances, sig, env;
	var numPartials = 12;
	var ratios, amps, decays;

	// Inharmonic frequency ratios (metal has non-integer partials)
	ratios = [1, 2.32, 3.17, 4.12, 4.98, 6.26, 7.41, 8.22, 9.13, 10.78, 11.52, 12.89];

	// Amplitude rolloff with brightness control
	amps = ratios.collect { |r, i|
		(1 / (i + 1).pow(1.5 - brightness)).clip(0, 1)
	};

	// Individual decay times (higher partials decay faster)
	decays = ratios.collect { |r, i|
		decay * (1 / (i + 1).sqrt).clip(0.1, 1)
	};

	// Impulse exciter (like striking the metal)
	exciter = Impulse.ar(0) + (Dust.ar(0.5) * 0.3);
	exciter = exciter + (EnvGen.kr(Env.perc(0.001, 0.01), gate) * WhiteNoise.ar(0.5));

	// Klank resonant filter bank
	resonances = Klank.ar(
		`[ratios * freq, amps, decays],
		exciter
	);

	// Add some ring modulation for extra shimmer
	sig = resonances + (resonances * SinOsc.ar(freq * 2.17) * 0.1);

	// Gentle highpass to remove mud
	sig = HPF.ar(sig, 80);

	// Amplitude envelope
	env = EnvGen.kr(Env.asr(0.001, 1, 0.1), gate, doneAction: 2);

	sig = sig * env * amp;

	Out.ar(out, sig ! 2);
}).add;

// Variation: struck bell/gong
SynthDef(\bell, {
	arg freq = 440, amp = 0.3, decay = 4, strike = 1, out = 0;
	var sig, env, exciter;
	var partials, amps, decays;

	// Bell partials (based on church bell measurements)
	partials = [0.5, 1, 1.19, 1.56, 2, 2.51, 2.66, 3.01, 4.1, 5.43];

	amps = [0.4, 1, 0.6, 0.5, 0.3, 0.25, 0.2, 0.15, 0.1, 0.08];
	decays = [decay * 1.5, decay, decay * 0.9, decay * 0.8, decay * 0.7,
		      decay * 0.5, decay * 0.4, decay * 0.3, decay * 0.2, decay * 0.15];

	// Strike exciter
	exciter = EnvGen.ar(Env.perc(0.001, 0.005)) * strike;
	exciter = exciter * (WhiteNoise.ar(0.5) + Impulse.ar(0));

	// Resonant body
	sig = Klank.ar(
		`[partials * freq, amps, decays],
		exciter
	);

	// Soft clip for warmth
	sig = (sig * 2).tanh * 0.5;

	env = EnvGen.kr(Env.perc(0.001, decay * 1.2), doneAction: 2);
	sig = sig * env * amp;

	Out.ar(out, Pan2.ar(sig, 0));
}).add;

// Variation: metallic percussion (like a hi-hat or cymbal)
SynthDef(\metalPerc, {
	arg freq = 6000, amp = 0.3, decay = 0.3, tone = 0.5, out = 0;
	var sig, env;
	var numOscs = 6;
	var freqs, ring;

	// Dense cluster of inharmonic frequencies
	freqs = Array.fill(numOscs, { |i|
		freq * (1 + (i * 0.23)) * LFNoise1.kr(0.1).range(0.98, 1.02)
	});

	// Square waves for harsh metallic timbre
	sig = Mix(Pulse.ar(freqs, 0.5, 1/numOscs));

	// Bandpass filter cluster for resonance
	sig = BPF.ar(sig, freq * tone.linexp(0, 1, 0.5, 2), 0.3) * 3;

	// Add filtered noise
	sig = sig + (HPF.ar(WhiteNoise.ar(0.4), 8000) * EnvGen.kr(Env.perc(0.001, decay * 0.5)));

	env = EnvGen.kr(Env.perc(0.001, decay), doneAction: 2);
	sig = sig * env * amp;

	Out.ar(out, sig ! 2);
}).add;
)
