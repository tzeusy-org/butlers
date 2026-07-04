// @vitest-environment jsdom
/**
 * Tests for useChatUnreadBadge (bu-p6ey8.4 — "Unread badge").
 *
 * `useConversations` is mocked so the test controls the conversation-summary
 * list directly, standing in for what a real ~60s poll would eventually
 * return. Covers:
 *  - no badge while the panel is open, even as replies land
 *  - a reply (total_output_tokens increase) while closed badges within the
 *    next observed poll
 *  - opening the panel clears an existing badge
 *  - the owner's own outgoing messages (message_count bump, no output-token
 *    change) never badge
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
  totalOutputTokens: number,
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
    total_input_tokens: 0,
    total_output_tokens: totalOutputTokens,
    total_duration_ms: 0,
    routed_butler: null,
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
    conversationsRef.current = [conv("c1", 20)];
    const { result, rerender } = renderHook(
      ({ open }) => useChatUnreadBadge("switchboard", open),
      { initialProps: { open: true } },
    );
    expect(result.current).toBe(false);

    conversationsRef.current = [conv("c1", 45)];
    rerender({ open: true });
    expect(result.current).toBe(false);
  });

  it("badges when a reply's total_output_tokens increases while the panel is closed", () => {
    conversationsRef.current = [conv("c1", 20)];
    const { result, rerender } = renderHook(
      ({ open }) => useChatUnreadBadge("switchboard", open),
      { initialProps: { open: false } },
    );
    // First observation of this conversation establishes its baseline —
    // never retroactively badges on history.
    expect(result.current).toBe(false);

    // A reply lands (assistant turn completes, output tokens increase).
    conversationsRef.current = [conv("c1", 45)];
    rerender({ open: false });
    expect(result.current).toBe(true);
  });

  it("opening the panel clears an existing badge", () => {
    conversationsRef.current = [conv("c1", 20)];
    const { result, rerender } = renderHook(
      ({ open }) => useChatUnreadBadge("switchboard", open),
      { initialProps: { open: false } },
    );
    expect(result.current).toBe(false);

    conversationsRef.current = [conv("c1", 45)];
    rerender({ open: false });
    expect(result.current).toBe(true);

    rerender({ open: true });
    expect(result.current).toBe(false);
  });

  it("never badges on the owner's own outgoing message (no output-token change)", () => {
    conversationsRef.current = [conv("c1", 20, { message_count: 2 })];
    const { result, rerender } = renderHook(
      ({ open }) => useChatUnreadBadge("switchboard", open),
      { initialProps: { open: false } },
    );
    expect(result.current).toBe(false);

    // Owner sends another message: message_count bumps, but total_output_tokens
    // does not — only a completed assistant turn carries model usage.
    conversationsRef.current = [conv("c1", 20, { message_count: 3 })];
    rerender({ open: false });
    expect(result.current).toBe(false);
  });

  it("persists the watermark across mounts via localStorage", () => {
    conversationsRef.current = [conv("c1", 20)];
    const { unmount } = renderHook(
      ({ open }) => useChatUnreadBadge("switchboard", open),
      { initialProps: { open: true } },
    );
    unmount();

    // Simulate a fresh page load: rehydrate strictly from what's actually
    // sitting in localStorage (not whatever is left in memory).
    __reloadChatUnreadWatermarkFromStorageForTests();

    conversationsRef.current = [conv("c1", 60)];
    const { result } = renderHook(
      ({ open }) => useChatUnreadBadge("switchboard", open),
      { initialProps: { open: false } },
    );
    expect(result.current).toBe(true);
  });
});
