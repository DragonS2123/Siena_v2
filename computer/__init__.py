"""Computer Awareness Layer (0.2.3, Phase 1) — a strictly READ-ONLY view of
the machine Siena runs on (CPU/RAM/VRAM, disks, her own runtime services),
so she can honestly answer "что сейчас с компьютером?" / "почему не работает
голос?" instead of guessing.

Hard Phase 1 boundaries, enforced by construction rather than by policy:
- this package contains no subprocess/command execution of any kind except
  the one pre-existing, read-only nvidia-smi query it reuses from
  core/system_metrics.py (see that module's own safety rationale);
- no clipboard, no screenshots, no webcam, no input control;
- no background threads/timers — state is collected on demand per request
  (frontend polls GET /api/computer/status; backend never polls itself);
- computer state is never written into conversation history, and the hidden
  [COMPUTER_CONTEXT] block is injected into a chat turn only when the user
  explicitly asks about the computer/Siena's runtime (computer_context.py),
  never on every message.
"""

from computer.computer_context import build_computer_context, wants_computer_context
from computer.computer_service import ComputerService, ComputerSettings
from computer.computer_state import ComputerState

__all__ = [
    "ComputerService",
    "ComputerSettings",
    "ComputerState",
    "build_computer_context",
    "wants_computer_context",
]
