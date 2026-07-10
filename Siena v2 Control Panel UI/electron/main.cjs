// Minimal Electron shell for the Siena v2 Control Panel UI.
//
// This process only opens a window and loads the Vite production build
// (../dist/index.html). It contains no application logic: the UI itself is
// the React renderer, and it talks to the Python backend (api/server.py)
// directly over HTTP/WebSocket — this file never starts, proxies, or knows
// about that backend. Restored after a Figma Make export overwrote the
// previous electron/ directory; kept intentionally small.

const { app, BrowserWindow, session } = require("electron");
const path = require("node:path");

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

  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
