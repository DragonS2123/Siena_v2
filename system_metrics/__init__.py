"""System Metrics service — local-only cross-vendor CPU/RAM/GPU/VRAM
telemetry (NVIDIA via nvidia-smi, AMD/Intel/any vendor via DXGI+PDH ctypes,
CPU/RAM via psutil). Strictly local: no WebSocket protocol, no remote
delivery — see GET /api/system/metrics in api/server.py.

Every field is None with a safe reason when genuinely unavailable — never a
fabricated 0%/0 GB/0°C. See system_metrics/models.py.
"""

from system_metrics.models import CpuMetrics, GpuMetrics, RamMetrics, SystemMetricsSnapshot
from system_metrics.providers.cpu_ram import CpuRamProvider
from system_metrics.providers.nvidia import NvidiaGpuProvider
from system_metrics.providers.null_gpu import NullGpuProvider
from system_metrics.providers.windows_gpu import WindowsGpuProvider
from system_metrics.service import SystemMetricsService

__all__ = [
    "CpuMetrics",
    "GpuMetrics",
    "RamMetrics",
    "SystemMetricsSnapshot",
    "CpuRamProvider",
    "NvidiaGpuProvider",
    "NullGpuProvider",
    "WindowsGpuProvider",
    "SystemMetricsService",
]
