// @vitest-environment jsdom
/**
 * DayBriefingCard — RTL tests (bu-jj0b3n).
 *
 * Covers:
 *  - Populated: grouped by butler/kind with chips for each underlying item.
 *  - has_domain_context=false → honest "no domain context" empty-state (never "clear").
 *  - has_domain_context=true but no entries → "nothing scheduled" (coverage ran).
 *  - Loading state.
 *  - Chip click links to the underlying item (onSelectEntry fired).
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

import type {
  DayBriefingButlerGroup,
  UnifiedCalendarEntry,
} from "@/api/types.ts";

import { DayBriefingCard } from "./DayBriefingCard.tsx";

afterEach(cleanup);

function overlayEntry(overrides: Partial<UnifiedCalendarEntry> = {}): UnifiedCalendarEntry {
  return {
    entry_id: "00000000-0000-0000-0000-000000000001",
    event_id: null,
    view: "overlays",
    source_type: "overlay_contribution",
    source_key: "overlays",
    title: "Electric Co",
    start_at: "2026-02-23T00:00:00+08:00",
    end_at: "2026-02-24T00:00:00+08:00",
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
      meta: { amount: 84, currency: "SGD" },
    },
    source_butler: "finance",
    source_session_id: null,
    ...overrides,
  };
}

function financeGroup(): DayBriefingButlerGroup {
  return {
    source_butler: "finance",
    count: 1,
    kinds: [{ kind: "bill_due", entries: [overlayEntry()] }],
  };
}

describe("DayBriefingCard", () => {
  it("renders grouped chips for a populated day", () => {
    render(
      <DayBriefingCard
        heading="Tomorrow · Mon, Feb 23"
        groups={[financeGroup()]}
        hasDomainContext
        hasEntries
      />,
    );

    const card = screen.getByTestId("day-briefing-card");
    expect(within(card).getByText("Tomorrow · Mon, Feb 23")).toBeTruthy();
    // Butler section + chip for the underlying item.
    expect(card.querySelector('[data-day-briefing-group="finance"]')).toBeTruthy();
    const chip = card.querySelector('[data-day-briefing-chip]');
    expect(chip).toBeTruthy();
    expect(within(card).getByText("Electric Co")).toBeTruthy();
    expect(screen.queryByTestId("day-briefing-clear")).toBeNull();
  });

  it("renders a failed overlay read as unavailable, not as no domain context", () => {
    render(
      <DayBriefingCard
        heading="Tomorrow · Mon, Feb 23"
        groups={[]}
        unavailable
        hasDomainContext={false}
        hasEntries={false}
      />,
    );
    expect(screen.getByTestId("day-briefing-unavailable").textContent).toMatch(/unavailable/i);
    expect(screen.queryByTestId("day-briefing-clear")).toBeNull();
  });

  it("keeps visible context and says the list may be incomplete when the read was partial", () => {
    render(
      <DayBriefingCard
        heading="Tomorrow · Mon, Feb 23"
        groups={[financeGroup()]}
        unavailable
        hasDomainContext
        hasEntries
      />,
    );
    const note = screen.getByTestId("day-briefing-partial");
    expect(note.textContent).toContain("may be incomplete");
    expect(note.getAttribute("role")).toBe("status");
    expect(screen.getByText("Electric Co")).toBeTruthy();
    expect(screen.queryByTestId("day-briefing-unavailable")).toBeNull();
  });

  it("shows an honest empty-state when no specialist contributed", () => {
    render(
      <DayBriefingCard
        heading="Tomorrow · Mon, Feb 23"
        groups={[]}
        hasDomainContext={false}
        hasEntries={false}
      />,
    );

    const clear = screen.getByTestId("day-briefing-clear");
    expect(clear.textContent).toMatch(/no domain context/i);
    expect(clear.textContent).not.toMatch(/clear/i);
    expect(screen.queryByText("Electric Co")).toBeNull();
  });

  it("shows a 'clear' state when a specialist contributed but had nothing", () => {
    render(
      <DayBriefingCard
        heading="Tomorrow · Mon, Feb 23"
        groups={[]}
        hasDomainContext
        hasEntries={false}
      />,
    );

    const clear = screen.getByTestId("day-briefing-clear");
    expect(clear.textContent).toMatch(/nothing scheduled/i);
    expect(clear.textContent).not.toMatch(/clear/i);
    // Distinct from the "no domain context" copy.
    expect(clear.textContent).not.toMatch(/no domain context/i);
  });

  it("renders a loading state", () => {
    render(
      <DayBriefingCard
        heading="Tomorrow"
        isLoading
        groups={[]}
        hasDomainContext={false}
        hasEntries={false}
      />,
    );
    expect(screen.getByText("Loading…")).toBeTruthy();
    expect(screen.queryByTestId("day-briefing-clear")).toBeNull();
  });

  it("keeps a previous briefing visible but dimmed while a new date is fetching", () => {
    render(
      <DayBriefingCard
        heading="Tomorrow"
        isFetching
        groups={[financeGroup()]}
        hasDomainContext
        hasEntries
      />,
    );

    expect(screen.getByText("Electric Co")).toBeTruthy();
    expect(screen.getByTestId("day-briefing-card").parentElement?.className).toContain(
      "opacity-60",
    );
  });

  it("shows a transport error instead of retained briefing data", () => {
    render(
      <DayBriefingCard
        heading="Tomorrow"
        isError
        error={new Error("briefing fetch failed")}
        groups={[financeGroup()]}
        hasDomainContext
        hasEntries
      />,
    );

    expect(screen.getByRole("alert").textContent).toContain("briefing fetch failed");
    expect(screen.queryByText("Electric Co")).toBeNull();
  });

  it("links a chip to its underlying item on click", () => {
    const onSelect = vi.fn();
    render(
      <DayBriefingCard
        heading="Tomorrow"
        groups={[financeGroup()]}
        hasDomainContext
        hasEntries
        onSelectEntry={onSelect}
      />,
    );

    const chip = screen.getByRole("button", { name: /Electric Co/ });
    fireEvent.click(chip);
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect.mock.calls[0][0].title).toBe("Electric Co");
  });
});

describe("DayBriefingCard freshness", () => {
  it("names a stale butler and says the list may not reflect recent changes", () => {
    render(
      <DayBriefingCard
        heading="Tomorrow"
        groups={[]}
        hasDomainContext
        hasEntries={false}
        freshness={[
          { butler: "finance", state: "stale", stale_since: "2026-02-24T10:00:00+00:00" },
          { butler: "travel", state: "ok", generated_at: "2026-02-24T09:00:00+00:00" },
        ]}
      />,
    );
    const plaque = screen.getByTestId("day-briefing-stale");
    expect(plaque.textContent).toContain("Finance context stale since 2026-02-24 10:00 UTC");
    expect(plaque.textContent).not.toContain("Travel");
  });

  it("shows no plaque when every butler is current", () => {
    render(
      <DayBriefingCard
        heading="Tomorrow"
        groups={[]}
        hasDomainContext
        hasEntries={false}
        freshness={[{ butler: "finance", state: "ok" }]}
      />,
    );
    expect(screen.queryByTestId("day-briefing-stale")).toBeNull();
  });
});
