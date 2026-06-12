// @vitest-environment jsdom
/**
 * Component tests for CoreDatesBlock (entity v3 core dates, server half, bu-xzh76).
 *
 * Covers:
 * - birthday renders with date, next occurrence, and days-until (tabular nums);
 * - provenance (verified / staleness / src) renders per row;
 * - items rendered in the server's days_until order (no client sort);
 * - empty list hides the section.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

import type { CoreDateEntry, CoreDatesResponse } from "@/api/types";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

vi.mock("@/hooks/use-entities", () => ({
  useEntityCoreDates: vi.fn(),
}));

import { useEntityCoreDates } from "@/hooks/use-entities";
import { CoreDatesBlock } from "./CoreDatesBlock";

function mockCoreDates(resp: CoreDatesResponse, isLoading = false) {
  vi.mocked(useEntityCoreDates).mockReturnValue({
    data: resp,
    isLoading,
  } as unknown as ReturnType<typeof useEntityCoreDates>);
}

function entry(over: Partial<CoreDateEntry>): CoreDateEntry {
  return {
    id: "d1",
    predicate: "has-birthday",
    value: "1990-06-15",
    month: 6,
    day: 15,
    year: 1990,
    next_occurrence: "2026-06-15",
    days_until: 2,
    src: "telegram",
    conf: 1,
    verified: true,
    staleness_band: "fresh",
    ...over,
  };
}

let container: HTMLDivElement;
let root: Root;

function render() {
  act(() => {
    root.render(<CoreDatesBlock entityId="ent-1" />);
  });
}

beforeEach(() => {
  vi.resetAllMocks();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("CoreDatesBlock", () => {
  it("renders a birthday with next occurrence and days-until", () => {
    mockCoreDates({ items: [entry({})] });
    render();

    expect(container.querySelector('[data-testid="core-dates-block"]')).not.toBeNull();
    const row = container.querySelector('[data-testid="core-date-row-has-birthday"]');
    expect(row).not.toBeNull();
    const text = row?.textContent ?? "";
    expect(text).toContain("Birthday");
    expect(text).toContain("Jun 15");
    expect(text).toContain("in 2 days");
  });

  it("renders provenance: verified badge, staleness band, and src", () => {
    mockCoreDates({ items: [entry({ verified: true, staleness_band: "stale", src: "email" })] });
    render();
    const text = container.querySelector('[data-testid="core-date-row-has-birthday"]')?.textContent ?? "";
    expect(text).toContain("verified");
    expect(text).toContain("stale");
    expect(text).toContain("email");
  });

  it("renders items in the server-provided order (soonest first)", () => {
    mockCoreDates({
      items: [
        entry({ id: "d1", predicate: "has-birthday", days_until: 2 }),
        entry({
          id: "d2",
          predicate: "has-anniversary",
          month: 9,
          day: 1,
          next_occurrence: "2026-09-01",
          days_until: 80,
        }),
      ],
    });
    render();
    const rows = container.querySelectorAll('[data-testid^="core-date-row-"]');
    expect(rows.length).toBe(2);
    expect(rows[0].getAttribute("data-testid")).toBe("core-date-row-has-birthday");
    expect(rows[1].getAttribute("data-testid")).toBe("core-date-row-has-anniversary");
  });

  it("hides the section when there are no date-kind facts", () => {
    mockCoreDates({ items: [] });
    render();
    expect(container.querySelector('[data-testid="core-dates-block"]')).toBeNull();
    expect(container.innerHTML).toBe("");
  });

  it("renders nothing while loading", () => {
    mockCoreDates({ items: [] }, /* isLoading */ true);
    render();
    expect(container.innerHTML).toBe("");
  });

  it("renders 'today' when days_until is 0", () => {
    mockCoreDates({ items: [entry({ days_until: 0 })] });
    render();
    expect(
      container.querySelector('[data-testid="core-date-row-has-birthday"]')?.textContent,
    ).toContain("today");
  });
});
