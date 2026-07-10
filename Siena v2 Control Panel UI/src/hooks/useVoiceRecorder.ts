import { useCallback, useEffect, useRef, useState } from "react";
import { sienaClient } from "../api/sienaClient";
import type { TranscribeSpeechResponse } from "../api/types";
import { TARGET_SAMPLE_RATE, createAudioContext, downmixToMono, encodeWavPcm16, resampleLinear, rms } from "./audioUtils";

// Real mic recording (Phase 2, HANDOFF_v2.md) on top of the Phase 1
// whisper.cpp backend (POST /api/voice/stt/transcribe). Deliberately does
// NOT use MediaRecorder (webm/opus) — the backend only accepts .wav and has
// no ffmpeg conversion step, so this captures raw PCM via the Web Audio API
// and encodes a WAV file itself.
//
// ScriptProcessorNode is deprecated but used here anyway, for the same
// reason useStreamingSpeech.ts avoids AudioWorklet for playback: a full
// AudioWorklet module needs a separately bundled worklet file and extra Vite
// wiring, which isn't worth it for what a modern Chromium/Electron runtime
// still fully supports. A future pass can revisit this if it ever becomes a
// real problem.
//
// This is push-to-talk only — a single manual record/stop cycle. The
// hands-free, auto-listening/auto-send sibling is useVoiceConversation.ts
// (Phase 3, experimental); the two share WAV encoding helpers (audioUtils.ts)
// but not state machines, since push-to-talk's is trivially simpler.

export type VoiceRecorderStatus = "idle" | "requesting_permission" | "recording" | "transcribing" | "error";

// Mirrors config.WHISPER_CPP_MAX_AUDIO_SECONDS — auto-stops and transcribes
// instead of letting the backend reject an over-long upload.
const MAX_RECORDING_SECONDS = 60;
const MIN_RECORDING_SECONDS = 0.2;

interface UseVoiceRecorderOptions {
  language?: string;
  onTranscribed: (text: string, result: TranscribeSpeechResponse) => void;
  // Called right as a new recording is about to start capturing — the
  // caller uses this to stop any in-flight TTS Speak/Stream playback first
  // (recording while Siena's own voice plays through the same speakers would
  // otherwise feed synthesized speech straight back into the transcription).
  onBeforeStart?: () => void;
}

export interface UseVoiceRecorderResult {
  status: VoiceRecorderStatus;
  error: string | null;
  elapsedSec: number;
  // Real mic input level while status === "recording", in [0, 1] — RMS of
  // each audio buffer, gained up and exponentially smoothed so VoiceOrb gets
  // a usable, non-jittery signal instead of raw sample noise. 0 at all other
  // times (there's no mic signal to show while requesting permission,
  // transcribing, idle, or errored).
  amplitude: number;
  start: () => Promise<void>;
  stopAndTranscribe: () => Promise<void>;
  cancel: () => void;
}

// Typical mic RMS for conversational speech (Float32 samples in [-1, 1]) is
// small — roughly 0.02-0.2 — so it's gained up before clamping to [0, 1] to
// give VoiceOrb a visible range instead of a barely-moving needle.
const AMPLITUDE_GAIN = 6;
// Exponential moving average factor applied per onaudioprocess callback
// (~10-12 times/sec for a 4096-sample buffer at typical mic sample rates) —
// smooths frame-to-frame RMS jitter without feeling laggy.
const AMPLITUDE_SMOOTHING = 0.35;

export function useVoiceRecorder(options: UseVoiceRecorderOptions): UseVoiceRecorderResult {
  const { language, onTranscribed, onBeforeStart } = options;
  const [status, setStatus] = useState<VoiceRecorderStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const [elapsedSec, setElapsedSec] = useState(0);
  const [amplitude, setAmplitude] = useState(0);

  const statusRef = useRef<VoiceRecorderStatus>("idle");
  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const gainRef = useRef<GainNode | null>(null);
  const chunksRef = useRef<Float32Array[]>([]);
  const totalSamplesRef = useRef(0);
  const elapsedTimerRef = useRef<ReturnType<typeof setInterval>>();
  const abortRef = useRef<AbortController | null>(null);
  const autoStopFiredRef = useRef(false);
  const smoothedAmplitudeRef = useRef(0);

  const setStatusBoth = useCallback((s: VoiceRecorderStatus) => {
    statusRef.current = s;
    setStatus(s);
  }, []);

  const teardownAudioGraph = useCallback(() => {
    clearInterval(elapsedTimerRef.current);
    elapsedTimerRef.current = undefined;
    smoothedAmplitudeRef.current = 0;
    setAmplitude(0);

    if (processorRef.current) {
      processorRef.current.disconnect();
      processorRef.current.onaudioprocess = null;
      processorRef.current = null;
    }
    if (gainRef.current) {
      gainRef.current.disconnect();
      gainRef.current = null;
    }
    if (sourceRef.current) {
      sourceRef.current.disconnect();
      sourceRef.current = null;
    }
    if (audioCtxRef.current) {
      audioCtxRef.current.close().catch(() => undefined);
      audioCtxRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
  }, []);

  useEffect(() => teardownAudioGraph, [teardownAudioGraph]);

  const stopAndTranscribe = useCallback(async () => {
    if (statusRef.current !== "recording") return;

    const nativeRate = audioCtxRef.current?.sampleRate ?? TARGET_SAMPLE_RATE;
    const totalSamples = totalSamplesRef.current;
    const chunks = chunksRef.current;

    teardownAudioGraph();
    sienaClient.logClientEvent("stt_ui_recording_stopped", {
      duration_sec: Math.round((totalSamples / nativeRate) * 100) / 100,
    });

    if (totalSamples / nativeRate < MIN_RECORDING_SECONDS) {
      chunksRef.current = [];
      totalSamplesRef.current = 0;
      setError("Recording too short — hold the mic button a bit longer.");
      setStatusBoth("error");
      return;
    }

    const merged = new Float32Array(totalSamples);
    let offset = 0;
    for (const chunk of chunks) {
      merged.set(chunk, offset);
      offset += chunk.length;
    }
    chunksRef.current = [];
    totalSamplesRef.current = 0;

    const resampled = resampleLinear(merged, nativeRate, TARGET_SAMPLE_RATE);
    const wavBlob = encodeWavPcm16(resampled, TARGET_SAMPLE_RATE);

    setStatusBoth("transcribing");
    sienaClient.logClientEvent("stt_ui_transcribe_started");

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const result = await sienaClient.transcribeSpeech(wavBlob, language, controller.signal);
      if (controller.signal.aborted) return;
      abortRef.current = null;
      sienaClient.logClientEvent("stt_ui_transcribe_completed", {
        backend: result.backend,
        elapsed_ms: result.elapsed_ms,
      });
      setStatusBoth("idle");
      onTranscribed(result.text, result);
    } catch (err) {
      if (controller.signal.aborted || (err instanceof DOMException && err.name === "AbortError")) {
        return; // cancel() landed mid-request — not a real failure
      }
      const message = err instanceof Error ? err.message : "Transcription failed";
      sienaClient.logClientEvent("stt_ui_transcribe_failed", { error: message });
      setError(message);
      setStatusBoth("error");
    }
  }, [language, onTranscribed, setStatusBoth, teardownAudioGraph]);

  const stopAndTranscribeRef = useRef(stopAndTranscribe);
  useEffect(() => {
    stopAndTranscribeRef.current = stopAndTranscribe;
  }, [stopAndTranscribe]);

  const start = useCallback(async () => {
    if (statusRef.current === "recording" || statusRef.current === "requesting_permission" || statusRef.current === "transcribing") {
      return; // no overlapping recordings
    }

    setError(null);
    setElapsedSec(0);
    chunksRef.current = [];
    totalSamplesRef.current = 0;
    autoStopFiredRef.current = false;
    smoothedAmplitudeRef.current = 0;
    setAmplitude(0);

    onBeforeStart?.();

    setStatusBoth("requesting_permission");
    sienaClient.logClientEvent("stt_ui_recording_requested");

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Microphone permission denied";
      sienaClient.logClientEvent("stt_ui_permission_denied", { error: message });
      setError(message);
      setStatusBoth("error");
      return;
    }

    if (statusRef.current !== "requesting_permission") {
      // cancel() landed while the permission prompt was open.
      stream.getTracks().forEach((t) => t.stop());
      return;
    }

    sienaClient.logClientEvent("stt_ui_permission_granted");
    streamRef.current = stream;

    const ctx = createAudioContext();
    audioCtxRef.current = ctx;

    const source = ctx.createMediaStreamSource(stream);
    sourceRef.current = source;

    // 2 input channels regardless of the actual device — onaudioprocess
    // below always averages whatever channel count it's actually given, so
    // this is just a safe upper bound, not an assumption about the mic.
    const processor = ctx.createScriptProcessor(4096, 2, 1);
    processorRef.current = processor;

    // A muted GainNode keeps onaudioprocess firing (Chromium only runs a
    // ScriptProcessorNode while it's connected through to a destination)
    // without echoing the mic back out through the speakers.
    const gain = ctx.createGain();
    gain.gain.value = 0;
    gainRef.current = gain;

    processor.onaudioprocess = (event) => {
      const input = event.inputBuffer;
      const channels: Float32Array[] = [];
      for (let c = 0; c < input.numberOfChannels; c++) channels.push(input.getChannelData(c));
      const mono = channels.length > 1 ? downmixToMono(channels, input.length) : channels[0].slice();
      chunksRef.current.push(mono);
      totalSamplesRef.current += mono.length;

      const target = Math.max(0, Math.min(1, rms(mono) * AMPLITUDE_GAIN));
      const smoothed = smoothedAmplitudeRef.current + (target - smoothedAmplitudeRef.current) * AMPLITUDE_SMOOTHING;
      smoothedAmplitudeRef.current = smoothed;
      setAmplitude(smoothed);

      if (!autoStopFiredRef.current && totalSamplesRef.current / ctx.sampleRate >= MAX_RECORDING_SECONDS) {
        autoStopFiredRef.current = true;
        void stopAndTranscribeRef.current();
      }
    };

    source.connect(processor);
    processor.connect(gain);
    gain.connect(ctx.destination);

    setStatusBoth("recording");
    sienaClient.logClientEvent("stt_ui_recording_started");

    elapsedTimerRef.current = setInterval(() => {
      setElapsedSec(Math.floor(totalSamplesRef.current / ctx.sampleRate));
    }, 250);
  }, [onBeforeStart, setStatusBoth]);

  const cancel = useCallback(() => {
    const wasActive =
      statusRef.current === "recording" || statusRef.current === "requesting_permission" || statusRef.current === "transcribing";
    abortRef.current?.abort();
    abortRef.current = null;
    teardownAudioGraph();
    chunksRef.current = [];
    totalSamplesRef.current = 0;
    setError(null);
    setElapsedSec(0);
    setStatusBoth("idle");
    if (wasActive) sienaClient.logClientEvent("stt_ui_cancelled");
  }, [setStatusBoth, teardownAudioGraph]);

  return { status, error, elapsedSec, amplitude, start, stopAndTranscribe, cancel };
}
