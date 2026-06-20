// @vitest-environment jsdom

/**
 * Tests for the editorial, date-navigable ChroniclesPage.
 *
 * SSR smoke tests verify the editorial layout (date eyebrow + stepper, headline,
 * voice paragraph, attention list, KPI strip, recent-days index) and the
 * stale-only provenance indicator. Interaction tests (react-dom/client) verify
 * the date stepper and deep-link drive the briefing request and clamp at the
 * most recent settled day.
 *
 * Drilldown internals live in ChroniclesDrilldownPanel and are exercised by the
 * component-level tests under frontend/src/components/chronicles/.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ChroniclesPage from "@/pages/ChroniclesPage";
import type { ChroniclesBriefing } from "@/api/types";

// react-dom/client + act() need this flag set in a non-browser test env.
(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/components/ui/timezone-context", () => ({
  useTimezone: () => "Asia/Singapore",
}));

let _briefing: ChroniclesBriefing | undefined;
let _briefingArgs: { date?: string; tz?: string } | undefined;

vi.mock("@/hooks/use-chronicles-briefing", () => ({
  useChroniclesBriefing: (args: { date?: string; tz?: string } = {}) => {
    _briefingArgs = args;
    return {
      data: _briefing,
      isFetching: false,
      isError: false,
      refetch: vi.fn(),
    };
  },
}));

// The drilldown panel pulls in heavy modules (Gantt, Map, Scrubber). For these
// editorial smoke tests we stub it out; content visibility is tested in its
// own component spec.
vi.mock("@/components/chronicles/ChroniclesDrilldownPanel", () => ({
  ChroniclesDrilldownPanel: () => (
    <section aria-label="Chronicles drilldown stub" data-testid="drilldown" />
  ),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderPage(entry = "/chronicles"): string {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <ChroniclesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function mountPage(entry = "/chronicles"): { container: HTMLElement; unmount: () => void } {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[entry]}>
          <ChroniclesPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  return {
    container,
    unmount: () => act(() => root.unmount()),
  };
}

function buildBriefing(overrides: Partial<ChroniclesBriefing> = {}): ChroniclesBriefing {
  return {
    date: "2026-05-08",
    state_class: "quiet",
    headline: "Quiet day.",
    voice_paragraph: "The day was led by conversations at 2.4 hours. Nothing needs attention.",
    voice_source: "templated",
    kpi: {
      hours_by_top_lanes: [
        { lane: "conversations", hours: 2.4 },
        { lane: "calendar", hours: 1.1 },
      ],
      longest_episode_minutes: 95,
      longest_episode_title: "Conversation with Anna",
      longest_gap_minutes: 312,
      sleep_minutes: 432,
      streaks: { sleep: 4, exercise: 2 },
    },
    attention_items: [],
    recent_days: [
      { date: "2026-05-07", total_minutes: 642, top_lane: "conversations", episode_count: 23 },
    ],
    earliest_date: "2026-01-01",
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ChroniclesPage editorial archetype", () => {
  beforeEach(() => {
    _briefing = undefined;
    _briefingArgs = undefined;
  });

  afterEach(() => {
    vi.useRealTimers();
    document.body.innerHTML = "";
  });

  it("renders headline, voice paragraph, KPI strip, and recent days", () => {
    _briefing = buildBriefing();
    const html = renderPage();
    expect(html).toContain("Quiet day.");
    expect(html).toContain("The day was led by conversations");
    // KPI top-lane cell now shows the hours as the number and the lane as delta.
    expect(html).toContain("2.4h");
    expect(html).toContain("conversations");
    expect(html).toContain("Sleep");
    expect(html).toContain("Recent days");
  });

  it("renders the date stepper controls", () => {
    _briefing = buildBriefing();
    const html = renderPage();
    expect(html).toContain('aria-label="Previous day"');
    expect(html).toContain('aria-label="Next day"');
  });

  it("renders the drilldown panel", () => {
    _briefing = buildBriefing();
    const html = renderPage();
    expect(html).toContain("Chronicles drilldown stub");
  });

  it("shows no provenance label for a templated briefing", () => {
    _briefing = buildBriefing({ voice_source: "templated" });
    const html = renderPage();
    expect(html).not.toContain("templated");
    expect(html).not.toContain("llm · cached");
  });

  it("shows no provenance label for a cached briefing", () => {
    _briefing = buildBriefing({ voice_source: "llm·cached" });
    const html = renderPage();
    expect(html).not.toContain("llm · cached");
    expect(html).not.toContain("cached");
  });

  it("surfaces a quiet stale indicator only when the briefing is stale", () => {
    _briefing = buildBriefing({ voice_source: "stale" });
    const html = renderPage();
    expect(html).toContain("stale");
  });

  it("voice rules: no em-dashes or exclamation marks in headline or voice paragraph copy", () => {
    _briefing = buildBriefing({
      headline: "5 things need attention.",
      voice_paragraph: "Sleep was logged at 7h 12m. Nothing needs attention.",
      state_class: "urgent",
      attention_items: [
        { kind: "anomaly", severity: "high", title: "Short sleep", detail: null, action_href: null },
      ],
    });
    const html = renderPage();
    expect(_briefing.headline).not.toContain("!");
    expect(_briefing.voice_paragraph).not.toContain("!");
    expect(html).toContain("5 things need attention.");
    expect(html).toContain("Nothing needs attention.");
    expect(_briefing.headline).not.toContain("—");
    expect(_briefing.voice_paragraph).not.toContain("—");
  });

  it("renders 'Nothing waiting.' when there are no attention items", () => {
    _briefing = buildBriefing();
    const html = renderPage();
    expect(html).toContain("Nothing waiting.");
  });

  it("requests yesterday in the owner timezone by default", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-05-09T16:30:00.000Z"));
    _briefing = buildBriefing({ date: "2026-05-09" });

    renderPage();

    // 2026-05-09T16:30Z is 2026-05-10 00:30 in SGT, so yesterday is 2026-05-09.
    expect(_briefingArgs).toEqual({ date: "2026-05-09", tz: "Asia/Singapore" });
  });

  it("requests the deep-linked date from the URL", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-05-09T16:30:00.000Z"));
    _briefing = buildBriefing({ date: "2026-05-03" });

    renderPage("/chronicles?date=2026-05-03");

    expect(_briefingArgs?.date).toBe("2026-05-03");
  });

  it("steps the requested date backward and clamps the next button at yesterday", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-05-09T16:30:00.000Z"));
    _briefing = buildBriefing({ date: "2026-05-09" });

    const { container, unmount } = mountPage();

    // Default day is yesterday (2026-05-09), so "next" is disabled.
    const next = container.querySelector('button[aria-label="Next day"]') as HTMLButtonElement;
    const prev = container.querySelector('button[aria-label="Previous day"]') as HTMLButtonElement;
    expect(next.disabled).toBe(true);
    expect(prev.disabled).toBe(false);

    act(() => {
      prev.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(_briefingArgs?.date).toBe("2026-05-08");
    unmount();
  });

  it("forward-clamps an out-of-range (future) deep link to the most recent settled day", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-05-09T16:30:00.000Z"));
    _briefing = buildBriefing();

    const { unmount } = mountPage("/chronicles?date=2099-01-01");

    // The self-heal path resolves a future deep link to yesterday.
    expect(_briefingArgs?.date).toBe("2026-05-09");
    unmount();
  });

  it("selects a day when a recent-days row is clicked", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-05-09T16:30:00.000Z"));
    _briefing = buildBriefing({
      recent_days: [
        { date: "2026-05-05", total_minutes: 120, top_lane: "tasks", episode_count: 4 },
      ],
    });

    const { container, unmount } = mountPage();
    const row = container.querySelector('button[aria-label^="View "]') as HTMLButtonElement;
    expect(row).toBeTruthy();
    act(() => {
      row.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(_briefingArgs?.date).toBe("2026-05-05");
    unmount();
  });

  it("disables the previous-day button at the earliest chronicled day", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-05-09T16:30:00.000Z"));
    _briefing = buildBriefing({ date: "2026-01-01", earliest_date: "2026-01-01" });

    const { container, unmount } = mountPage("/chronicles?date=2026-01-01");
    const prev = container.querySelector('button[aria-label="Previous day"]') as HTMLButtonElement;
    const next = container.querySelector('button[aria-label="Next day"]') as HTMLButtonElement;
    expect(prev.disabled).toBe(true);
    expect(next.disabled).toBe(false);
    unmount();
  });

  it("leaves backward navigation open when earliest_date is null", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-05-09T16:30:00.000Z"));
    _briefing = buildBriefing({ date: "2026-03-01", earliest_date: null });

    const { container, unmount } = mountPage("/chronicles?date=2026-03-01");
    const prev = container.querySelector('button[aria-label="Previous day"]') as HTMLButtonElement;
    expect(prev.disabled).toBe(false);
    unmount();
  });

  it("renders KPI and greeting edge branches", () => {
    _briefing = buildBriefing({
      state_class: "urgent",
      headline: "2 things need attention.",
      kpi: {
        hours_by_top_lanes: [],
        longest_episode_minutes: 0,
        longest_episode_title: null,
        longest_gap_minutes: 400, // >= 6h
        sleep_minutes: 0,
        streaks: { sleep: 0, exercise: 0 },
      },
    });
    const html = renderPage();
    expect(html).toContain("no lane data");
    expect(html).toContain("above 6h waking");
    // urgent state predicate, with the most-recent-day subject.
    expect(html).toContain("had loose ends.");
  });
});
