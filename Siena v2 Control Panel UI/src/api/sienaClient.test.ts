import { beforeEach, describe, expect, it, vi } from "vitest";
import { sienaClient } from "./sienaClient";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("modular Core API adapter", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("maps the dynamic Ollama catalog, all eight roles, and missing assignments", async () => {
    const roles = ["chat", "deep", "coder", "reviewer", "memory", "ocr", "vision", "embedding"].map((role, index) => ({
      role,
      model: role === "deep" ? "missing:latest" : "qwen:latest",
      missing: role === "deep",
    }));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({
      available: true,
      models: [{ name: "qwen:latest", family: "qwen", parameter_size: "9B", quantization: "Q4", loaded: true }],
      roles,
    })));

    const result = await sienaClient.getModels();

    expect(result.roles).toHaveLength(8);
    expect(result.roles?.find((role) => role.role === "deep")?.missing).toBe(true);
    expect(result.ollama_connected).toBe(true);
    expect(result.models[0]).toMatchObject({ name: "qwen:latest", loaded: true, is_active_chat_model: true });
  });

  it("activates a newly created conversation before the next chat request", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ conversation_id: "new-conversation" }))
      .mockResolvedValueOnce(jsonResponse({ answer: "hello", conversation_id: "new-conversation" }));
    vi.stubGlobal("fetch", fetchMock);

    await sienaClient.createConversation("New Chat");
    await sienaClient.sendChatMessage("hello");

    expect(fetchMock).toHaveBeenNthCalledWith(1, "http://127.0.0.1:8000/api/conversations", expect.any(Object));
    const chatInit = fetchMock.mock.calls[1][1] as RequestInit;
    expect(JSON.parse(String(chatInit.body))).toMatchObject({
      message: "hello",
      conversation_id: "new-conversation",
      attachments: [],
    });
  });

  it("uses the current role assignment endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ model_roles: { vision: "qwen-vl:latest" }, roles: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await sienaClient.assignModelRole("vision", "qwen-vl:latest");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/models/roles/vision",
      expect.objectContaining({ method: "PUT", body: JSON.stringify({ model: "qwen-vl:latest" }) }),
    );
  });

  it("adapts nested STT/TTS status and the current insights payload", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({
        stt: { available: true, model: "ggml.bin", provider: "whisper.cpp", reason: null },
        tts: { available: false, provider: "qwen3", language: "ru", voice: "Siena" },
      }))
      .mockResolvedValueOnce(jsonResponse({ items: [{ id: 7, status: "pending", text: "remember" }] }));
    vi.stubGlobal("fetch", fetchMock);

    const voice = await sienaClient.getVoiceStatus();
    const insights = await sienaClient.listInsights("pending");

    expect(voice).toMatchObject({ stt_available: true, tts_available: false, tts_voice: "Siena" });
    expect(insights.entries).toEqual([expect.objectContaining({ id: 7, status: "pending" })]);
  });
});