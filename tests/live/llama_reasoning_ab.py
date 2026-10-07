"""Explicit live test, never collected by pytest. Requires the configured GGUF.

Run: python tests/live/llama_reasoning_ab.py
Only --reasoning differs between two owned manager children; all inference
parameters and the complex prompt are identical. No user settings are changed.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
from fractions import Fraction as F
import json
from pathlib import Path
import socket
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import config
from core.llama_cpp_process import LlamaCppProcessManager
from core.provider_factory import create_provider
from core.runtime_settings import RuntimeSettingsService
from storage.settings_store import SettingsStore


SYSTEM = "Ты решаешь задачи вероятностей и принятия решений. Дай точный ответ на русском языке."
PROMPT = """Реши задачу контроля качества с неизвестным поставщиком.
Деталь от A с вероятностью 0.6, от B с вероятностью 0.4.
Вероятность брака: для A 0.02, для B 0.08. Поставщик одной детали неизменен.
Тесты условно независимы ТОЛЬКО при одновременно известных поставщике и
состоянии детали (брак/исправна). Нельзя считать их независимыми после
смешивания поставщиков.

Вероятности положительного результата:
                    A, брак | A, исправна | B, брак | B, исправна
T1                  0.95   | 0.03        | 0.90    | 0.06
T2                  0.90   | 0.02        | 0.85    | 0.04
T3                  0.98   | 0.01        | 0.80    | 0.10

У одной случайной детали наблюдали T1 положительный, T2 отрицательный.
Можно принять деталь или отклонить. Потери при принятии: 200, если брак,
и 0, если исправна. При отклонении: 5, если брак, и 30, если исправна.
Есть третий вариант: заплатить 3 за T3, затем оптимально принять или
отклонить по его результату. Больше тестов нет. Стоимость первых двух
тестов уже понесена и не учитывается.

Вычисли апостериорные вероятности и сравни все три стратегии по ожидаемым
потерям. Выбери минимум. Не округляй промежуточные вычисления.
Ответ только JSON без Markdown, числовые поля с шестью знаками после точки:
p_defect, p_supplier_b, p_t3_positive, p_defect_t3_positive,
p_defect_t3_negative, cost_accept, cost_reject, cost_retest.
Также optimal_strategy ('accept', 'reject' или 'retest'), policy
с ключами positive и negative ('accept'/'reject') и explanation
(краткое обоснование, максимум три предложения)."""


def oracle():
    weights = {("A", True): F("0.6") * F("0.02") * F("0.95") * F("0.1"),
               ("A", False): F("0.6") * F("0.98") * F("0.03") * F("0.98"),
               ("B", True): F("0.4") * F("0.08") * F("0.90") * F("0.15"),
               ("B", False): F("0.4") * F("0.92") * F("0.06") * F("0.96")}
    rates = {("A", True): F("0.98"), ("A", False): F("0.01"),
             ("B", True): F("0.80"), ("B", False): F("0.10")}
    z = sum(weights.values())
    positive = {k: v * rates[k] for k, v in weights.items()}
    negative = {k: v * (1 - rates[k]) for k, v in weights.items()}
    defect = lambda branch: sum(v for (_, bad), v in branch.items() if bad)
    accept = lambda branch: defect(branch) * 200
    reject = lambda branch: sum(v * (5 if bad else 30) for (_, bad), v in branch.items())
    values = {"p_defect": defect(weights) / z,
              "p_supplier_b": sum(v for (supplier, _), v in weights.items() if supplier == "B") / z,
              "p_t3_positive": sum(positive.values()) / z,
              "p_defect_t3_positive": defect(positive) / sum(positive.values()),
              "p_defect_t3_negative": defect(negative) / sum(negative.values()),
              "cost_accept": accept(weights) / z, "cost_reject": reject(weights) / z,
              "cost_retest": 3 + (min(accept(positive), reject(positive)) + min(accept(negative), reject(negative))) / z}
    return {"values": {k: float(v) for k, v in values.items()},
            "exact_fractions": {k: str(v) for k, v in values.items()},
            "optimal_strategy": "retest", "policy": {"positive": "reject", "negative": "accept"}}


def evaluate(content, reference):
    checks = {"json": False, **{k: False for k in reference["values"]}, "optimal_policy": False}
    try:
        answer = json.loads(content)
        checks["json"] = isinstance(answer, dict)
        for key, expected in reference["values"].items():
            value = answer.get(key)
            checks[key] = type(value) in (int, float) and abs(value - expected) <= 0.00001
        checks["optimal_policy"] = (answer.get("optimal_strategy") == reference["optimal_strategy"]
                                    and answer.get("policy") == reference["policy"])
    except (ValueError, TypeError, AttributeError):
        pass
    return {"score": 10 * sum(checks.values()), "checks": checks}


async def measured_stream(provider):
    started = time.monotonic()
    first_delta = first_content = None
    content = ""
    thinking_chars = 0
    terminal = None
    stream = provider.stream_chat([{"role": "system", "content": SYSTEM},
                                   {"role": "user", "content": PROMPT}])
    try:
        async for chunk in stream:
            message = chunk["message"]
            thought, text = message.get("thinking", ""), message.get("content", "")
            now = time.monotonic() - started
            if first_delta is None and (thought or text):
                first_delta = now
            if first_content is None and text:
                first_content = now
            content += text
            thinking_chars += len(thought)
            if chunk["done"]:
                terminal = chunk
    finally:
        await stream.aclose()
    return {"latency_seconds": time.monotonic() - started,
            "first_delta_seconds": first_delta, "first_content_seconds": first_content,
            "thinking_chars": thinking_chars, "content": content,
            "generated_tokens": terminal["eval_count"], "prompt_tokens": terminal["prompt_eval_count"],
            "finish_reason": terminal["done_reason"], "usage": terminal["usage"], "timings": terminal["timings"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=config.BASE_DIR / "external/gemma4-validation/reasoning-ab")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    user_file = config.SETTINGS_STORE_PATH
    original = hashlib.sha256(user_file.read_bytes()).hexdigest() if user_file.exists() else None
    store = SettingsStore(args.output / "test-settings.json")
    store.replace({"provider": "llama_cpp", "llama_cpp_managed": True,
                   "llama_cpp_profile": "normal", "chat_output_tokens": 8192,
                   "request_timeout_seconds": 300})
    snapshot = RuntimeSettingsService(store).current()
    result = {"reference": oracle(), "system": SYSTEM, "prompt": PROMPT,
              "generation": {"temperature": 0, "seed": 42, "max_tokens": 8192}, "runs": []}
    for mode in ("off", "auto"):
        manager = LlamaCppProcessManager(snapshot, args.output / mode)
        original_builder = manager._build_command
        def command(binary, model):
            values = original_builder(binary, model)
            values[values.index("--reasoning") + 1] = mode
            return values
        manager._build_command = command  # Test-only CLI override; no production option/layer.
        run = {"mode": mode}
        result["runs"].append(run)
        try:
            ready_at = time.monotonic()
            run["diagnostics"] = manager.start()
            run["startup_seconds"] = time.monotonic() - ready_at
            print(mode, "READY", run["diagnostics"]["pid"], flush=True)
            warmup = create_provider(snapshot, num_predict=128, generation_options={"temperature": 0, "seed": 42})
            warmup.generate("Ответь числом: 2+2.", system=SYSTEM)
            provider = create_provider(snapshot, generation_options={"temperature": 0, "seed": 42})
            assert provider.config.reasoning  # Do not disable auto in the HTTP payload.
            run.update(asyncio.run(measured_stream(provider)))
            run["quality"] = evaluate(run["content"], result["reference"])
            print(mode, json.dumps({k: run[k] for k in ("latency_seconds", "first_content_seconds",
                "generated_tokens", "thinking_chars", "finish_reason", "quality")}, ensure_ascii=False), flush=True)
        finally:
            run["stop"] = manager.stop()
            manager.close()
            with socket.socket() as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((snapshot.get("llama_cpp_host"), snapshot.get("llama_cpp_port")))
            run["pid_exists_after_stop"] = (Path("/proc") / str(run.get("diagnostics", {}).get("pid"))).exists()
            (args.output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    off, auto = (run["diagnostics"]["command"] for run in result["runs"])
    assert len(off) == len(auto)
    assert [(a, b) for a, b in zip(off, auto) if a != b] == [("off", "auto")]
    result["user_settings_unchanged"] = original == (hashlib.sha256(user_file.read_bytes()).hexdigest() if user_file.exists() else None)
    assert result["user_settings_unchanged"]
    assert all(not run["pid_exists_after_stop"] for run in result["runs"])
    (args.output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print("A/B COMPLETE; DEFAULT REMAINS AUTO; OWNED SERVERS STOPPED", flush=True)


if __name__ == "__main__":
    main()
