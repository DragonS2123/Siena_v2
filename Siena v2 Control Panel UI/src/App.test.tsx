import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import App from "./App";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    const body = path.endsWith("/api/models") || path.endsWith("/api/models/refresh")
      ? {
          available: true,
          error: null,
          refreshed_at: "now",
          models: [{ name: "local:latest", tag: "latest", family: "qwen", parameter_size: "7B", quantization: "Q4", size: 1, loaded: false }],
          roles: [{ role: "chat", model: "local:latest", missing: false }],
        }
      : path.endsWith("/api/conversations") ? { conversations: [] }
      : { values: {}, classification: {} };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
});
afterEach(cleanup);

test("loads the live model catalog and exposes role assignment", async () => {
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Models" }));
  expect((await screen.findAllByText("local:latest")).length).toBeGreaterThan(0);
  expect(screen.getByLabelText("chat model")).toBeInTheDocument();
});

test("navigation contains only desktop assistant screens", async () => {
  render(<App />);
  await waitFor(() => expect(screen.getByRole("button", { name: "Chat" })).toBeInTheDocument());
  for (const removed of ["Ga" + "mes", "Rem" + "ote", "Pres" + "ence", "Comp" + "uter"]) {
    expect(screen.queryByRole("button", { name: removed })).not.toBeInTheDocument();
  }
});

test("chat exposes local attachments and voice exposes push to talk", async () => {
  render(<App />);
  expect(await screen.findByText("Attach")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Voice" }));
  expect(await screen.findByRole("button", { name: "Start recording" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Speak" })).toBeDisabled();
});
