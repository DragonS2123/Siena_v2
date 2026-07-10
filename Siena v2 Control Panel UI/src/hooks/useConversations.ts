import { useCallback, useEffect, useState } from "react";
import { sienaClient } from "../api/sienaClient";
import type { ConversationSummary } from "../api/types";

interface UseConversationsResult {
  conversations: ConversationSummary[];
  activeConversationId: string | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  createConversation: (title?: string) => Promise<string>;
  activateConversation: (id: string) => Promise<void>;
}

/** Wraps GET/POST /api/conversations + activate, replacing the SESSIONS mock array. */
export function useConversations(): UseConversationsResult {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await sienaClient.listConversations();
      setConversations(data.conversations);
      setActiveConversationId(data.active_conversation_id);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load conversations");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const createConversation = useCallback(
    async (title?: string) => {
      const { conversation_id } = await sienaClient.createConversation(title);
      await refresh();
      return conversation_id;
    },
    [refresh],
  );

  const activateConversation = useCallback(
    async (id: string) => {
      await sienaClient.activateConversation(id);
      setActiveConversationId(id);
      await refresh();
    },
    [refresh],
  );

  return {
    conversations,
    activeConversationId,
    loading,
    error,
    refresh,
    createConversation,
    activateConversation,
  };
}
