"""One request per child: release native STT/GPU resources when it exits."""
from __future__ import annotations

import argparse
import json
import sys
import wave


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--expected-gpu", required=True)
    parser.add_argument("wav")
    args = parser.parse_args()
    import numpy as np
    import transcribe_cpp as tc
    devices = [d for d in tc.backends() if d.kind == "vulkan" and d.device_type == "gpu"
               and d.device_id == args.device_id and args.expected_gpu in d.description
               and not any(s in d.description.lower() for s in ("llvmpipe", "ryzen", "lavapipe"))]
    if len(devices) != 1:
        raise RuntimeError("expected discrete GPU unavailable; refusing CPU/iGPU fallback")
    with wave.open(args.wav, "rb") as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (16000, 1, 2):
            raise ValueError("GigaAM input must be 16 kHz mono PCM16 WAV")
        if not 0 < wav.getnframes() <= 16000 * 60:
            raise ValueError("GigaAM audio duration must be between 0 and 60 seconds")
        pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32) / 32768
    texts = []
    with tc.Model(args.model, device=devices[0]) as model:
        cursor = 0
        while cursor < len(pcm):
            end = min(cursor + 24 * 16000, len(pcm))
            if end < len(pcm):
                # Prefer the quietest 100 ms in the last four seconds of a
                # <=24 s window, so long utterances avoid the model's 25 s cap.
                candidates = range(cursor + 20 * 16000, end, 1600)
                end = min(candidates, key=lambda n: float(np.mean(pcm[n:n + 1600] ** 2))) + 800
            with model.session() as session:
                result = session.run(pcm[cursor:end], language="ru")
                if result.text.strip():
                    texts.append(result.text.strip())
            cursor = end
    print(json.dumps({"text": " ".join(texts), "segments": len(texts), "device": devices[0].name,
                      "device_id": devices[0].device_id, "selected_gpu": devices[0].description}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(2)
