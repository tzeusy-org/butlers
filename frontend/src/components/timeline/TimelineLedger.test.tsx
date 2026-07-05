// @vitest-environment jsdom
/**
 * Unit tests for TimelineLedger — the fleet chronicle's ledger view
 * (rebuilt on the ingestion dispatch ledger's component system, bu-86c4c.10).
 *
 * Covers:
 * - Hour grouping: events split into correct hour-bucket sections.
 * - Heartbeat collapsing: consecutive is_heartbeat events collapse into one
 *   row with an honest ticks/butlers rollup (not the old miscounted copy).
 * - Drawer: opens via `?event=<id>` in the URL, closes and clears the param.
 * - Loading / empty / error states.
 * - Load older button appears only when hasMore is true, and fires onLoadMore.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

import type { TimelineEvent } from "@/api/types.ts";
import { TimelineLedger } from "./TimelineLedger";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function makeEvent(id: string, timestamp: string, overrides: Partial<TimelineEvent> = {}): TimelineEvent {
  return {
    id,
    type: "session",
    butler: "home",
    timestamp,
    summary: `event ${id}`,
    is_heartbeat: false,
    data: {},
    ...overrides,
  };
}

let container: HTMLDivElement;
let root: Root;

function renderLedger(props: Partial<React.ComponentProps<typeof TimelineLedger>>, initialEntries = ["/timeline"]) {
  const defaultProps: React.ComponentProps<typeof TimelineLedger> = {
    events: [],
    isLoading: false,
    ...props,
  };
  act(() => {
    root.render(
      <MemoryRouter initialEntries={initialEntries}>
        <TimelineLedger {...defaultProps} />
      </MemoryRouter>,
    );
  });
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

describe("TimelineLedger — states", () => {
  it("renders loading skeleton", () => {
    renderLedger({ isLoading: true });
    expect(container.querySelectorAll('[class*="animate-pulse"]').length).toBeGreaterThan(0);
  });

  it("renders the error state, not the empty state, on fetch failure", () => {
    renderLedger({ isLoading: false, isError: true, events: [] });
    expect(container.textContent).toContain("Could not load the timeline.");
    expect(container.textContent).not.toContain("No events found.");
  });

  it("renders the empty state on a successful fetch with zero events", () => {
    renderLedger({ isLoading: false, isError: false, events: [] });
    expect(container.textContent).toContain("No events found.");
  });
});

describe("TimelineLedger — hour grouping", () => {
  it("splits events into separate hour-group sections", () => {
    const events = [
      makeEvent("e1", "2026-07-04T15:10:00Z"),
      makeEvent("e2", "2026-07-04T15:05:00Z"),
      makeEvent("e3", "2026-07-04T14:50:00Z"),
    ];
    renderLedger({ events });
    const groups = container.querySelectorAll('[data-testid="hour-group"]');
    expect(groups).toHaveLength(2);
    expect(groups[0].getAttribute("data-hour-key")).toBe("2026-07-04T15");
    expect(groups[1].getAttribute("data-hour-key")).toBe("2026-07-04T14");
  });
});

describe("TimelineLedger — heartbeat collapsing", () => {
  it("collapses consecutive is_heartbeat events with an honest ticks/butlers rollup", () => {
    const events = [
      makeEvent("hb1", "2026-07-04T15:03:00Z", { is_heartbeat: true, butler: "home" }),
      makeEvent("hb2", "2026-07-04T15:02:00Z", { is_heartbeat: true, butler: "atlas" }),
      makeEvent("hb3", "2026-07-04T15:01:00Z", { is_heartbeat: true, butler: "home" }),
    ];
    renderLedger({ events });
    const group = container.querySelector('[data-testid="heartbeat-group-row"]');
    expect(group).not.toBeNull();
    // 3 ticks across 2 distinct butlers — not "3 butlers ticked".
    expect(group!.textContent).toContain("3 ticks");
    expect(group!.textContent).toContain("2 butlers ticked");
  });

  it("does not collapse a single heartbeat event", () => {
    const events = [makeEvent("hb1", "2026-07-04T15:03:00Z", { is_heartbeat: true })];
    renderLedger({ events });
    expect(container.querySelector('[data-testid="heartbeat-group-row"]')).toBeNull();
    expect(container.querySelector('[data-testid="timeline-row"]')).not.toBeNull();
  });

  it("surfaces failed heartbeats in the rollup", () => {
    const events = [
      makeEvent("hb1", "2026-07-04T15:03:00Z", { is_heartbeat: true, data: { success: false } }),
      makeEvent("hb2", "2026-07-04T15:02:00Z", { is_heartbeat: true }),
    ];
    renderLedger({ events });
    const group = container.querySelector('[data-testid="heartbeat-group-row"]');
    expect(group!.textContent).toContain("1 failed");
  });
});

describe("TimelineLedger — drawer", () => {
  it("opens the drawer when ?event=<id> is in the URL", () => {
    const events = [makeEvent("e1", "2026-07-04T15:10:00Z", { type: "notification" })];
    renderLedger({ events }, ["/timeline?event=e1"]);
    expect(container.querySelector('[data-testid="timeline-event-drawer"]')).not.toBeNull();
  });

  it("does not render a drawer when no event is focused", () => {
    const events = [makeEvent("e1", "2026-07-04T15:10:00Z")];
    renderLedger({ events });
    expect(container.querySelector('[data-testid="timeline-event-drawer"]')).toBeNull();
  });

  it("clicking a row opens its drawer", () => {
    const events = [makeEvent("e1", "2026-07-04T15:10:00Z")];
    renderLedger({ events });
    const row = container.querySelector('[data-testid="timeline-row"]') as HTMLElement;
    act(() => {
      row.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="timeline-event-drawer"]')).not.toBeNull();
  });

  it("clicking the drawer's close button clears the event param", () => {
    const events = [makeEvent("e1", "2026-07-04T15:10:00Z")];
    renderLedger({ events }, ["/timeline?event=e1"]);
    const closeButton = container.querySelector('[data-testid="drawer-close-button"]') as HTMLElement;
    act(() => {
      closeButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="timeline-event-drawer"]')).toBeNull();
  });

  // Off-page ?event= resolution (bu-qvnce.13): a deep link to an event
  // outside the currently-loaded window must not silently render nothing.
  it("shows an honest not-found notice when ?event= points at an id outside the loaded window", () => {
    const events = [makeEvent("e1", "2026-07-04T15:10:00Z")];
    renderLedger({ events }, ["/timeline?event=nonexistent"]);
    expect(container.querySelector('[data-testid="timeline-event-not-found"]')).not.toBeNull();
    expect(container.textContent).toContain("nonexistent");
    expect(container.querySelector('[data-testid="timeline-event-drawer"]')).toBeNull();
  });

  it("does not show the not-found notice when the ?event= id is loaded", () => {
    const events = [makeEvent("e1", "2026-07-04T15:10:00Z")];
    renderLedger({ events }, ["/timeline?event=e1"]);
    expect(container.querySelector('[data-testid="timeline-event-not-found"]')).toBeNull();
  });

  it("shows the not-found notice even when the loaded window is empty", () => {
    renderLedger({ events: [] }, ["/timeline?event=nonexistent"]);
    expect(container.querySelector('[data-testid="timeline-event-not-found"]')).not.toBeNull();
    expect(container.textContent).toContain("No events found.");
  });

  it("clearing the not-found notice removes the ?event= param", () => {
    const events = [makeEvent("e1", "2026-07-04T15:10:00Z")];
    renderLedger({ events }, ["/timeline?event=nonexistent"]);
    const dismiss = container.querySelector(
      '[data-testid="timeline-event-not-found-dismiss"]',
    ) as HTMLElement;
    act(() => {
      dismiss.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="timeline-event-not-found"]')).toBeNull();
  });
});

describe("TimelineLedger — pagination", () => {
  it("does not render Load older when hasMore is false", () => {
    renderLedger({ events: [makeEvent("e1", "2026-07-04T15:10:00Z")], hasMore: false, onLoadMore: vi.fn() });
    expect(container.textContent).not.toContain("Load older");
  });

  it("keeps the Load older button visible (as 'Loading…') while a fetch is in flight", () => {
    // hasMore flips false the instant loadMore() is called (before the older
    // page resolves) — isLoadingMore must keep the button from vanishing
    // during that gap instead of hiding the only loading affordance.
    renderLedger({
      events: [makeEvent("e1", "2026-07-04T15:10:00Z")],
      hasMore: false,
      isLoadingMore: true,
      onLoadMore: vi.fn(),
    });
    expect(container.textContent).toContain("Loading…");
  });

  it("renders Load older and fires onLoadMore on click", () => {
    const onLoadMore = vi.fn();
    renderLedger({
      events: [makeEvent("e1", "2026-07-04T15:10:00Z")],
      hasMore: true,
      onLoadMore,
    });
    const button = Array.from(container.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("Load older"),
    );
    expect(button).toBeTruthy();
    act(() => {
      button!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(onLoadMore).toHaveBeenCalledTimes(1);
  });
});
