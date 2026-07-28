import { useCallback, useEffect, useState } from "react";
import { sienaClient } from "../api/sienaClient";
import type { VoiceStatusResponse } from "../api/types";

interface UseVoiceStatusResult {
  status: VoiceStatusResponse | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

/** Fetches GET /api/voice/status once, plus optional polling. Used by the
 * Composer mic button (Phase 2, HANDOFF_v2.md) to decide whether STT is
 * actually usable — if the request fails, stt_available is treated as false
 * (mic stays disabled) rather than assumed true. */
export function useVoiceStatus(pollMs = 0): UseVoiceStatusResult {
  const [status, setStatus] = useState<VoiceStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const result = await sienaClient.getVoiceStatus();
      setStatus(result);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load voice status");
      setStatus(null);
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

  return { status, loading, error, refresh };
}
