// @vitest-environment jsdom
/**
 * DayBriefingCard — RTL tests (bu-jj0b3n).
 *
 * Covers:
 *  - Populated: grouped by butler/kind with chips for each underlying item.
 *  - has_domain_context=false → honest "tomorrow is clear" empty-state.
 *  - has_domain_context=true but no entries → "clear" (coverage ran).
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
    expect(clear.textContent).toMatch(/clear/i);
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
