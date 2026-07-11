// Electron shell for the Siena v2 Control Panel UI.
//
// The renderer is still the whole application: it talks to the Python
// backend (api/server.py) directly over HTTP/WebSocket, and this process
// never proxies that traffic. What lives here since 0.2.2 is the Desktop
// Presence Shell: a Windows tray icon, minimize/close-to-tray, a backend
// online/offline indicator, and safe Start/Stop backend actions that call
// the repo's own start_backend.bat / stop_backend.bat (stop_backend.bat
// only ever kills the uvicorn api.server:app process listening on port
// 8000 — never "all python.exe"; see that script).
//
// Settings (enable_tray_icon / minimize_to_tray / close_to_tray /
// show_tray_notifications / auto_start_backend_with_desktop) are managed by
// the Settings UI -> POST /api/settings -> storage/settings.json, and this
// process reads that file directly:
//   - minimize_to_tray / close_to_tray / show_tray_notifications are
//     re-read on every window event, so they apply live, no restart;
//   - enable_tray_icon and auto_start_backend_with_desktop are read once at
//     startup — changing them requires restarting the desktop app (the
//     Settings UI says so).
// There is deliberately NO Windows autostart here: auto_start_backend_with_desktop
// only starts the backend together with a desktop app the human launched.

const { app, BrowserWindow, Menu, Tray, nativeImage, session } = require("electron");
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");

const REPO_ROOT = path.join(__dirname, "..", "..");
const SETTINGS_PATH = path.join(REPO_ROOT, "storage", "settings.json");
const HEALTH_POLL_MS = 10_000;

const SHELL_SETTING_DEFAULTS = {
  enable_tray_icon: true,
  minimize_to_tray: true,
  close_to_tray: true,
  show_tray_notifications: false,
  auto_start_backend_with_desktop: false,
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
let backendActionInFlight = null; // "start" | "stop" | null

function readShellSettings() {
  try {
    // utf-8 with a possible BOM (settings_store writes plain utf-8, but a
    // human may re-save the file in Notepad) — strip it before JSON.parse.
    const raw = fs.readFileSync(SETTINGS_PATH, "utf-8").replace(/^﻿/, "");
    const data = JSON.parse(raw);
    const settings = { ...SHELL_SETTING_DEFAULTS };
    for (const key of Object.keys(SHELL_SETTING_DEFAULTS)) {
      if (typeof data[key] === typeof SHELL_SETTING_DEFAULTS[key]) settings[key] = data[key];
    }
    return settings;
  } catch {
    // Missing/corrupt settings.json must never break the shell — same
    // contract as the backend's own settings_store.load().
    return { ...SHELL_SETTING_DEFAULTS };
  }
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

function runBackendScript(scriptName) {
  // Absolute path on purpose: bare-name resolution via the working
  // directory silently fails when NoDefaultCurrentDirectoryInExePath is set
  // in the inherited environment (found during the 0.2.2 Electron smoke) —
  // an absolute path doesn't depend on cmd's cwd search at all.
  //
  // Detached + ignored stdio so the spawned backend (or the stop script)
  // never ties its lifetime to this Electron process — quitting the desktop
  // app must not take a deliberately-started backend down with it.
  const child = spawn("cmd.exe", ["/c", path.join(REPO_ROOT, scriptName)], {
    cwd: REPO_ROOT,
    detached: true,
    stdio: "ignore",
    windowsHide: true,
  });
  child.unref();
}

function refreshBackendStatus({ pollAfterAction = false } = {}) {
  checkBackendHealth((online) => {
    const changed = online !== backendOnline;
    backendOnline = online;
    if (!pollAfterAction || changed) backendActionInFlight = null;
    if (changed || pollAfterAction) updateTray();
  });
}

function startBackend() {
  if (backendOnline === true || backendActionInFlight) return; // never spawn a second backend
  backendActionInFlight = "start";
  updateTray();
  runBackendScript("start_backend.bat");
  // uvicorn takes a few seconds to bind — poll a handful of times so the
  // tray label flips to Online without waiting for the regular 10s tick.
  for (const delay of [2000, 4000, 7000, 11000, 16000]) {
    setTimeout(() => refreshBackendStatus({ pollAfterAction: true }), delay);
  }
}

function stopBackend() {
  if (backendOnline !== true || backendActionInFlight) return;
  backendActionInFlight = "stop";
  updateTray();
  runBackendScript("stop_backend.bat"); // safe: kills only uvicorn api.server:app on port 8000
  for (const delay of [1500, 3000, 6000]) {
    setTimeout(() => refreshBackendStatus({ pollAfterAction: true }), delay);
  }
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
  // At most one balloon per session, and only when explicitly enabled —
  // "non-annoying" is the whole point of the presence layer.
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
      { label: "Start backend", enabled: backendOnline === false && !backendActionInFlight, click: startBackend },
      { label: "Stop backend", enabled: backendOnline === true && !backendActionInFlight, click: stopBackend },
      { type: "separator" },
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
  const win = new BrowserWindow({
    width: 1320,
    height: 860,
    backgroundColor: "#1a1714", // matches src/styles/theme.css --background, avoids a white flash on load
    autoHideMenuBar: true,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  mainWindow = win;

  // Desktop Presence Shell: X / minimize hide to the tray instead of
  // killing the app — but only while the tray exists and the (re-read live,
  // so no restart needed) setting says so. Quit from the tray menu really
  // quits: Electron fires 'before-quit' before window close in that path,
  // which flips app.isQuittingForReal below.
  win.on("close", (event) => {
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
  if (settings.auto_start_backend_with_desktop) {
    // Only ever starts the backend if it isn't already running — the health
    // probe decides, so two backends can never race for port 8000 from here.
    checkBackendHealth((online) => {
      backendOnline = online;
      updateTray();
      if (!online) startBackend();
    });
  }

  createWindow();

  // Smoke-test hook (SIENA_SHELL_DEBUG=1 only): exposes the shell's state
  // and actions to a --inspect session so the tray behavior can be driven
  // and asserted from an automated Electron smoke run. Never set in normal
  // use — without the env var this block does nothing at all.
  if (process.env.SIENA_SHELL_DEBUG === "1") {
    global.__sienaShellDebug = {
      showWindow,
      hideToTray,
      startBackend,
      stopBackend,
      getState: () => ({
        trayActive: tray !== null,
        backendOnline,
        backendActionInFlight,
        windowVisible: mainWindow ? mainWindow.isVisible() : null,
        settings: readShellSettings(),
      }),
      // Diagnostic only: run a backend script synchronously with captured
      // output, so a smoke run can see why a spawn failed.
      probeScript: (name, timeout = 6000) => {
        const r = spawnSync("cmd.exe", ["/c", name], { cwd: REPO_ROOT, timeout, encoding: "utf8", windowsHide: true });
        return { status: r.status, error: r.error ? String(r.error) : null, out: (r.stdout || "").slice(0, 800), err: (r.stderr || "").slice(0, 400) };
      },
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
