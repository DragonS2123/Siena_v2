"""Read-only checks for the unified Linux desktop launcher."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import config


def main():
    data = Path(os.environ.get('SIENA_DATA_DIR', str(REPO / 'storage/linux'))).resolve()
    settings = data / 'settings.json'
    values = json.loads(settings.read_text()) if settings.exists() else {}
    assets = {
        'llama-server': values.get('llama_cpp_binary', config.LLAMA_CPP_BINARY),
        'Gemma GGUF': values.get('llama_cpp_model_path', config.LLAMA_CPP_MODEL_PATH),
        'GigaAM library': config.GIGAAM_LIBRARY,
        'GigaAM GGUF': config.GIGAAM_MODEL,
        'CosyVoice server': config.COSYVOICE_BINARY,
        'CosyVoice GGUF': config.COSYVOICE_MODEL,
        'Russian female voice': config.COSYVOICE_PROMPT,
        'RADV ICD': config.VOICE_VULKAN_ICD,
        'Electron binary': REPO / 'Siena v2 Control Panel UI/node_modules/electron/dist/electron',
        'UI build': REPO / 'Siena v2 Control Panel UI/dist/index.html',
    }
    for label, value in assets.items():
        path = Path(value).expanduser()
        if not path.is_absolute(): path = REPO / path
        if not path.is_file(): raise SystemExit(f'Siena: missing {label}: {path}')
        if label in {'llama-server', 'CosyVoice server', 'Electron binary'} and not os.access(path, os.X_OK):
            raise SystemExit(f'Siena: {label} is not executable: {path}')
    # UI-only Mesa selector. Native inference retains its independently checked selectors.
    pci = config.GIGAAM_DEVICE_ID
    device = Path('/sys/bus/pci/devices') / pci
    if not device.is_dir() or (device / 'vendor').read_text().strip() != '0x1002':
        raise SystemExit(f'Siena: expected AMD GPU PCI device unavailable: {pci}')
    print('pci-' + pci.replace(':', '_').replace('.', '_'))


if __name__ == '__main__': main()
