import { useCallback, useEffect, useState } from "react";
import { sienaClient } from "../api/sienaClient";
import type { SettingsPayload } from "../api/types";

// Fired after every successful save so OTHER useSettings() instances can
// refetch — each call site holds its own copy of the settings, and before
// this event existed a component like the sidebar's PresenceCard kept
// rendering off a stale snapshot after the Settings screen changed a value
// (found by the Phase 2 Electron UI smoke). Not a state-management rewrite:
// just an invalidation ping.
export const SETTINGS_UPDATED_EVENT = "siena:settings-updated";

export type SettingsSaveState = "idle" | "dirty" | "saving" | "saved" | "error" | "restart_required";

interface UseSettingsResult {
  settings: SettingsPayload | null;
  loading: boolean;
  saving: boolean;
  error: string | null;
  saveError: string | null;
  saveState: SettingsSaveState;
  markDirty: () => void;
  refresh: () => Promise<void>;
  save: (update: Partial<SettingsPayload>) => Promise<boolean>;
}

export function useSettings(): UseSettingsResult {
  const [settings, setSettings] = useState<SettingsPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SettingsSaveState>("idle");

  const refresh = useCallback(async () => {
    try {
      const data = await sienaClient.getSettings();
      setSettings(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load settings");
    } finally {
      setLoading(false);
    }
  }, []);

  const save = useCallback(async (update: Partial<SettingsPayload>) => {
    setSaving(true);
    setSaveState("saving");
    setSaveError(null);
    try {
      const data = await sienaClient.updateSettings(update);
      setSettings(data);
      setSaveState(data.restart_required.length > 0 ? "restart_required" : "saved");
      window.dispatchEvent(new Event(SETTINGS_UPDATED_EVENT));
      return true;
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Failed to save settings");
      setSaveState("error");
      return false;
    } finally {
      setSaving(false);
    }
  }, []);
  useEffect(() => {
    refresh();
    // Refetch when any other useSettings() instance saves — see
    // SETTINGS_UPDATED_EVENT above.
    window.addEventListener(SETTINGS_UPDATED_EVENT, refresh);
    return () => window.removeEventListener(SETTINGS_UPDATED_EVENT, refresh);
  }, [refresh]);

  const markDirty = useCallback(() => setSaveState("dirty"), []);

  return { settings, loading, saving, error, saveError, saveState, markDirty, refresh, save };
}

