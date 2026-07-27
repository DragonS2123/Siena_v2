const { app, BrowserWindow } = require("electron");
const path = require("path");

function createWindow() {
  const window = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 900,
    minHeight: 620,
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });
  if (app.isPackaged || process.env.SIENA_LOAD_DIST === "1") {
    window.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  } else {
    window.loadURL("http://127.0.0.1:5173");
  }
}

app.whenReady().then(createWindow);
app.on("window-all-closed", () => app.quit());
