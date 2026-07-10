// Locale registry — adding a new UI language means adding a new entry here
// (a new locale file + one line in index.ts's LOCALES map), nothing else in
// the app needs to change. Keep this list in sync with the backend's
// _INTERFACE_LANGUAGES enum (api/server.py) — the server also only accepts
// locale ids that have a matching file registered here.
export type Locale = "en" | "ru";

export const SUPPORTED_LOCALES: Locale[] = ["en", "ru"];

export const DEFAULT_LOCALE: Locale = "en";

// Flat dot-namespaced key -> string map (e.g. "settings.appearance.title").
// Deliberately flat, not nested objects — keeps the fallback/lookup logic in
// index.ts a single object index instead of a recursive walk.
export type TranslationDict = Record<string, string>;
