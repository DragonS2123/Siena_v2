import { useCallback, useEffect, useState } from "react";
import { sienaClient } from "../api/sienaClient";
import type { CandidateMemoryEntry } from "../api/types";

export type InsightStatusFilter = "pending" | "later" | "rejected" | "promoted" | "all";

interface UseInsightsResult {
  entries: CandidateMemoryEntry[];
  loading: boolean;
  error: string | null;
  actionError: string | null;
  actingId: number | null;
  refresh: () => Promise<void>;
  promote: (id: number) => Promise<void>;
  reject: (id: number) => Promise<void>;
  later: (id: number) => Promise<void>;
  remove: (id: number) => Promise<void>;
}

// Candidate memories proposed by candidate_memory_create (core cognitive
// cycle: Observation -> Insight -> Reflection -> Candidate Memory). The model
// can only ever create a candidate here; promote/reject/later/delete are
// plain REST actions fired by an explicit human click in the Insights view
// (api/server.py, /api/insights*) — there is no tool the model can call to
// resolve its own candidates.
export function useInsights(status: InsightStatusFilter = "pending", limit = 50): UseInsightsResult {
  const [entries, setEntries] = useState<CandidateMemoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actingId, setActingId] = useState<number | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await sienaClient.listInsights(status === "all" ? "" : status, limit);
      setEntries(data.entries);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load insights");
    } finally {
      setLoading(false);
    }
  }, [status, limit]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const runAction = useCallback(
    async (id: number, action: () => Promise<unknown>) => {
      setActingId(id);
      setActionError(null);
      try {
        await action();
        await refresh();
      } catch (err) {
        setActionError(err instanceof Error ? err.message : "Action failed");
      } finally {
        setActingId(null);
      }
    },
    [refresh],
  );

  const promote = useCallback((id: number) => runAction(id, () => sienaClient.promoteInsight(id)), [runAction]);
  const reject = useCallback((id: number) => runAction(id, () => sienaClient.rejectInsight(id)), [runAction]);
  const later = useCallback((id: number) => runAction(id, () => sienaClient.laterInsight(id)), [runAction]);
  const remove = useCallback((id: number) => runAction(id, () => sienaClient.deleteInsight(id)), [runAction]);

  return { entries, loading, error, actionError, actingId, refresh, promote, reject, later, remove };
}
