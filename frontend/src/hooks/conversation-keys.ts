/**
 * Query keys for the dashboard conversations API. Kept in a leaf module so
 * cache helpers (use-conversation-metadata.ts) can share them without
 * importing the query hooks.
 */

import type { ConversationListParams } from "@/api/index.ts";

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
