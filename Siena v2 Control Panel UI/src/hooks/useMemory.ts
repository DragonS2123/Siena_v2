import { useCallback, useEffect, useState } from "react";
import { sienaClient } from "../api/sienaClient";
import type { LongMemoryEntry, ShortMemoryEntry } from "../api/types";

interface UseMemoryResult<T> {
  entries: T[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

export function useShortMemory(): UseMemoryResult<ShortMemoryEntry> {
  const [entries, setEntries] = useState<ShortMemoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await sienaClient.getShortMemory();
      setEntries(data.entries);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load short memory");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { entries, loading, error, refresh };
}

export function useLongMemory(limit = 50): UseMemoryResult<LongMemoryEntry> {
  const [entries, setEntries] = useState<LongMemoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await sienaClient.getLongMemory(limit);
      setEntries(data.entries);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load long memory");
    } finally {
      setLoading(false);
    }
  }, [limit]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { entries, loading, error, refresh };
}
