import torch
from qwen_tts import Qwen3TTSModel

MODEL_ID = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"

device_map = None
dtype = torch.float32

print(f"Loading model: {MODEL_ID}")

model = Qwen3TTSModel.from_pretrained(
    MODEL_ID,
    device_map=device_map,
    dtype=dtype,
)

print("\nSupported speakers:")
print(model.get_supported_speakers())

print("\nSupported languages:")
print(model.get_supported_languages())