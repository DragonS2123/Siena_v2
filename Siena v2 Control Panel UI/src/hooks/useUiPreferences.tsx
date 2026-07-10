import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { sienaClient } from "../api/sienaClient";
import type { SettingsPayload } from "../api/types";
import { DEFAULT_LOCALE, isSupportedLocale, translate, type Locale } from "../i18n";

export type AppearanceTheme = "dark" | "light" | "system";
export type AccentColor = "sienna" | "slate" | "forest" | "amber" | "violet";
export type UiFontSize = "small" | "default" | "large";
export type UiDensity = "comfortable" | "compact";
export type StartupPage = "chat" | "runtime" | "settings";
export type CodeFontSize = "small" | "default" | "large";
export type PreferredResponseLanguage = "auto" | "ru" | "en";

export interface UiPreferences {
  appearanceTheme: AppearanceTheme;
  accentColor: AccentColor;
  uiFontSize: UiFontSize;
  uiDensity: UiDensity;
  showMessageTimestamps: boolean;
  showTypingAnimation: boolean;
  copyBeforeClearChat: boolean;
  startupPage: StartupPage;
  codeFontSize: CodeFontSize;
  codeLineWrap: boolean;
  codeSyntaxHighlighting: boolean;
  codeShowLineNumbers: boolean;
  codeShowLanguageBadge: boolean;
  codeShowCopyButton: boolean;
  codeShowCollapseButton: boolean;
  codeShowSaveButton: boolean;
  showExperimentalStreamButton: boolean;
  preferredResponseLanguage: PreferredResponseLanguage;
  /** Application UI language — separate from stt_language (voice input) and
   * preferredResponseLanguage (soft model reply preference) above. */
  interfaceLanguage: Locale;
}

const DEFAULT_PREFS: UiPreferences = {
  appearanceTheme: "dark",
  accentColor: "sienna",
  uiFontSize: "default",
  uiDensity: "comfortable",
  showMessageTimestamps: true,
  showTypingAnimation: true,
  copyBeforeClearChat: false,
  startupPage: "chat",
  codeFontSize: "default",
  codeLineWrap: false,
  codeSyntaxHighlighting: true,
  codeShowLineNumbers: true,
  codeShowLanguageBadge: true,
  codeShowCopyButton: true,
  codeShowCollapseButton: true,
  codeShowSaveButton: true,
  showExperimentalStreamButton: true,
  preferredResponseLanguage: "auto",
  interfaceLanguage: DEFAULT_LOCALE,
};

// Kept in sync with the flash-avoidance snippet in main.tsx (same key,
// same subset of fields — the only ones that affect document-root
// CSS/zoom before React has even mounted).
const CACHE_KEY = "siena_ui_preferences_cache_v1";

function resolveTheme(theme: AppearanceTheme): "dark" | "light" {
  if (theme === "dark" || theme === "light") return theme;
  if (typeof window === "undefined" || !window.matchMedia) return "dark";
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function applyToDocument(prefs: UiPreferences) {
  const root = document.documentElement;
  root.dataset.theme = resolveTheme(prefs.appearanceTheme);
  root.dataset.accent = prefs.accentColor;
  root.dataset.fontSize = prefs.uiFontSize;
  root.dataset.density = prefs.uiDensity;
  root.lang = prefs.interfaceLanguage;
}

function cachePrefs(prefs: UiPreferences) {
  try {
    localStorage.setItem(
      CACHE_KEY,
      JSON.stringify({
        appearanceTheme: prefs.appearanceTheme,
        accentColor: prefs.accentColor,
        uiFontSize: prefs.uiFontSize,
        uiDensity: prefs.uiDensity,
        interfaceLanguage: prefs.interfaceLanguage,
      }),
    );
  } catch {
    // localStorage unavailable (e.g. private mode) — flash-avoidance on the
    // next launch is best-effort only, never required for correctness.
  }
}

function loadCachedPrefs(): UiPreferences {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    if (raw) return { ...DEFAULT_PREFS, ...JSON.parse(raw) };
  } catch {
    // ignore corrupt/unavailable cache — fall through to defaults
  }
  return DEFAULT_PREFS;
}

const ACCENT_COLORS: AccentColor[] = ["sienna", "slate", "forest", "amber", "violet"];

function fromSettingsPayload(data: SettingsPayload): UiPreferences {
  const theme = data.appearance_theme;
  const accent = data.accent_color;
  const fontSize = data.ui_font_size;
  const density = data.ui_density;
  const startup = data.startup_page;
  const codeFontSize = data.code_font_size;
  return {
    appearanceTheme: theme === "light" || theme === "system" ? theme : "dark",
    accentColor: ACCENT_COLORS.includes(accent as AccentColor) ? (accent as AccentColor) : "sienna",
    uiFontSize: fontSize === "small" || fontSize === "large" ? fontSize : "default",
    uiDensity: density === "compact" ? "compact" : "comfortable",
    showMessageTimestamps: data.show_message_timestamps ?? true,
    showTypingAnimation: data.show_typing_animation ?? true,
    copyBeforeClearChat: data.copy_before_clear_chat ?? false,
    startupPage: startup === "runtime" || startup === "settings" ? startup : "chat",
    codeFontSize: codeFontSize === "small" || codeFontSize === "large" ? codeFontSize : "default",
    codeLineWrap: data.code_line_wrap ?? false,
    codeSyntaxHighlighting: data.code_syntax_highlighting ?? true,
    codeShowLineNumbers: data.code_show_line_numbers ?? true,
    codeShowLanguageBadge: data.code_show_language_badge ?? true,
    codeShowCopyButton: data.code_show_copy_button ?? true,
    codeShowCollapseButton: data.code_show_collapse_button ?? true,
    codeShowSaveButton: data.code_show_save_button ?? true,
    showExperimentalStreamButton: data.show_experimental_stream_button ?? true,
    preferredResponseLanguage: data.preferred_response_language === "ru" || data.preferred_response_language === "en" ? data.preferred_response_language : "auto",
    interfaceLanguage: isSupportedLocale(data.interface_language) ? data.interface_language : DEFAULT_LOCALE,
  };
}

interface UiPreferencesContextValue {
  prefs: UiPreferences;
  loading: boolean;
  /** True once the real GET /api/settings response has been applied at
   * least once (vs. still running on cache/defaults) — AppShell waits on
   * this before honoring startup_page, so it doesn't route by cache alone. */
  settingsLoaded: boolean;
  saveError: string | null;
  save: (patch: Partial<SettingsPayload>) => Promise<boolean>;
  /** Real i18n lookup (src/i18n) for the current interfaceLanguage — falls
   * back to English, then to the raw key itself, so a missing translation
   * is visibly wrong rather than blank. */
  t: (key: string, params?: Record<string, string | number>) => string;
}

const UiPreferencesContext = createContext<UiPreferencesContextValue | null>(null);

/**
 * App-wide UI/display preferences (Settings > Appearance, Settings Pass 2).
 * Fetches GET /api/settings once, applies theme/accent/font-size/density to
 * document.documentElement's data-* attributes (consumed by
 * src/styles/ui-preferences.css), and caches the last-known values to
 * localStorage so main.tsx can apply them synchronously on the next launch
 * before this provider has even mounted (avoids a flash of the wrong theme).
 * If the settings fetch fails, defaults/cache are kept so the app stays usable.
 */
export function UiPreferencesProvider({ children }: { children: ReactNode }) {
  const [prefs, setPrefs] = useState<UiPreferences>(loadCachedPrefs);
  const [loading, setLoading] = useState(true);
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    applyToDocument(prefs);
  }, [prefs]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await sienaClient.getSettings();
        if (cancelled) return;
        const next = fromSettingsPayload(data);
        setPrefs(next);
        cachePrefs(next);
      } catch {
        // Keep whatever we already have (cache or defaults) — a failed
        // settings fetch must never block the app from being usable.
      } finally {
        if (!cancelled) {
          setLoading(false);
          setSettingsLoaded(true);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (prefs.appearanceTheme !== "system" || typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    const handler = () => applyToDocument(prefs);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, [prefs]);

  const save = useCallback(async (patch: Partial<SettingsPayload>) => {
    setSaveError(null);
    try {
      const data = await sienaClient.updateSettings(patch);
      const next = fromSettingsPayload(data);
      setPrefs(next);
      cachePrefs(next);
      return true;
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Failed to save settings");
      return false;
    }
  }, []);

  const locale = prefs.interfaceLanguage;
  const t = useCallback(
    (key: string, params?: Record<string, string | number>) => translate(locale, key, params),
    [locale],
  );

  const value = useMemo(
    () => ({ prefs, loading, settingsLoaded, saveError, save, t }),
    [prefs, loading, settingsLoaded, saveError, save, t],
  );

  return <UiPreferencesContext.Provider value={value}>{children}</UiPreferencesContext.Provider>;
}

export function useUiPreferences(): UiPreferencesContextValue {
  const ctx = useContext(UiPreferencesContext);
  if (!ctx) throw new Error("useUiPreferences() must be used within a UiPreferencesProvider");
  return ctx;
}
