/**
 * TanStack Query hooks for the conversations (chat UI) API.
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  listConversations,
  getConversationMessages,
  getConversation,
  searchConversations,
} from "@/api/index.ts";
import type { ConversationListParams } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Query keys
// ---------------------------------------------------------------------------

export const conversationKeys = {
  all: (butlerName: string) => ["conversations", butlerName] as const,
  list: (butlerName: string, params?: ConversationListParams) =>
    ["conversations", butlerName, "list", params] as const,
  detail: (butlerName: string, conversationId: string) =>
    ["conversations", butlerName, conversationId] as const,
  messages: (butlerName: string, conversationId: string) =>
    ["conversation-messages", butlerName, conversationId] as const,
  search: (butlerName: string, query: string) =>
    ["conversations", butlerName, "search", query] as const,
};

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

/**
 * Fetch a paginated list of conversations for a butler.
 * staleTime = 10 seconds (conversations update frequently during active chat).
 */
export function useConversations(
  butlerName: string,
  params?: ConversationListParams,
) {
  return useQuery({
    queryKey: conversationKeys.list(butlerName, params),
    queryFn: () => listConversations(butlerName, params),
    enabled: !!butlerName,
    staleTime: 10_000,
  });
}

/**
 * Fetch full detail for a single conversation (includes messages).
 * staleTime = 0: always refetch when switching conversations.
 */
export function useConversation(
  butlerName: string,
  conversationId: string | null,
) {
  return useQuery({
    queryKey: conversationKeys.detail(butlerName, conversationId ?? ""),
    queryFn: () => getConversation(butlerName, conversationId!),
    enabled: !!butlerName && !!conversationId,
    staleTime: 0,
  });
}

/**
 * Fetch messages for a specific conversation.
 * staleTime = 0: always refetch when switching conversations.
 */
export function useConversationMessages(
  butlerName: string,
  conversationId: string | null,
) {
  return useQuery({
    queryKey: conversationKeys.messages(butlerName, conversationId ?? ""),
    queryFn: () => getConversationMessages(butlerName, conversationId!),
    enabled: !!butlerName && !!conversationId,
    staleTime: 0,
  });
}

/**
 * Full-text search across conversations for a butler.
 * Only fires when query is non-empty; debounce should be applied at call site.
 */
export function useConversationSearch(butlerName: string, query: string) {
  return useQuery({
    queryKey: conversationKeys.search(butlerName, query),
    queryFn: () => searchConversations(butlerName, query),
    enabled: !!butlerName && query.trim().length > 0,
    staleTime: 30_000,
  });
}

// ---------------------------------------------------------------------------
// Invalidation helpers (used after SSE stream completes)
// ---------------------------------------------------------------------------

/** Returns a callback that invalidates conversation list + message queries after a send/create. */
export function useInvalidateConversations() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      butlerName,
      conversationId,
    }: {
      butlerName: string;
      conversationId?: string;
    }) => {
      await queryClient.invalidateQueries({
        queryKey: conversationKeys.all(butlerName),
      });
      if (conversationId) {
        await queryClient.invalidateQueries({
          queryKey: conversationKeys.messages(butlerName, conversationId),
        });
        await queryClient.invalidateQueries({
          queryKey: conversationKeys.detail(butlerName, conversationId),
        });
      }
    },
  });
}
