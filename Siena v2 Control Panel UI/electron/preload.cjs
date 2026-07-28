"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("sienaDesktop", {
  minimize: () => ipcRenderer.invoke("siena:window:minimize"),
  toggleMaximize: () => ipcRenderer.invoke("siena:window:toggle-maximize"),
  close: () => ipcRenderer.invoke("siena:window:close"),
  isMaximized: () => ipcRenderer.invoke("siena:window:is-maximized"),
  onMaximizedChange: (listener) => {
    const handler = (_event, maximized) => listener(Boolean(maximized));
    ipcRenderer.on("siena:window:maximized-change", handler);
    return () => ipcRenderer.removeListener("siena:window:maximized-change", handler);
  },
});