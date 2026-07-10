import { useCallback, useEffect, useRef, useState } from "react";
import { sienaClient } from "../api/sienaClient";
import type { PresenceStatus } from "../api/types";

const POLL_MS = 5000;
// Meaningful user interaction is throttled client-side so a burst of
// clicks/keystrokes doesn't turn into a burst of network calls — presence
// only needs to know "the user is still around", not exactly when.
const PING_THROTTLE_MS = 30000;

interface UsePresenceResult {
  status: PresenceStatus | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  quiet: () => Promise<void>;
  wake: () => Promise<void>;
  say: () => Promise<{ message: string | null; throttled: boolean }>;
  dismissEvent: () => Promise<void>;
}

/**
 * Presence layer (0.2.1, Phase 1) — polls GET /api/presence/status every 5s
 * (same cadence as RuntimeStatusProvider) and pings the backend on
 * meaningful user interaction (click/keydown, throttled to once per 30s) so
 * idle-detection reflects real activity without spamming the network. Local,
 * lightweight, no LLM calls involved anywhere in this hook — see
 * presence/presence_service.py for the backend side.
 */
export function usePresence(): UsePresenceResult {
  const [status, setStatus] = useState<PresenceStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const lastPingRef = useRef(0);

  const refresh = useCallback(async () => {
    try {
      const data = await sienaClient.getPresenceStatus();
      setStatus(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load presence status");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  useEffect(() => {
    const ping = () => {
      const now = Date.now();
      if (now - lastPingRef.current < PING_THROTTLE_MS) return;
      lastPingRef.current = now;
      sienaClient
        .pingPresence()
        .then(setStatus)
        .catch(() => undefined);
    };
    window.addEventListener("pointerdown", ping);
    window.addEventListener("keydown", ping);
    return () => {
      window.removeEventListener("pointerdown", ping);
      window.removeEventListener("keydown", ping);
    };
  }, []);

  const quiet = useCallback(async () => {
    try {
      const data = await sienaClient.setPresenceQuiet();
      setStatus(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to enable quiet mode");
    }
  }, []);

  const wake = useCallback(async () => {
    try {
      const data = await sienaClient.setPresenceWake();
      setStatus(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to wake Siena");
    }
  }, []);

  const say = useCallback(async () => {
    try {
      const data = await sienaClient.sayPresence();
      return { message: data.message, throttled: data.throttled };
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to say something");
      return { message: null, throttled: false };
    }
  }, []);

  const dismissEvent = useCallback(async () => {
    try {
      const data = await sienaClient.dismissPresenceEvent();
      setStatus(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to dismiss event");
    }
  }, []);

  return { status, loading, error, refresh, quiet, wake, say, dismissEvent };
}
