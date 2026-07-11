// @vitest-environment jsdom
/**
 * Tests for NotificationStatsBar degraded-source honesty (bu-jad4j.2).
 *
 * When the Switchboard notifications source is unreachable the stats endpoint
 * returns HTTP 200 with all-zero counts plus `source_available: false`. The
 * tiles must NOT render a fabricated `0` / green `0.0%` (which reads as a
 * truthful "everything delivered" all-clear); they em-dash instead. A
 * reachable-but-empty source (`source_available` absent or `true` with genuine
 * zeros) keeps its honest zeros.
 */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { NotificationStatsBar } from "@/components/notifications/notification-stats-bar";
import type { NotificationStats } from "@/api/types";

function makeStats(overrides: Partial<NotificationStats> = {}): NotificationStats {
  return {
    total: 34,
    sent: 29,
    failed: 5,
    by_channel: { telegram: 34 },
    by_butler: {},
    ...overrides,
  };
}

describe("NotificationStatsBar degraded honesty", () => {
  afterEach(() => cleanup());

  it("em-dashes every tile and never a green 0.0% when source_available is false", () => {
    // Degraded fan-out: the endpoint answered 200 but the Switchboard source was
    // unreachable, so the zeros below are placeholders, not a real tally.
    render(
      <NotificationStatsBar
        stats={makeStats({ total: 0, sent: 0, failed: 0, by_channel: {}, source_available: false })}
      />,
    );

    const EM_DASH = "—";
    expect(screen.getByTestId("stat-value-total").textContent).toBe(EM_DASH);
    expect(screen.getByTestId("stat-value-sent").textContent).toBe(EM_DASH);
    expect(screen.getByTestId("stat-value-failed").textContent).toBe(EM_DASH);
    // Mutation guard: the failure-rate tile is the one that used to render a
    // confident green "0.0%". It must em-dash — not show any percentage — when
    // the source is down.
    const rate = screen.getByTestId("stat-value-failure-rate");
    expect(rate.textContent).toBe(EM_DASH);
    expect(rate.textContent).not.toContain("0.0");
    expect(rate.className).not.toContain("var(--green)");
  });

  it("renders real counts on the happy path (flag absent)", () => {
    render(<NotificationStatsBar stats={makeStats()} />);
    expect(screen.getByTestId("stat-value-total").textContent).toBe("34");
    expect(screen.getByTestId("stat-value-sent").textContent).toBe("29");
    expect(screen.getByTestId("stat-value-failed").textContent).toBe("5");
    // 5 / 34 = 14.7% failure rate — a real value, not an em-dash.
    expect(screen.getByTestId("stat-value-failure-rate").textContent).toBe("14.7%");
  });

  it("keeps honest zeros for a reachable-but-empty source (source_available true)", () => {
    // Classify-before-flagging: a genuinely empty, reachable source is a
    // legitimate all-clear and must still show its zeros, not em-dashes.
    render(
      <NotificationStatsBar
        stats={makeStats({ total: 0, sent: 0, failed: 0, by_channel: {}, source_available: true })}
      />,
    );
    expect(screen.getByTestId("stat-value-total").textContent).toBe("0");
    expect(screen.getByTestId("stat-value-sent").textContent).toBe("0");
    expect(screen.getByTestId("stat-value-failed").textContent).toBe("0");
    expect(screen.getByTestId("stat-value-failure-rate").textContent).toBe("0.0%");
  });
});
