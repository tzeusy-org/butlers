/**
 * Unread-reply badge (bu-p6ey8.4 — "Unread badge").
 *
 * Polls the EXISTING conversation-summary list (`useConversations`, no new
 * backend endpoint) roughly every 60s and compares each conversation's
 * `latest_assistant_reply_at` against a per-conversation watermark persisted
 * in localStorage. `latest_assistant_reply_at` only moves when an ASSISTANT
 * message is actually persisted (`conversation_reply_create` — see
 * `src/butlers/api/conversations.py`) — sending a user message alone never
 * changes it — so this can never badge on the owner's own outgoing messages.
 *
 * `latest_assistant_reply_at` is a timestamp derived from
 * `MAX(dashboard_messages.created_at) WHERE role = 'assistant'`, so it moves
 * the instant a reply — of any kind — is written.
 *
 * `panelOpen` drives watermark advancement: while the panel is open the
 * owner is considered caught up, so the watermark tracks the live value and
 * any badge clears. The moment it closes, the watermark freezes; a later
 * poll that shows a conversation's `latest_assistant_reply_at` strictly
 * later than its frozen watermark means a reply arrived while the panel was
 * closed, and badges the trigger.
 *
 * Watermark storage follows the same module-scope-store +
 * `useSyncExternalStore` pattern as `use-approval-decisions.ts`'s scheduled
 * decisions: reading a mutable ref's `.current` directly during render (or
 * storing it in `useState` and setting that state from inside an effect) is
 * disallowed by this repo's react-hooks lint config, so the watermark lives
 * in a module-level snapshot read via `useSyncExternalStore` (render-safe)
 * and written through a plain notifying setter (effect-safe).
 */

import { useEffect, useMemo, useSyncExternalStore } from "react";

import { useConversations } from "./use-conversations.ts";
import type { ConversationSummary } from "@/api/types.ts";

// The watermark is a `latest_assistant_reply_at` ISO timestamp (or null).
const WATERMARK_STORAGE_KEY = "butlers:chat-widget-last-seen-v2";
const POLL_INTERVAL_MS = 60_000;

/**
 * conversationId -> `latest_assistant_reply_at` as of the last time it was
 * "seen". `null` means the conversation was seen with no assistant reply yet
 * (a fresh baseline); the key being absent entirely means never seen this
 * session.
 */
type Watermark = Record<string, string | null>;

function readWatermark(): Watermark {
  try {
    const raw = window.localStorage.getItem(WATERMARK_STORAGE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Watermark;
    }
    return {};
  } catch {
    // localStorage unavailable (private mode / quota) — degrade to
    // session-only watermark tracking rather than throwing.
    return {};
  }
}

function persistWatermark(watermark: Watermark): void {
  try {
    window.localStorage.setItem(WATERMARK_STORAGE_KEY, JSON.stringify(watermark));
  } catch {
    /* see readWatermark — best-effort persistence only. */
  }
}

// Module-scope store (not component state): a single floating widget exists
// per app instance, so one shared watermark is the correct model, and it
// lets `useSyncExternalStore` read it safely during render.
let watermarkSnapshot: Watermark = readWatermark();
const watermarkListeners = new Set<() => void>();

function setWatermarkSnapshot(next: Watermark): void {
  watermarkSnapshot = next;
  persistWatermark(next);
  for (const listener of watermarkListeners) listener();
}

function subscribeWatermark(onStoreChange: () => void): () => void {
  watermarkListeners.add(onStoreChange);
  return () => {
    watermarkListeners.delete(onStoreChange);
  };
}

function getWatermarkSnapshot(): Watermark {
  return watermarkSnapshot;
}

/** Test-only escape hatch: reset the module-scope watermark between tests. */
export function __resetChatUnreadWatermarkForTests(): void {
  setWatermarkSnapshot({});
}

/**
 * Test-only escape hatch: re-hydrate the module-scope watermark strictly
 * from whatever is currently in localStorage, discarding any in-memory
 * value — simulates a fresh page load that persisted state survives.
 */
export function __reloadChatUnreadWatermarkFromStorageForTests(): void {
  watermarkSnapshot = readWatermark();
  for (const listener of watermarkListeners) listener();
}

export function useChatUnreadBadge(butlerName: string, panelOpen: boolean): boolean {
  const { data } = useConversations(butlerName, undefined, {
    refetchInterval: POLL_INTERVAL_MS,
  });
  const conversations: ConversationSummary[] = useMemo(() => data?.data ?? [], [data]);

  const watermark = useSyncExternalStore(subscribeWatermark, getWatermarkSnapshot);

  // Establish a baseline for any conversation id seen for the first time
  // this session (so history never retroactively badges, even when the
  // conversation already has replies), and — while the panel is open —
  // advance every known conversation's watermark to its current
  // `latest_assistant_reply_at` (the owner is caught up).
  useEffect(() => {
    if (conversations.length === 0) return;
    const next: Watermark = { ...watermark };
    let changed = false;
    for (const conv of conversations) {
      const latest = conv.latest_assistant_reply_at ?? null;
      if (!(conv.id in next)) {
        next[conv.id] = latest;
        changed = true;
      } else if (panelOpen && next[conv.id] !== latest) {
        next[conv.id] = latest;
        changed = true;
      }
    }
    if (changed) setWatermarkSnapshot(next);
  }, [conversations, panelOpen, watermark]);

  // Derived badge state: while open, always caught up. While closed, badge
  // if any conversation's `latest_assistant_reply_at` is later than the
  // watermark frozen at (or established after) the last time the panel was
  // open. A conversation not yet baselined this session (effect hasn't run
  // for it yet) never badges on this render.
  return useMemo(() => {
    if (panelOpen) return false;
    return conversations.some((conv) => {
      if (!(conv.id in watermark)) return false;
      const latest = conv.latest_assistant_reply_at ?? null;
      if (!latest) return false;
      const seen = watermark[conv.id];
      if (seen === latest) return false;
      if (seen === null) return true;
      return new Date(latest).getTime() > new Date(seen).getTime();
    });
  }, [panelOpen, conversations, watermark]);
}
