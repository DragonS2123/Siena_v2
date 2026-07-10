"""STT — whisper.cpp (GGML/Vulkan), Phase 1 (HANDOFF_v2.md).

Same technical-service role as every other voice/ provider — only turns
audio into text, never decides what to do with it (ARCHITECTURE.md: the
recognized text goes back through the normal chat flow exactly as if the
user had typed it). This is a SEPARATE, standalone service from
voice/stt.py's WhisperSTTProvider (faster-whisper, Python/CTranslate2) —
that file and its provider are completely untouched by this pass; this one
shells out to a real whisper.cpp CLI subprocess instead.

AMD/Vulkan finding (storage/stt_probe/whisper_cpp_build_probe.txt,
confirmed via live crash reproduction, not just in theory): on this
machine/build, whisper-cli.exe on the Vulkan backend with its DEFAULT
decode settings (beam-size 5, best-of 5) segfaults 100% of the time.
Greedy decode (beam-size 1, best-of 1) works correctly and is fast. This
service always forces greedy decode (config.WHISPER_CPP_BEAM_SIZE/
WHISPER_CPP_BEST_OF) — do not change that without re-testing against a
real crash reproduction first.
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Any, Protocol


class _LoggerLike(Protocol):
    def event(self, event_type: str, console_message: str | None = None, **fields: Any) -> None: ...
    def error(self, event_type: str, console_message: str, **fields: Any) -> None: ...


class WhisperCppUnavailableError(Exception):
    """Base class for any whisper.cpp STT failure — infrastructure, not
    semantic (same discipline as TTSUnavailableError/OcrUnavailableError
    elsewhere in voice/ and ocr/)."""


class WhisperCppExecutableMissingError(WhisperCppUnavailableError):
    pass


class WhisperCppModelMissingError(WhisperCppUnavailableError):
    pass


class WhisperCppTimeoutError(WhisperCppUnavailableError):
    pass


class WhisperCppTranscriptionError(WhisperCppUnavailableError):
    """The CLI ran (Vulkan, and — if enabled — the CPU fallback too) but
    exited non-zero on both attempts."""


class WhisperCppEmptyResultError(WhisperCppUnavailableError):
    """The CLI exited 0 but produced no usable text — most likely
    silence/noise-only audio, not a technical failure. Kept as a distinct
    error (not just an empty string) so callers can tell the two apart."""


# whisper-cli.exe is invoked with -np -nt (see _run_cli below), so stdout is
# normally already just the plain transcribed text. This regex is defense
# in depth only, in case a future whisper.cpp version ignores -nt: it
# strips a leading `[00:00:00.000 --> 00:00:01.000]`-style timestamp from
# any line before joining, so a format change upstream degrades gracefully
# instead of leaking raw timestamps into the model's context.
_TIMESTAMP_PREFIX_RE = re.compile(r"^\s*\[\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}\]\s*")


def _clean_transcription(raw_stdout: str) -> str:
    lines: list[str] = []
    for line in raw_stdout.splitlines():
        cleaned = _TIMESTAMP_PREFIX_RE.sub("", line).strip()
        if cleaned:
            lines.append(cleaned)
    return " ".join(lines).strip()


class WhisperCppSTTProvider:
    PROVIDER_NAME = "whisper_cpp"

    def __init__(
        self,
        exe_path: Path,
        model_path: Path,
        timeout: int,
        beam_size: int = 1,
        best_of: int = 1,
        use_vulkan: bool = True,
        cpu_fallback: bool = True,
        logger: _LoggerLike | None = None,
    ):
        self._exe_path = exe_path
        self._model_path = model_path
        self._timeout = timeout
        self._beam_size = beam_size
        self._best_of = best_of
        self._use_vulkan = use_vulkan
        self._cpu_fallback = cpu_fallback
        self._logger = logger

    @property
    def model_path(self) -> Path:
        return self._model_path

    def is_available(self) -> bool:
        """Cheap check: does the exe and the model file actually exist on
        disk. Never runs the CLI just to check status."""
        return self._exe_path.exists() and self._model_path.exists()

    def unavailable_reason(self) -> str | None:
        if not self._exe_path.exists():
            return f"whisper-cli.exe not found: {self._exe_path}"
        if not self._model_path.exists():
            return f"whisper.cpp model not found: {self._model_path}"
        return None

    def _run_cli(self, wav_path: str, language: str, extra_args: list[str]) -> tuple[str, str, int]:
        cmd = [
            str(self._exe_path),
            "-m", str(self._model_path),
            "-f", wav_path,
            "-l", language,
            "-bs", str(self._beam_size),
            "-bo", str(self._best_of),
            "-np", "-nt",
            *extra_args,
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise WhisperCppTimeoutError(
                f"whisper-cli.exe timed out after {self._timeout}s"
            ) from exc
        return proc.stdout, proc.stderr, proc.returncode

    def transcribe_wav(self, wav_path: str, language: str | None = None) -> dict[str, Any]:
        """Runs one WAV file through whisper.cpp. Returns
        {text, language, provider, elapsed_ms, backend, model_path}.

        backend is "vulkan" (succeeded on the first try), "cpu_fallback"
        (Vulkan failed, CPU retry succeeded), or "cpu" if
        config.WHISPER_CPP_USE_VULKAN is False to begin with.

        Raises WhisperCppExecutableMissingError/WhisperCppModelMissingError
        before ever spawning a subprocess if either is missing;
        WhisperCppTimeoutError if a run exceeds the configured timeout;
        WhisperCppTranscriptionError if the CLI exits non-zero on every
        attempt (Vulkan, and CPU fallback if enabled); or
        WhisperCppEmptyResultError if it exits 0 but yields no text.
        Never lets a subprocess crash (e.g. the Vulkan beam-search segfault)
        propagate as an unhandled exception — a non-zero/negative
        returncode is just data here, handled the same way as any other
        non-zero exit.
        """
        if not self._exe_path.exists():
            raise WhisperCppExecutableMissingError(f"whisper-cli.exe not found: {self._exe_path}")
        if not self._model_path.exists():
            raise WhisperCppModelMissingError(f"whisper.cpp model not found: {self._model_path}")

        effective_language = language or "auto"
        start = time.monotonic()
        backend = "vulkan" if self._use_vulkan else "cpu"

        extra_args = [] if self._use_vulkan else ["-ng"]
        stdout, stderr, returncode = self._run_cli(wav_path, effective_language, extra_args)

        if returncode != 0 and self._use_vulkan and self._cpu_fallback:
            if self._logger:
                self._logger.event(
                    "stt_cpu_fallback_started",
                    exit_code=returncode,
                    console_message=(
                        f"[VOICE][STT][whisper.cpp] Vulkan call failed (exit {returncode}) — retrying on CPU (-ng)"
                    ),
                )
            backend = "cpu_fallback"
            stdout, stderr, returncode = self._run_cli(wav_path, effective_language, ["-ng"])
            if returncode != 0:
                if self._logger:
                    self._logger.error(
                        "stt_cpu_fallback_failed",
                        exit_code=returncode,
                        stderr_tail=stderr[-500:],
                        console_message=f"[VOICE][STT][whisper.cpp] CPU fallback also failed (exit {returncode})",
                    )
                raise WhisperCppTranscriptionError(
                    f"whisper-cli.exe failed on both Vulkan and CPU fallback (exit {returncode}): {stderr[-500:]}"
                )
            if self._logger:
                self._logger.event(
                    "stt_cpu_fallback_completed",
                    console_message="[VOICE][STT][whisper.cpp] CPU fallback succeeded",
                )
        elif returncode != 0:
            raise WhisperCppTranscriptionError(f"whisper-cli.exe exited {returncode}: {stderr[-500:]}")

        elapsed_ms = round((time.monotonic() - start) * 1000)
        text = _clean_transcription(stdout)

        if not text:
            raise WhisperCppEmptyResultError(
                "whisper.cpp produced no usable text (likely silence/noise-only audio)"
            )

        return {
            "text": text,
            "language": effective_language,
            "provider": self.PROVIDER_NAME,
            "elapsed_ms": elapsed_ms,
            "backend": backend,
            "model_path": str(self._model_path),
        }
