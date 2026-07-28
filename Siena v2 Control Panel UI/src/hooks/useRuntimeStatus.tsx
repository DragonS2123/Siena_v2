import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { sienaClient } from "../api/sienaClient";
import type { RuntimeStatus } from "../api/types";

interface RuntimeStatusContextValue {
  status: RuntimeStatus | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

const RuntimeStatusContext = createContext<RuntimeStatusContextValue | null>(null);

const POLL_MS = 5000;

/**
 * Single app-wide GET /api/runtime/status polling loop (every 5s). Every
 * screen that needs runtime status (Chat header, Composer, Inspector,
 * ModelStatusWidget, Runtime view, Debug view) reads this same context
 * instead of running its own interval — previously each of those six call
 * sites polled independently, producing dozens of duplicate requests.
 */
export function RuntimeStatusProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<RuntimeStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await sienaClient.getRuntimeStatus();
      setStatus(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load runtime status");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  return (
    <RuntimeStatusContext.Provider value={{ status, loading, error, refresh }}>
      {children}
    </RuntimeStatusContext.Provider>
  );
}

export function useRuntimeStatus(): RuntimeStatusContextValue {
  const ctx = useContext(RuntimeStatusContext);
  if (!ctx) throw new Error("useRuntimeStatus() must be used within a RuntimeStatusProvider");
  return ctx;
}
