/**
 * Unread-reply badge (bu-p6ey8.4 — "Unread badge").
 *
 * Polls the EXISTING conversation-summary list (`useConversations`, no new
 * backend endpoint) roughly every 60s and compares each conversation's
 * `total_output_tokens` against a per-conversation watermark persisted in
 * localStorage. `total_output_tokens` only increases when an ASSISTANT reply
 * lands and a model call actually completes — sending a user message alone
 * never changes it — so this can never badge on the owner's own outgoing
 * messages.
 *
 * `panelOpen` drives watermark advancement: while the panel is open the
 * owner is considered caught up, so the watermark tracks live totals and any
 * badge clears. The moment it closes, the watermark freezes; a later poll
 * that shows a conversation's total strictly above its frozen watermark
 * means a reply arrived while the panel was closed, and badges the trigger.
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

const WATERMARK_STORAGE_KEY = "butlers:chat-widget-last-seen-v1";
const POLL_INTERVAL_MS = 60_000;

/** conversationId -> total_output_tokens as of the last time it was "seen". */
type Watermark = Record<string, number>;

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
  // this session (so history never retroactively badges), and — while the
  // panel is open — advance every known conversation's watermark to its
  // current total (the owner is caught up).
  useEffect(() => {
    if (conversations.length === 0) return;
    const next: Watermark = { ...watermark };
    let changed = false;
    for (const conv of conversations) {
      if (!(conv.id in next)) {
        next[conv.id] = conv.total_output_tokens;
        changed = true;
      } else if (panelOpen && next[conv.id] !== conv.total_output_tokens) {
        next[conv.id] = conv.total_output_tokens;
        changed = true;
      }
    }
    if (changed) setWatermarkSnapshot(next);
  }, [conversations, panelOpen, watermark]);

  // Derived badge state: while open, always caught up. While closed, badge
  // if any conversation's total_output_tokens has grown past the watermark
  // frozen at (or established after) the last time the panel was open.
  return useMemo(() => {
    if (panelOpen) return false;
    return conversations.some((conv) => {
      const seen = watermark[conv.id];
      return seen !== undefined && conv.total_output_tokens > seen;
    });
  }, [panelOpen, conversations, watermark]);
}
