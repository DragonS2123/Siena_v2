"""Manual Linux launch using separate data, preserving the Windows settings/data."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config


def main():
    root = Path(os.environ.get('SIENA_DATA_DIR', str(config.BASE_DIR / 'storage/linux'))).resolve()
    root.mkdir(parents=True, exist_ok=True)
    # Existing services are wired as before; only their data locations differ.
    paths = {
        'SETTINGS_STORE_PATH': 'settings.json', 'CONVERSATIONS_DB_PATH': 'conversations.sqlite3',
        'ATTACHMENTS_STORAGE_ROOT': 'attachments', 'VOICE_PROFILES_PATH': 'voice_profiles.json',
        'TTS_OUTPUT_DIR': 'tts', 'LOG_DIR': 'logs', 'SHORT_MEMORY_PATH': 'memory/short_memory.json',
        'LONG_MEMORY_DB_PATH': 'memory/long_memory.sqlite3',
        'CANDIDATE_MEMORY_DB_PATH': 'memory/candidate_memory.sqlite3',
        'MEMORY_VECTORS_DB_PATH': 'memory/memory_vectors.sqlite3',
    }
    for name, relative in paths.items():
        setattr(config, name, root / relative)
    if not config.SETTINGS_STORE_PATH.exists():
        config.SETTINGS_STORE_PATH.write_text(json.dumps({
            'inference_provider': 'llama_cpp', 'model_roles': config.inference_model_roles('llama_cpp'),
            'context_size': config.LLAMA_CPP_PROFILES['normal'], 'llama_cpp_reasoning_budget_tokens': 512,
            'stt_language': 'ru', 'tts_provider': 'cosyvoice3_cpp',
        }, ensure_ascii=False, indent=2))
    print(f'Siena Linux data: {root}', flush=True)
    import uvicorn
    from api.app import app
    uvicorn.run(app, host='127.0.0.1', port=8000)


if __name__ == '__main__':
    main()
