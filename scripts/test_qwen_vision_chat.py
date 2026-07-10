"""Живой smoke-тест image understanding (vision) — против уже запущенного
Siena backend'а (тот же подход, что scripts/test_wagner_regression.py и
scripts/test_qwen_ggml_vulkan.py), не unit-стаб.

Контекст: до этого прохода в Siena был подключён только OCR (glm-ocr) —
никакой модели понимания сцены/объектов не было (ENABLE_IMAGE_UNDERSTANDING
= False). core/image_intent.py теперь различает два разных намерения:
  - "что написано" / "прочитай" / "OCR" -> OCR-путь (glm-ocr), без изменений;
  - "что изображено" / "опиши картинку" -> vision-путь (qwen2.5vl,
    vision/qwen_vision_service.py), вызывается ТОЛЬКО при этом намерении.

Этот скрипт создаёт один тестовый PNG (Pillow) с одновременно и понятным
визуальным содержимым (жёлтый круг на синем фоне — что-то, что OCR не может
"прочитать"), и явным текстом ("SIENA VISION TEST"), чтобы OCR- и
vision-пути можно было надёжно различить по ответу модели.

Проверяет:
  A) "Что изображено на картинке?" -> vision_intent_detected/started/completed,
     ответ описывает визуальное содержимое (круг/форма/цвет), а не только
     "текст не найден"/OCR-заглушку.
  B) "Что написано на изображении?" -> OCR-путь используется, vision НЕ
     вызывается (никаких vision_* событий для этого хода).
  C) "Опиши изображение" -> qwen2.5vl вызывается, ответ на основе vision-результата.
  D) Если qwen2.5vl не установлен в Ollama — backend не должен падать, ответ
     должен честно сообщать о недоступности image understanding.

ВАЖНО: LLM недетерминирована — проверяются ключевые маркеры/события, а не
точный текст ответа.

Запуск:

    python scripts/test_qwen_vision_chat.py

Требования: Siena backend запущен на http://127.0.0.1:8000, Ollama на
127.0.0.1:11434. Если qwen2.5vl не установлен, сценарии A/B/C сообщат об
этом честно (см. вывод) — выполните: ollama pull qwen2.5vl

Ничего не трогает в TTS/STT/Voice Orb/model routing/research/Insights/
Settings persistence/Runtime meters — только /api/chat, /api/conversations,
/api/trace/recent, и напрямую GET http://127.0.0.1:11434/api/tags для
проверки установленных моделей.
"""

from __future__ import annotations

import base64
import io
import sys

import requests

# Windows consoles default to a legacy codepage (cp1251/cp866), which mangles
# the Cyrillic prompts/answers this script prints — cosmetic only, the actual
# strings in memory are correct UTF-8 either way, but garbled output makes
# manual review of the printed answers useless.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

try:
    from PIL import Image, ImageDraw
except ImportError:
    print("Pillow is required to generate the test image: pip install Pillow")
    sys.exit(1)

BASE_URL = "http://127.0.0.1:8000"
OLLAMA_URL = "http://127.0.0.1:11434"
VISION_MODEL = "qwen2.5vl"

OCR_TEXT_MARKER = "SIENA VISION TEST"


def _make_test_image_data_url() -> str:
    """A synthetic PNG with both a plain visual shape (yellow circle on a
    blue background — nothing OCR can read) and an explicit text banner
    (OCR_TEXT_MARKER), so OCR vs vision answers can be told apart reliably
    without depending on any file on disk."""
    img = Image.new("RGB", (400, 300), color=(30, 60, 200))
    draw = ImageDraw.Draw(img)
    draw.ellipse((120, 60, 280, 220), fill=(250, 220, 40))
    draw.rectangle((0, 250, 400, 300), fill=(255, 255, 255))
    draw.text((20, 265), OCR_TEXT_MARKER, fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _check_vision_model_installed() -> bool:
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        r.raise_for_status()
        names = {m.get("name") for m in r.json().get("models", [])}
    except Exception as exc:
        print(f"[WARN] could not reach Ollama at {OLLAMA_URL}: {exc}")
        return False
    return any(n == VISION_MODEL or (n or "").startswith(f"{VISION_MODEL}:") for n in names)


def _create_and_activate_conversation() -> str:
    r = requests.post(f"{BASE_URL}/api/conversations", json={"title": "Vision smoke test"}, timeout=30)
    r.raise_for_status()
    conversation_id = r.json()["conversation_id"]
    r = requests.post(f"{BASE_URL}/api/conversations/{conversation_id}/activate", timeout=30)
    r.raise_for_status()
    return conversation_id


def _send_chat(message: str, data_url: str) -> dict:
    attachment = {
        "name": "test.png",
        "type": "image",
        "size": "1 KB",
        "mime": "image/png",
        "data_url": data_url,
    }
    r = requests.post(
        f"{BASE_URL}/api/chat",
        json={"message": message, "attachments": [attachment]},
        timeout=280,
    )
    r.raise_for_status()
    return r.json()


def _turn_window(events: list[dict], user_msg_idx: int) -> list[dict]:
    """Returns every event belonging to the same /api/chat turn as the
    "user_message" event at user_msg_idx — NOT just events at/after that
    index. api/server.py logs OCR/vision events (_run_image_ocr,
    _run_image_vision) *before* it logs "user_message" itself (they run
    earlier in the handler), so a naive forward-only slice from
    user_msg_idx would silently miss ocr_started/vision_intent_detected/
    vision_started/vision_completed for this exact turn. This walks
    backward to the previous "final_answer" (end of the prior turn, or log
    start) and forward to this turn's own "final_answer"."""
    start = 0
    for i in range(user_msg_idx - 1, -1, -1):
        if events[i].get("event") == "final_answer":
            start = i + 1
            break
    end = len(events)
    for i in range(user_msg_idx, len(events)):
        if events[i].get("event") == "final_answer":
            end = i + 1
            break
    return events[start:end]


def _find_user_message_index(events: list[dict], content_prefix: str) -> int | None:
    """Returns the LAST matching index — /api/trace/recent accumulates
    across every run of this script within the same JSONL log file, so
    picking the first match could silently analyze a stale prior run (same
    bug class fixed in scripts/test_wagner_regression.py)."""
    last = None
    for i, e in enumerate(events):
        content = e.get("content") or ""
        if e.get("event") == "user_message" and content.startswith(content_prefix):
            last = i
    return last


def _get_recent_events() -> list[dict]:
    return requests.get(f"{BASE_URL}/api/trace/recent?limit=300", timeout=30).json()["events"]


def main() -> int:
    print(f"Siena backend: {BASE_URL}")
    vision_installed = _check_vision_model_installed()
    print(f"qwen2.5vl installed in Ollama: {vision_installed}")
    if not vision_installed:
        print(f"[NOTE] {VISION_MODEL} not found — run: ollama pull {VISION_MODEL}")
        print("       Scenarios A/B/C below will still run and should degrade honestly (scenario D behavior).")

    data_url = _make_test_image_data_url()
    conversation_id = _create_and_activate_conversation()
    print(f"conversation_id: {conversation_id}")

    all_ok = True

    # --- A: vision intent ---------------------------------------------
    prompt_a = "Что изображено на картинке?"
    print(f"\n--- [A] {prompt_a}")
    result = _send_chat(prompt_a, data_url)
    answer_a = result["answer"]
    print(f"  answer: {answer_a[:200]}{'...' if len(answer_a) > 200 else ''}")
    print(f"  vision_results: {result.get('vision_results')}")

    events = _get_recent_events()
    idx = _find_user_message_index(events, prompt_a)
    turn_events = _turn_window(events, idx) if idx is not None else []
    names = [e.get("event") for e in turn_events]
    print(f"  events: {names}")

    if vision_installed:
        if "vision_intent_detected" in names and "vision_started" in names and "vision_completed" in names:
            print("  [OK] vision_intent_detected -> vision_started -> vision_completed all present")
        else:
            print("  [FAIL] expected vision_intent_detected/vision_started/vision_completed missing")
            all_ok = False
        if "жёлт" in answer_a.lower() or "yellow" in answer_a.lower() or "круг" in answer_a.lower() or "circle" in answer_a.lower() or "ellipse" in answer_a.lower():
            print("  [OK] answer appears to describe visual content (shape/color mentioned)")
        else:
            print("  [WARN] answer did not obviously mention shape/color — model wording may vary, inspect manually")
        if "недоступ" in answer_a.lower() and "OCR" in answer_a and "vision" not in [n for n in names]:
            print("  [FAIL] answer looks like the old OCR-only disclaimer, not a grounded vision answer")
            all_ok = False
    else:
        if "vision_completed" in names:
            print("  [FAIL] vision_completed fired even though the model is reportedly not installed")
            all_ok = False
        else:
            print("  [OK] no crash; degraded honestly (model not installed) — see scenario D check below")

    # --- B: OCR intent (vision must NOT be called) ---------------------
    prompt_b = "Что написано на изображении?"
    print(f"\n--- [B] {prompt_b}")
    result_b = _send_chat(prompt_b, data_url)
    answer_b = result_b["answer"]
    print(f"  answer: {answer_b[:200]}{'...' if len(answer_b) > 200 else ''}")

    events = _get_recent_events()
    idx_b = _find_user_message_index(events, prompt_b)
    turn_events_b = _turn_window(events, idx_b) if idx_b is not None else []
    names_b = [e.get("event") for e in turn_events_b]
    print(f"  events: {names_b}")

    if any(n and n.startswith("vision_") for n in names_b):
        print("  [FAIL] a vision_* event fired for an explicit OCR-only question")
        all_ok = False
    else:
        print("  [OK] no vision_* events for the OCR-only question")
    if "ocr_started" in names_b or "ocr_completed" in names_b:
        print("  [OK] OCR path used")
    else:
        print("  [FAIL] OCR path did not run (ocr_started/ocr_completed missing)")
        all_ok = False

    # --- C: "Опиши изображение" (vision intent, different phrasing) ----
    prompt_c = "Опиши изображение"
    print(f"\n--- [C] {prompt_c}")
    result_c = _send_chat(prompt_c, data_url)
    answer_c = result_c["answer"]
    print(f"  answer: {answer_c[:200]}{'...' if len(answer_c) > 200 else ''}")

    events = _get_recent_events()
    idx_c = _find_user_message_index(events, prompt_c)
    turn_events_c = _turn_window(events, idx_c) if idx_c is not None else []
    names_c = [e.get("event") for e in turn_events_c]
    print(f"  events: {names_c}")

    if vision_installed:
        if "vision_completed" in names_c:
            print("  [OK] qwen2.5vl was called for 'Опиши изображение'")
        else:
            print("  [FAIL] vision_completed missing for an explicit describe-the-image request")
            all_ok = False

    # --- D: model unavailable degrades honestly -------------------------
    print("\n--- [D] qwen2.5vl unavailable -> honest degrade (no crash)")
    if vision_installed:
        print("  [SKIP] qwen2.5vl is installed in this environment — cannot exercise the not-installed path live.")
        print("         (VisionModelNotInstalledError / VisionUnavailableError in vision/qwen_vision_service.py")
        print("         are exercised by _run_image_vision's except-branches; reviewed, not re-run here.)")
    else:
        if result.get("vision_results") and any(r.get("status") == "unavailable" for r in result["vision_results"]):
            print("  [OK] vision_results reports status=unavailable")
        else:
            print("  [WARN] vision_results did not report an 'unavailable' status entry — check manually")
        disclaimer_present = "недоступ" in answer_a.lower() or "unavailable" in answer_a.lower()
        if disclaimer_present:
            print("  [OK] answer honestly states image understanding is unavailable")
        else:
            print("  [FAIL] answer does not mention that image understanding is unavailable")
            all_ok = False

    print("\n=== ИТОГ ===")
    print("RESULT:", "PASS" if all_ok else "FAIL")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
