"""Own one Linux llama-server child; never adopt or signal an external PID."""
from __future__ import annotations

import atexit
import os
from pathlib import Path
import re
import socket
import subprocess
import threading
import time

try:
    import fcntl
except ImportError:  # Legacy Windows/Ollama runtime may still import this module.
    fcntl = None

import config
from core.owned_exec import owned_command
from core.errors import SienaInfraError, SienaTimeoutError
from core.provider_factory import create_provider


PROCESS_FIELDS = frozenset({
    "inference_provider", "llama_cpp_managed", "llama_cpp_binary",
    "llama_cpp_model_path", "llama_cpp_model", "llama_cpp_host", "llama_cpp_port",
    "llama_cpp_url", "llama_cpp_device", "llama_cpp_profile", "context_size",
    "llama_cpp_startup_timeout", "llama_cpp_shutdown_timeout",
})


class LlamaCppProcessManager:
    def __init__(self, snapshot, log_dir: Path, logger=None):
        self._snapshot = snapshot
        self._log_dir = Path(log_dir)
        self._logger = logger
        self._mutex = threading.RLock()
        self._stop_requested = threading.Event()
        self._process = None
        self._closed = False
        self._lock_file = None
        self._stdout = self._stderr = None
        self._started_at = None
        self._selected_gpu = None
        self._last_startup_error = None
        self._last_exit_code = None
        self._last_stop_forced = False
        self._command = []
        self._state = "stopped" if self.active else self._inactive_state()
        self._atexit_callback = self.close
        atexit.register(self._atexit_callback)

    @property
    def active(self):
        return (self._snapshot.get("inference_provider") == "llama_cpp"
                and bool(self._snapshot.get("llama_cpp_managed")))

    def _inactive_state(self):
        return "external" if self._snapshot.get("inference_provider") == "llama_cpp" else "inactive"

    def _path(self, field):
        path = Path(self._snapshot.get(field)).expanduser()
        return (path if path.is_absolute() else config.BASE_DIR / path).resolve()

    def _environment(self):
        # Ignore inherited llama arguments that could enable other models,
        # devices, router mode, tools, downloading, or authentication.
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("LLAMA_ARG_") and k not in {"VK_ICD_FILENAMES", "LLAMA_API_KEY"}}
        env["VK_DRIVER_FILES"] = str(config.LLAMA_CPP_VULKAN_ICD)
        env["GGML_VK_VISIBLE_DEVICES"] = config.LLAMA_CPP_VISIBLE_DEVICES
        return env

    def _validate_files(self):
        if os.name != "posix" or fcntl is None:
            raise SienaInfraError("managed llama-server currently requires Linux/POSIX")
        binary, model = self._path("llama_cpp_binary"), self._path("llama_cpp_model_path")
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise SienaInfraError(f"llama-server binary is missing or not executable: {binary}")
        with binary.open("rb") as source:
            if source.read(4) != b"\x7fELF":
                raise SienaInfraError("llama-server binary must be a native Linux ELF executable")
        if not model.is_file():
            raise SienaInfraError(f"GGUF model is missing: {model}")
        with model.open("rb") as source:
            if source.read(4) != b"GGUF":
                raise SienaInfraError("configured model is not a GGUF file")
        if not Path(config.LLAMA_CPP_VULKAN_ICD).is_file():
            raise SienaInfraError("configured RADV Vulkan ICD does not exist")
        return binary, model

    def _acquire_port_lock(self):
        self._log_dir.mkdir(parents=True, exist_ok=True)
        port = self._snapshot.get("llama_cpp_port")
        path = self._log_dir / f"llama-server-{port}.lock"
        stream = path.open("a+")
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            stream.close()
            raise SienaInfraError(f"another Siena manager owns port {port}") from exc
        self._lock_file = stream
        # Informational only: never use a PID file to adopt/kill a process.
        stream.seek(0)
        stream.truncate()
        stream.write(f"runtime_pid={os.getpid()}\n")
        stream.flush()

    def _check_port(self):
        host, port = self._snapshot.get("llama_cpp_host"), self._snapshot.get("llama_cpp_port")
        host = "127.0.0.1" if host == "localhost" else host
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                # Permit rebinding after TIME_WAIT, but never share a listener.
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((host, port))
        except OSError as exc:
            raise SienaInfraError(f"llama-server port {host}:{port} is occupied or unavailable; external process will not be adopted") from exc

    def _check_device(self, binary, env, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SienaTimeoutError("llama-server startup timeout before GPU validation")
        try:
            result = subprocess.run([str(binary), "--list-devices"], env=env,
                                    capture_output=True, text=True, check=True,
                                    timeout=min(10, remaining))
        except subprocess.TimeoutExpired as exc:
            raise SienaTimeoutError("llama-server GPU inventory timeout") from exc
        except (OSError, subprocess.CalledProcessError) as exc:
            raise SienaInfraError("llama-server --list-devices failed") from exc
        inventory = {}
        for line in (result.stdout + "\n" + result.stderr).splitlines():
            # Parse only selector and name in the documented device inventory.
            # Do not interpret the free/total MiB suffix as a VRAM metric.
            match = re.match(r"^\s*(Vulkan\d+):\s+([^()]+)", line)
            if match:
                if match[1] in inventory:
                    raise SienaInfraError("ambiguous Vulkan device inventory")
                inventory[match[1]] = match[2].strip()
        selector = self._snapshot.get("llama_cpp_device")
        name = inventory.get(selector)
        if (name != config.LLAMA_CPP_EXPECTED_GPU
                or any(word in (name or "").lower() for word in ("ryzen", "llvmpipe", "lavapipe", "integrated"))):
            raise SienaInfraError(f"configured device {selector} does not identify expected discrete GPU {config.LLAMA_CPP_EXPECTED_GPU}; refusing fallback")
        self._selected_gpu = name

    def _build_command(self, binary, model):
        s = self._snapshot
        host = "127.0.0.1" if s.get("llama_cpp_host") == "localhost" else s.get("llama_cpp_host")
        return [str(binary), "--model", str(model), "--alias", s.get("llama_cpp_model"),
                "--host", host, "--port", str(s.get("llama_cpp_port")),
                "--device", s.get("llama_cpp_device"), "--gpu-layers", "all",
                "--split-mode", "none", "--fit", "off", "--ctx-size", str(s.get("context_size")),
                "--parallel", "1", "--ctx-checkpoints", "0", "--batch-size", "2048",
                "--ubatch-size", "512", "--flash-attn", "on",
                "--cache-type-k", "f16", "--cache-type-v", "f16",
                "--jinja", "--reasoning", "auto", "--perf", "--no-ui", "--offline"]

    def _owns_listener(self):
        """Confirm the listening socket belongs to our child, even in a bind race.

        Linux procfs socket inodes link PID fd ownership to the TCP listener.
        Failure to inspect this interface is a readiness failure, not adoption.
        """
        if self._process is None:
            return False
        root = Path(f"/proc/{self._process.pid}")
        try:
            inodes = set()
            for fd in (root / "fd").iterdir():
                try:
                    match = re.fullmatch(r"socket:\[(\d+)\]", str(fd.readlink()))
                    if match:
                        inodes.add(match[1])
                except OSError:
                    continue
            for table in ("tcp", "tcp6"):
                path = root / "net" / table
                if not path.exists():
                    continue
                for line in path.read_text().splitlines()[1:]:
                    fields = line.split()
                    if (len(fields) > 9 and fields[3] == "0A" and fields[9] in inodes
                            and int(fields[1].rsplit(":", 1)[1], 16) == self._snapshot.get("llama_cpp_port")):
                        return True
        except (OSError, ValueError):
            pass
        return False

    def _emit(self, event, **fields):
        if self._logger is not None:
            self._logger.event(event, **fields)

    def _release_resources(self):
        for stream in (self._stdout, self._stderr):
            if stream is not None:
                stream.close()
        self._stdout = self._stderr = None
        if self._lock_file is not None:
            fcntl.flock(self._lock_file, fcntl.LOCK_UN)
            self._lock_file.close()
            self._lock_file = None

    def _refresh_exit(self):
        if self._process is not None and self._process.poll() is not None:
            self._last_exit_code = self._process.wait()
            self._process = None
            self._started_at = None
            self._state = "failed"
            self._release_resources()

    def start(self):
        with self._mutex:
            if self._closed:
                raise SienaInfraError("llama-server manager is closed")
            if not self.active:
                self._state = self._inactive_state()
                return self.diagnostics()
            self._refresh_exit()
            if self._process is not None:
                return self.diagnostics()  # Idempotent; do not spawn a duplicate.
            self._state = "starting"
            self._stop_requested.clear()
            self._last_startup_error = None
            self._selected_gpu = None
            self._last_stop_forced = False
            deadline = time.monotonic() + self._snapshot.get("llama_cpp_startup_timeout")
            try:
                binary, model = self._validate_files()
                self._acquire_port_lock()
                self._check_port()
                env = self._environment()
                self._check_device(binary, env, deadline)
                if self._stop_requested.is_set():
                    raise SienaInfraError("llama-server startup cancelled by stop request")
                self._command = self._build_command(binary, model)
                port = self._snapshot.get("llama_cpp_port")
                self._stdout = (self._log_dir / f"llama-server-{port}.stdout.log").open("ab")
                self._stderr = (self._log_dir / f"llama-server-{port}.stderr.log").open("ab")
                self._process = subprocess.Popen(owned_command(self._command), env=env,
                    stdin=subprocess.DEVNULL, stdout=self._stdout, stderr=self._stderr,
                    start_new_session=True, close_fds=True)
                self._started_at = time.monotonic()
                self._lock_file.write(f"child_pid={self._process.pid}\n")
                self._lock_file.flush()
                self._emit("llama_server.starting", pid=self._process.pid,
                           device=self._snapshot.get("llama_cpp_device"), gpu=self._selected_gpu)
                last_error = None
                while True:
                    if self._stop_requested.is_set():
                        raise SienaInfraError("llama-server startup cancelled by stop request")
                    code = self._process.poll()
                    if code is not None:
                        raise SienaInfraError(f"llama-server exited during startup (exit code {code})")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise SienaTimeoutError(f"llama-server startup timeout; last readiness error: {last_error}")
                    health = self._probe(min(1, remaining))
                    if self._process.poll() is not None:
                        raise SienaInfraError("llama-server exited during readiness check")
                    if health["available"] and self._owns_listener():
                        self._state = "ready"
                        self._emit("llama_server.ready", pid=self._process.pid,
                                   profile=self._snapshot.get("llama_cpp_profile"))
                        return self.diagnostics()
                    last_error = health["error"] or "configured listener is not owned by llama-server child"
                    time.sleep(min(0.1, max(0, deadline - time.monotonic())))
            except BaseException as exc:
                self._last_startup_error = str(exc)
                try:
                    self.stop()
                finally:
                    self._state = "failed"
                if isinstance(exc, (SienaInfraError, KeyboardInterrupt, SystemExit)):
                    raise
                raise SienaInfraError(f"llama-server startup failed: {exc}") from exc

    def _probe(self, timeout):
        from dataclasses import replace
        from core.providers.llama_cpp_provider import LlamaCppProvider
        provider = create_provider(self._snapshot)
        # Health plus model catalog are two requests under one readiness budget.
        return LlamaCppProvider(replace(provider.config, health_timeout=timeout / 3,
                                       connect_timeout=timeout / 3)).health()

    def health(self):
        with self._mutex:
            self._refresh_exit()
            if self._process is None or self._state not in {"ready", "unhealthy"}:
                return {"available": False, "error": self._last_startup_error or f"llama-server is {self._state}",
                        **self.diagnostics()}
            if not self._owns_listener():
                self._state = "unhealthy"
                return {"available": False, "error": "configured listener is not owned by llama-server child",
                        **self.diagnostics()}
            result = self._probe(1)
            self._state = "ready" if result["available"] else "unhealthy"
            return {**result, **self.diagnostics()}

    def stop(self):
        # A shutdown thread can interrupt a start holding the lifecycle lock.
        self._stop_requested.set()
        with self._mutex:
            process = self._process
            if process is not None:
                self._state = "stopping"
                if process.poll() is None:
                    process.terminate()  # Only the Popen handle owned by this manager.
                    try:
                        process.wait(timeout=self._snapshot.get("llama_cpp_shutdown_timeout"))
                    except subprocess.TimeoutExpired:
                        self._last_stop_forced = True
                        process.kill()
                        try:
                            process.wait(timeout=self._snapshot.get("llama_cpp_shutdown_timeout"))
                        except subprocess.TimeoutExpired as exc:
                            self._state = "failed"
                            raise SienaTimeoutError(f"llama-server PID {process.pid} did not exit after kill; ownership retained") from exc
                self._last_exit_code = process.wait()
            self._process = None
            self._started_at = None
            self._release_resources()
            self._state = "stopped" if self.active else self._inactive_state()
            if process is not None:
                self._emit("llama_server.stopped", pid=process.pid, forced=self._last_stop_forced,
                           exit_code=self._last_exit_code)
            return self.diagnostics()

    def restart(self):
        with self._mutex:
            self.stop()
            return self.start()

    def configure(self, snapshot):
        with self._mutex:
            if self._closed:
                raise SienaInfraError("llama-server manager is closed")
            if any(snapshot.get(k) != self._snapshot.get(k) for k in PROCESS_FIELDS):
                self.stop()
                self._snapshot = snapshot
                return self.start()
            # Generation options may change without restarting the child.
            self._snapshot = snapshot
            return self.diagnostics()

    def close(self):
        self._stop_requested.set()
        with self._mutex:
            self._closed = True
            self.stop()
            atexit.unregister(self._atexit_callback)

    def diagnostics(self):
        with self._mutex:
            self._refresh_exit()
            s = self._snapshot
            return {"provider": s.get("inference_provider"), "model": s.get("llama_cpp_model"),
                    "model_path": str(self._path("llama_cpp_model_path")),
                    "profile": s.get("llama_cpp_profile"), "context_size": s.get("context_size"),
                    "managed": self.active, "llama_server_state": self._state,
                    "pid": self._process.pid if self._process is not None else None,
                    "owned": self._process is not None, "closed": self._closed, "host": s.get("llama_cpp_host"),
                    "port": s.get("llama_cpp_port"), "url": s.get("llama_cpp_url"),
                    "device": s.get("llama_cpp_device"), "selected_gpu": self._selected_gpu,
                    "backend": "Vulkan", "uptime_seconds": max(0, time.monotonic() - self._started_at) if self._started_at is not None else 0,
                    "last_startup_error": self._last_startup_error,
                    "last_exit_code": self._last_exit_code, "last_stop_forced": self._last_stop_forced,
                    "command": list(self._command),
                    "stdout_log": str(self._log_dir / f"llama-server-{s.get('llama_cpp_port')}.stdout.log"),
                    "stderr_log": str(self._log_dir / f"llama-server-{s.get('llama_cpp_port')}.stderr.log"),
                    "vram_usage_bytes": None, "vram_metric_status": "future metric; not parsed from logs"}
