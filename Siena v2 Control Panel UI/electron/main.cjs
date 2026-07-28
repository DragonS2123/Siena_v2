// Electron shell for the Siena v2 Control Panel UI.
//
// The renderer is still the whole application: it talks to the Python
// backend (api/server.py) directly over HTTP/WebSocket, and this process
// never proxies that traffic. What lives here since 0.2.2 is the Desktop
// Presence Shell: a Windows tray icon, minimize/close-to-tray, a backend
// online/offline indicator. Backend lifecycle belongs to the modular core
// and is intentionally not controlled from the desktop shell.

const { app, BrowserWindow, Menu, Tray, nativeImage, session, ipcMain, screen, dialog } = require("electron");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");

const HEALTH_POLL_MS = 10_000;

const SHELL_SETTING_DEFAULTS = {
  enable_tray_icon: true,
  minimize_to_tray: true,
  close_to_tray: true,
  show_tray_notifications: false,
};

// 32x32 sienna circle, generated once from the app's accent color (#c4644a)
// and embedded as base64 so the shell needs no binary asset or new
// dependency for the tray.
const TRAY_ICON_DATA_URL =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAA/ElEQVR42u2XsRGDMAxF6empww4pmCEjUOcYwGUK94zhAbxFJmAAr6GSyHeqOAvO4Fi6XHz3Ggz+30aW5ab5t4z2ut9aZEBGxCCWMPQs9rXfEO6RCXHIggCybgDqc/RuX0o8zswzohxA34xXhDta3pAhvCXQGN0Z8Tk16/fzsQuzGnOWCXINOcIHRuJYNuefhyvijIlwGBMU7b6EOGPC7+4O2j5QSjxhIo497SUZV3L2zCq4ZLKiLLaUFk+YiBoDF3xQwQAkg5Hy+VrBQMRwe7+WAavSgPgvEA9C8W0om4jEU7GKw0j8OFZRkKgoycSLUhVluZqLiYqr2U+3DwO5E1RV/XgJAAAAAElFTkSuQmCC";

let mainWindow = null;
let tray = null;
let backendOnline = null; // null = not checked yet, then true/false
let hiddenToTrayNotifiedThisSession = false;
let backendActionInFlight = null;
let windowStateSaveTimer = null;

function windowStatePath() {
  return path.join(app.getPath("userData"), "window-state.json");
}

function defaultWindowState() {
  return { bounds: { width: 1320, height: 860 }, maximized: false };
}

function readWindowState() {
  try {
    const parsed = JSON.parse(fs.readFileSync(windowStatePath(), "utf8"));
    const bounds = parsed?.bounds;
    if (!bounds || !Number.isFinite(bounds.width) || !Number.isFinite(bounds.height)) return defaultWindowState();
    const candidate = {
      x: Number.isFinite(bounds.x) ? bounds.x : undefined,
      y: Number.isFinite(bounds.y) ? bounds.y : undefined,
      width: Math.max(900, bounds.width),
      height: Math.max(620, bounds.height),
    };
    const visible = screen.getAllDisplays().some(({ workArea }) => {
      if (candidate.x === undefined || candidate.y === undefined) return true;
      return candidate.x < workArea.x + workArea.width && candidate.x + candidate.width > workArea.x &&
        candidate.y < workArea.y + workArea.height && candidate.y + candidate.height > workArea.y;
    });
    return { bounds: visible ? candidate : defaultWindowState().bounds, maximized: Boolean(parsed.maximized) };
  } catch {
    return defaultWindowState();
  }
}

function persistWindowState(win) {
  if (!win || win.isDestroyed()) return;
  try {
    fs.mkdirSync(app.getPath("userData"), { recursive: true });
    fs.writeFileSync(windowStatePath(), JSON.stringify({
      bounds: win.getNormalBounds(),
      maximized: win.isMaximized(),
    }, null, 2), "utf8");
  } catch {
    // Window-state persistence must never prevent the desktop from closing.
  }
}

function scheduleWindowStateSave(win) {
  clearTimeout(windowStateSaveTimer);
  windowStateSaveTimer = setTimeout(() => persistWindowState(win), 250);
}
function readShellSettings() {
  return { ...SHELL_SETTING_DEFAULTS };
}

function checkBackendHealth(callback) {
  const request = http.get(
    { host: "127.0.0.1", port: 8000, path: "/api/health", timeout: 1500 },
    (res) => {
      res.resume(); // drain
      callback(res.statusCode === 200);
    },
  );
  request.on("timeout", () => request.destroy());
  request.on("error", () => callback(false));
}

function refreshBackendStatus({ pollAfterAction = false } = {}) {
  checkBackendHealth((online) => {
    const changed = online !== backendOnline;
    backendOnline = online;
    if (!pollAfterAction || changed) backendActionInFlight = null;
    if (changed || pollAfterAction) updateTray();
  });
}

function showWindow() {
  if (!mainWindow) {
    createWindow();
    return;
  }
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

function hideToTray() {
  if (!mainWindow) return;
  mainWindow.hide();
  const settings = readShellSettings();
  // At most one balloon per session, and only when explicitly enabled.
  if (settings.show_tray_notifications && !hiddenToTrayNotifiedThisSession && tray && typeof tray.displayBalloon === "function") {
    hiddenToTrayNotifiedThisSession = true;
    tray.displayBalloon({
      title: "Siena v2",
      content: "Siena продолжает работать в трее / Siena keeps running in the tray.",
      iconType: "info",
    });
  }
}

function backendStatusLabel() {
  if (backendActionInFlight === "start") return "Backend: starting…";
  if (backendActionInFlight === "stop") return "Backend: stopping…";
  if (backendOnline === null) return "Backend: checking…";
  return backendOnline ? "Backend: Online" : "Backend: Offline";
}

function updateTray() {
  if (!tray) return;
  tray.setToolTip(`Siena v2 — ${backendStatusLabel().replace("Backend: ", "backend ")}`);
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: "Show Siena", click: showWindow },
      { label: "Hide to tray", click: hideToTray },
      { type: "separator" },
      { label: backendStatusLabel(), enabled: false },
      { label: "Quit", click: () => app.quit() },
    ]),
  );
}

function createTray() {
  tray = new Tray(nativeImage.createFromDataURL(TRAY_ICON_DATA_URL));
  // Single left click shows/focuses the window (double click comes through
  // as two clicks — same handler, harmless).
  tray.on("click", showWindow);
  updateTray();
  refreshBackendStatus();
  const timer = setInterval(refreshBackendStatus, HEALTH_POLL_MS);
  timer.unref?.();
}

function createWindow() {
  const savedState = readWindowState();
  const win = new BrowserWindow({
    ...savedState.bounds,
    minWidth: 900,
    minHeight: 620,
    frame: false,
    backgroundColor: "#1a1714", // matches src/styles/theme.css --background, avoids a white flash on load
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  mainWindow = win;
  if (savedState.maximized) win.maximize();
  win.on("move", () => scheduleWindowStateSave(win));
  win.on("resize", () => scheduleWindowStateSave(win));
  const publishMaximized = () => win.webContents.send("siena:window:maximized-change", win.isMaximized());
  win.on("maximize", publishMaximized);
  win.on("unmaximize", publishMaximized);

  // Desktop Presence Shell: X / minimize hide to the tray instead of
  // killing the app. Quit from the tray menu sets app.isQuittingForReal,
  // so the final close is not intercepted.
  win.on("close", (event) => {
    persistWindowState(win);
    if (app.isQuittingForReal || !tray) return;
    if (readShellSettings().close_to_tray) {
      event.preventDefault();
      hideToTray();
    }
  });
  win.on("minimize", (event) => {
    if (!tray) return;
    if (readShellSettings().minimize_to_tray) {
      event.preventDefault();
      hideToTray();
    }
  });
  win.on("closed", () => {
    if (mainWindow === win) mainWindow = null;
  });
  // SIENA_DEV_SERVER_URL points at the Vite dev server (e.g. during `npm run
  // dev` + `npm run desktop:dev`) so the renderer's origin is http://127.0.0.1:5173,
  // which the backend's CORS allowlist already accepts. Without it, falls back
  // to the built production bundle in dist/ (unchanged default behavior).
  const devUrl = process.env.SIENA_DEV_SERVER_URL;
  if (devUrl) {
    win.loadURL(devUrl);
  } else {
    win.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  }

  if (process.env.SIENA_OPEN_DEVTOOLS === "1") {
    win.webContents.openDevTools();
  }
}

app.isQuittingForReal = false;
app.on("before-quit", () => {
  app.isQuittingForReal = true;
});

app.whenReady().then(() => {
  const senderWindow = (event) => BrowserWindow.fromWebContents(event.sender);
  ipcMain.handle("siena:window:minimize", (event) => senderWindow(event)?.minimize());
  ipcMain.handle("siena:window:toggle-maximize", (event) => {
    const win = senderWindow(event);
    if (!win) return false;
    if (win.isMaximized()) win.unmaximize(); else win.maximize();
    return win.isMaximized();
  });
  ipcMain.handle("siena:window:close", (event) => senderWindow(event)?.close());
  ipcMain.handle("siena:window:is-maximized", (event) => senderWindow(event)?.isMaximized() ?? false);
  ipcMain.handle("siena:code:save", async (event, payload) => {
    const win = senderWindow(event);
    if (!win || !payload || typeof payload !== "object") throw new TypeError("Invalid code save request");
    const { content, suggestedName, language } = payload;
    if (typeof content !== "string" || Buffer.byteLength(content, "utf8") > 10 * 1024 * 1024) {
      throw new TypeError("Code content must be UTF-8 text smaller than 10 MiB");
    }
    const rawName = typeof suggestedName === "string" ? path.basename(suggestedName) : "code.txt";
    const safeName = rawName
      .replace(/^\.+/, "")
      .replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_")
      .trim()
      .slice(0, 120) || "code.txt";
    const extension = path.extname(safeName).slice(1).replace(/[^a-z0-9]/gi, "").slice(0, 10) || "txt";
    const result = await dialog.showSaveDialog(win, {
      title: "Save code",
      defaultPath: safeName,
      showOverwriteConfirmation: true,
      filters: [
        { name: typeof language === "string" ? `${language.toUpperCase()} source` : "Source code", extensions: [extension] },
        { name: "All files", extensions: ["*"] },
      ],
    });
    if (result.canceled || !result.filePath) return { saved: false, canceled: true };
    await fs.promises.writeFile(result.filePath, content, { encoding: "utf8", flag: "w" });
    return { saved: true, canceled: false, filename: path.basename(result.filePath) };
  });

  // Mic recording (Phase 2 STT UI, HANDOFF_v2.md) needs getUserMedia({audio:true})
  // to work from the renderer. Electron auto-approves every permission
  // request when no handler is registered at all, which is broader than
  // this app needs — this handler replaces that implicit "allow everything"
  // default with an explicit, narrower one: only audio-only 'media'
  // requests (no camera/video) are approved; anything else (geolocation,
  // notifications, clipboard-read, etc.) is denied.
  session.defaultSession.setPermissionRequestHandler((_webContents, permission, callback, details) => {
    const mediaTypes = details?.mediaTypes ?? [];
    if (permission === "media" && mediaTypes.includes("audio") && !mediaTypes.includes("video")) {
      callback(true);
      return;
    }
    callback(false);
  });

  const settings = readShellSettings();
  if (settings.enable_tray_icon) {
    createTray();
  }
  createWindow();

  // Smoke-test hook (SIENA_SHELL_DEBUG=1 only): exposes the shell's state
  // and actions to a --inspect session so the tray behavior can be driven
  // and asserted from an automated Electron smoke run. Never set in normal
  // use — without the env var this block does nothing at all.
  if (process.env.SIENA_SHELL_DEBUG === "1") {
    mainWindow?.webContents.setBackgroundThrottling(false);
    global.__sienaShellDebug = {
      showWindow,
      hideToTray,
      getState: () => ({
        trayActive: tray !== null,
        backendOnline,
        backendActionInFlight,
        windowVisible: mainWindow ? mainWindow.isVisible() : null,
        settings: readShellSettings(),
      }),
      closeWindow: () => mainWindow?.close(),
      minimizeWindow: () => mainWindow?.minimize(),
      quit: () => app.quit(),
      // Smoke runs drive the UI while the window is occluded by the user's
      // own windows — Chromium freezes rAF for occluded pages, which stalls
      // framer-motion view transitions and makes assertions flaky.
      setBackgroundThrottling: (value) => mainWindow?.webContents.setBackgroundThrottling(value),
    };
  }

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  // With close-to-tray active the window hides instead of closing, so this
  // only fires when the tray is disabled (or Quit already closed the
  // window) — classic "no tray, no window, no app" behavior.
  if (process.platform !== "darwin") app.quit();
});
