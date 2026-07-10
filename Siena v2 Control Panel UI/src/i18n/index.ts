// Real UI localization (not decorative). Adding a new language means:
//   1. Add src/i18n/locales/<code>.json with the same keys as en.json.
//   2. Add one entry to LOCALES below.
//   3. Add "<code>" to Locale (types.ts) and SUPPORTED_LOCALES.
//   4. Add "<code>" to the backend's _INTERFACE_LANGUAGES set (api/server.py)
//      if it should be persisted/validated server-side.
// Nothing else in the app needs to change — every component reads strings
// through t(), never a hardcoded literal.
import en from "./locales/en.json";
import ru from "./locales/ru.json";
import { DEFAULT_LOCALE, type Locale, type TranslationDict } from "./types";

export type { Locale } from "./types";
export { DEFAULT_LOCALE, SUPPORTED_LOCALES } from "./types";

const LOCALES: Record<Locale, TranslationDict> = { en, ru };

const PARAM_RE = /\{(\w+)\}/g;

/**
 * Resolves `key` in `locale`'s dictionary, falling back to the default
 * locale (English) if missing there, and finally to the raw key itself if
 * it's missing everywhere — so a typo'd/unregistered key is visibly wrong
 * in the UI instead of silently rendering blank.
 */
export function translate(locale: Locale, key: string, params?: Record<string, string | number>): string {
  const template = LOCALES[locale]?.[key] ?? LOCALES[DEFAULT_LOCALE]?.[key] ?? key;
  if (!params) return template;
  return template.replace(PARAM_RE, (match, name: string) =>
    Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match,
  );
}

export function isSupportedLocale(value: string): value is Locale {
  return value in LOCALES;
}
