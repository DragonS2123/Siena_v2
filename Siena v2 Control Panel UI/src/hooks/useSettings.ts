import { useCallback, useEffect, useState } from "react";
import { sienaClient } from "../api/sienaClient";
import type { SettingsPayload } from "../api/types";

interface UseSettingsResult {
  settings: SettingsPayload | null;
  loading: boolean;
  saving: boolean;
  error: string | null;
  saveError: string | null;
  refresh: () => Promise<void>;
  save: (update: Partial<SettingsPayload>) => Promise<boolean>;
}

export function useSettings(): UseSettingsResult {
  const [settings, setSettings] = useState<SettingsPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

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
    setSaveError(null);
    try {
      const data = await sienaClient.updateSettings(update);
      setSettings(data);
      return true;
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Failed to save settings");
      return false;
    } finally {
      setSaving(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { settings, loading, saving, error, saveError, refresh, save };
}
