// @vitest-environment jsdom

/**
 * Tests for RecentDaysIndex (bu archive nav): navigable rows + active marking.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { act } from "react";
import { createRoot } from "react-dom/client";

import { RecentDaysIndex } from "./RecentDaysIndex";
import type { ChroniclesRecentDay } from "@/api/types";

// react-dom/client + act() need this flag set in a non-browser test env.
(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const DAYS: ChroniclesRecentDay[] = [
  { date: "2026-05-07", total_minutes: 642, top_lane: "conversations", episode_count: 23 },
  { date: "2026-05-06", total_minutes: 120, top_lane: "tasks", episode_count: 4 },
];

describe("RecentDaysIndex", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("renders an empty state when there are no days", () => {
    const html = renderToStaticMarkup(<RecentDaysIndex days={[]} />);
    expect(html).toContain("Recent days");
    expect(html).toContain("No prior days projected yet.");
  });

  it("renders static rows (no buttons) without onSelect", () => {
    const html = renderToStaticMarkup(<RecentDaysIndex days={DAYS} />);
    expect(html).toContain("Recent days");
    expect(html).toContain("conversations");
    expect(html).not.toContain("<button");
  });

  it("renders rows as buttons when onSelect is provided", () => {
    const html = renderToStaticMarkup(<RecentDaysIndex days={DAYS} onSelect={() => {}} />);
    expect(html).toContain("<button");
    expect(html).toContain("conversations");
  });

  it("marks the selected day with aria-current", () => {
    const html = renderToStaticMarkup(
      <RecentDaysIndex days={DAYS} onSelect={() => {}} selectedDate="2026-05-06" />,
    );
    expect(html).toContain('aria-current="true"');
  });

  it("calls onSelect with the row's date when a row is clicked", () => {
    const spy = vi.fn();
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    act(() => {
      root.render(<RecentDaysIndex days={DAYS} onSelect={spy} />);
    });

    const buttons = container.querySelectorAll("button");
    expect(buttons.length).toBe(2);
    act(() => {
      buttons[0].dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(spy).toHaveBeenCalledWith("2026-05-07");

    act(() => root.unmount());
  });
});
