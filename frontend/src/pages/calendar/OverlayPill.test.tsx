// @vitest-environment jsdom
/** Grid overlay chips never read as current when their butler is stale or partial. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";

import type { UnifiedCalendarEntry } from "@/api/types.ts";
import { overlayFreshnessNotes } from "@/lib/calendar-overlays";

import { OverlayPill } from "./atoms";

afterEach(cleanup);

function entry(metadata: Record<string, unknown>): UnifiedCalendarEntry {
  return {
    entry_id: "00000000-0000-0000-0000-000000000001",
    view: "overlays",
    source_type: "overlay_contribution",
    source_key: "overlays",
    title: "Electric Co",
    start_at: "2026-02-23T00:00:00+08:00",
    end_at: "2026-02-24T00:00:00+08:00",
    timezone: "Asia/Singapore",
    all_day: true,
    butler_name: "finance",
    status: "active",
    editable: false,
    metadata: {
      source_type: "overlay_contribution",
      kind: "bill_due",
      priority: "high",
      source_butler: "finance",
      meta: {},
      ...metadata,
    },
  } as unknown as UnifiedCalendarEntry;
}

describe("OverlayPill freshness", () => {
  it("marks a stale chip as not current", () => {
    const { container } = render(<OverlayPill entry={entry({ stale: true })} />);
    const pill = container.querySelector("[data-overlay-entry-id]") as HTMLElement;
    expect(pill.getAttribute("data-overlay-not-current")).toBe("stale");
    expect(pill.getAttribute("title")).toContain("not current");
    expect(pill.textContent).toContain("stale");
  });

  it("marks a partial chip as not current", () => {
    const { container } = render(<OverlayPill entry={entry({ partial: true })} />);
    const pill = container.querySelector("[data-overlay-entry-id]") as HTMLElement;
    expect(pill.getAttribute("data-overlay-not-current")).toBe("partial");
  });

  it("leaves a current chip alone", () => {
    const { container } = render(<OverlayPill entry={entry({})} />);
    const pill = container.querySelector("[data-overlay-entry-id]") as HTMLElement;
    expect(pill.getAttribute("data-overlay-not-current")).toBeNull();
  });
});

describe("overlayFreshnessNotes", () => {
  it("renders stale-since in the owner zone and names partial butlers", () => {
    const notes = overlayFreshnessNotes(
      [
        { butler: "finance", state: "stale", stale_since: "2026-02-24T10:00:00+00:00" },
        { butler: "travel", state: "partial" },
        { butler: "health", state: "ok" },
      ],
      "America/Los_Angeles",
    );
    expect(notes).toEqual([
      "Finance context stale since Feb 24, 2:00 AM",
      "Travel context is incomplete",
    ]);
  });
});
