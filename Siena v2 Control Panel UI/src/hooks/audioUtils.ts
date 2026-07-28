// Shared pure helpers for turning raw Web Audio API samples into a WAV file
// the whisper.cpp backend (POST /api/voice/stt/transcribe) accepts. Used by
// both useVoiceRecorder.ts (push-to-talk) and useVoiceConversation.ts
// (hands-free Voice Conversation Mode) — kept here so the two don't drift
// out of sync with two separate copies of the same encoding logic.

// whisper.cpp's model operates natively at 16kHz (WHISPER_SAMPLE_RATE in
// whisper.cpp/src/whisper.cpp) — miniaudio would resample on the backend
// side regardless, but resampling here keeps the uploaded WAV small and
// matches the model's native rate exactly.
export const TARGET_SAMPLE_RATE = 16000;

export function downmixToMono(channels: Float32Array[], length: number): Float32Array {
  const out = new Float32Array(length);
  const count = channels.length;
  for (let i = 0; i < length; i++) {
    let sum = 0;
    for (let c = 0; c < count; c++) sum += channels[c][i];
    out[i] = sum / count;
  }
  return out;
}

export function resampleLinear(samples: Float32Array, fromRate: number, toRate: number): Float32Array {
  if (fromRate === toRate) return samples;
  const ratio = fromRate / toRate;
  const outLength = Math.max(1, Math.round(samples.length / ratio));
  const out = new Float32Array(outLength);
  for (let i = 0; i < outLength; i++) {
    const srcPos = i * ratio;
    const i0 = Math.floor(srcPos);
    const i1 = Math.min(i0 + 1, samples.length - 1);
    const frac = srcPos - i0;
    out[i] = samples[i0] * (1 - frac) + samples[i1] * frac;
  }
  return out;
}

export function encodeWavPcm16(samples: Float32Array, sampleRate: number): Blob {
  const bytesPerSample = 2;
  const dataSize = samples.length * bytesPerSample;
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);

  const writeString = (offset: number, str: string) => {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
  };

  writeString(0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * bytesPerSample, true); // byte rate
  view.setUint16(32, bytesPerSample, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  writeString(36, "data");
  view.setUint32(40, dataSize, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    offset += 2;
  }

  return new Blob([buffer], { type: "audio/wav" });
}

/** RMS (root-mean-square) level of a mono buffer, in [0, 1] sample-amplitude
 * terms (not yet gained/smoothed for display — callers apply their own gain
 * curve, since push-to-talk and Conversation Mode want slightly different
 * treatment: one only for display, the other also for VAD thresholding). */
export function rms(mono: Float32Array): number {
  if (mono.length === 0) return 0;
  let sumSquares = 0;
  for (let i = 0; i < mono.length; i++) sumSquares += mono[i] * mono[i];
  return Math.sqrt(sumSquares / mono.length);
}

/** Creates a new AudioContext, falling back to the vendor-prefixed
 * constructor some older WebKit builds still expose. */
export function createAudioContext(): AudioContext {
  const AudioContextCtor: typeof AudioContext =
    window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
  return new AudioContextCtor();
}
