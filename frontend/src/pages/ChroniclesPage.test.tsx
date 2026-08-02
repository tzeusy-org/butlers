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
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, useNavigate } from "react-router";
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
  useTimezone: () => _timezone,
}));

let _briefing: ChroniclesBriefing | undefined;
let _briefingArgs: { date?: string; tz?: string } | undefined;
let _isFetching = false;
let _isError = false;
let _isPlaceholderData = false;
let _refetch = vi.fn();
let _drilldownArgs: { date: string; tz: string } | undefined;
let _navigate: ((to: string) => void) | undefined;
let _timezone = "Asia/Singapore";

vi.mock("@/hooks/use-chronicles-briefing", () => ({
  useChroniclesBriefing: (args: { date?: string; tz?: string } = {}) => {
    _briefingArgs = args;
    return {
      data: _briefing,
      isFetching: _isFetching,
      isError: _isError,
      isPlaceholderData: _isPlaceholderData,
      refetch: _refetch,
    };
  },
}));

// The drilldown panel pulls in heavy modules (Gantt, Map, Scrubber). For these
// editorial smoke tests we stub it out; content visibility is tested in its
// own component spec.
vi.mock("@/components/chronicles/ChroniclesDrilldownPanel", () => ({
  ChroniclesDrilldownPanel: (args: { date: string; tz: string }) => {
    _drilldownArgs = args;
    return <section aria-label="Chronicles drilldown stub" data-testid="drilldown" />;
  },
}));

function NavigationHarness() {
  const navigate = useNavigate();
  useEffect(() => {
    _navigate = navigate;
    return () => {
      _navigate = undefined;
    };
  }, [navigate]);
  return null;
}

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

function mountPage(entry = "/chronicles"): {
  container: HTMLElement;
  navigate: (to: string) => void;
  unmount: () => void;
} {
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
          <NavigationHarness />
          <ChroniclesPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  return {
    container,
    navigate: (to) => {
      act(() => {
        if (_navigate === undefined) {
          throw new Error("Navigation harness was not mounted");
        }
        _navigate(to);
      });
    },
    unmount: () => act(() => root.unmount()),
  };
}

function buildBriefing(overrides: Partial<ChroniclesBriefing> = {}): ChroniclesBriefing {
  return {
    date: "2026-05-08",
    state_class: "quiet",
    headline: "Quiet day.",
    voice_paragraph: "The day was led by butler_ops at 2.4 hours. Nothing needs attention.",
    voice_source: "templated",
    kpi: {
      hours_by_top_lanes: [
        { lane: "butler_ops", hours: 2.4 },
        { lane: "play", hours: 1.1 },
      ],
      longest_episode_minutes: 95,
      longest_episode_title: "Conversation with Anna",
      longest_gap_minutes: 312,
      sleep_minutes: 432,
      streaks: { sleep: 4, exercise: 2 },
    },
    attention_items: [],
    recent_days: [
      { date: "2026-05-07", total_minutes: 642, top_lane: "butler_ops", episode_count: 23 },
    ],
    earliest_date: "2026-01-01",
    subquery_availability: [],
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ChroniclesPage editorial archetype", () => {
  beforeEach(() => {
    // Default rendering targets yesterday in SGT, which makes the default
    // fixture's date (2026-05-08) an exact response-date match.
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-05-08T16:30:00.000Z"));
    _briefing = undefined;
    _briefingArgs = undefined;
    _isFetching = false;
    _isError = false;
    _isPlaceholderData = false;
    _refetch = vi.fn();
    _drilldownArgs = undefined;
    _navigate = undefined;
    _timezone = "Asia/Singapore";
  });

  afterEach(() => {
    vi.useRealTimers();
    document.body.innerHTML = "";
  });

  it("renders headline, voice paragraph, KPI strip, and recent days", () => {
    _briefing = buildBriefing();
    const html = renderPage();
    expect(html).toContain("Quiet day.");
    expect(html).toContain("The day was led by butler_ops");
    // KPI top-lane cell now shows the hours as the number and the lane as delta.
    expect(html).toContain("2.4h");
    expect(html).toContain("butler_ops");
    expect(html).toContain("Sleep");
    expect(html).toContain("Recent days");
  });

  it("renders a legacy briefing response without the availability ledger", () => {
    const legacyBriefing = buildBriefing();
    delete legacyBriefing.subquery_availability;
    _briefing = legacyBriefing;

    const html = renderPage();

    expect(html).toContain("Quiet day.");
    expect(html).toContain("The day was led by butler_ops");
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
      date: "2026-05-09",
      recent_days: [
        { date: "2026-05-05", total_minutes: 120, top_lane: "butler_ops", episode_count: 4 },
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

  it("preserves a valid pre-floor deep link, blocks further backward travel, and recovers forward", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-05-09T16:30:00.000Z"));
    _briefing = buildBriefing({
      date: "2025-12-31",
      state_class: "no_data",
      headline: "Before the chronicled archive.",
      voice_paragraph: "This day is before the earliest day the archive can confirm was chronicled.",
      earliest_date: "2026-01-01",
      attention_items: [],
      recent_days: [],
    });

    const { container, unmount } = mountPage("/chronicles?date=2025-12-31");
    try {
      const prev = container.querySelector('button[aria-label="Previous day"]') as HTMLButtonElement;
      const next = container.querySelector('button[aria-label="Next day"]') as HTMLButtonElement;
      expect(_briefingArgs?.date).toBe("2025-12-31");
      expect(prev.disabled).toBe(true);
      expect(next.disabled).toBe(false);
      expect(_drilldownArgs).toBeUndefined();

      act(() => {
        next.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });

      expect(_briefingArgs?.date).toBe("2026-01-01");
    } finally {
      unmount();
    }
  });

  it("does not render a covered day's placeholder briefing after navigating to a pre-floor URL", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-05-08T16:30:00.000Z"));
    _briefing = buildBriefing({
      date: "2026-05-08",
      headline: "Covered day headline that must not leak.",
      voice_paragraph: "Covered day prose that must not leak.",
      voice_source: "stale",
    });

    const { container, navigate, unmount } = mountPage("/chronicles?date=2026-05-08");
    try {
      expect(container.textContent).toContain("Covered day headline that must not leak.");
      _isFetching = true;

      navigate("/chronicles?date=2025-12-31");

      expect(_briefingArgs?.date).toBe("2025-12-31");
      expect(container.querySelector('[data-testid="workspace-skeleton"]')).toBeTruthy();
      expect(container.textContent).not.toContain("Covered day headline that must not leak.");
      expect(container.textContent).not.toContain("Covered day prose that must not leak.");
      expect(container.textContent).not.toContain("2.4h");
      expect(
        container.querySelector('[aria-label="Day-close summary may be out of date"]'),
      ).toBeNull();
      expect(container.textContent).not.toContain("Chronicles drilldown stub");
    } finally {
      unmount();
    }
  });

  it("treats same-date cross-timezone placeholder data as loading", () => {
    // Both owner timezones resolve this instant to the same settled local day.
    // The query key still changes because the coverage witness and day-close
    // prose are timezone-specific.
    vi.setSystemTime(new Date("2026-05-09T12:00:00.000Z"));
    _briefing = buildBriefing({
      date: "2026-05-08",
      headline: "Singapore headline that must not leak.",
      voice_paragraph: "Singapore prose that must not leak.",
      attention_items: [
        {
          kind: "source_error",
          severity: "high",
          title: "Singapore coverage that must not leak.",
          detail: "Timezone-specific archive coverage.",
          action_href: null,
        },
      ],
    });

    const { container, navigate, unmount } = mountPage("/chronicles?date=2026-05-08");
    try {
      expect(container.textContent).toContain("Singapore headline that must not leak.");
      expect(container.textContent).toContain("Singapore prose that must not leak.");
      expect(container.textContent).toContain("Singapore coverage that must not leak.");
      expect(container.textContent).toContain("2.4h");
      expect(container.querySelector('[aria-label="Previous day"]')).toBeTruthy();
      expect(container.querySelector('[aria-label="Next day"]')).toBeTruthy();

      _timezone = "America/Los_Angeles";
      _isFetching = true;
      _isPlaceholderData = true;
      navigate("/chronicles?date=2026-05-08&timezone-transition=1");

      expect(_briefingArgs).toEqual({ date: "2026-05-08", tz: "America/Los_Angeles" });
      expect(container.querySelector('[data-testid="workspace-skeleton"]')).toBeTruthy();
      expect(container.textContent).not.toContain("Singapore headline that must not leak.");
      expect(container.textContent).not.toContain("Singapore prose that must not leak.");
      expect(container.textContent).not.toContain("Singapore coverage that must not leak.");
      expect(container.textContent).not.toContain("2.4h");
      expect(container.querySelector('[aria-label="Previous day"]')).toBeNull();
      expect(container.querySelector('[aria-label="Next day"]')).toBeNull();
      expect(container.textContent).not.toContain("Chronicles drilldown stub");
    } finally {
      unmount();
    }
  });

  it("disables backward navigation while the archive boundary is unavailable", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-05-09T16:30:00.000Z"));
    _briefing = buildBriefing({ date: "2026-03-01", earliest_date: null });

    const { container, unmount } = mountPage("/chronicles?date=2026-03-01");
    try {
      const prev = container.querySelector(
        'button[aria-label="Previous day: archive boundary unavailable"]',
      ) as HTMLButtonElement;
      expect(prev.disabled).toBe(true);
      expect(prev.title).toBe("Archive boundary unavailable");
    } finally {
      unmount();
    }
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

  // ---------------------------------------------------------------------
  // A same-date refresh keeps its matching content on screen (dimmed), while
  // a cross-date placeholder takes the safe loading path tested above. An
  // in-place dim also must never mask a real error.
  // ---------------------------------------------------------------------

  it("keeps matching content visible (not the full skeleton) while it is fetching", () => {
    _briefing = buildBriefing({ headline: "Quiet day." });
    _isFetching = true;
    const html = renderPage();
    // Matching data may be refreshed in place rather than showing the editorial
    // WorkspaceSkeleton (ui/page.tsx's WorkspaceSkeleton is marked with
    // data-testid="workspace-skeleton").
    expect(html).not.toContain("workspace-skeleton");
    expect(html).toContain("Quiet day.");
  });

  it("dims matching content with FetchingDim while a refetch is in flight", () => {
    _briefing = buildBriefing();
    _isFetching = true;
    const html = renderPage();
    expect(html).toContain("opacity-60");
  });

  it("does not double-dim the Voice paragraph inside the stale-content wrapper", () => {
    _briefing = buildBriefing();
    _isFetching = true;
    const container = document.createElement("div");
    container.innerHTML = renderPage();
    const voiceParagraph = Array.from(container.querySelectorAll("p")).find((paragraph) =>
      paragraph.textContent?.includes("The day was led by butler_ops"),
    );

    expect(voiceParagraph?.parentElement?.getAttribute("aria-busy")).toBe("false");
    expect(voiceParagraph?.parentElement?.className).not.toContain("opacity-60");
  });

  it("undims once the new day's data settles", () => {
    _briefing = buildBriefing();
    _isFetching = false;
    const html = renderPage();
    expect(html).not.toContain("opacity-60");
  });

  it("surfaces the error state instead of masking it behind dimmed stale data", () => {
    // Stale data can still be present in the cache (e.g. a previous day's
    // briefing) when a day-step request fails; the error must win, not the
    // dimmed stale render.
    _briefing = buildBriefing({ headline: "Quiet day." });
    _isError = true;
    _isFetching = false;
    const html = renderPage();
    expect(html).toContain("Something went wrong");
  });

  // ---------------------------------------------------------------------
  // Non-content states (bu-ep4ks.1): no_data/unavailable/degraded must never
  // render with quiet-day copy or the Attention/KPI content -- the exact
  // fabricated-calm failure clarify-chronicles-narrative-truth exists to
  // eliminate (an outage or an unproven historical day narrating as a
  // quiet day).
  // ---------------------------------------------------------------------

  describe.each([
    ["no_data", "Before the chronicled archive.", "is before the chronicled archive."],
    ["unavailable", "Coverage for this day could not be confirmed.", "could not be confirmed."],
    ["degraded", "Coverage for this day is degraded.", "has degraded coverage."],
  ] as const)("non-content state %s", (stateClass, headline, predicate) => {
    it(`renders ${stateClass} distinctly, never as a quiet day`, () => {
      _briefing = buildBriefing({
        state_class: stateClass,
        headline,
        voice_paragraph: "Deterministic state-specific copy.",
        voice_source: "templated",
        attention_items: [],
        kpi: {
          hours_by_top_lanes: [],
          longest_episode_minutes: 0,
          longest_episode_title: null,
          longest_gap_minutes: 0,
          sleep_minutes: 0,
          streaks: { sleep: 0, exercise: 0 },
        },
        recent_days: [],
      });
      const html = renderPage();

      expect(html).not.toContain("Quiet day.");
      expect(html).not.toContain("was quiet.");
      expect(html).not.toContain("Nothing waiting.");
      expect(html).toContain(headline);
      expect(html).toContain(predicate);
      expect(html).toContain("Deterministic state-specific copy.");
      expect(html).toContain(stateClass.replace("_", " "));
      expect(html).not.toContain("Chronicles drilldown stub");
    });
  });

  it("names a degraded briefing source and retries the briefing on demand", () => {
    _briefing = buildBriefing({
      state_class: "degraded",
      headline: "Coverage for this day is degraded.",
      voice_paragraph: "Chronicler's coverage for this day is degraded and may be incomplete.",
      attention_items: [
        {
          kind: "source_error",
          severity: "high",
          title: "Episodes unavailable",
          detail: "Chronicler could not read episodes.",
          action_href: null,
        },
      ],
      recent_days: [],
      subquery_availability: [{ subquery: "episodes", state: "unavailable" }],
    });

    const { container, unmount } = mountPage();
    try {
      expect(container.textContent).toContain("Episodes unavailable");
      const alert = container.querySelector('[role="alert"]');
      expect(alert?.textContent).toContain("Episodes unavailable");
      const retry = Array.from(container.querySelectorAll("button")).find(
        (button) => button.textContent === "Retry",
      ) as HTMLButtonElement;
      expect(retry).toBeTruthy();

      act(() => {
        retry.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });

      expect(_refetch).toHaveBeenCalledOnce();
    } finally {
      unmount();
    }
  });

  it("renders the quiet day normally when the state truly is quiet", () => {
    _briefing = buildBriefing({ state_class: "quiet", headline: "Quiet day." });
    const html = renderPage();
    expect(html).toContain("Quiet day.");
    expect(html).toContain("was quiet.");
  });

  it("fails closed for an unrecognized state_class without leaking stale editorial content", () => {
    _briefing = buildBriefing({
      // Cast: simulating a future backend value this build predates.
      state_class: "mystery" as ChroniclesBriefing["state_class"],
      headline: "Stale headline that must not render.",
      voice_paragraph: "Stale prose that must not render.",
      voice_source: "stale",
      attention_items: [
        { kind: "anomaly", severity: "high", title: "Stale attention", detail: null, action_href: null },
      ],
      recent_days: [
        { date: "2026-05-07", total_minutes: 642, top_lane: "stale_lane", episode_count: 23 },
      ],
    });
    const html = renderPage();

    expect(html).not.toContain("was quiet.");
    expect(html).toContain("Coverage for this day could not be confirmed.");
    expect(html).toContain("Chronicler could not confirm whether this day was chronicled.");
    expect(html).not.toContain("Stale headline that must not render.");
    expect(html).not.toContain("Stale prose that must not render.");
    expect(html).not.toContain("Stale attention");
    expect(html).not.toContain("stale_lane");
    expect(html).not.toContain("Chronicles drilldown stub");
  });

  it("fails closed when a briefing response omits state_class", () => {
    const malformed = buildBriefing({
      headline: "Stale headline that must not render.",
      voice_paragraph: "Stale prose that must not render.",
    }) as Partial<ChroniclesBriefing>;
    delete malformed.state_class;
    _briefing = malformed as ChroniclesBriefing;

    const html = renderPage();

    expect(html).toContain("Coverage for this day could not be confirmed.");
    expect(html).not.toContain("Stale headline that must not render.");
    expect(html).not.toContain("Stale prose that must not render.");
    expect(html).not.toContain("Chronicles drilldown stub");
  });

  describe("palette verbs + bindings (bu-t64p2)", () => {
    function pressKey(key: string): void {
      act(() => {
        window.dispatchEvent(
          new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }),
        );
      });
    }

    it("'[' steps the requested date backward", () => {
      vi.useFakeTimers();
      vi.setSystemTime(new Date("2026-05-09T16:30:00.000Z"));
      _briefing = buildBriefing({ date: "2026-05-05" });

      const { unmount } = mountPage("/chronicles?date=2026-05-05");
      pressKey("[");
      expect(_briefingArgs?.date).toBe("2026-05-04");
      unmount();
    });

    it("']' steps the requested date forward", () => {
      vi.useFakeTimers();
      vi.setSystemTime(new Date("2026-05-09T16:30:00.000Z"));
      _briefing = buildBriefing({ date: "2026-05-05" });

      const { unmount } = mountPage("/chronicles?date=2026-05-05");
      pressKey("]");
      expect(_briefingArgs?.date).toBe("2026-05-06");
      unmount();
    });

    it("'t' jumps to the latest settled day", () => {
      vi.useFakeTimers();
      vi.setSystemTime(new Date("2026-05-09T16:30:00.000Z"));
      _briefing = buildBriefing({ date: "2026-05-05" });

      const { unmount } = mountPage("/chronicles?date=2026-05-05");
      // Yesterday in SGT relative to the mocked instant above.
      pressKey("t");
      expect(_briefingArgs?.date).toBe("2026-05-09");
      unmount();
    });
  });
});
