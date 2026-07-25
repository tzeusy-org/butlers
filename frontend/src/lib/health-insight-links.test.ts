/**
 * insightHref -- category routing for health insights (bu-ep4ks.7, last-hop
 * door repair pack).
 *
 * Before this fix, the switch cases ("medication", "symptom", "condition",
 * "meal", "nutrition", "adherence") never matched any category string
 * actually emitted by run_insight_scan()
 * (roster/health/jobs/health_jobs.py: "medication-refill", "symptom-trend",
 * "health-streak", "correlation-adherence", "correlation-drift",
 * "correlation-environment", "measurement-gap") -- every real insight except
 * the two door-driven ones silently fell through to the
 * `/health/measurements` default regardless of what it was actually about.
 */

import { describe, expect, it } from "vitest";

import { insightHref } from "./health-insight-links";
import type { InsightCandidate } from "@/api/types";

const CHART_ELIGIBLE = new Set(["weight", "blood_pressure"]);

function candidate(overrides: Partial<InsightCandidate> = {}): InsightCandidate {
  return {
    id: "insight-1",
    origin_butler: "health",
    priority: 50,
    category: "medication-refill",
    dedup_key: "dedup-1",
    cooldown_days: null,
    expires_at: null,
    message: "Metformin supply estimated to run out in 3 day(s).",
    channel: null,
    metadata: null,
    created_at: null,
    status: "pending",
    delivered_at: null,
    delivery_attempt_count: 0,
    ...overrides,
  };
}

describe("insightHref -- real backend category strings", () => {
  it("routes medication-refill to /health/medications", () => {
    expect(insightHref(candidate({ category: "medication-refill" }), CHART_ELIGIBLE)).toBe(
      "/health/medications",
    );
  });

  it("routes symptom-trend to /health/symptoms", () => {
    expect(insightHref(candidate({ category: "symptom-trend" }), CHART_ELIGIBLE)).toBe(
      "/health/symptoms",
    );
  });

  it("routes correlation-adherence to /health/medications", () => {
    expect(insightHref(candidate({ category: "correlation-adherence" }), CHART_ELIGIBLE)).toBe(
      "/health/medications",
    );
  });

  it("routes correlation-environment to /health/symptoms", () => {
    expect(insightHref(candidate({ category: "correlation-environment" }), CHART_ELIGIBLE)).toBe(
      "/health/symptoms",
    );
  });

  it("routes health-streak to /health/measurements", () => {
    expect(insightHref(candidate({ category: "health-streak" }), CHART_ELIGIBLE)).toBe(
      "/health/measurements",
    );
  });

  it("falls back to /health/measurements for an unrecognized category", () => {
    expect(insightHref(candidate({ category: "something-new" }), CHART_ELIGIBLE)).toBe(
      "/health/measurements",
    );
  });
});

describe("insightHref -- measurement door (typed metadata carries record context)", () => {
  it("carries type/since/until for measurement-gap when the door is valid", () => {
    const href = insightHref(
      candidate({
        category: "measurement-gap",
        metadata: {
          measurement_door: { type: "weight", since: "2026-07-01", until: "2026-07-20" },
        },
      }),
      CHART_ELIGIBLE,
    );
    expect(href).toBe("/health/measurements?type=weight&since=2026-07-01&until=2026-07-20");
  });

  it("carries type/since/until for correlation-drift when the door is valid", () => {
    const href = insightHref(
      candidate({
        category: "correlation-drift",
        metadata: {
          measurement_door: {
            type: "blood_pressure",
            since: "2026-06-01",
            until: "2026-07-20",
          },
        },
      }),
      CHART_ELIGIBLE,
    );
    expect(href).toBe(
      "/health/measurements?type=blood_pressure&since=2026-06-01&until=2026-07-20",
    );
  });

  it("falls back to the fixed route when the door type is not chart-eligible", () => {
    const href = insightHref(
      candidate({
        category: "measurement-gap",
        metadata: {
          measurement_door: { type: "steps", since: "2026-07-01", until: "2026-07-20" },
        },
      }),
      CHART_ELIGIBLE,
    );
    expect(href).toBe("/health/measurements");
  });
});
