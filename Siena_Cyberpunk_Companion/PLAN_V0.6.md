# Siena Cyberpunk Companion v0.6 — Safe Voice Reactions

## Audited Siena v2 TTS contract

Siena v2 currently selects `qwen3_tts_ggml_vulkan` in `config.VOICE_TTS_PROVIDER`. It also retains Qwen3-TTS, Faster Qwen3-TTS, and Silero implementations. The established stable UI path is non-streaming WAV synthesis through `VoiceService`; the raw 24 kHz mono PCM stream is experimental, qwentts.cpp-only, and cannot reliably cancel an upstream generation after a client abort.

v0.6 therefore uses completed WAV clips. A minimal feature-gated `POST /api/external/speech` endpoint returns binary `audio/wav`, never invokes an LLM or chat pipeline, and deletes the provider's temporary WAV after the response. `GET /api/voice/status` is used as a cheap readiness probe and never starts synthesis.

## Companion flow

```text
published SienaReaction
  -> VoiceBehaviorPolicy (speak or silence)
  -> bounded priority VoiceRequest queue
  -> one asynchronous VoiceWorker
  -> SienaTtsClient + independent TTS circuit breaker
  -> bounded in-memory WAV VoiceClipStore with TTL
  -> existing EventBus / REST / existing WebSocket
  -> React bounded playback queue
  -> explicit autoplay unlock + one-tab leader lease
```

Text publication is independent of voice. TTS never runs in telemetry ingestion, and voice failures preserve text, scene processing, Siena Core, and headless backend operation.

## Safety decisions

- Voice defaults disabled and TTS URL defaults empty.
- Siena external speech requires the explicit `-EnableExternalSpeech` launcher flag.
- No automatic chat-model changes, Ollama unloads, process kills, Windows TTS, overlay, game control, or game-audio ducking.
- No second event pipeline, reaction planner, or WebSocket.
- Only already-published reaction text can create one voice request.
- Queued weak work is replaceable by critical; synthesizing work is never dangerously cancelled, but a superseded result is discarded.
- Session, scene/revision/focus, age, recovery, expiry, and replacement are checked before synthesis, after synthesis, before ready publication, audio fetch, and playback acknowledgement.
- Audio is never placed in JSON/WebSocket and is never persisted by the companion.

## Validation gates

1. Siena external speech contract tests.
2. Companion unit/integration tests with deterministic WAV and fake clocks.
3. React behavior tests for queue, expiry, interruption, and leader lease.
4. Production build and CET Lua static checks.
5. Fake Siena/TTS HTTP smoke.
6. Full Siena v2 regression suite.
7. Optional live TTS and real Cyberpunk checks only when explicitly available.
