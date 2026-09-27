/**
 * TanStack Query hooks for the conversations (chat UI) API.
 */

import { useQuery } from "@tanstack/react-query";
import {
  listConversations,
  getConversationById,
  getConversationMessages,
  searchConversations,
  searchMessages,
} from "@/api/index.ts";
import type { ConversationListParams, MessageSearchParams } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Query keys
// ---------------------------------------------------------------------------

export const conversationKeys = {
  all: (butlerName: string) => ["conversations", butlerName] as const,
  list: (butlerName: string, params?: ConversationListParams) =>
    ["conversations", butlerName, "list", params] as const,
  detail: (butlerName: string, conversationId: string) =>
    ["conversations", butlerName, conversationId] as const,
  /** Cross-butler identity lookup by id alone (bu-0ynlk.11) — the
   * /chat/:conversationId deep link, not scoped to a known butlerName. */
  byId: (conversationId: string) => ["conversations", "by-id", conversationId] as const,
  messages: (butlerName: string, conversationId: string) =>
    ["conversation-messages", butlerName, conversationId] as const,
  search: (butlerName: string, query: string) =>
    ["conversations", butlerName, "search", query] as const,
};

/** Query key for the owner-scoped cross-butler message search (bu-0ynlk.9). */
export const messageSearchKeys = {
  search: (query: string) => ["message-search", query] as const,
};

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

/** Extra query options a caller can opt into on top of the defaults below. */
export interface UseConversationsOptions {
  /** Enables polling (e.g. the unread-badge watcher) at this interval (ms). */
  refetchInterval?: number;
}

/**
 * Fetch a paginated list of conversations for a butler.
 * staleTime = 10 seconds (conversations update frequently during active chat).
 */
export function useConversations(
  butlerName: string,
  params?: ConversationListParams,
  options?: UseConversationsOptions,
) {
  return useQuery({
    queryKey: conversationKeys.list(butlerName, params),
    queryFn: () => listConversations(butlerName, params),
    enabled: !!butlerName,
    staleTime: 10_000,
    ...(options?.refetchInterval ? { refetchInterval: options.refetchInterval } : {}),
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
 * Cross-butler conversation identity lookup by id alone (bu-0ynlk.11) — the
 * /chat/:conversationId deep link and cmdk recall resolve the owning
 * `butler_name` this way before fetching messages. `retry: false` since a
 * 404 here is a legitimate terminal state (unknown/deleted conversation),
 * not a transient failure worth retrying.
 */
export function useConversationById(conversationId: string | null) {
  return useQuery({
    queryKey: conversationKeys.byId(conversationId ?? ""),
    queryFn: () => getConversationById(conversationId!),
    enabled: !!conversationId,
    retry: false,
    staleTime: 10_000,
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

/**
 * Owner-scoped message-level full-text search across every butler's
 * dashboard chats (bu-0ynlk.9). Only fires when `query` is non-empty;
 * debounce should be applied at the call site.
 */
export function useMessageSearch(
  query: string,
  params?: Omit<MessageSearchParams, "q">,
) {
  return useQuery({
    queryKey: messageSearchKeys.search(query),
    queryFn: () => searchMessages({ q: query, ...params }),
    enabled: query.trim().length > 0,
    staleTime: 30_000,
  });
}

// ---------------------------------------------------------------------------
// Invalidation helpers (used after SSE stream completes)
// ---------------------------------------------------------------------------
