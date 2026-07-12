import { useCallback, useEffect, useState } from "react";
import { sienaClient } from "../api/sienaClient";
import type { RemoteGatewayStatus } from "../api/types";

const POLL_MS = 5000;

interface UseRemoteGatewayResult {
  status: RemoteGatewayStatus | null;
  loading: boolean;
  error: string | null;
  /** True while a connect/disconnect/reconnect action is in flight. */
  acting: boolean;
  refresh: () => Promise<void>;
  connect: () => Promise<void>;
  disconnect: () => Promise<void>;
  reconnect: () => Promise<void>;
}

/**
 * Siena Remote Presence (0.2.3) — polls GET /api/remote-gateway/status while
 * the Remote Gateway settings section is mounted (mount-scoped, so the rest
 * of the app pays nothing for it) and wraps the three manual control
 * endpoints. Status payloads are already token-free server-side
 * (remote_gateway/agent.snapshot()); nothing here ever sees or stores a
 * credential.
 */
export function useRemoteGateway(): UseRemoteGatewayResult {
  const [status, setStatus] = useState<RemoteGatewayStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [acting, setActing] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const data = await sienaClient.getRemoteGatewayStatus();
      setStatus(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load remote gateway status");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  const runAction = useCallback(async (action: () => Promise<RemoteGatewayStatus>) => {
    setActing(true);
    setError(null);
    try {
      const data = await action();
      setStatus(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Remote gateway action failed");
    } finally {
      setActing(false);
    }
  }, []);

  const connect = useCallback(() => runAction(() => sienaClient.connectRemoteGateway()), [runAction]);
  const disconnect = useCallback(() => runAction(() => sienaClient.disconnectRemoteGateway()), [runAction]);
  const reconnect = useCallback(() => runAction(() => sienaClient.reconnectRemoteGateway()), [runAction]);

  return { status, loading, error, acting, refresh, connect, disconnect, reconnect };
}
