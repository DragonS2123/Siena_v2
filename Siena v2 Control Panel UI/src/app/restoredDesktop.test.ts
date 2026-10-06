import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const appSource = readFileSync(resolve(__dirname, "App.tsx"), "utf8");
const electronSource = readFileSync(resolve(__dirname, "../../electron/main.cjs"), "utf8");
const codeViewerSource = readFileSync(resolve(__dirname, "CodeViewer.tsx"), "utf8");

describe("restored original Desktop surface", () => {
  it("keeps the original core pages and panels", () => {
    for (const view of ["chat", "tool-trace", "short-memory", "long-memory", "insights", "logs", "models", "runtime", "debug", "settings"]) {
      expect(appSource).toContain(`id: "${view}"`);
    }
    expect(appSource).toContain("function Composer(");
    expect(appSource).toContain("function MessageBubble(");
    expect(appSource).toContain('import { GenerationLimitNotice, MessageCodeContent } from "./CodeViewer"');
    expect(codeViewerSource).toContain("export function parseMessageSegments(");
    expect(codeViewerSource).toContain("export function CodeViewer(");
    expect(appSource).toContain("function DesktopTitlebar(");
    expect(appSource).toContain('<SplashScreen onDone={finishSplash} />');
  });

  it("does not expose removed subsystem navigation or sidebar cards", () => {
    expect(appSource).not.toMatch(/id: "(?:presence|computer|remote|games|cyberpunk|nucleares)"/i);
    expect(appSource).not.toContain("<PresenceCard");
    expect(appSource).not.toContain("<ComputerStatusCard");
    expect(appSource).not.toContain("<RemoteGatewaySettings");
  });

  it("keeps a sandboxed preload bridge and persistent window geometry", () => {
    expect(electronSource).toContain('preload: path.join(__dirname, "preload.cjs")');
    expect(electronSource).toContain("contextIsolation: true");
    expect(electronSource).toContain("nodeIntegration: false");
    expect(electronSource).toContain("getNormalBounds()");
    expect(electronSource).toContain("window-state.json");
    expect(electronSource).toContain("frame: false");
    expect(electronSource).toContain('ipcMain.handle("siena:code:save"');
    expect(electronSource).toContain("showOverwriteConfirmation: true");
  });
});
