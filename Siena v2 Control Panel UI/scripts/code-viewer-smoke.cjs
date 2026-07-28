"use strict";

const fs = require("node:fs");
const path = require("node:path");

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function main() {
  const [, , screenshotDir] = process.argv;
  if (!screenshotDir) throw new Error("Usage: node code-viewer-smoke.cjs <screenshot-dir>");

  const targets = await (await fetch("http://127.0.0.1:9333/json/list")).json();
  const target = targets.find((item) => item.type === "page");
  if (!target) throw new Error("No Electron renderer target");

  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.onopen = resolve;
    socket.onerror = reject;
  });

  let nextId = 1;
  const pending = new Map();
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (!message.id) return;
    const entry = pending.get(message.id);
    if (!entry) return;
    pending.delete(message.id);
    if (message.error) entry.reject(new Error(JSON.stringify(message.error)));
    else entry.resolve(message.result);
  };

  function send(method, params = {}) {
    const id = nextId++;
    socket.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
  }

  await send("Page.enable");
  await send("Runtime.enable");
  await send("Page.bringToFront");

  let summary;
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const result = await send("Runtime.evaluate", {
      expression: `(() => {
        const viewers = [...document.querySelectorAll('[data-testid="code-viewer"]')];
        return {
          count: viewers.length,
          languages: viewers.map((node) => node.dataset.language),
          filenames: viewers.map((node) => node.dataset.filename),
          literalBackticks: document.body.innerText.includes(String.fromCharCode(96).repeat(3)),
          bridge: typeof window.sienaDesktop?.saveCodeFile,
          buttons: viewers[0]
            ? [...viewers[0].querySelectorAll("button")].map((button) => button.getAttribute("aria-label"))
            : [],
        };
      })()`,
      returnByValue: true,
    });
    summary = result.result.value;
    if (summary?.count === 12) break;
    await wait(250);
  }

  const expectedLanguages = [
    "html", "css", "javascript", "typescript", "python", "csharp",
    "json", "powershell", "lua", "plaintext", "bash", "go",
  ];
  if (!summary || summary.count !== expectedLanguages.length) {
    throw new Error(`Expected 12 Code Viewers: ${JSON.stringify(summary)}`);
  }
  if (JSON.stringify(summary.languages) !== JSON.stringify(expectedLanguages)) {
    throw new Error(`Language mismatch: ${JSON.stringify(summary)}`);
  }
  if (summary.literalBackticks || summary.bridge !== "function") {
    throw new Error(`Renderer contract failed: ${JSON.stringify(summary)}`);
  }
  for (const requiredAction of ["copy", "save", "expand"]) {
    if (!summary.buttons.includes(requiredAction)) {
      throw new Error(`Missing ${requiredAction}: ${JSON.stringify(summary)}`);
    }
  }

  const screenshotLanguages = ["html", "csharp", "powershell", "plaintext"];
  for (const language of screenshotLanguages) {
    await send("Runtime.evaluate", {
      expression: `document.querySelector('[data-language="${language}"]').scrollIntoView({block: "center"}); true`,
      returnByValue: true,
    });
    await wait(350);
    const screenshot = await send("Page.captureScreenshot", { format: "jpeg", quality: 55, fromSurface: true, clip: { x: 0, y: 0, width: 1320, height: 800, scale: 0.5 } });
    fs.writeFileSync(
      path.join(screenshotDir, `${language}.jpg`),
      Buffer.from(screenshot.data, "base64"),
    );
  }

  await send("Runtime.evaluate", {
    expression: `document.querySelector('[data-language="csharp"] button[aria-label="expand"]').click(); true`,
    returnByValue: true,
  });
  await wait(250);
  const expandedResult = await send("Runtime.evaluate", {
    expression: `({
      dialog: !!document.querySelector('[role="dialog"]'),
      expandedScroll: !!document.querySelector('[data-testid="expanded-code-scroll"]'),
      focused: document.activeElement?.getAttribute("aria-label"),
    })`,
    returnByValue: true,
  });
  const expanded = expandedResult.result.value;
  if (!expanded.dialog || !expanded.expandedScroll || expanded.focused !== "close") {
    throw new Error(`Expand failed: ${JSON.stringify(expanded)}`);
  }

  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape" });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape" });
  await wait(250);
  const closedResult = await send("Runtime.evaluate", {
    expression: `!document.querySelector('[role="dialog"]')`,
    returnByValue: true,
  });
  if (!closedResult.result.value) throw new Error("Escape did not close expanded viewer");

  console.log(JSON.stringify({
    ...summary,
    expanded,
    escapeClosed: true,
    screenshots: screenshotLanguages,
  }));
  socket.close();
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
