// @vitest-environment jsdom
/** DaySheetStrip (bu-5ii4gj.2 S4): a failed or stale read is never shown as a clear day. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import type { DaySheetResponse } from "@/api/types.ts";

import { DaySheetStrip } from "./DaySheetStrip.tsx";

afterEach(cleanup);

function sheet(overrides: Partial<DaySheetResponse> = {}): DaySheetResponse {
  return {
    date: "2026-11-24",
    zone: "Asia/Singapore",
    freshness: { state: "current", as_of: null, sources_unreadable: [], sources_total: 1 },
    items: [],
    gaps: [],
    attended_minutes: 0,
    attended_count: 0,
    longest_gap: null,
    digest: "d",
    text: "",
    ...overrides,
  };
}

function view(props: Parameters<typeof DaySheetStrip>[0]) {
  return render(
    <MemoryRouter>
      <DaySheetStrip {...props} />
    </MemoryRouter>,
  );
}

describe("DaySheetStrip", () => {
  it("says nothing is on the calendar only for a current empty sheet", () => {
    view({ sheet: sheet() });
    expect(screen.getByTestId("day-sheet-empty").textContent).toContain(
      "Nothing on your calendar.",
    );
  });

  it("renders an unreadable sheet as unreadable and lists nothing, never clear", () => {
    view({
      sheet: sheet({
        freshness: {
          state: "unreadable",
          as_of: null,
          sources_unreadable: ["general"],
          sources_total: 2,
        },
        items: [
          {
            ref: "x",
            start: "2026-11-24T10:00:00+08:00",
            end: "2026-11-24T11:00:00+08:00",
            all_day: false,
            kind: "attended",
            title: "Should not show",
            label_class: "third_party",
            people_count: 0,
            conflict_ref: null,
            door: "/calendar?entry=x",
          },
        ],
      }),
    });
    expect(screen.getByTestId("day-sheet-unreadable").textContent).toContain("1 of 2 sources");
    expect(screen.queryByTestId("day-sheet-empty")).toBeNull();
    expect(screen.queryByText("Should not show")).toBeNull();
  });

  it("flags a stale sheet and does not call an empty one clear", () => {
    view({
      sheet: sheet({
        freshness: {
          state: "stale",
          as_of: "2026-11-24T00:00:00+08:00",
          sources_unreadable: [],
          sources_total: 1,
        },
      }),
    });
    expect(screen.getByTestId("day-sheet-stale").textContent).toContain("As of 00:00");
    expect(screen.getByTestId("day-sheet-empty").textContent).toContain("not current");
    expect(screen.queryByText("Nothing on your calendar.")).toBeNull();
  });

  it("shows a failed request as an error, not as a clear day", () => {
    view({ isError: true, error: new Error("boom") });
    expect(screen.getByTestId("day-sheet-error").textContent).toContain("boom");
    expect(screen.queryByTestId("day-sheet-empty")).toBeNull();
  });

  it("lists items in the sheet's zone with a link and the committed load", () => {
    view({
      sheet: sheet({
        items: [
          {
            ref: "m1",
            start: "2026-11-24T10:00:00+08:00",
            end: "2026-11-24T11:30:00+08:00",
            all_day: false,
            kind: "tentative",
            title: "<b>Standup</b>",
            label_class: "third_party",
            people_count: 3,
            conflict_ref: null,
            door: "/calendar?entry=m1",
          },
        ],
        attended_minutes: 90,
        attended_count: 1,
        longest_gap: { start: "2026-11-24T12:00:00+08:00", end: "2026-11-24T15:00:00+08:00" },
      }),
    });
    const item = document.querySelector('[data-day-sheet-item="m1"]')!;
    expect(item.textContent).toContain("10:00-11:30");
    expect(item.textContent).toContain("? <b>Standup</b>");
    expect(item.querySelector("b")).toBeNull();
    expect(item.querySelector("a")!.getAttribute("href")).toBe("/calendar?entry=m1");
    expect(screen.getByTestId("day-sheet-load").textContent).toContain(
      "Committed 1h 30m across 1 meeting. Longest free: 12:00-15:00.",
    );
  });
});
