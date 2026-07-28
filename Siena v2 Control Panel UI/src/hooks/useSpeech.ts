import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE_URL, sienaClient } from "../api/sienaClient";

export type SpeechState = "idle" | "preparing" | "speaking" | "error";

export interface UseSpeechResult {
  state: SpeechState;
  activeMessageId: string | null;
  provider: string | null;
  error: string | null;
  speak: (text: string, messageId: string) => Promise<void>;
  stop: () => void;
}

// TTS playback only (HANDOFF_v2.md) — speaks arbitrary assistant text
// through POST /api/voice/synthesize (VoiceService: primary
// qwen3_tts_ggml_vulkan, automatic Silero fallback) and plays the returned
// WAV via a single shared <audio> element. Deliberately does NOT touch
// mic/STT — this hook only ever sends text out, never listens. No fake
// streaming here either — this is a plain WAV-per-request path; the raw
// tts-server response_format=pcm path is untouched and unused.
//
// Lifecycle invariant: at most one playback (and at most one in-flight
// synthesize request) is ever live at a time. Every entry point that could
// start something new (speak(), stop(), unmount) first tears down whatever
// came before it via cleanup() — pausing/detaching the previous <audio> and
// aborting the previous fetch — so callers never need to reason about
// overlap themselves.
//
// stop() only pauses local playback; it does not call /api/voice/tts/stop,
// so the tts-server subprocess (if the primary provider started one) stays
// warm for the next Speak click instead of being torn down on every Stop.
export function useSpeech(): UseSpeechResult {
  const [state, setState] = useState<SpeechState>("idle");
  const [activeMessageId, setActiveMessageId] = useState<string | null>(null);
  const [provider, setProvider] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Tears down whatever the previous speak() call started: aborts its
  // in-flight /api/voice/synthesize request (a no-op if it already
  // resolved) and detaches + pauses its <audio> element. Detaching
  // onended/onerror first means a late-arriving event from a superseded
  // audio element can never reach setState after a newer speak()/stop().
  const cleanup = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;

    const audio = audioRef.current;
    if (audio) {
      audio.onended = null;
      audio.onerror = null;
      audio.pause();
      audio.currentTime = 0;
      audio.src = "";
      audioRef.current = null;
    }
  }, []);

  const stop = useCallback(() => {
    cleanup();
    setState("idle");
    setActiveMessageId(null);
  }, [cleanup]);

  // Unmount (e.g. navigating away from Chat entirely — ChatView unmounts
  // this hook instance) must not leave audio playing or a request in
  // flight behind it.
  useEffect(() => cleanup, [cleanup]);

  const speak = useCallback(async (text: string, messageId: string) => {
    cleanup();

    const controller = new AbortController();
    abortRef.current = controller;

    setState("preparing");
    setActiveMessageId(messageId);
    setError(null);

    try {
      const result = await sienaClient.synthesizeSpeech(text, undefined, controller.signal);
      // Superseded by a newer speak()/stop() while this request was in
      // flight — its own cleanup() already aborted us; just walk away
      // instead of resurrecting stale state.
      if (controller.signal.aborted) return;

      setProvider(result.provider);

      const audio = new Audio(`${API_BASE_URL}${result.audio_url}`);
      audioRef.current = audio;
      audio.onended = () => {
        setState("idle");
        setActiveMessageId(null);
      };
      audio.onerror = () => {
        setState("error");
        setError("Playback failed — the audio file could not be played.");
      };

      await audio.play();
      if (controller.signal.aborted) return; // stop()/another speak() landed while play() was resolving

      setState("speaking");
    } catch (err) {
      if (controller.signal.aborted || (err instanceof DOMException && err.name === "AbortError")) {
        return; // intentional cancellation, not a real failure — nothing to show the user
      }
      setState("error");
      setError(err instanceof Error ? err.message : "Speech synthesis failed");
      // Deliberately keep activeMessageId pointing at this message — the
      // whole point of message-level state is that FeedbackRow can only
      // show the error on the message that actually failed
      // (isThisMessage = speech.activeMessageId === messageId); clearing it
      // here would silently drop the error back to every button's "idle".
    }
  }, [cleanup]);

  return { state, activeMessageId, provider, error, speak, stop };
}
