import { useCallback, useEffect, useState } from "react";
import { sienaClient } from "../api/sienaClient";
import type { ResourcesStatusResponse } from "../api/types";

interface UseResourcesStatusResult {
  status: ResourcesStatusResponse | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

/** Resource/Model Lifecycle Phase 1 (HANDOFF_v2.md) — fetches
 * GET /api/resources/status on mount, manual refresh only (no polling —
 * this is a diagnostic panel a human opens deliberately, not a live meter). */
export function useResourcesStatus(): UseResourcesStatusResult {
  const [status, setStatus] = useState<ResourcesStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const result = await sienaClient.getResourcesStatus();
      setStatus(result);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load resource status");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { status, loading, error, refresh };
}
