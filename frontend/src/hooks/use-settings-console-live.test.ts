// @vitest-environment jsdom
/**
 * Tests for useSettingsConsoleLive (bu-3quv8 -- ports useSettingsConsoleStream
 * off its own bespoke `/api/settings/stream` socket onto the shared
 * EventBusProvider, completing bu-qvnce.14 slice 2's settings-console half).
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

type Listener = (
  event: { type: string; ts: number; data: Record<string, unknown> },
  meta: { replayed: boolean },
) => void;

const capturedListeners: Record<string, Listener> = {};

vi.mock("@/lib/event-bus", () => ({
  useBusEvent: (type: string, listener: Listener) => {
    capturedListeners[type] = listener;
  },
}));

afterEach(() => {
  for (const key of Object.keys(capturedListeners))
    delete capturedListeners[key];
});

import {
  useSettingsConsoleLive,
  applyHeaderDelta,
  applyAttentionAdd,
  applyAttentionRemove,
  type AttentionItem,
  type ConsoleData,
} from "./use-settings-console-live";

function attention(
  id: string,
  kind: string,
  tone: "red" | "amber" = "red",
): AttentionItem {
  return {
    id,
    tone,
    kind,
    text: `${id} needs attention`,
    action_route: `/settings/${id}`,
  };
}

const APPROVAL = attention("open_approvals", "approval");

const SNAPSHOT: ConsoleData = {
  header_counts: {
    active_butlers: 3,
    spend_mtd_usd: 12.5,
    open_approvals: 1,
    models_verified: 4,
    models_total: 6,
  },
  attention: [APPROVAL],
  attention_all: [APPROVAL],
  attention_truncated_count: 0,
};

function withAttention(items: AttentionItem[]): ConsoleData {
  return {
    ...SNAPSHOT,
    attention: items.slice(0, 5),
    attention_all: items,
    attention_truncated_count: Math.max(0, items.length - 5),
  };
}

function emit(
  type: "header_delta" | "attention_add" | "attention_remove",
  data: Record<string, unknown>,
  replayed = false,
) {
  capturedListeners[type]?.({ type, ts: Date.now(), data }, { replayed });
}

// ---------------------------------------------------------------------------
// Pure reducer tests
// ---------------------------------------------------------------------------

describe("applyHeaderDelta / applyAttentionAdd / applyAttentionRemove", () => {
  it("header delta shallow-merges header counts, leaving other fields untouched", () => {
    const next = applyHeaderDelta(SNAPSHOT, {
      open_approvals: 5,
      spend_mtd_usd: 99.9,
    });
    expect(next.header_counts.open_approvals).toBe(5);
    expect(next.header_counts.spend_mtd_usd).toBe(99.9);
    expect(next.header_counts.active_butlers).toBe(3);
    expect(next.attention).toEqual(SNAPSHOT.attention);
  });

  it("attention_add upserts by stable identity (no duplicates)", () => {
    const withModel = applyAttentionAdd(SNAPSHOT, {
      id: "model_error",
      tone: "amber",
      kind: "model",
      text: "1 model errored",
      action_route: "/settings/models",
    });
    expect(withModel.attention).toHaveLength(2);

    const replaced = applyAttentionAdd(withModel, {
      id: "model_error",
      tone: "red",
      kind: "model",
      text: "2 models errored",
      action_route: "/settings/models",
    });
    expect(replaced.attention.filter((i) => i.kind === "model")).toHaveLength(
      1,
    );
    expect(replaced.attention.find((i) => i.kind === "model")?.text).toBe(
      "2 models errored",
    );
  });

  it("retains same-kind providers and promotes capped items after one is removed", () => {
    const codex = attention("auth_renewal:codex", "auth_renewal");
    const opencode = attention("auth_renewal:opencode", "auth_renewal");
    const items = [
      codex,
      opencode,
      attention("model_error", "model"),
      attention("open_approvals", "approval"),
      attention("spend_ceiling", "spend", "amber"),
      attention("webhook_failure", "webhook", "amber"),
    ];

    const capped = withAttention(items);
    expect(capped.attention).toHaveLength(5);
    expect(capped.attention_all).toHaveLength(6);
    expect(capped.attention_truncated_count).toBe(1);

    const removed = applyAttentionRemove(capped, "auth_renewal:codex");
    expect(removed.attention_all.map((item) => item.id)).toEqual([
      "auth_renewal:opencode",
      "model_error",
      "open_approvals",
      "spend_ceiling",
      "webhook_failure",
    ]);
    expect(removed.attention).toHaveLength(5);
    expect(removed.attention.map((item) => item.id)).toContain(
      "webhook_failure",
    );
    expect(removed.attention_truncated_count).toBe(0);
  });

  it("attention_add sorts red items before amber items", () => {
    const withAmber = applyAttentionAdd(SNAPSHOT, {
      id: "model_error",
      tone: "amber",
      kind: "model",
      text: "1 model errored",
      action_route: "/settings/models",
    });
    const withRed = applyAttentionAdd(withAmber, {
      id: "auth_renewal:codex",
      tone: "red",
      kind: "cli_auth",
      text: "CLI needs auth",
      action_route: "/secrets",
    });
    expect(withRed.attention.map((i) => i.tone)).toEqual([
      "red",
      "red",
      "amber",
    ]);
  });

  it("attention_remove drops the item with the given stable identity", () => {
    const next = applyAttentionRemove(SNAPSHOT, "open_approvals");
    expect(next.attention).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Hook tests
// ---------------------------------------------------------------------------

describe("useSettingsConsoleLive", () => {
  it("subscribes to all three console bus event types", () => {
    renderHook(() => useSettingsConsoleLive(undefined));
    expect(Object.keys(capturedListeners).sort()).toEqual([
      "attention_add",
      "attention_remove",
      "header_delta",
    ]);
  });

  it("returns undefined until initialData is provided", () => {
    const { result } = renderHook(() => useSettingsConsoleLive(undefined));
    expect(result.current).toBeUndefined();
  });

  it("seeds from initialData once provided", () => {
    const { result, rerender } = renderHook(
      ({ data }) => useSettingsConsoleLive(data),
      {
        initialProps: { data: undefined as ConsoleData | undefined },
      },
    );
    expect(result.current).toBeUndefined();

    rerender({ data: SNAPSHOT });
    expect(result.current).toEqual(SNAPSHOT);
  });

  it("applies header_delta / attention_add / attention_remove live events incrementally", () => {
    const { result, rerender } = renderHook(
      ({ data }) => useSettingsConsoleLive(data),
      {
        initialProps: { data: SNAPSHOT as ConsoleData | undefined },
      },
    );
    rerender({ data: SNAPSHOT });

    act(() => emit("header_delta", { open_approvals: 4 }));
    expect(result.current?.header_counts.open_approvals).toBe(4);
    expect(result.current?.header_counts.active_butlers).toBe(3);

    act(() =>
      emit("attention_add", {
        id: "spend_ceiling",
        tone: "amber",
        kind: "spend",
        text: "near ceiling",
        action_route: "/settings/spend",
      }),
    );
    expect(result.current?.attention.map((i) => i.kind)).toContain("spend");

    act(() => emit("attention_remove", { id: "open_approvals" }));
    expect(result.current?.attention.map((i) => i.kind)).not.toContain(
      "approval",
    );
  });

  it("ignores replayed events", () => {
    const { result, rerender } = renderHook(
      ({ data }) => useSettingsConsoleLive(data),
      {
        initialProps: { data: SNAPSHOT as ConsoleData | undefined },
      },
    );
    rerender({ data: SNAPSHOT });

    act(() => emit("header_delta", { open_approvals: 99 }, true));
    expect(result.current?.header_counts.open_approvals).toBe(1);

    act(() =>
      emit(
        "attention_add",
        {
          id: "auth_renewal:codex",
          tone: "red",
          kind: "auth_renewal",
          text: "Codex needs auth",
          action_route: "/secrets?focus=c:cli-auth/codex",
        },
        true,
      ),
    );
    expect(result.current?.attention_all.map((item) => item.id)).toEqual([
      "open_approvals",
    ]);
  });

  it("ignores live deltas before any initialData has arrived", () => {
    const { result } = renderHook(() => useSettingsConsoleLive(undefined));

    act(() => emit("header_delta", { open_approvals: 4 }));
    expect(result.current).toBeUndefined();
  });

  it("a fresh initialData reseed (reconciliation poll) fully replaces prior live state", () => {
    const { result, rerender } = renderHook(
      ({ data }) => useSettingsConsoleLive(data),
      {
        initialProps: { data: SNAPSHOT as ConsoleData | undefined },
      },
    );
    rerender({ data: SNAPSHOT });

    act(() => emit("header_delta", { open_approvals: 4 }));
    expect(result.current?.header_counts.open_approvals).toBe(4);

    const fresh: ConsoleData = {
      ...withAttention([
        attention("auth_renewal:codex", "auth_renewal"),
        attention("auth_renewal:opencode", "auth_renewal"),
        attention("model_error", "model"),
        attention("open_approvals", "approval"),
        attention("spend_ceiling", "spend", "amber"),
        attention("webhook_failure", "webhook", "amber"),
      ]),
      header_counts: { ...SNAPSHOT.header_counts, open_approvals: 42 },
    };
    rerender({ data: fresh });
    expect(result.current?.header_counts.open_approvals).toBe(42);
    expect(result.current?.attention).toHaveLength(5);
    expect(result.current?.attention_all).toHaveLength(6);
    expect(result.current?.attention_truncated_count).toBe(1);
  });
});
