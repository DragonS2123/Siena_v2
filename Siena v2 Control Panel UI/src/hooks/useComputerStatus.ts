import { useCallback, useEffect, useState } from "react";
import { sienaClient } from "../api/sienaClient";
import type { ComputerStatusResponse } from "../api/types";

// Clamp what the UI will actually poll at, whatever the persisted setting
// says — the backend validates 2..3600 too, this is just belt & braces so a
// hand-edited settings.json can't make the app hammer the endpoint.
const MIN_POLL_MS = 2000;
const DEFAULT_POLL_MS = 10000;

interface UseComputerStatusResult {
  status: ComputerStatusResponse | null;
  loading: boolean;
  error: string | null;
  /** True when the layer reports enabled=false — callers hide their UI
   * instead of rendering stale/empty metrics. */
  disabled: boolean;
  refresh: () => Promise<void>;
}

/**
 * Computer Awareness Layer (0.2.3, Phase 1) — polls the read-only
 * GET /api/computer/status at computer_status_poll_seconds (default 10s;
 * pass the current settings value in). A failed request keeps the previous
 * snapshot and records the error instead of blanking the card, and polling
 * simply keeps trying — the backend being down is exactly the kind of state
 * this layer exists to surface.
 */
export function useComputerStatus(pollSeconds?: number, enabled = true): UseComputerStatusResult {
  const [status, setStatus] = useState<ComputerStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await sienaClient.getComputerStatus();
      setStatus(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load computer status");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    refresh();
    const pollMs = Math.max(MIN_POLL_MS, (pollSeconds ?? DEFAULT_POLL_MS / 1000) * 1000);
    const id = setInterval(refresh, pollMs);
    return () => clearInterval(id);
  }, [refresh, pollSeconds, enabled]);

  return {
    status,
    loading,
    error,
    disabled: status?.enabled === false,
    refresh,
  };
}
