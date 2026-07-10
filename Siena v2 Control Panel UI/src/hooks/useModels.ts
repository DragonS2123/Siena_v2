import { useCallback, useEffect, useState } from "react";
import { sienaClient } from "../api/sienaClient";
import type { ModelsResponse } from "../api/types";

interface UseModelsResult {
  data: ModelsResponse | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  /** Manual active chat model switch (Phase 4E) — explicit human action only. */
  setActiveChatModel: (model: string) => Promise<void>;
  switching: boolean;
  activeModelError: string | null;
}

/** Fetches GET /api/models (model registry, Phase 4D) once, plus optional polling. */
export function useModels(pollMs = 0): UseModelsResult {
  const [data, setData] = useState<ModelsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [switching, setSwitching] = useState(false);
  const [activeModelError, setActiveModelError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const result = await sienaClient.getModels();
      setData(result);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load models");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    if (!pollMs) return;
    const id = setInterval(refresh, pollMs);
    return () => clearInterval(id);
  }, [refresh, pollMs]);

  const setActiveChatModel = useCallback(
    async (model: string) => {
      setSwitching(true);
      setActiveModelError(null);
      try {
        await sienaClient.setActiveChatModel(model);
        await refresh();
      } catch (err) {
        setActiveModelError(err instanceof Error ? err.message : "Failed to switch active chat model");
      } finally {
        setSwitching(false);
      }
    },
    [refresh],
  );

  return { data, loading, error, refresh, setActiveChatModel, switching, activeModelError };
}
