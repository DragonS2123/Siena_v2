
  import { createRoot } from "react-dom/client";
  import App from "./app/App.tsx";
  import "./styles/index.css";

  // Flash-avoidance: apply the last-known UI preferences to <html> synchronously,
  // before React mounts and before GET /api/settings resolves. Same cache key/shape
  // as src/hooks/useUiPreferences.tsx, which takes over once it mounts.
  try {
    const cached = localStorage.getItem("siena_ui_preferences_cache_v1");
    if (cached) {
      const prefs = JSON.parse(cached);
      const root = document.documentElement;
      if (prefs.appearanceTheme === "light" || prefs.appearanceTheme === "dark") {
        root.dataset.theme = prefs.appearanceTheme;
      } else if (prefs.appearanceTheme === "system") {
        root.dataset.theme = window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
      }
      if (prefs.accentColor) root.dataset.accent = prefs.accentColor;
      if (prefs.uiFontSize) root.dataset.fontSize = prefs.uiFontSize;
      if (prefs.uiDensity) root.dataset.density = prefs.uiDensity;
      if (prefs.interfaceLanguage) root.lang = prefs.interfaceLanguage;
    }
  } catch {
    // Corrupt/unavailable cache — UiPreferencesProvider will apply real
    // settings (or defaults) once it mounts regardless.
  }

  createRoot(document.getElementById("root")!).render(<App />);
