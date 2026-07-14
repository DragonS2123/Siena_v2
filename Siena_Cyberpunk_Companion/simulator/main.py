import argparse
import asyncio
import copy
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from client import TelemetryClient

SCENARIO_DIR = Path(__file__).parent / "scenarios"
TICK_SECONDS = 0.25


def merge(target: dict, patch: dict) -> dict:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge(target[key], value)
        else:
            target[key] = value
    return target


async def run_scenario(name: str, base_url: str, speed: float, loop: bool) -> None:
    definition = json.loads((SCENARIO_DIR / f"{name}.json").read_text(encoding="utf-8"))
    client = TelemetryClient(base_url)
    try:
        bridge = await client.bridge_status()
        if bridge.get("configured_source") == "auto" and bridge.get("active_source") == "cet" and bridge.get("connected"):
            raise RuntimeError("source_conflict: an active CET bridge owns telemetry while configured_source=auto")
        while True:
            state = copy.deepcopy(definition["initial"])
            state["source"] = "simulator"
            state["session_id"] = f"{name}-{uuid4().hex[:8]}"
            sequence = 0
            simulated_at = datetime.now(timezone.utc)
            for step in definition["steps"]:
                merge(state, step.get("patch", {}))
                ticks = max(1, round(float(step.get("duration", TICK_SECONDS)) / TICK_SECONDS))
                for _ in range(ticks):
                    sequence += 1
                    simulated_at += timedelta(seconds=TICK_SECONDS)
                    state["sequence"] = sequence
                    state["captured_at"] = simulated_at.isoformat()
                    await client.send(state)
                    logging.debug("sent scenario=%s session=%s sequence=%s", name, state["session_id"], sequence)
                    await asyncio.sleep(TICK_SECONDS / speed)
            if not loop:
                break
    finally:
        await client.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Siena Cyberpunk telemetry simulator")
    parser.add_argument("--scenario", choices=[path.stem for path in SCENARIO_DIR.glob("*.json")], default="exploration")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()
    if args.speed <= 0:
        parser.error("--speed must be greater than zero")
    return args


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    options = parse_args()
    try:
        asyncio.run(run_scenario(options.scenario, options.base_url, options.speed, options.loop))
    except KeyboardInterrupt:
        logging.info("simulator stopped")
    except RuntimeError as exc:
        logging.error("simulator refused to start: %s", exc)
        raise SystemExit(2) from exc
