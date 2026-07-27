# Siena

Siena is a local Windows desktop assistant backed by Ollama. It provides
conversation history, memory and Insights, attachments, OCR, vision, STT,
TTS, model-role assignment, logs, Tool Trace and diagnostics.

Quick start:

```powershell
.\scripts\install.ps1
.\scripts\start_backend.ps1
.\scripts\start_desktop.ps1
```

User data stays under `storage/` and `memory/` and is excluded from commits.
See [Quick Start](docs/QUICK_START.md) and [Architecture](docs/ARCHITECTURE.md).
