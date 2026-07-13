"""GPU adapter selection (system_metrics/selection.py) — the required
scenarios from the task spec: AMD + integrated, NVIDIA + integrated, two
discrete cards, a software adapter that must never be chosen, a GPU with no
utilization data, and a 24GB VRAM card.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from system_metrics.models import GpuMetrics  # noqa: E402
from system_metrics.selection import GpuCandidate, pick_active_gpu  # noqa: E402


def _candidate(
    name: str,
    vendor: str,
    vram_total_gb: float | None,
    *,
    is_discrete: bool | None,
    is_software: bool = False,
    is_active: bool = True,
    usage_percent: float | None = 10.0,
) -> GpuCandidate:
    vram_total_bytes = round(vram_total_gb * 1024**3) if vram_total_gb is not None else None
    metrics = GpuMetrics(
        available=True,
        adapter_id=name,
        vendor=vendor,
        name=name,
        usage_percent=usage_percent,
        vram_total_bytes=vram_total_bytes,
    )
    return GpuCandidate(metrics=metrics, is_discrete=is_discrete, is_software=is_software, is_active=is_active)


def test_amd_discrete_plus_integrated_picks_discrete():
    discrete = _candidate("AMD Radeon RX 7900 XTX", "amd", 24, is_discrete=True, is_active=True)
    integrated = _candidate("AMD Radeon Graphics", "amd", 0.5, is_discrete=False, is_active=False)

    winner = pick_active_gpu([integrated, discrete])
    assert winner is discrete


def test_nvidia_discrete_plus_integrated_picks_nvidia():
    discrete = _candidate("NVIDIA GeForce RTX 4090", "nvidia", 24, is_discrete=True, is_active=True)
    integrated = _candidate("Intel UHD Graphics", "intel", 0.128, is_discrete=False, is_active=False)

    winner = pick_active_gpu([integrated, discrete])
    assert winner is discrete
    assert winner.metrics.vendor == "nvidia"


def test_two_discrete_cards_picks_largest_vram_when_both_active():
    small = _candidate("NVIDIA RTX 3060", "nvidia", 12, is_discrete=True, is_active=True)
    large = _candidate("NVIDIA RTX 4090", "nvidia", 24, is_discrete=True, is_active=True)

    winner = pick_active_gpu([small, large])
    assert winner is large


def test_two_discrete_cards_prefers_active_over_larger_inactive():
    active_smaller = _candidate("AMD RX 6600", "amd", 8, is_discrete=True, is_active=True)
    inactive_larger = _candidate("AMD RX 7900 XTX (headless)", "amd", 24, is_discrete=True, is_active=False)

    winner = pick_active_gpu([active_smaller, inactive_larger])
    assert winner is active_smaller


def test_software_adapter_is_never_selected():
    software = _candidate("Microsoft Basic Render Driver", "unknown", 0, is_discrete=None, is_software=True)
    real = _candidate("AMD Radeon RX 7900 XTX", "amd", 24, is_discrete=True, is_active=True)

    winner = pick_active_gpu([software, real])
    assert winner is real


def test_only_software_adapter_returns_none():
    software = _candidate("Microsoft Basic Render Driver", "unknown", 0, is_discrete=None, is_software=True)
    assert pick_active_gpu([software]) is None


def test_empty_candidate_list_returns_none():
    assert pick_active_gpu([]) is None


def test_gpu_without_utilization_is_still_selectable():
    candidate = _candidate("AMD Radeon RX 7900 XTX", "amd", 24, is_discrete=True, is_active=True, usage_percent=None)
    winner = pick_active_gpu([candidate])
    assert winner is candidate
    assert winner.metrics.usage_percent is None


def test_24gb_vram_card_is_selected_correctly():
    candidate = _candidate("AMD Radeon RX 7900 XTX", "amd", 24, is_discrete=True, is_active=True)
    winner = pick_active_gpu([candidate])
    assert winner.metrics.vram_total_bytes == 24 * 1024**3


def test_active_integrated_wins_when_no_discrete_present():
    integrated = _candidate("Intel Iris Xe Graphics", "intel", 1, is_discrete=False, is_active=True)
    winner = pick_active_gpu([integrated])
    assert winner is integrated


def test_first_well_formed_adapter_as_last_resort():
    unknown_a = _candidate("Unknown Adapter A", "unknown", None, is_discrete=None, is_active=False)
    unknown_b = _candidate("Unknown Adapter B", "unknown", None, is_discrete=None, is_active=False)
    winner = pick_active_gpu([unknown_a, unknown_b])
    assert winner is unknown_a
