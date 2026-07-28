// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  CODE_VIEWER_SUPPORTED_LANGUAGES,
  CodeViewer,
  MessageCodeContent,
  parseMessageSegments,
} from "./CodeViewer";

const labels: Record<string, string> = {
  "codeBlock.copy": "copy",
  "codeBlock.copied": "copied",
  "codeBlock.collapse": "collapse",
  "codeBlock.expand": "expand collapsed",
  "codeBlock.save": "save",
  "codeBlock.openExpanded": "expand",
  "codeBlock.closeExpanded": "close",
};

vi.mock("../hooks/useUiPreferences", () => ({
  useUiPreferences: () => ({
    prefs: {
      codeFontSize: "default",
      codeLineWrap: false,
      codeSyntaxHighlighting: true,
      codeShowLineNumbers: true,
      codeShowLanguageBadge: true,
      codeShowCopyButton: true,
      codeShowCollapseButton: true,
      codeShowSaveButton: true,
    },
    t: (key: string) => labels[key] ?? key,
  }),
}));

function assistant(content: string) {
  return render(<MessageCodeContent content={content} />);
}

beforeEach(() => {
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
  });
  delete window.sienaDesktop;
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  document.body.style.overflow = "";
});

describe("assistant Markdown → original Code Viewer mapping", () => {
  it("turns a fenced html block into a Code Viewer", () => {
    assistant("```html\n<!DOCTYPE html>\n<html></html>\n```");
    expect(screen.getByTestId("code-viewer")).toBeInTheDocument();
    expect(screen.getByTestId("code-viewer")).toHaveAttribute("data-filename", "code.html");
  });

  it("does not expose the fence backticks after a block is complete", () => {
    const { container } = assistant("before\n\n```html\n<p>Hello</p>\n```\n\nafter");
    expect(container.textContent).not.toContain("```");
  });

  it("normalizes and displays the language identifier", () => {
    assistant("```cs\nusing System;\n```");
    expect(screen.getByTestId("code-viewer")).toHaveAttribute("data-language", "csharp");
    expect(screen.getByTestId("language-badge")).toHaveTextContent("C#");
  });

  it("renders line numbers separately from code", () => {
    const { container } = assistant("```python\nprint('one')\nprint('two')\n```");
    const lineNumbers = container.querySelectorAll('[aria-hidden="true"]');
    expect([...lineNumbers].map((node) => node.textContent)).toEqual(["1", "2"]);
  });

  it("Copy writes only code, without fences or line numbers", async () => {
    assistant("```javascript\nconst answer = 42;\n```");
    fireEvent.click(screen.getByRole("button", { name: "copy" }));
    await waitFor(() => {
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith("const answer = 42;");
    });
  });

  it("Save uses the sandboxed Electron bridge with a safe suggested name", async () => {
    const saveCodeFile = vi.fn().mockResolvedValue({ saved: true, canceled: false });
    window.sienaDesktop = {
      minimize: vi.fn(),
      toggleMaximize: vi.fn(),
      close: vi.fn(),
      isMaximized: vi.fn(),
      onMaximizedChange: vi.fn(),
      saveCodeFile,
    };
    assistant("```csharp filename=\"QuantumCircuit.cs\"\nusing System;\n```");
    fireEvent.click(screen.getByRole("button", { name: "save" }));
    await waitFor(() => {
      expect(saveCodeFile).toHaveBeenCalledWith({
        content: "using System;",
        suggestedName: "QuantumCircuit.cs",
        language: "csharp",
      });
    });
  });

  it("Expand opens the full viewer without removing the message viewer", () => {
    assistant("```typescript\nconst value: number = 1;\n```");
    fireEvent.click(screen.getByRole("button", { name: "expand" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getAllByTestId("language-badge")).toHaveLength(2);
    expect(screen.getAllByText("const")).toHaveLength(2);
  });

  it("Escape closes expanded view and restores focus to Expand", async () => {
    assistant("```json\n{\"ready\": true}\n```");
    const expand = screen.getByRole("button", { name: "expand" });
    fireEvent.click(expand);
    expect(screen.getByRole("button", { name: "close" })).toHaveFocus();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(expand).toHaveFocus());
  });

  it("keeps ordinary inline code inline", () => {
    const { container } = assistant("Run `npm test` before committing.");
    expect(screen.queryByTestId("code-viewer")).not.toBeInTheDocument();
    expect(container.querySelector("code")).toHaveTextContent("npm test");
  });

  it("does not turn ordinary text into a Code Viewer", () => {
    assistant("This is a normal answer without source code.");
    expect(screen.queryByTestId("code-viewer")).not.toBeInTheDocument();
    expect(screen.getByText("This is a normal answer without source code.")).toBeInTheDocument();
  });

  it("renders HTML source as inert text and never creates model-supplied elements", () => {
    const { container } = assistant("```html\n<script>window.pwned = true</script>\n<img src=x onerror=alert(1)>\n```");
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("window.pwned");
    expect(container.textContent).toContain("onerror");
  });

  it("renders multiple fenced blocks as separate viewers", () => {
    assistant("```css\nbody { color: red; }\n```\n\nthen\n\n```lua\nprint('ok')\n```");
    expect(screen.getAllByTestId("code-viewer")).toHaveLength(2);
  });

  it("preserves paragraphs around a fenced block", () => {
    assistant("First paragraph.\n\n```sql\nselect 1;\n```\n\nSecond paragraph.");
    expect(screen.getByText("First paragraph.")).toBeInTheDocument();
    expect(screen.getByText("Second paragraph.")).toBeInTheDocument();
    expect(screen.getByTestId("code-viewer")).toBeInTheDocument();
  });

  it("keeps an unfinished streaming fence in a safe plain-text state", () => {
    expect(() => assistant("Answer:\n\n```python\nprint('still streaming')")).not.toThrow();
    expect(screen.queryByTestId("code-viewer")).not.toBeInTheDocument();
    expect(document.body.textContent).toContain("```python");
  });

  it("upgrades an unfinished streaming fence after the closing fence arrives", () => {
    const view = render(<MessageCodeContent content={"```python\nprint('streaming')"} />);
    expect(screen.queryByTestId("code-viewer")).not.toBeInTheDocument();
    view.rerender(<MessageCodeContent content={"```python\nprint('streaming')\n```"} />);
    expect(screen.getByTestId("code-viewer")).toHaveAttribute("data-language", "python");
    expect(document.body.textContent).not.toContain("```");
  });

  it("keeps the Cyberpunk regression word as ordinary code content", () => {
    const message = [
      "Конечно, вот калькулятор.",
      "",
      "```html",
      "<!DOCTYPE html>",
      "<html lang=\"ru\">",
      "<head><title>Cyberpunk Calculator</title></head>",
      "<body></body>",
      "</html>",
      "```",
    ].join("\n");
    const { container } = assistant(message);
    expect(screen.getByTestId("code-viewer")).toBeInTheDocument();
    expect(container.textContent).toContain("Cyberpunk Calculator");
  });
});

describe("parser, filenames, and language coverage", () => {
  it("supports every required language and tilde fences", () => {
    for (const language of [
      "plaintext", "txt", "markdown", "json", "yaml", "xml", "html", "css", "javascript",
      "typescript", "jsx", "tsx", "python", "powershell", "bash", "shell", "lua", "c", "cpp",
      "csharp", "java", "sql", "rust", "go",
    ]) {
      const segment = parseMessageSegments(`~~~${language}\nvalue\n~~~`).find(({ type }) => type === "code");
      expect(segment?.type).toBe("code");
    }
    expect(CODE_VIEWER_SUPPORTED_LANGUAGES).toEqual(expect.arrayContaining([
      "plaintext", "markdown", "json", "yaml", "xml", "html", "css", "javascript", "typescript",
      "jsx", "tsx", "python", "powershell", "bash", "shell", "lua", "c", "cpp", "csharp", "java",
      "sql", "rust", "go",
    ]));
  });

  it("uses a safe basename from metadata or the first source line", () => {
    const metadata = parseMessageSegments("```python filename=\"../../tools/build.py\"\nprint(1)\n```")[0];
    const firstLine = parseMessageSegments("```csharp\n// QuantumCircuit.cs\nclass Q {}\n```")[0];
    expect(metadata.filename).toBe("build.py");
    expect(firstLine.filename).toBe("QuantumCircuit.cs");
  });

  it("uses a sanitized filename supplied by message metadata", () => {
    render(<MessageCodeContent content={"```csharp\nclass Q {}\n```"} filename={"C:\\unsafe\\QuantumCircuit.cs"} />);
    expect(screen.getByTestId("code-viewer")).toHaveAttribute("data-filename", "QuantumCircuit.cs");
  });
  it("caps normal message height and preserves horizontal scrolling", () => {
    assistant(`\`\`\`go\n${"fmt.Println(\"long\")\n".repeat(80)}\`\`\``);
    expect(screen.getByTestId("code-scroll")).toHaveClass("max-h-[26rem]", "overflow-auto");
    expect(screen.getByTestId("code-scroll").firstElementChild).toHaveClass("min-w-max");
  });
});
