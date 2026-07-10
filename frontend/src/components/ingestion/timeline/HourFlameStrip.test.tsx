// @vitest-environment jsdom
/**
 * Unit tests for HourFlameStrip (bu-4utdw.7).
 *
 * Covers:
 * - bucket alignment: 1m and 5m granularities produce the right slot count
 * - status stacking: an all-error minute renders solid destructive color
 * - zero-count minutes render as a hairline border-color bar
 * - honesty: totals reflect histogram counts, not a derived/loaded fallback
 * - interaction: click fires onMinuteClick with the minute's ISO + counts
 * - a11y: group aria-label is a summary, not aria-hidden; buttons are
 *   keyboard-focusable and reveal a hover/focus label
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, type ComponentProps } from "react";
import { createRoot, type Root } from "react-dom/client";

import type { IngestionHistogramBucket, IngestionHistogramCounts } from "@/api/index.ts";
import { AppTimezoneProvider } from "@/components/ui/timezone-context";
import { HourFlameStrip } from "./HourFlameStrip";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

// Render with a fixed UTC timezone so HH:mm labels match the UTC fixture
// timestamps directly — the component otherwise reads the owner timezone
// from AppTimezoneContext (default Asia/Singapore, UTC+8).
function renderStrip(props: ComponentProps<typeof HourFlameStrip>) {
  return (
    <AppTimezoneProvider timezone="UTC">
      <HourFlameStrip {...props} />
    </AppTimezoneProvider>
  );
}

function counts(overrides: Partial<IngestionHistogramCounts> = {}): IngestionHistogramCounts {
  return {
    ingested: 0,
    skipped: 0,
    filtered: 0,
    error: 0,
    failed: 0,
    replay_pending: 0,
    replay_complete: 0,
    replay_failed: 0,
    ...overrides,
  };
}

function bucket(ts: string, overrides: Partial<IngestionHistogramCounts> = {}): IngestionHistogramBucket {
  return { ts, counts: counts(overrides) };
}

const HOUR_START = "2026-05-17T14:00:00Z";

describe("HourFlameStrip", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it("renders 60 minute buttons for a 1-minute bucket", () => {
    act(() => {
      root.render(renderStrip({ hourStart: HOUR_START, buckets: [], bucketMinutes: 1 }));
    });
    const buttons = container.querySelectorAll("[data-testid='hour-strip-minute']");
    expect(buttons).toHaveLength(60);
  });

  it("renders 12 minute buttons for a 5-minute bucket", () => {
    act(() => {
      root.render(renderStrip({ hourStart: HOUR_START, buckets: [], bucketMinutes: 5 }));
    });
    const buttons = container.querySelectorAll("[data-testid='hour-strip-minute']");
    expect(buttons).toHaveLength(12);
  });

  it("renders a zero-count minute as a hairline border-color bar", () => {
    act(() => {
      root.render(renderStrip({ hourStart: HOUR_START, buckets: [], bucketMinutes: 1 }));
    });
    const firstButton = container.querySelector("[data-testid='hour-strip-minute']");
    expect(firstButton).not.toBeNull();
    expect(firstButton!.querySelector(".bg-border")).not.toBeNull();
    expect(firstButton!.getAttribute("data-has-error")).toBeNull();
  });

  it("renders an all-error minute as solid destructive color", () => {
    const buckets = [bucket("2026-05-17T14:05:00Z", { error: 4 })];
    act(() => {
      root.render(renderStrip({ hourStart: HOUR_START, buckets, bucketMinutes: 1 }));
    });
    const errorButton = container.querySelector(
      "[data-testid='hour-strip-minute'][data-minute-iso='2026-05-17T14:05:00.000Z']",
    );
    expect(errorButton).not.toBeNull();
    expect(errorButton!.getAttribute("data-has-error")).toBe("true");
    const segments = errorButton!.querySelectorAll(":scope > div > div");
    // All-error minute: exactly one segment, colored destructive, filling 100%.
    expect(segments).toHaveLength(1);
    expect(segments[0].classList.contains("bg-destructive")).toBe(true);
    expect((segments[0] as HTMLElement).style.height).toBe("100%");
  });

  it("counts 'failed' (routing failure after ingestion, bu-lkzsf.1) into the same destructive segment as 'error'", () => {
    const buckets = [bucket("2026-05-17T14:06:00Z", { error: 1, failed: 3 })];
    act(() => {
      root.render(renderStrip({ hourStart: HOUR_START, buckets, bucketMinutes: 1 }));
    });
    const failedButton = container.querySelector(
      "[data-testid='hour-strip-minute'][data-minute-iso='2026-05-17T14:06:00.000Z']",
    );
    expect(failedButton).not.toBeNull();
    expect(failedButton!.getAttribute("data-has-error")).toBe("true");
    const segments = failedButton!.querySelectorAll(":scope > div > div");
    // 4 total (1 error + 3 failed), one merged destructive segment, filling 100%.
    expect(segments).toHaveLength(1);
    expect(segments[0].classList.contains("bg-destructive")).toBe(true);
    expect((segments[0] as HTMLElement).style.height).toBe("100%");
    expect(failedButton!.getAttribute("aria-label")).toContain("4 errors");
  });

  it("stacks mixed-status minutes with error, replay, ingested, and filtered/skipped segments", () => {
    const buckets = [
      bucket("2026-05-17T14:10:00Z", {
        error: 1,
        replay_pending: 1,
        ingested: 2,
        filtered: 1,
        skipped: 1,
      }),
    ];
    act(() => {
      root.render(renderStrip({ hourStart: HOUR_START, buckets, bucketMinutes: 1 }));
    });
    const minuteButton = container.querySelector(
      "[data-testid='hour-strip-minute'][data-minute-iso='2026-05-17T14:10:00.000Z']",
    );
    expect(minuteButton).not.toBeNull();
    const segmentClasses = Array.from(minuteButton!.querySelectorAll(":scope > div > div")).map(
      (el) => el.className,
    );
    expect(segmentClasses).toEqual([
      "bg-destructive",
      "bg-blue-500",
      "bg-foreground/30",
      "bg-foreground/10",
    ]);
  });

  it("fires onMinuteClick with the minute's ISO timestamp and counts", () => {
    const onMinuteClick = vi.fn();
    const buckets = [bucket("2026-05-17T14:20:00Z", { error: 3 })];
    act(() => {
      root.render(renderStrip({ hourStart: HOUR_START, buckets, bucketMinutes: 1, onMinuteClick }));
    });
    const target = container.querySelector(
      "[data-testid='hour-strip-minute'][data-minute-iso='2026-05-17T14:20:00.000Z']",
    ) as HTMLElement;
    act(() => {
      target.click();
    });
    expect(onMinuteClick).toHaveBeenCalledWith("2026-05-17T14:20:00.000Z", { ...counts(), error: 3 });
  });

  it("fires onMinuteClick with null counts for an empty minute", () => {
    const onMinuteClick = vi.fn();
    act(() => {
      root.render(renderStrip({ hourStart: HOUR_START, buckets: [], bucketMinutes: 1, onMinuteClick }));
    });
    const target = container.querySelector("[data-testid='hour-strip-minute']") as HTMLElement;
    act(() => {
      target.click();
    });
    expect(onMinuteClick).toHaveBeenCalledWith("2026-05-17T14:00:00.000Z", null);
  });

  it("exposes a group aria-label summary instead of aria-hidden", () => {
    const buckets = [bucket("2026-05-17T14:05:00Z", { error: 2 })];
    act(() => {
      root.render(renderStrip({ hourStart: HOUR_START, buckets, bucketMinutes: 1 }));
    });
    const group = container.querySelector("[role='group']");
    expect(group).not.toBeNull();
    expect(group!.getAttribute("aria-hidden")).toBeNull();
    expect(group!.getAttribute("aria-label")).toContain("14:00");
    expect(group!.getAttribute("aria-label")).toContain("2 errors");
  });

  it("shows a hover label with time and per-status counts", () => {
    const buckets = [bucket("2026-05-17T14:12:00Z", { error: 3, filtered: 9 })];
    act(() => {
      root.render(renderStrip({ hourStart: HOUR_START, buckets, bucketMinutes: 1 }));
    });
    expect(container.querySelector("[data-testid='hour-strip-tooltip']")).toBeNull();

    const target = container.querySelector(
      "[data-testid='hour-strip-minute'][data-minute-iso='2026-05-17T14:12:00.000Z']",
    ) as HTMLElement;

    act(() => {
      target.focus();
    });
    const tooltip = container.querySelector("[data-testid='hour-strip-tooltip']");
    expect(tooltip).not.toBeNull();
    expect(tooltip!.textContent).toContain("14:12");
    expect(tooltip!.textContent).toContain("3 errors");
    expect(tooltip!.textContent).toContain("9 filtered");
  });

  it("minute buttons are native buttons, reachable via keyboard focus", () => {
    act(() => {
      root.render(renderStrip({ hourStart: HOUR_START, buckets: [], bucketMinutes: 1 }));
    });
    const buttons = Array.from(container.querySelectorAll("[data-testid='hour-strip-minute']"));
    expect(buttons.every((b) => b.tagName === "BUTTON")).toBe(true);
    const target = buttons[3] as HTMLElement;
    act(() => {
      target.focus();
    });
    expect(document.activeElement).toBe(target);
  });

  it("ignores buckets outside the hour window", () => {
    const buckets = [
      bucket("2026-05-17T13:59:00Z", { error: 5 }), // before hour start
      bucket("2026-05-17T15:00:00Z", { error: 5 }), // after hour end
    ];
    act(() => {
      root.render(renderStrip({ hourStart: HOUR_START, buckets, bucketMinutes: 1 }));
    });
    const errored = container.querySelectorAll("[data-has-error='true']");
    expect(errored).toHaveLength(0);
  });
});
