import { useCallback, useEffect, useState } from "react";
import { sienaClient } from "../api/sienaClient";
import type { TraceEvent } from "../api/types";

interface UseLogsResult {
  entries: TraceEvent[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

export function useLogs(limit = 200): UseLogsResult {
  const [entries, setEntries] = useState<TraceEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await sienaClient.getRecentLogs(limit);
      setEntries(data.entries);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load logs");
    } finally {
      setLoading(false);
    }
  }, [limit]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { entries, loading, error, refresh };
}
