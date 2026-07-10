import { useCallback, useEffect, useRef, useState } from "react";
import { sienaClient } from "../api/sienaClient";

export type StreamingSpeechStatus = "idle" | "preparing" | "streaming" | "stopping" | "error";

export interface StreamingDiagnostics {
  firstChunkMs: number | null;
  totalBytes: number;
  estimatedDurationSec: number | null;
  chunkCount: number;
}

export interface UseStreamingSpeechResult {
  status: StreamingSpeechStatus;
  activeMessageId: string | null;
  error: string | null;
  diagnostics: StreamingDiagnostics;
  streamSpeak: (messageId: string, text: string, voice?: string) => Promise<void>;
  stop: () => void;
}

const IDLE_DIAGNOSTICS: StreamingDiagnostics = {
  firstChunkMs: null,
  totalBytes: 0,
  estimatedDurationSec: null,
  chunkCount: 0,
};

// Experimental (Phase 3, HANDOFF_v2.md) — plays raw PCM (s16le, mono,
// sampleRate from POST /api/voice/tts/stream's X-Siena-TTS-Sample-Rate
// header, default 24000) as it streams in, via the Web Audio API.
//
// Playback strategy: each incoming chunk is decoded into its own small
// AudioBuffer and scheduled back-to-back on a running `nextStartTime`
// cursor (AudioBufferSourceNode.start(when)) — this is the standard
// gapless-chunk-scheduling technique for streaming raw PCM through Web
// Audio API. Deliberately NOT ScriptProcessorNode (deprecated) and NOT a
// full AudioWorklet module (would need a separate bundled worklet file and
// extra Vite wiring for what is, for now, an experimental/dev-only path) —
// this achieves real incremental streaming playback (audio starts as soon
// as the first chunk is scheduled, not after the whole response finishes)
// without either of those costs. A future non-experimental pass could
// revisit AudioWorklet if this needs tighter latency/jitter control.
class PcmStreamPlayer {
  private ctx: AudioContext;
  private readonly channels: number;
  private nextStartTime: number;
  private sources: AudioBufferSourceNode[] = [];
  private stopped = false;

  constructor(sampleRate: number, channels: number) {
    this.ctx = new AudioContext({ sampleRate });
    this.channels = Math.max(1, channels);
    this.nextStartTime = this.ctx.currentTime;
  }

  enqueue(samples: Float32Array): void {
    if (this.stopped) return;
    const frameCount = Math.floor(samples.length / this.channels);
    if (frameCount <= 0) return;

    const buffer = this.ctx.createBuffer(this.channels, frameCount, this.ctx.sampleRate);
    for (let ch = 0; ch < this.channels; ch++) {
      const channelData = buffer.getChannelData(ch);
      for (let i = 0; i < frameCount; i++) {
        channelData[i] = samples[i * this.channels + ch];
      }
    }

    const source = this.ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(this.ctx.destination);
    const startAt = Math.max(this.nextStartTime, this.ctx.currentTime);
    source.start(startAt);
    this.nextStartTime = startAt + buffer.duration;
    this.sources.push(source);
    source.onended = () => {
      this.sources = this.sources.filter((s) => s !== source);
    };
  }

  stop(): void {
    if (this.stopped) return;
    this.stopped = true;
    for (const source of this.sources) {
      try {
        source.stop();
      } catch {
        // already ended — nothing to do
      }
    }
    this.sources = [];
    this.ctx.close().catch(() => undefined);
  }
}

// Converts a byte range known to be a whole number of 16-bit samples into
// Float32 [-1, 1] samples. Uses DataView (not an Int16Array view over the
// raw bytes) specifically to avoid the alignment restriction Int16Array
// views have — network chunk boundaries have no reason to land on even
// byte offsets.
function pcm16BytesToFloat32(bytes: Uint8Array): Float32Array {
  const sampleCount = Math.floor(bytes.length / 2);
  const out = new Float32Array(sampleCount);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  for (let i = 0; i < sampleCount; i++) {
    out[i] = view.getInt16(i * 2, true) / 32768;
  }
  return out;
}

export function useStreamingSpeech(): UseStreamingSpeechResult {
  const [status, setStatus] = useState<StreamingSpeechStatus>("idle");
  const [activeMessageId, setActiveMessageId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [diagnostics, setDiagnostics] = useState<StreamingDiagnostics>(IDLE_DIAGNOSTICS);

  const abortRef = useRef<AbortController | null>(null);
  const playerRef = useRef<PcmStreamPlayer | null>(null);
  // Mirrors status/activeMessageId for reportDisconnectIfActive to read
  // without depending on the state itself. This isn't just style: an
  // earlier version depended on `status`/`activeMessageId` directly, which
  // gave `stop`/`streamSpeak` a new identity on every status change — and
  // since `streaming.stop` sits in ChatView's conversation-switch effect
  // dependency array, that identity change re-ran the effect immediately
  // after streamSpeak() called setStatus("preparing"), which called
  // stop() on the stream that had just started, aborting it instantly.
  // Confirmed live (button never left "preparing"/idle) before this fix.
  const statusRef = useRef<StreamingSpeechStatus>("idle");
  const activeMessageIdRef = useRef<string | null>(null);

  // Tears down whatever the previous streamSpeak() call started — aborts
  // its in-flight fetch and stops+closes its AudioContext.
  //
  // Disconnect reporting: a backend-side ASGI disconnect watcher was tried
  // and confirmed via live testing to NOT reliably detect an aborted fetch
  // while the server is blocked reading from tts-server (see
  // api/server.py::_stream_pcm_body's docstring for the full story) — so
  // the frontend, which always knows the exact instant it calls abort(),
  // reports tts_stream_client_disconnected itself via the existing
  // /api/trace/client-event bridge. This is purely a trace-visibility
  // signal; it does not (and cannot) stop the backend's already-in-flight
  // upstream generation, which keeps running until tts-server finishes that
  // utterance on its own.
  const reportDisconnectIfActive = useCallback(() => {
    const s = statusRef.current;
    const id = activeMessageIdRef.current;
    if ((s === "preparing" || s === "streaming") && id) {
      sienaClient.logClientEvent("tts_stream_client_disconnected", { message_id: id });
    }
  }, []);

  const cleanup = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    playerRef.current?.stop();
    playerRef.current = null;
  }, []);

  const stop = useCallback(() => {
    reportDisconnectIfActive();
    cleanup();
    statusRef.current = "idle";
    activeMessageIdRef.current = null;
    setStatus("idle");
    setActiveMessageId(null);
  }, [cleanup, reportDisconnectIfActive]);

  useEffect(() => cleanup, [cleanup]);

  const streamSpeak = useCallback(async (messageId: string, text: string, voice?: string) => {
    reportDisconnectIfActive();
    cleanup();

    const controller = new AbortController();
    abortRef.current = controller;
    statusRef.current = "preparing";
    activeMessageIdRef.current = messageId;
    setStatus("preparing");
    setActiveMessageId(messageId);
    setError(null);
    setDiagnostics(IDLE_DIAGNOSTICS);

    const startedAt = performance.now();
    let totalBytes = 0;
    let chunkCount = 0;
    let firstChunkMs: number | null = null;
    let sampleRateForEstimate = 24000;
    let channelsForEstimate = 1;

    try {
      const { body, sampleRate, channels } = await sienaClient.streamSpeech(text, voice, controller.signal);
      if (controller.signal.aborted) return;

      sampleRateForEstimate = sampleRate;
      channelsForEstimate = channels;
      const player = new PcmStreamPlayer(sampleRate, channels);
      playerRef.current = player;

      const reader = body.getReader();
      let pending = new Uint8Array(0); // odd trailing byte carried over between reads

      while (true) {
        const { done, value } = await reader.read();
        if (controller.signal.aborted) break;
        if (done) break;
        if (!value || value.length === 0) continue;

        chunkCount += 1;
        totalBytes += value.length;
        if (firstChunkMs === null) {
          firstChunkMs = Math.round(performance.now() - startedAt);
          statusRef.current = "streaming";
          setStatus("streaming");
        }

        let combined: Uint8Array;
        if (pending.length > 0) {
          combined = new Uint8Array(pending.length + value.length);
          combined.set(pending, 0);
          combined.set(value, pending.length);
        } else {
          combined = value;
        }
        const usableLength = combined.length - (combined.length % 2);
        pending = combined.slice(usableLength);
        if (usableLength > 0) {
          player.enqueue(pcm16BytesToFloat32(combined.subarray(0, usableLength)));
        }

        setDiagnostics({
          firstChunkMs,
          totalBytes,
          estimatedDurationSec: totalBytes / (sampleRateForEstimate * channelsForEstimate * 2),
          chunkCount,
        });
      }

      if (!controller.signal.aborted) {
        statusRef.current = "idle";
        activeMessageIdRef.current = null;
        setStatus("idle");
        setActiveMessageId(null);
      }
    } catch (err) {
      if (controller.signal.aborted || (err instanceof DOMException && err.name === "AbortError")) {
        return; // Stop()/a newer streamSpeak() — intentional, not a real failure.
      }
      statusRef.current = "error";
      setStatus("error");
      setError(err instanceof Error ? err.message : "Streaming speech failed");
    }
  }, [cleanup, reportDisconnectIfActive]);

  return { status, activeMessageId, error, diagnostics, streamSpeak, stop };
}
