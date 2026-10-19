// @vitest-environment jsdom

/**
 * ChroniclesPage: a busy day-close regeneration (409 `scheduler_busy`) shows the server's message
 * as a status, not as the generic failure alert (bu-3fle50). Other failures keep their copy.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, useNavigate } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ChroniclesPage from "@/pages/ChroniclesPage";
import type { ChroniclesBriefing } from "@/api/types";

// react-dom/client + act() need this flag set in a non-browser test env.
(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const { postChroniclerDayCloseRefresh } = vi.hoisted(() => ({
  postChroniclerDayCloseRefresh: vi.fn(),
}));

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/components/ui/timezone-context", () => ({
  useTimezone: () => _timezone,
}));

let _briefing: ChroniclesBriefing | undefined;
let _isFetching = false;
let _isError = false;
let _isPlaceholderData = false;
let _refetch = vi.fn();
let _navigate: ((to: string) => void) | undefined;
let _timezone = "Asia/Singapore";

vi.mock("@/hooks/use-chronicles-briefing", () => ({
  useChroniclesBriefing: () => {
    return {
      data: _briefing,
      isFetching: _isFetching,
      isError: _isError,
      isPlaceholderData: _isPlaceholderData,
      refetch: _refetch,
    };
  },
}));

vi.mock("@/api/client.ts", () => ({ postChroniclerDayCloseRefresh }));

// The drilldown panel pulls in heavy modules (Gantt, Map, Scrubber). For these
// editorial smoke tests we stub it out; content visibility is tested in its
// own component spec.
vi.mock("@/components/chronicles/ChroniclesDrilldownPanel", () => ({
  ChroniclesDrilldownPanel: () => {
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

const HEALTHY_COVERAGE_GAP_LEDGER: NonNullable<
  ChroniclesBriefing["subquery_availability"]
> = [
  { subquery: "coverage_floor", state: "available" },
  { subquery: "coverage_witness", state: "available" },
  { subquery: "episodes", state: "not_requested" },
];

function buildRegeneratableBriefing(
  date: string,
  kind: "stale" | "coverage-gap",
): ChroniclesBriefing {
  if (kind === "stale") return buildBriefing({ date, voice_source: "stale" });
  return buildBriefing({
    date,
    state_class: "unavailable",
    headline: "Coverage for this day could not be confirmed.",
    voice_paragraph: "Chronicler could not confirm whether this day was chronicled.",
    voice_source: "templated",
    subquery_availability: HEALTHY_COVERAGE_GAP_LEDGER,
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------


describe("ChroniclesPage busy day-close regeneration", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-05-08T16:30:00.000Z"));
    _briefing = undefined;
    _isFetching = false;
    _isError = false;
    _isPlaceholderData = false;
    _refetch = vi.fn();
    _navigate = undefined;
    _timezone = "Asia/Singapore";
    postChroniclerDayCloseRefresh.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
    document.body.innerHTML = "";
  });

  async function regenerateWith(refreshError: Error) {
    _briefing = buildRegeneratableBriefing("2026-05-08", "stale");
    postChroniclerDayCloseRefresh.mockRejectedValue(refreshError);
    const mounted = mountPage("/chronicles?date=2026-05-08");
    const regenerate = mounted.container.querySelector(
      'button[aria-label="Regenerate day-close summary"]',
    ) as HTMLButtonElement;
    await act(async () => {
      regenerate.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(0);
    });
    return mounted;
  }

  const BUSY = "Scheduled work is running on the Chronicler; try again when it finishes.";

  it("shows the server message as a status, not the failure alert, when busy", async () => {
    const { container, unmount } = await regenerateWith(
      Object.assign(new Error(BUSY), { status: 409, code: "scheduler_busy" }),
    );
    try {
      const statuses = Array.from(container.querySelectorAll('[role="status"]'));
      expect(statuses.map((el) => el.textContent)).toContain(BUSY);
      expect(container.querySelector('[role="alert"]')).toBeNull();
      expect(container.textContent).not.toContain("Regeneration failed");
    } finally {
      unmount();
    }
  });

  it.each([
    ["a plain failure", new Error("refresh failed"), "Regeneration failed. Try again later."],
    [
      "a 409 without the busy code",
      Object.assign(new Error(BUSY), { status: 409, code: "UNKNOWN_ERROR" }),
      "Regeneration failed. Try again later.",
    ],
    [
      "a rate limit",
      Object.assign(new Error("rate limited"), { status: 429, code: "day_close_rate_limited" }),
      "Regenerated recently. Try again later.",
    ],
  ])("keeps %s as the existing alert", async (_label, refreshError, expected) => {
    const { container, unmount } = await regenerateWith(refreshError);
    try {
      expect(container.querySelector('[role="alert"]')?.textContent).toBe(expected);
      expect(container.textContent).not.toContain(BUSY);
    } finally {
      unmount();
    }
  });
});
