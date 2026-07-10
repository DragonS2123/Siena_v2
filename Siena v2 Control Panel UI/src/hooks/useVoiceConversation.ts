import { useCallback, useEffect, useRef, useState } from "react";
import { sienaClient } from "../api/sienaClient";
import type { SendResult } from "./useChat";
import type { UseSpeechResult } from "./useSpeech";
import type { UseStreamingSpeechResult } from "./useStreamingSpeech";
import { TARGET_SAMPLE_RATE, createAudioContext, downmixToMono, encodeWavPcm16, resampleLinear, rms } from "./audioUtils";

// Voice Conversation Mode (experimental, dev-only) — hands-free voice loop:
// listening -> speech detected -> (two-stage) silence -> transcribe ->
// auto-send to /api/chat -> speak the reply -> back to listening. This is a
// SEPARATE, additive mode next to push-to-talk (useVoiceRecorder.ts):
// clicking the mic button still does the exact same manual record/stop/
// insert-into-composer flow as before. This hook is only ever active when
// the user explicitly toggles Conversation Mode on.
//
// First version is deliberately half-duplex: the mic's audio-processing
// callback below is gated to only run VAD logic while state is
// "listening"/"speech_detected"/"silence_wait"/"finalizing_wait" — while
// transcribing, waiting for the assistant, or speaking the reply back,
// incoming mic samples are ignored outright, so Siena's own TTS voice can
// never be picked up and misread as the next utterance. This is also the
// answer for speaker (non-headphone) users: no full-duplex/barge-in
// (interrupting Siena mid-reply by talking) is implemented yet — that would
// need echo cancellation to tell "the user talking" apart from "the
// speakers playing Siena's own voice into the same mic", which is a
// separate, harder problem left for a future phase.
//
// VAD is a simple amplitude/RMS threshold plus a one-time ambient noise
// floor calibration at session start, not a neural model — see the
// constants below. This mirrors the project's own "verify empirically,
// don't over-engineer" stance elsewhere (whisper.cpp Vulkan probing, etc.):
// good enough to demo a real hands-free loop, tunable later if it turns out
// too trigger-happy or too sluggish in practice.
//
// Bug fixed in this pass: the first version cut utterances on a single
// ~1s pause, causing auto-send mid-sentence. Fixed with a two-stage
// silence/finalize design (see SILENCE_END_MS/FINALIZE_GRACE_MS below) that
// gives the user roughly SILENCE_END_MS + FINALIZE_GRACE_MS of real silence
// (~2.9s with the defaults below) before anything is sent, and lets them
// resume mid-utterance during that window without losing what they already
// said.

export type VoiceConversationState =
  | "idle"
  | "listening"
  | "speech_detected"
  | "silence_wait"
  | "finalizing_wait"
  | "transcribing"
  | "thinking"
  | "speaking"
  | "error";

// Must stay continuously above threshold this long before we treat it as a
// real utterance starting (filters out short blips/clicks/coughs).
const VOICE_START_MS = 250;
// Stage 1: once speech drops below threshold, this long a pause moves
// "speech_detected" -> "silence_wait" ("Waiting for you…") and, once
// SILENCE_END_MS of continuous quiet has passed, into stage 2 below. This is
// deliberately generous — a natural mid-sentence breath or a pause to think
// of the next word must not be mistaken for the end of the utterance (the
// exact bug this pass fixes).
const SILENCE_END_MS = 2000;
// Stage 2: after SILENCE_END_MS is reached, one more FINALIZE_GRACE_MS of
// continued silence ("finalizing_wait", "Finishing phrase…") is required
// before the utterance is actually finalized and sent. If the user starts
// talking again anywhere in this window, the loop resumes the SAME
// utterance instead of losing it. Total silence before auto-send with the
// defaults below: SILENCE_END_MS + FINALIZE_GRACE_MS ≈ 2.9s.
const FINALIZE_GRACE_MS = 900;
// An utterance shorter than this (even after confirmed start+silence) is
// discarded as noise rather than sent to whisper.cpp at all.
const MIN_UTTERANCE_MS = 700;
// Hard ceiling so a stuck-open mic (or someone who just keeps talking) can't
// grow the buffer forever — forces a cut and transcribes whatever's
// buffered so far.
const MAX_UTTERANCE_MS = 45000;
// How much audio captured *before* VOICE_START_MS confirms real speech is
// kept as a rolling pre-roll buffer while "listening", so the confirmed
// utterance doesn't clip the first ~250ms of what the user actually said.
const PRE_ROLL_MS = 400;

// Dynamic noise floor — replaces a single fixed activity threshold. The
// first CALIBRATION_MS of "listening" right after start() (assuming the
// user hasn't already started talking) sample ambient room noise; the VAD
// threshold is then noiseFloor * NOISE_MULTIPLIER, clamped to
// [MIN_THRESHOLD, MAX_THRESHOLD]. Calibration runs once per Conversation
// Mode session (at start()), not before every utterance — recalibrating
// after every single reply would force an awkward mandatory quiet pause
// each time. Before calibration completes (or if speech starts before it
// can), MIN_THRESHOLD is used as a conservative fallback so real speech is
// never missed just because calibration hadn't finished yet.
const CALIBRATION_MS = 750;
const MIN_THRESHOLD = 0.018;
const NOISE_MULTIPLIER = 3.0;
const MAX_THRESHOLD = 0.06;

// Same gain/smoothing treatment as useVoiceRecorder.ts's push-to-talk
// amplitude — VAD detection itself uses the raw (ungained) RMS against the
// dynamic threshold; this is only for the VoiceOrb display value.
const AMPLITUDE_GAIN = 6;
const AMPLITUDE_SMOOTHING = 0.35;

interface UseVoiceConversationOptions {
  language?: string;
  speech: UseSpeechResult;
  streaming: UseStreamingSpeechResult;
  // The same function the manual Send button/Enter key calls
  // (ChatView::handleSend) — reused as-is so a voice-driven turn creates a
  // completely normal user+assistant message pair through the existing
  // /api/chat flow and conversation history. Never a separate/fake message
  // path.
  sendMessage: (text: string) => Promise<SendResult>;
}

// Temporary/dev diagnostics (HANDOFF_v2.md) — shown subtly in the Voice
// Panel so the VAD thresholds above can actually be tuned against real
// speech instead of guessed blind.
export interface VoiceConversationDiagnostics {
  amplitude: number;
  threshold: number;
  noiseFloor: number;
  /** ms elapsed since the current utterance started (speech + any pauses so far). */
  speechMs: number;
  /** ms of continuous silence right now; 0 while actively speech_detected. */
  silenceMs: number;
  /** ms remaining until auto-finalize; only non-null during "finalizing_wait". */
  autoSendInMs: number | null;
}

export interface UseVoiceConversationResult {
  state: VoiceConversationState;
  active: boolean;
  amplitude: number;
  lastTranscript: string | null;
  error: string | null;
  diagnostics: VoiceConversationDiagnostics;
  start: () => Promise<void>;
  stop: () => void;
  cancel: () => void;
  /** Manual override (optional per HANDOFF_v2.md) — finalizes the current
   * utterance immediately instead of waiting out the silence/finalize
   * timers. No-op outside speech_detected/silence_wait/finalizing_wait
   * (nothing to finalize while merely "listening"). Does not replace Stop. */
  finishNow: () => void;
}

export function useVoiceConversation(options: UseVoiceConversationOptions): UseVoiceConversationResult {
  const { language, speech, streaming, sendMessage } = options;
  // useSpeech()/useStreamingSpeech() return a brand-new object literal every
  // render of whichever component owns them (Composer, via ChatView) — only
  // the individual functions/fields inside are memoized with stable
  // identity. Depending on the whole `speech`/`streaming` object anywhere
  // below would give start/stop/cancel a new identity every render, which
  // is exactly the stale-identity bug useStreamingSpeech.ts's own docstring
  // already describes hitting once (a churning `stop` sitting in another
  // effect's dependency array re-ran that effect on every render, killing a
  // stream moments after it started). Destructuring here and depending only
  // on these primitives avoids repeating that bug for Conversation Mode's
  // own start()/stop()/cancel().
  const { stop: speechStop, speak: speechSpeak, state: speechState, error: speechError } = speech;
  const { stop: streamingStop, streamSpeak: streamingStreamSpeak, status: streamingStatus } = streaming;

  const [state, setState] = useState<VoiceConversationState>("idle");
  const [amplitude, setAmplitude] = useState(0);
  const [threshold, setThreshold] = useState(MIN_THRESHOLD);
  const [noiseFloor, setNoiseFloor] = useState(0);
  const [speechMs, setSpeechMs] = useState(0);
  const [silenceMs, setSilenceMs] = useState(0);
  const [autoSendInMs, setAutoSendInMs] = useState<number | null>(null);
  const [lastTranscript, setLastTranscript] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const stateRef = useRef<VoiceConversationState>("idle");
  // True from a successful start() until stop()/cancel() — lets in-flight
  // async work (transcribe/chat-send/tts) notice the session ended and quietly
  // stand down instead of resurrecting the UI into "listening" after the user
  // already stopped it.
  const sessionActiveRef = useRef(false);

  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const gainRef = useRef<GainNode | null>(null);

  const utteranceChunksRef = useRef<Float32Array[]>([]);
  const utteranceSamplesRef = useRef(0);
  const utteranceStartAtRef = useRef(0);
  const preRollChunksRef = useRef<Float32Array[]>([]);
  const preRollSamplesRef = useRef(0);
  const aboveThresholdSinceRef = useRef<number | null>(null);
  const belowThresholdSinceRef = useRef<number | null>(null);
  const smoothedAmplitudeRef = useRef(0);

  // Dynamic noise floor calibration state (see CALIBRATION_MS above).
  const thresholdRef = useRef(MIN_THRESHOLD);
  const calibratingRef = useRef(false);
  const calibrationSumRef = useRef(0);
  const calibrationCountRef = useRef(0);
  const calibrationStartAtRef = useRef(0);

  const abortRef = useRef<AbortController | null>(null);
  const pendingReplyRef = useRef<{ id: string; content: string } | null>(null);
  // Which TTS path we're currently waiting to finish, so the two effects
  // below (watching streaming.status / speech.state) know whether a status
  // change is actually "our" playback finishing versus some unrelated
  // per-message Speak/Stream click elsewhere in the app.
  const awaitingTtsRef = useRef<"stream" | "speech" | null>(null);
  // Guards against the effect reacting to a stale "idle"/"error" status that
  // was already there *before* we called streamSpeak()/speak() this cycle —
  // only treat idle/error as "finished" once we've actually observed the
  // busy state at least once.
  const hasStreamStartedRef = useRef(false);
  const hasSpeechStartedRef = useRef(false);

  const setStateBoth = useCallback((s: VoiceConversationState) => {
    stateRef.current = s;
    setState(s);
  }, []);

  const teardownAudioGraph = useCallback(() => {
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

  const resetBuffers = useCallback(() => {
    utteranceChunksRef.current = [];
    utteranceSamplesRef.current = 0;
    preRollChunksRef.current = [];
    preRollSamplesRef.current = 0;
    aboveThresholdSinceRef.current = null;
    belowThresholdSinceRef.current = null;
    smoothedAmplitudeRef.current = 0;
    setAmplitude(0);
    setSpeechMs(0);
    setSilenceMs(0);
    setAutoSendInMs(null);
  }, []);

  // Returns to "listening" if the session is still active, or "idle" if
  // stop()/cancel() landed while we were transcribing/thinking/speaking.
  const backToListeningOrIdle = useCallback(() => {
    resetBuffers();
    if (!sessionActiveRef.current) {
      setStateBoth("idle");
      return;
    }
    setStateBoth("listening");
    sienaClient.logClientEvent("voice_conversation_listening");
  }, [resetBuffers, setStateBoth]);

  const finalizeUtterance = useCallback(async () => {
    if (
      stateRef.current !== "speech_detected" &&
      stateRef.current !== "silence_wait" &&
      stateRef.current !== "finalizing_wait"
    ) {
      return;
    }

    const nativeRate = audioCtxRef.current?.sampleRate ?? TARGET_SAMPLE_RATE;
    const chunks = utteranceChunksRef.current;
    const totalSamples = utteranceSamplesRef.current;
    const durationMs = (totalSamples / nativeRate) * 1000;

    if (durationMs < MIN_UTTERANCE_MS) {
      // Too short to be a real utterance (noise blip / cough) — discard
      // silently and keep listening, no transcription attempt at all.
      sienaClient.logClientEvent("voice_conversation_utterance_ignored", {
        reason: "utterance_too_short",
        duration_ms: Math.round(durationMs),
      });
      backToListeningOrIdle();
      return;
    }

    setStateBoth("transcribing");
    sienaClient.logClientEvent("voice_conversation_transcribe_started");

    const merged = new Float32Array(totalSamples);
    let offset = 0;
    for (const chunk of chunks) {
      merged.set(chunk, offset);
      offset += chunk.length;
    }
    const resampled = resampleLinear(merged, nativeRate, TARGET_SAMPLE_RATE);
    const wavBlob = encodeWavPcm16(resampled, TARGET_SAMPLE_RATE);

    const controller = new AbortController();
    abortRef.current = controller;

    let text = "";
    try {
      const result = await sienaClient.transcribeSpeech(wavBlob, language, controller.signal);
      if (controller.signal.aborted || !sessionActiveRef.current) return;
      text = result.text.trim();
      sienaClient.logClientEvent("voice_conversation_transcribe_completed", {
        text_length: text.length,
        backend: result.backend,
      });
    } catch (err) {
      if (controller.signal.aborted) return; // stop() aborted us — not a real failure
      const message = err instanceof Error ? err.message : "Transcription failed";
      sienaClient.logClientEvent("voice_conversation_failed", { stage: "transcribe", error: message });
      if (!sessionActiveRef.current) return;
      setError(message);
      backToListeningOrIdle();
      return;
    } finally {
      abortRef.current = null;
    }

    // Never auto-send empty transcripts, or a single very short "word" (e.g.
    // stray noise misread as 1-2 characters) — no fake confidence score
    // here, just a plain length floor. Known tradeoff: this also filters out
    // genuinely short replies like "да" (2 chars) — acceptable for an
    // experimental hands-free mode where a false negative just means "say
    // it again", not a wrong answer.
    if (!text || text.length < 3) {
      sienaClient.logClientEvent("voice_conversation_utterance_ignored", {
        reason: "empty_or_short_transcript",
        text_length: text.length,
      });
      backToListeningOrIdle();
      return;
    }

    setLastTranscript(text);
    setStateBoth("thinking");
    sienaClient.logClientEvent("voice_conversation_chat_send_started");

    let sendResult: SendResult;
    try {
      sendResult = await sendMessage(text);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to send message";
      sienaClient.logClientEvent("voice_conversation_failed", { stage: "chat_send", error: message });
      if (!sessionActiveRef.current) return;
      setError(message);
      backToListeningOrIdle();
      return;
    }

    if (!sessionActiveRef.current) return; // stopped while waiting for the reply — history already has the real turn, just don't auto-speak it

    sienaClient.logClientEvent("voice_conversation_chat_send_completed", { ok: Boolean(sendResult.turn) });

    if (!sendResult.turn || !sendResult.turn.content.trim()) {
      if (sendResult.errorMessage) setError(sendResult.errorMessage);
      backToListeningOrIdle();
      return;
    }

    const turn = sendResult.turn;
    pendingReplyRef.current = { id: turn.id, content: turn.content };
    setStateBoth("speaking");
    sienaClient.logClientEvent("voice_conversation_tts_started", { provider: "stream" });
    hasStreamStartedRef.current = false;
    awaitingTtsRef.current = "stream";
    void streamingStreamSpeak(turn.id, turn.content);
  }, [language, sendMessage, streamingStreamSpeak, backToListeningOrIdle, setStateBoth]);

  // onaudioprocess (defined once inside start(), long-lived for the whole
  // conversation session) must always call the *latest* finalizeUtterance —
  // routing through a ref avoids the exact stale-closure bug this project
  // already hit once in useStreamingSpeech.ts.
  const finalizeUtteranceRef = useRef(finalizeUtterance);
  useEffect(() => {
    finalizeUtteranceRef.current = finalizeUtterance;
  }, [finalizeUtterance]);

  const finishNow = useCallback(() => {
    const s = stateRef.current;
    if (s !== "speech_detected" && s !== "silence_wait" && s !== "finalizing_wait") return; // nothing to finalize yet
    sienaClient.logClientEvent("voice_conversation_utterance_finalized", { manual: true });
    void finalizeUtteranceRef.current();
  }, []);

  // Detect the streaming TTS reply finishing (or failing) so the loop can
  // return to listening. Guarded by awaitingTtsRef so a Stream/Speak click
  // on some unrelated message elsewhere in the app is never mistaken for
  // "our" reply finishing.
  useEffect(() => {
    if (awaitingTtsRef.current !== "stream") return;
    if (streamingStatus === "preparing" || streamingStatus === "streaming") {
      hasStreamStartedRef.current = true;
      return;
    }
    if (!hasStreamStartedRef.current) return;

    if (streamingStatus === "idle") {
      awaitingTtsRef.current = null;
      sienaClient.logClientEvent("voice_conversation_tts_completed", { provider: "stream" });
      pendingReplyRef.current = null;
      backToListeningOrIdle();
    } else if (streamingStatus === "error") {
      const reply = pendingReplyRef.current;
      if (!reply) {
        awaitingTtsRef.current = null;
        backToListeningOrIdle();
        return;
      }
      // Stream failed — fall back to the stable WAV Speak path once so the
      // conversation doesn't just go silent. If this fails too, the sibling
      // effect below gives up and returns to listening.
      awaitingTtsRef.current = "speech";
      hasSpeechStartedRef.current = false;
      sienaClient.logClientEvent("voice_conversation_tts_started", { provider: "speech_fallback" });
      void speechSpeak(reply.content, reply.id);
    }
  }, [streamingStatus, backToListeningOrIdle, speechSpeak]);

  useEffect(() => {
    if (awaitingTtsRef.current !== "speech") return;
    if (speechState === "preparing" || speechState === "speaking") {
      hasSpeechStartedRef.current = true;
      return;
    }
    if (!hasSpeechStartedRef.current) return;

    if (speechState === "idle") {
      awaitingTtsRef.current = null;
      sienaClient.logClientEvent("voice_conversation_tts_completed", { provider: "speech_fallback" });
      pendingReplyRef.current = null;
      backToListeningOrIdle();
    } else if (speechState === "error") {
      awaitingTtsRef.current = null;
      sienaClient.logClientEvent("voice_conversation_failed", { stage: "tts", error: speechError ?? "TTS failed" });
      pendingReplyRef.current = null;
      backToListeningOrIdle(); // give up speaking this one reply, keep the loop alive
    }
  }, [speechState, speechError, backToListeningOrIdle]);

  useEffect(() => teardownAudioGraph, [teardownAudioGraph]);

  const resetToIdle = useCallback(
    (logStopped: boolean) => {
      const wasActive = stateRef.current !== "idle";
      sessionActiveRef.current = false;
      awaitingTtsRef.current = null;
      abortRef.current?.abort();
      abortRef.current = null;
      // "Stop" must really stop everything, including whatever Siena might
      // be speaking back at this exact moment — not just the mic.
      streamingStop();
      speechStop();
      teardownAudioGraph();
      resetBuffers();
      pendingReplyRef.current = null;
      setError(null);
      setLastTranscript(null);
      setStateBoth("idle");
      if (wasActive && logStopped) sienaClient.logClientEvent("voice_conversation_stopped");
    },
    [streamingStop, speechStop, teardownAudioGraph, resetBuffers, setStateBoth],
  );

  const stop = useCallback(() => resetToIdle(true), [resetToIdle]);
  const cancel = useCallback(() => resetToIdle(false), [resetToIdle]);

  const start = useCallback(async () => {
    if (stateRef.current !== "idle") return; // already active, one session at a time

    setError(null);
    setLastTranscript(null);
    resetBuffers();
    pendingReplyRef.current = null;
    awaitingTtsRef.current = null;
    sessionActiveRef.current = true;

    // Never start listening while Siena's own voice is playing — same
    // discipline as push-to-talk's onBeforeStart.
    streamingStop();
    speechStop();

    sienaClient.logClientEvent("voice_conversation_started");

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Microphone permission denied";
      sienaClient.logClientEvent("voice_conversation_failed", { stage: "permission", error: message });
      sessionActiveRef.current = false;
      setError(message);
      setStateBoth("error");
      return;
    }

    if (!sessionActiveRef.current) {
      // stop()/cancel() landed while the permission prompt was open.
      stream.getTracks().forEach((t) => t.stop());
      return;
    }

    streamRef.current = stream;
    const ctx = createAudioContext();
    audioCtxRef.current = ctx;

    const source = ctx.createMediaStreamSource(stream);
    sourceRef.current = source;

    const processor = ctx.createScriptProcessor(4096, 2, 1);
    processorRef.current = processor;

    // Muted GainNode: keeps onaudioprocess firing without echoing the mic
    // back out through the speakers (same trick as useVoiceRecorder.ts).
    const gain = ctx.createGain();
    gain.gain.value = 0;
    gainRef.current = gain;

    // Reset + kick off the one-time ambient noise floor calibration for
    // this session (see CALIBRATION_MS above).
    thresholdRef.current = MIN_THRESHOLD;
    calibratingRef.current = true;
    calibrationSumRef.current = 0;
    calibrationCountRef.current = 0;
    calibrationStartAtRef.current = performance.now();
    setThreshold(MIN_THRESHOLD);
    setNoiseFloor(0);

    const finishCalibration = () => {
      const avg = calibrationCountRef.current > 0 ? calibrationSumRef.current / calibrationCountRef.current : 0;
      calibratingRef.current = false;
      const nextThreshold = Math.max(MIN_THRESHOLD, Math.min(MAX_THRESHOLD, avg * NOISE_MULTIPLIER));
      thresholdRef.current = nextThreshold;
      setNoiseFloor(avg);
      setThreshold(nextThreshold);
    };

    processor.onaudioprocess = (event) => {
      const currentState = stateRef.current;
      // Half-duplex gate: while transcribing/thinking/speaking (or
      // idle/error), the mic stream stays open but its samples are ignored
      // outright — this is what stops Siena's own TTS playback from ever
      // being picked up as the next utterance.
      if (
        currentState !== "listening" &&
        currentState !== "speech_detected" &&
        currentState !== "silence_wait" &&
        currentState !== "finalizing_wait"
      ) {
        return;
      }

      const input = event.inputBuffer;
      const channels: Float32Array[] = [];
      for (let c = 0; c < input.numberOfChannels; c++) channels.push(input.getChannelData(c));
      const mono = channels.length > 1 ? downmixToMono(channels, input.length) : channels[0].slice();

      const level = rms(mono);
      const now = performance.now();

      if (currentState === "listening" && calibratingRef.current) {
        calibrationSumRef.current += level;
        calibrationCountRef.current += 1;
        if (now - calibrationStartAtRef.current >= CALIBRATION_MS) {
          finishCalibration();
        }
      }

      const target = Math.max(0, Math.min(1, level * AMPLITUDE_GAIN));
      smoothedAmplitudeRef.current += (target - smoothedAmplitudeRef.current) * AMPLITUDE_SMOOTHING;
      setAmplitude(smoothedAmplitudeRef.current);

      const isAboveThreshold = level >= thresholdRef.current;

      if (currentState === "listening") {
        preRollChunksRef.current.push(mono);
        preRollSamplesRef.current += mono.length;
        const maxPreRollSamples = Math.round((PRE_ROLL_MS / 1000) * ctx.sampleRate);
        while (preRollSamplesRef.current > maxPreRollSamples && preRollChunksRef.current.length > 1) {
          const removed = preRollChunksRef.current.shift();
          if (removed) preRollSamplesRef.current -= removed.length;
        }

        if (isAboveThreshold) {
          // Speech started before calibration finished — stop calibrating
          // now (using whatever partial average we have) rather than miss
          // real speech waiting for a calibration window that no longer
          // makes sense.
          if (calibratingRef.current) finishCalibration();

          if (aboveThresholdSinceRef.current === null) {
            aboveThresholdSinceRef.current = now;
          } else if (now - aboveThresholdSinceRef.current >= VOICE_START_MS) {
            utteranceChunksRef.current = [...preRollChunksRef.current];
            utteranceSamplesRef.current = preRollSamplesRef.current;
            utteranceStartAtRef.current = aboveThresholdSinceRef.current;
            aboveThresholdSinceRef.current = null;
            belowThresholdSinceRef.current = null;
            setSilenceMs(0);
            setAutoSendInMs(null);
            setStateBoth("speech_detected");
            sienaClient.logClientEvent("voice_conversation_speech_detected");
          }
        } else {
          aboveThresholdSinceRef.current = null;
        }
        return;
      }

      // speech_detected / silence_wait / finalizing_wait: actively
      // capturing this utterance (including any pauses in it).
      utteranceChunksRef.current.push(mono);
      utteranceSamplesRef.current += mono.length;
      setSpeechMs(Math.floor(now - utteranceStartAtRef.current));

      if (isAboveThreshold) {
        if (currentState === "silence_wait" || currentState === "finalizing_wait") {
          if (currentState === "finalizing_wait") {
            sienaClient.logClientEvent("voice_conversation_resumed_before_finalize");
          }
          belowThresholdSinceRef.current = null;
          setSilenceMs(0);
          setAutoSendInMs(null);
          setStateBoth("speech_detected");
        }
      } else {
        if (belowThresholdSinceRef.current === null) belowThresholdSinceRef.current = now;
        const silenceDur = now - belowThresholdSinceRef.current;
        setSilenceMs(Math.floor(silenceDur));

        if (currentState === "speech_detected") {
          // Amplitude just dropped — this is stage 1: "Waiting for you…".
          setStateBoth("silence_wait");
        } else if (currentState === "silence_wait") {
          if (silenceDur >= SILENCE_END_MS) {
            // Stage 1 threshold crossed — enter the final grace window
            // ("Finishing phrase…") instead of finalizing immediately.
            sienaClient.logClientEvent("voice_conversation_soft_silence_detected");
            sienaClient.logClientEvent("voice_conversation_finalize_wait_started");
            setAutoSendInMs(Math.max(0, SILENCE_END_MS + FINALIZE_GRACE_MS - silenceDur));
            setStateBoth("finalizing_wait");
          }
        } else if (currentState === "finalizing_wait") {
          const remaining = SILENCE_END_MS + FINALIZE_GRACE_MS - silenceDur;
          setAutoSendInMs(Math.max(0, remaining));
          if (silenceDur >= SILENCE_END_MS + FINALIZE_GRACE_MS) {
            sienaClient.logClientEvent("voice_conversation_utterance_finalized", { manual: false });
            void finalizeUtteranceRef.current();
            return;
          }
        }
      }

      if (now - utteranceStartAtRef.current >= MAX_UTTERANCE_MS) {
        void finalizeUtteranceRef.current();
      }
    };

    source.connect(processor);
    processor.connect(gain);
    gain.connect(ctx.destination);

    setStateBoth("listening");
    sienaClient.logClientEvent("voice_conversation_listening");
  }, [resetBuffers, setStateBoth, speechStop, streamingStop]);

  return {
    state,
    active: state !== "idle",
    amplitude,
    lastTranscript,
    error,
    diagnostics: { amplitude, threshold, noiseFloor, speechMs, silenceMs, autoSendInMs },
    start,
    stop,
    cancel,
    finishNow,
  };
}
