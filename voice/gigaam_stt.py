"""GigaAM-v3 via native transcribe.cpp, isolated in a bounded worker process."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from core.errors import SienaInfraError, SienaTimeoutError


class GigaAMSTTProvider:
    PROVIDER_NAME = "gigaam_v3_e2e_rnnt"

    def __init__(self, library_path: Path, model_path: Path, device_id: str,
                 expected_gpu: str, vulkan_icd: Path, timeout: int = 120, logger=None):
        self._library = library_path
        self._model_path = model_path
        self._device_id = device_id
        self._expected_gpu = expected_gpu
        self._icd = vulkan_icd
        self._timeout = timeout
        self._logger = logger

    @property
    def model_path(self):
        return self._model_path

    def unavailable_reason(self):
        for name, path in (("transcribe.cpp library", self._library), ("GigaAM GGUF", self._model_path),
                           ("RADV ICD", self._icd)):
            if not path.is_file():
                return f"{name} missing: {path}"
        if importlib.util.find_spec("transcribe_cpp") is None:
            return "transcribe-cpp Python binding is missing"
        return None

    def is_available(self):
        return self.unavailable_reason() is None

    def transcribe_wav(self, wav_path: str, language: str = "ru"):
        if language not in {"ru", "auto", None}:
            raise SienaInfraError("GigaAM-v3 e2e-RNNT supports Russian speech only")
        if reason := self.unavailable_reason():
            raise SienaInfraError(reason)
        env = dict(os.environ)
        env.pop("VK_ICD_FILENAMES", None)
        env.update(TRANSCRIBE_LIBRARY=str(self._library.resolve()), VK_DRIVER_FILES=str(self._icd),
                   GGML_VK_VISIBLE_DEVICES="0")
        command = [sys.executable, str(Path(__file__).with_name("transcribe_worker.py")),
                   "--model", str(self._model_path), "--device-id", self._device_id,
                   "--expected-gpu", self._expected_gpu, str(wav_path)]
        start = time.monotonic()
        try:
            response = subprocess.run(command, env=env, stdin=subprocess.DEVNULL,
                                      capture_output=True, text=True, timeout=self._timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise SienaTimeoutError("GigaAM transcription timed out; worker terminated") from exc
        except OSError as exc:
            raise SienaInfraError("unable to launch GigaAM worker") from exc
        if response.returncode:
            raise SienaInfraError(f"GigaAM worker failed (exit {response.returncode}): {response.stderr[-1000:]}")
        try:
            data = json.loads(response.stdout)
            if not isinstance(data, dict) or not isinstance(data.get("text"), str):
                raise ValueError("invalid result")
        except ValueError as exc:
            raise SienaInfraError("malformed GigaAM worker response") from exc
        if not data["text"].strip():
            raise SienaInfraError("GigaAM produced no speech text")
        return {**data, "provider": self.PROVIDER_NAME, "language": "ru", "backend": "vulkan",
                "model_path": str(self._model_path), "elapsed_ms": round((time.monotonic() - start) * 1000)}
