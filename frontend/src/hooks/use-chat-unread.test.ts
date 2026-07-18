// @vitest-environment jsdom
/**
 * Tests for useChatUnreadBadge (bu-p6ey8.4 — "Unread badge").
 *
 * `useConversations` is mocked so the test controls the conversation-summary
 * list directly, standing in for what a real ~60s poll would eventually
 * return.
 *
 * The fixture feeds realistic `latest_assistant_reply_at` timestamps because
 * that is what changes when a confirm-loop reply is persisted.
 *
 * Covers:
 *  - no badge while the panel is open, even as replies land
 *  - a reply (`latest_assistant_reply_at` advances) while closed badges
 *    within the next observed poll
 *  - opening the panel clears an existing badge
 *  - the owner's own outgoing messages (message_count bump, no
 *    `latest_assistant_reply_at` change) never badge
 *  - first-sight of a conversation that already has reply history (while
 *    closed) does NOT badge — only a reply arriving *after* first sight does
 *  - the watermark persists across mounts via localStorage
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderHook } from "@testing-library/react";
import type { ConversationSummary } from "@/api/types.ts";

const conversationsRef: { current: ConversationSummary[] } = { current: [] };

vi.mock("./use-conversations.ts", () => ({
  useConversations: () => ({
    data: { data: conversationsRef.current, meta: {} },
    isLoading: false,
  }),
}));

import {
  useChatUnreadBadge,
  __resetChatUnreadWatermarkForTests,
  __reloadChatUnreadWatermarkFromStorageForTests,
} from "./use-chat-unread.ts";

function conv(
  id: string,
  latestAssistantReplyAt: string | null,
  overrides: Partial<ConversationSummary> = {},
): ConversationSummary {
  return {
    id,
    butler_name: "switchboard",
    title: null,
    status: "active",
    created_at: "2026-07-05T00:00:00.000Z",
    updated_at: "2026-07-05T00:00:00.000Z",
    message_count: 1,
    routed_butler: null,
    latest_assistant_reply_at: latestAssistantReplyAt,
    ...overrides,
  };
}

beforeEach(() => {
  window.localStorage.clear();
  __resetChatUnreadWatermarkForTests();
  conversationsRef.current = [];
});

afterEach(() => cleanup());

describe("useChatUnreadBadge", () => {
  it("never badges while the panel is open, even as a reply lands", () => {
    conversationsRef.current = [conv("c1", "2026-07-05T00:00:20.000Z")];
    const { result, rerender } = renderHook(
      ({ open }) => useChatUnreadBadge("switchboard", open),
      { initialProps: { open: true } },
    );
    expect(result.current).toBe(false);

    conversationsRef.current = [conv("c1", "2026-07-05T00:00:45.000Z")];
    rerender({ open: true });
    expect(result.current).toBe(false);
  });

  it("badges when latest_assistant_reply_at advances while the panel is closed", () => {
    conversationsRef.current = [conv("c1", "2026-07-05T00:00:20.000Z")];
    const { result, rerender } = renderHook(
      ({ open }) => useChatUnreadBadge("switchboard", open),
      { initialProps: { open: false } },
    );
    // First observation of this conversation establishes its baseline —
    // never retroactively badges on history.
    expect(result.current).toBe(false);

    // A reply lands (a new assistant message is persisted).
    conversationsRef.current = [conv("c1", "2026-07-05T00:00:45.000Z")];
    rerender({ open: false });
    expect(result.current).toBe(true);
  });

  it("opening the panel clears an existing badge", () => {
    conversationsRef.current = [conv("c1", "2026-07-05T00:00:20.000Z")];
    const { result, rerender } = renderHook(
      ({ open }) => useChatUnreadBadge("switchboard", open),
      { initialProps: { open: false } },
    );
    expect(result.current).toBe(false);

    conversationsRef.current = [conv("c1", "2026-07-05T00:00:45.000Z")];
    rerender({ open: false });
    expect(result.current).toBe(true);

    rerender({ open: true });
    expect(result.current).toBe(false);
  });

  it("never badges on the owner's own outgoing message (no reply timestamp change)", () => {
    conversationsRef.current = [conv("c1", "2026-07-05T00:00:20.000Z", { message_count: 2 })];
    const { result, rerender } = renderHook(
      ({ open }) => useChatUnreadBadge("switchboard", open),
      { initialProps: { open: false } },
    );
    expect(result.current).toBe(false);

    // Owner sends another message: message_count bumps, but
    // latest_assistant_reply_at does not — only a persisted assistant reply
    // moves it.
    conversationsRef.current = [
      conv("c1", "2026-07-05T00:00:20.000Z", { message_count: 3 }),
    ];
    rerender({ open: false });
    expect(result.current).toBe(false);
  });

  it("never badges on first sight of a conversation with pre-existing reply history", () => {
    // The very first poll already shows a reply — this must be treated as
    // "already seen" (a baseline), not a fresh unread reply.
    conversationsRef.current = [conv("c1", "2026-07-05T00:00:20.000Z")];
    const { result, rerender } = renderHook(
      ({ open }) => useChatUnreadBadge("switchboard", open),
      { initialProps: { open: false } },
    );
    expect(result.current).toBe(false);

    // No change yet — still caught up.
    rerender({ open: false });
    expect(result.current).toBe(false);
  });

  it("badges a conversation with no prior replies once its first reply lands while closed", () => {
    conversationsRef.current = [conv("c1", null)];
    const { result, rerender } = renderHook(
      ({ open }) => useChatUnreadBadge("switchboard", open),
      { initialProps: { open: false } },
    );
    expect(result.current).toBe(false);

    conversationsRef.current = [conv("c1", "2026-07-05T00:00:45.000Z")];
    rerender({ open: false });
    expect(result.current).toBe(true);
  });

  it("persists the watermark across mounts via localStorage", () => {
    conversationsRef.current = [conv("c1", "2026-07-05T00:00:20.000Z")];
    const { unmount } = renderHook(
      ({ open }) => useChatUnreadBadge("switchboard", open),
      { initialProps: { open: true } },
    );
    unmount();

    // Simulate a fresh page load: rehydrate strictly from what's actually
    // sitting in localStorage (not whatever is left in memory).
    __reloadChatUnreadWatermarkFromStorageForTests();

    conversationsRef.current = [conv("c1", "2026-07-05T00:01:00.000Z")];
    const { result } = renderHook(
      ({ open }) => useChatUnreadBadge("switchboard", open),
      { initialProps: { open: false } },
    );
    expect(result.current).toBe(true);
  });
});
