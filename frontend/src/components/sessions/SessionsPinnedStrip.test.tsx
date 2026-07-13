// @vitest-environment jsdom
//
// SessionsPinnedStrip contract (bu-ptaub):
// - Collapses to nothing when there is nothing to pin.
// - A running session is pinned with a ticking elapsed label.
// - A recent failure is pinned with an inline (truncated) error excerpt.
// - A session that resolves (removed from the caller's arrays) drops back
//   out of the strip on the next render -- pinning eligibility is entirely
//   caller-driven (SessionsPage decides via its status=running/failed
//   queries), so this is exercised as a prop-driven rerender.
// - Clicking a pinned row calls onSessionClick with that session (same
//   affordance as a SessionTable row).

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";

import type { SessionSummary } from "@/api/types";

const mockUseSessionErrorExcerpts = vi.fn();
const mockUseTickingNow = vi.fn();

vi.mock("@/hooks/use-sessions", () => ({
  useSessionErrorExcerpts: (...args: unknown[]) => mockUseSessionErrorExcerpts(...args),
}));
vi.mock("@/hooks/use-ticking-now", () => ({
  useTickingNow: (...args: unknown[]) => mockUseTickingNow(...args),
}));

import { SessionsPinnedStrip } from "@/components/sessions/SessionsPinnedStrip";

const NOW = Date.parse("2026-07-06T12:00:00.000Z");

function makeSession(overrides: Partial<SessionSummary> = {}): SessionSummary {
  return {
    id: "sess-1",
    butler: "health",
    prompt: "Roll up daily stats",
    trigger_source: "cron",
    request_id: null,
    success: true,
    started_at: new Date(NOW - 6 * 60_000).toISOString(),
    completed_at: null,
    duration_ms: null,
    input_tokens: null,
    output_tokens: null,
    model: null,
    complexity: null,
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function setup({
  now = NOW,
  errors = new Map<string, string | null>(),
}: { now?: number; errors?: Map<string, string | null> } = {}) {
  mockUseTickingNow.mockReturnValue(now);
  mockUseSessionErrorExcerpts.mockReturnValue(errors);
}

describe("SessionsPinnedStrip — empty state", () => {
  it("renders nothing when there are no running sessions and no recent failures", () => {
    setup();
    const { container } = render(
      <SessionsPinnedStrip runningSessions={[]} recentFailures={[]} />,
    );
    expect(container.firstChild).toBeNull();
  });
});

describe("SessionsPinnedStrip — running sessions", () => {
  it("pins a running session with a ticking elapsed label", () => {
    setup();
    const running = makeSession({ id: "run-1", success: null });
    const { getByTestId } = render(
      <SessionsPinnedStrip runningSessions={[running]} recentFailures={[]} />,
    );
    const row = getByTestId("pinned-session-row");
    expect(row.textContent).toContain("6m elapsed");
    expect(row.textContent).toContain("Running");
  });

  it("re-renders the elapsed label as the ticking `now` advances", () => {
    setup({ now: NOW });
    const running = makeSession({ id: "run-1", success: null });
    const { getByTestId, rerender } = render(
      <SessionsPinnedStrip runningSessions={[running]} recentFailures={[]} />,
    );
    expect(getByTestId("pinned-session-row").textContent).toContain("6m elapsed");

    // Simulate the ticking hook advancing by 4 more minutes (no prop change,
    // no refetch -- just the ticking `now` moving forward).
    mockUseTickingNow.mockReturnValue(NOW + 4 * 60_000);
    rerender(<SessionsPinnedStrip runningSessions={[running]} recentFailures={[]} />);
    expect(getByTestId("pinned-session-row").textContent).toContain("10m elapsed");
  });

  it("drops a resolved session back out of the strip on the next render", () => {
    setup();
    const running = makeSession({ id: "run-1", success: null });
    const { queryAllByTestId, rerender } = render(
      <SessionsPinnedStrip runningSessions={[running]} recentFailures={[]} />,
    );
    expect(queryAllByTestId("pinned-session-row")).toHaveLength(1);

    // The session has resolved -- SessionsPage's status=running query no
    // longer returns it, so the caller passes an empty array.
    rerender(<SessionsPinnedStrip runningSessions={[]} recentFailures={[]} />);
    expect(queryAllByTestId("pinned-session-row")).toHaveLength(0);
  });
});

describe("SessionsPinnedStrip — recent failures", () => {
  it("pins a recent failure with its Failed badge and an inline error excerpt", () => {
    const failed = makeSession({ id: "fail-1", success: false });
    setup({ errors: new Map([["fail-1", "TimeoutError: upstream did not respond in 30s"]]) });

    const { getByTestId } = render(
      <SessionsPinnedStrip runningSessions={[]} recentFailures={[failed]} />,
    );
    const row = getByTestId("pinned-session-row");
    expect(row.textContent).toContain("Failed");
    expect(getByTestId("pinned-failure-excerpt").textContent).toContain(
      "TimeoutError: upstream did not respond in 30s",
    );
  });

  it("truncates a long error excerpt", () => {
    const longError = "E".repeat(200);
    const failed = makeSession({ id: "fail-1", success: false });
    setup({ errors: new Map([["fail-1", longError]]) });

    const { getByTestId } = render(
      <SessionsPinnedStrip runningSessions={[]} recentFailures={[failed]} />,
    );
    const excerpt = getByTestId("pinned-failure-excerpt").textContent ?? "";
    expect(excerpt.length).toBeLessThan(longError.length);
    expect(excerpt).toContain("…");
  });

  it("falls back to a plain label when no error detail is available yet", () => {
    const failed = makeSession({ id: "fail-1", success: false });
    setup({ errors: new Map([["fail-1", null]]) });

    const { getByTestId } = render(
      <SessionsPinnedStrip runningSessions={[]} recentFailures={[failed]} />,
    );
    expect(getByTestId("pinned-failure-excerpt").textContent).toBe("no error detail");
  });
});

describe("SessionsPinnedStrip — degraded sources", () => {
  it("does not render a degraded note when both queries succeed with no rows", () => {
    setup();
    const { container, queryByText } = render(
      <SessionsPinnedStrip runningSessions={[]} recentFailures={[]} />,
    );
    expect(container.firstChild).toBeNull();
    expect(queryByText(/unavailable/i)).toBeNull();
  });

  it("renders a degraded note for running sessions on error, even with zero rows", () => {
    setup();
    const { getByText, queryAllByTestId } = render(
      <SessionsPinnedStrip runningSessions={[]} recentFailures={[]} runningError />,
    );
    expect(getByText(/Running sessions: unavailable/i)).toBeTruthy();
    // No fabricated rows -- only the degraded note renders.
    expect(queryAllByTestId("pinned-session-row")).toHaveLength(0);
  });

  it("renders a degraded note for recent failures on error, even with zero rows", () => {
    setup();
    const { getByText } = render(
      <SessionsPinnedStrip runningSessions={[]} recentFailures={[]} recentFailuresError />,
    );
    expect(getByText(/Recent failures: unavailable/i)).toBeTruthy();
  });

  it("renders both the degraded note and real rows when one source errors and the other has data", () => {
    setup();
    const running = makeSession({ id: "run-1", success: null });
    const { getByText, getByTestId } = render(
      <SessionsPinnedStrip
        runningSessions={[running]}
        recentFailures={[]}
        recentFailuresError
      />,
    );
    expect(getByText(/Recent failures: unavailable/i)).toBeTruthy();
    expect(getByTestId("pinned-session-row")).toBeTruthy();
  });

  // Partial per-pool drop (meta.sources_degraded) — an otherwise-200 response
  // that undercounts, distinct from the whole-request *Error flags (bu-hmdqz.12).
  it("names the dropped pool for running sessions on a partial fan-out, even with zero rows", () => {
    setup();
    const { getByText, queryAllByTestId } = render(
      <SessionsPinnedStrip
        runningSessions={[]}
        recentFailures={[]}
        runningSourcesDegraded={["atlas"]}
      />,
    );
    expect(getByText(/Running sessions: partial — atlas unreachable/i)).toBeTruthy();
    expect(queryAllByTestId("pinned-session-row")).toHaveLength(0);
  });

  it("names the dropped pool for recent failures on a partial fan-out", () => {
    setup();
    const { getByText } = render(
      <SessionsPinnedStrip
        runningSessions={[]}
        recentFailures={[]}
        recentFailuresSourcesDegraded={["atlas"]}
      />,
    );
    expect(getByText(/Recent failures: partial — atlas unreachable/i)).toBeTruthy();
  });
});

describe("SessionsPinnedStrip — interaction", () => {
  it("calls onSessionClick with the clicked pinned session", () => {
    setup();
    const running = makeSession({ id: "run-1", success: null });
    const onSessionClick = vi.fn();
    const { getByTestId } = render(
      <SessionsPinnedStrip
        runningSessions={[running]}
        recentFailures={[]}
        onSessionClick={onSessionClick}
      />,
    );

    fireEvent.click(getByTestId("pinned-session-row"));
    expect(onSessionClick).toHaveBeenCalledWith(running);
  });

  it("highlights the row matching selectedId", () => {
    setup();
    const running = makeSession({ id: "run-1", success: null });
    const { getByTestId } = render(
      <SessionsPinnedStrip
        runningSessions={[running]}
        recentFailures={[]}
        onSessionClick={vi.fn()}
        selectedId="run-1"
      />,
    );
    expect(getByTestId("pinned-session-row").getAttribute("aria-selected")).toBe("true");
  });
});
