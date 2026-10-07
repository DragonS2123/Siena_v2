from __future__ import annotations

import json
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import threading

import httpx
import pytest

import config
import core.llama_cpp_process as process_module
from core.errors import SienaInfraError, SienaTimeoutError
from core.llama_cpp_process import LlamaCppProcessManager
from core.providers.llama_cpp_provider import LlamaCppProvider
from core.runtime_settings import RuntimeSettingsService, SettingsValidationError
from storage.settings_store import SettingsStore


GPU_LIST = "Available devices:\n  Vulkan0: AMD Radeon RX 7900 XTX (RADV NAVI31) (24560 MiB, 100 MiB free)\n"


class FakeProcess:
    def __init__(self, pid, *, ignore_term=False, code=None):
        self.pid, self.code, self.ignore_term = pid, code, ignore_term
        self.terminated = self.killed = False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True
        if not self.ignore_term:
            self.code = -15

    def kill(self):
        self.killed = True
        self.code = -9

    def wait(self, timeout=None):
        if self.code is None:
            assert timeout is not None, "unbounded wait on running fake process"
            raise subprocess.TimeoutExpired("fake-server", timeout)
        return self.code


@pytest.fixture
def environment(tmp_path, monkeypatch):
    binary = tmp_path / "llama-server"
    binary.write_bytes(b"\x7fELFfake")
    binary.chmod(0o700)
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUFfake")
    icd = tmp_path / "radeon.json"
    icd.write_text("{}")
    monkeypatch.setattr(config, "LLAMA_CPP_VULKAN_ICD", icd)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    store = SettingsStore(tmp_path / "settings.json")
    store.replace({"provider": "llama_cpp", "llama_cpp_managed": True,
                   "llama_cpp_binary": str(binary), "llama_cpp_model_path": str(model),
                   "llama_cpp_host": "127.0.0.1", "llama_cpp_port": port,
                   "llama_cpp_profile": "normal", "llama_cpp_startup_timeout": 1,
                   "llama_cpp_shutdown_timeout": 1})
    settings = RuntimeSettingsService(store)
    processes, commands, probes = [], [], []
    state = {"inventory": GPU_LIST, "exit": None, "ignore_term": False, "ready": True}
    def run(command, **kwargs):
        probes.append((command, kwargs))
        return SimpleNamespace(stdout=state["inventory"], stderr="")
    def popen(command, **kwargs):
        commands.append((command, kwargs))
        process = FakeProcess(10000 + len(processes), ignore_term=state["ignore_term"], code=state["exit"])
        processes.append(process)
        kwargs["stdout"].write(b"fake stdout\n")
        kwargs["stderr"].write(b"fake stderr\n")
        return process
    monkeypatch.setattr(process_module.subprocess, "run", run)
    monkeypatch.setattr(process_module.subprocess, "Popen", popen)
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(process_module, "time", SimpleNamespace(
        monotonic=lambda: clock.now, sleep=lambda duration: setattr(clock, "now", clock.now + duration)))
    real_probe = LlamaCppProcessManager._probe
    monkeypatch.setattr(LlamaCppProcessManager, "_probe", lambda self, timeout: {
        "available": state["ready"], "error": None if state["ready"] else "loading"})
    monkeypatch.setattr(LlamaCppProcessManager, "_owns_listener", lambda self: True)
    manager = LlamaCppProcessManager(settings.current(), tmp_path / "logs")
    yield SimpleNamespace(manager=manager, settings=settings, state=state, processes=processes,
                          commands=commands, probes=probes, clock=clock, binary=binary, model=model,
                          real_probe=real_probe)
    manager.close()


def test_success_start_readiness_health_diagnostics(environment):
    env = environment
    diag = env.manager.start()
    assert diag["llama_server_state"] == "ready" and diag["owned"] and diag["pid"] == 10000
    assert diag["selected_gpu"] == "AMD Radeon RX 7900 XTX" and diag["device"] == "Vulkan0"
    assert diag["backend"] == "Vulkan" and diag["context_size"] == 16384
    assert diag["vram_usage_bytes"] is None
    env.clock.now += 2
    assert env.manager.health()["available"]
    assert env.manager.diagnostics()["uptime_seconds"] == 2
    env.manager.stop()
    assert Path(diag["stdout_log"]).read_text() == "fake stdout\n"
    assert Path(diag["stderr_log"]).read_text() == "fake stderr\n"


def test_launch_arguments_and_gpu_environment(environment, monkeypatch):
    monkeypatch.setenv("LLAMA_ARG_DEVICE", "Vulkan1")
    monkeypatch.setenv("LLAMA_ARG_HF_REPO", "unwanted/model")
    monkeypatch.setenv("VK_ICD_FILENAMES", "llvmpipe.json")
    environment.manager.start()
    command, args = environment.commands[0]
    assert command[command.index("--device") + 1] == "Vulkan0"
    assert command[command.index("--gpu-layers") + 1] == "all"
    assert command[command.index("--split-mode") + 1] == "none"
    assert command[command.index("--ctx-size") + 1] == "16384"
    assert command[command.index("--parallel") + 1] == "1" and "--offline" in command
    assert command[command.index("--reasoning") + 1] == "auto"
    assert args["start_new_session"] and args["stdin"] == subprocess.DEVNULL
    assert args["env"]["GGML_VK_VISIBLE_DEVICES"] == "0"
    assert "LLAMA_ARG_DEVICE" not in args["env"] and "LLAMA_ARG_HF_REPO" not in args["env"]
    assert "VK_ICD_FILENAMES" not in args["env"]
    assert environment.probes[0][1]["env"] == args["env"]


def test_duplicate_and_concurrent_start_use_one_owned_process(environment):
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: environment.manager.start(), range(2)))
    third = environment.manager.start()
    assert first["pid"] == second["pid"] == third["pid"]
    assert len(environment.processes) == 1


def test_other_manager_cannot_adopt_owned_process(environment, tmp_path):
    environment.manager.start()
    other = LlamaCppProcessManager(environment.settings.current(), tmp_path / "logs")
    try:
        with pytest.raises(SienaInfraError, match="another Siena manager"):
            other.start()
        assert other.diagnostics()["pid"] is None
        other.stop()
        assert not environment.processes[0].terminated
    finally:
        other.close()


def test_port_occupied_by_external_listener_no_spawn_or_adoption(environment):
    port = environment.settings.current().get("llama_cpp_port")
    with socket.socket() as external:
        external.bind(("127.0.0.1", port))
        external.listen()
        with pytest.raises(SienaInfraError, match="occupied"):
            environment.manager.start()
        environment.manager.stop()
        assert external.fileno() >= 0
    assert not environment.processes and not environment.probes


@pytest.mark.parametrize("problem", ["missing", "not_executable", "windows_binary"])
def test_incorrect_binary(environment, problem):
    if problem == "missing":
        environment.binary.unlink()
    elif problem == "not_executable":
        environment.binary.chmod(0o600)
    else:
        environment.binary.write_bytes(b"MZwindows")
    with pytest.raises(SienaInfraError, match="binary"):
        environment.manager.start()
    assert not environment.processes and environment.manager.diagnostics()["last_startup_error"]


@pytest.mark.parametrize("missing", [True, False])
def test_missing_or_invalid_gguf(environment, missing):
    environment.model.unlink() if missing else environment.model.write_bytes(b"not GGUF")
    with pytest.raises(SienaInfraError, match="GGUF"):
        environment.manager.start()
    assert not environment.processes


def test_startup_timeout_stops_and_reaps_child_and_releases_lock(environment):
    environment.state["ready"] = False
    with pytest.raises(SienaTimeoutError, match="startup timeout"):
        environment.manager.start()
    assert environment.processes[0].terminated
    diag = environment.manager.diagnostics()
    assert diag["llama_server_state"] == "failed" and diag["pid"] is None
    environment.state["ready"] = True
    assert environment.manager.start()["llama_server_state"] == "ready"
    assert environment.manager.diagnostics()["last_startup_error"] is None


def test_server_exits_during_startup(environment):
    environment.state["exit"] = 17
    with pytest.raises(SienaInfraError, match="exited during startup"):
        environment.manager.start()
    diag = environment.manager.diagnostics()
    assert diag["pid"] is None and diag["last_exit_code"] == 17 and "17" in diag["last_startup_error"]


def test_graceful_stop_and_idempotent_stop(environment):
    environment.manager.start()
    environment.manager.stop()
    environment.manager.stop()
    process = environment.processes[0]
    assert process.terminated and not process.killed
    assert environment.manager.diagnostics()["llama_server_state"] == "stopped"


def test_forced_kill_fallback(environment):
    environment.state["ignore_term"] = True
    environment.manager.start()
    diag = environment.manager.stop()
    assert environment.processes[0].terminated and environment.processes[0].killed
    assert diag["last_stop_forced"] and diag["pid"] is None and diag["last_exit_code"] == -9


def test_restart_and_profile_reconfigure_share_one_gguf(environment):
    manager = environment.manager
    manager.start()
    first_pid = manager.diagnostics()["pid"]
    assert manager.restart()["pid"] != first_pid
    environment.settings.update({"llama_cpp_profile": "long"})
    diag = manager.configure(environment.settings.current())
    assert diag["profile"] == "long" and diag["context_size"] == 32768
    model_paths = [cmd[cmd.index("--model") + 1] for cmd, _ in environment.commands]
    assert len(set(model_paths)) == 1 and len(environment.processes) == 3
    assert all(p.terminated for p in environment.processes[:-1])


@pytest.mark.parametrize("inventory,device", [
    (GPU_LIST, "Vulkan1"), (GPU_LIST, "none"),
    (" Vulkan0: AMD Ryzen 7 7800X3D (RADV) (16000 MiB, 100 MiB free)", "Vulkan0"),
    (" Vulkan0: llvmpipe (LLVM) (2000 MiB, 100 MiB free)", "Vulkan0"),
    (" Vulkan0: AMD Radeon RX 7800 XT (RADV) (2000 MiB, 100 MiB free)", "Vulkan0"),
])
def test_incorrect_gpu_device_refuses_fallback(environment, inventory, device):
    environment.state["inventory"] = inventory
    environment.settings.update({"llama_cpp_device": device})
    with pytest.raises(SienaInfraError, match="expected discrete GPU"):
        environment.manager.configure(environment.settings.current())
        environment.manager.start()
    assert not environment.processes


def test_runtime_exit_after_readiness_is_observable_and_reaped(environment):
    environment.manager.start()
    environment.processes[0].code = 23
    diag = environment.manager.diagnostics()
    assert diag["llama_server_state"] == "failed" and diag["last_exit_code"] == 23
    assert diag["pid"] is None and not environment.manager.health()["available"]


def test_popen_failure_releases_lock_and_logs(environment, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("cannot execute binary")
    monkeypatch.setattr(process_module.subprocess, "Popen", fail)
    with pytest.raises(SienaInfraError, match="startup failed"):
        environment.manager.start()
    assert environment.manager._lock_file is None
    assert environment.manager._stdout is None and environment.manager._stderr is None


def test_readiness_uses_health_model_alias_and_context(environment, monkeypatch):
    # Replace the fake probe with the actual provider-based readiness protocol.
    monkeypatch.setattr(LlamaCppProcessManager, "_probe", environment.real_probe)
    requests = []
    def handle(request):
        requests.append(request.url.path)
        if request.url.path == "/health":
            if requests.count("/health") == 1:
                return httpx.Response(503, json={"error": "loading"})
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, json={"data": [{"id": config.LLAMA_CPP_MODEL, "meta": {"n_ctx": 16384}}]})
    monkeypatch.setattr(LlamaCppProvider, "_client", lambda self, **kwargs: httpx.Client(
        base_url=self.config.url, transport=httpx.MockTransport(handle)))
    assert environment.manager.start()["llama_server_state"] == "ready"
    assert requests == ["/health", "/health", "/v1/models"]


def test_settings_host_port_aliases_profiles_and_reserved_port(tmp_path):
    settings = RuntimeSettingsService(SettingsStore(tmp_path / "settings.json"))
    assert settings.current().get("llama_cpp_port") == 8088
    settings.update({"provider": "llama_cpp", "llama_cpp_port": 18089})
    assert settings.current().get("llama_cpp_url") == "http://127.0.0.1:18089"
    settings.update({"llama_cpp_profile": "gemma4-32k"})
    assert settings.current().get("llama_cpp_profile") == "long"
    assert settings.current().get("context_size") == 32768
    for patch in ({"llama_cpp_port": 8080}, {"llama_cpp_port": 0},
                  {"llama_cpp_url": "http://127.0.0.1:18088", "llama_cpp_port": 18089},
                  {"llama_cpp_host": "0.0.0.0"}):
        with pytest.raises(SettingsValidationError):
            settings.update(patch)


def test_runtime_shutdown_cleans_up_owned_child(client, environment, monkeypatch):
    from core.runtime import close_runtime
    runtime = client.app.state.runtime
    old = runtime.llama_cpp
    environment.manager.start()
    monkeypatch.setattr(runtime, "llama_cpp", environment.manager)
    close_runtime(runtime)
    assert environment.processes[0].terminated
    assert environment.manager.diagnostics()["pid"] is None
    old.close()


def test_runtime_settings_restart_owned_server_and_report_diagnostics(client, environment):
    runtime = client.app.state.runtime
    raw = environment.settings.current().as_dict(include_aliases=False)
    raw.pop("settings_revision")
    runtime.settings.update(raw)
    first_pid = runtime.llama_cpp.diagnostics()["pid"]
    assert first_pid is not None
    runtime.settings.update({"llama_cpp_profile": "long"})
    diag = runtime.llama_cpp.diagnostics()
    assert diag["pid"] != first_pid and diag["context_size"] == 32768
    assert environment.processes[0].terminated
    # Avoid probing real HTTP even though process readiness is faked.
    runtime.catalog.refresh = lambda: {"available": True, "models": [], "error": None}
    api = client.get("/api/diagnostics").json()["llama_server"]
    assert api["pid"] == diag["pid"] and api["selected_gpu"] == config.LLAMA_CPP_EXPECTED_GPU
    runtime.llama_cpp.stop()


def test_failed_runtime_start_never_uses_external_catalog(client, environment):
    runtime = client.app.state.runtime
    environment.binary.unlink()
    raw = environment.settings.current().as_dict(include_aliases=False)
    raw.pop("settings_revision")
    runtime.settings.update(raw)
    assert runtime.llama_cpp.diagnostics()["llama_server_state"] == "failed"
    catalog = runtime.catalog.refresh()
    assert not catalog["available"] and catalog["models"] == []
    assert "binary" in catalog["error"]


def test_lifespan_starts_managed_server_and_closes_it(client, environment, monkeypatch):
    from api.app import create_app
    from fastapi.testclient import TestClient
    monkeypatch.setattr(config, "SETTINGS_STORE_PATH", environment.settings._store._path)
    with TestClient(create_app()) as managed_client:
        runtime = managed_client.app.state.runtime
        assert runtime.llama_cpp.diagnostics()["llama_server_state"] == "ready"
        assert len(environment.processes) == 1
    assert environment.processes[0].terminated
    assert runtime.llama_cpp.diagnostics()["pid"] is None


def test_exit_during_readiness_and_gpu_probe_timeout(environment, monkeypatch):
    def exited(self, timeout):
        environment.processes[-1].code = 19
        return {"available": True, "error": None}
    monkeypatch.setattr(LlamaCppProcessManager, "_probe", exited)
    with pytest.raises(SienaInfraError, match="exited during readiness"):
        environment.manager.start()
    assert environment.manager.diagnostics()["pid"] is None
    def timeout(*a, **k):
        raise subprocess.TimeoutExpired("inventory", 1)
    monkeypatch.setattr(process_module.subprocess, "run", timeout)
    with pytest.raises(SienaTimeoutError, match="GPU inventory timeout"):
        environment.manager.start()
    assert len(environment.processes) == 1


def test_generation_setting_change_does_not_restart(environment):
    environment.manager.start()
    environment.settings.update({"temperature": 0.1})
    environment.manager.configure(environment.settings.current())
    assert len(environment.processes) == 1 and not environment.processes[0].terminated


def test_switch_away_from_managed_provider_stops_owned_child(environment):
    environment.manager.start()
    environment.settings.update({"provider": "ollama"})
    environment.manager.configure(environment.settings.current())
    assert environment.processes[0].terminated
    assert environment.manager.diagnostics()["llama_server_state"] == "inactive"


def test_matching_health_from_unowned_listener_is_not_readiness(environment, monkeypatch):
    monkeypatch.setattr(LlamaCppProcessManager, "_owns_listener", lambda self: False)
    with pytest.raises(SienaTimeoutError, match="not owned"):
        environment.manager.start()
    assert environment.processes[0].terminated


def test_stop_interrupts_in_progress_startup(environment, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def probe(self, timeout):
        entered.set()
        assert release.wait(timeout=2)
        return {"available": False, "error": "loading"}
    monkeypatch.setattr(LlamaCppProcessManager, "_probe", probe)
    with ThreadPoolExecutor(max_workers=2) as executor:
        starting = executor.submit(environment.manager.start)
        assert entered.wait(timeout=2)
        stopping = executor.submit(environment.manager.stop)
        assert environment.manager._stop_requested.wait(timeout=2)
        release.set()
        with pytest.raises(SienaInfraError, match="startup cancelled"):
            starting.result(timeout=2)
        assert stopping.result(timeout=2)["pid"] is None
    assert environment.processes[0].terminated


def test_shutdown_prevents_late_settings_update_from_spawning_new_child(environment):
    environment.manager.start()
    environment.manager.close()
    environment.settings.update({"llama_cpp_profile": "long"})
    with pytest.raises(SienaInfraError, match="closed"):
        environment.manager.configure(environment.settings.current())
    with pytest.raises(SienaInfraError, match="closed"):
        environment.manager.start()
    assert len(environment.processes) == 1 and environment.processes[0].terminated
