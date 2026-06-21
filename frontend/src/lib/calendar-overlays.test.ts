import { describe, expect, it } from "vitest";

import type { UnifiedCalendarEntry } from "@/api/types.ts";

import {
  isOverlayEntry,
  overlayAmountBadge,
  overlayButlerAccent,
  overlayKindGlyph,
  overlayMetadata,
  overlayPriorityRank,
  overlaysByDay,
} from "./calendar-overlays.ts";

function overlayEntry(overrides: Partial<UnifiedCalendarEntry> = {}): UnifiedCalendarEntry {
  return {
    entry_id: "00000000-0000-0000-0000-000000000001",
    event_id: null,
    view: "overlays",
    source_type: "overlay_contribution",
    source_key: "overlays",
    title: "Electric Co",
    start_at: "2026-02-22T00:00:00+08:00",
    end_at: "2026-02-23T00:00:00+08:00",
    timezone: "Asia/Singapore",
    all_day: true,
    calendar_id: null,
    provider_event_id: null,
    butler_name: "finance",
    schedule_id: null,
    reminder_id: null,
    rrule: null,
    cron: null,
    until_at: null,
    status: "active",
    sync_state: null,
    editable: false,
    metadata: {
      source_type: "overlay_contribution",
      kind: "bill_due",
      priority: "high",
      source_butler: "finance",
      meta: { amount: 84.2, currency: "SGD" },
    },
    source_butler: "finance",
    ...overrides,
  };
}

describe("isOverlayEntry", () => {
  it("is true only for overlay_contribution rows", () => {
    expect(isOverlayEntry(overlayEntry())).toBe(true);
    expect(isOverlayEntry(overlayEntry({ source_type: "provider_event" }))).toBe(false);
  });
});

describe("overlayMetadata", () => {
  it("reads kind/priority/source_butler/meta defensively", () => {
    const md = overlayMetadata(overlayEntry());
    expect(md.kind).toBe("bill_due");
    expect(md.priority).toBe("high");
    expect(md.source_butler).toBe("finance");
    expect(md.meta).toEqual({ amount: 84.2, currency: "SGD" });
  });

  it("falls back to null priority and entry.source_butler", () => {
    const md = overlayMetadata(
      overlayEntry({ metadata: { kind: "appointment", priority: "bogus" } }),
    );
    expect(md.priority).toBeNull();
    expect(md.source_butler).toBe("finance");
    expect(md.meta).toEqual({});
  });
});

describe("overlaysByDay", () => {
  it("groups by local day, skips non-overlay entries, and sorts by priority", () => {
    const high = overlayEntry({ entry_id: "a", metadata: { kind: "bill_due", priority: "high" } });
    const low = overlayEntry({ entry_id: "b", metadata: { kind: "bill_due", priority: "low" } });
    const realEvent = overlayEntry({ entry_id: "c", source_type: "provider_event" });
    const map = overlaysByDay([low, realEvent, high]);
    const day = map.get("2026-02-22");
    expect(day).toBeDefined();
    expect(day?.map((e) => e.entry_id)).toEqual(["a", "b"]); // high before low, real event excluded
  });
});

describe("overlayPriorityRank", () => {
  it("orders high < medium < low < unknown", () => {
    expect(overlayPriorityRank("high")).toBeLessThan(overlayPriorityRank("medium"));
    expect(overlayPriorityRank("medium")).toBeLessThan(overlayPriorityRank("low"));
    expect(overlayPriorityRank("low")).toBeLessThan(overlayPriorityRank(null));
  });
});

describe("overlayAmountBadge", () => {
  it("formats a currency-prefixed rounded amount", () => {
    expect(overlayAmountBadge({ amount: 84.2, currency: "SGD" })).toBe("SGD 84");
  });
  it("omits currency when absent and returns null without an amount", () => {
    expect(overlayAmountBadge({ amount: 1200 })).toBe("1,200");
    expect(overlayAmountBadge({ currency: "USD" })).toBeNull();
    expect(overlayAmountBadge({})).toBeNull();
  });
});

describe("overlayButlerAccent / overlayKindGlyph", () => {
  it("returns a stable accent per butler and a glyph per kind", () => {
    expect(overlayButlerAccent("finance")).toContain("emerald");
    expect(overlayButlerAccent("travel")).toContain("sky");
    expect(overlayButlerAccent(null)).toContain("var(--border)");
    expect(overlayKindGlyph("departure")).toBe("✈");
    expect(overlayKindGlyph("unknown_kind")).toBe("•");
  });
});
