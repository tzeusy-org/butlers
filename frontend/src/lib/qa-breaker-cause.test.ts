import { describe, expect, it } from "vitest";

import { breakerCauseLabel, breakerResetForecast, haltReasonLabel } from "./qa-breaker-cause";

describe("qa breaker cause words", () => {
  it("names known causes and shows an unknown code as text", () => {
    expect(breakerCauseLabel("publisher_unconfigured")).toBe("no publisher configured");
    expect(breakerCauseLabel("mixed")).toBe("mixed causes");
    expect(breakerCauseLabel("brand_new_cause")).toBe("brand new cause");
    expect(breakerCauseLabel(null)).toBeNull();
  });

  it("names the gate behind a halted patrol", () => {
    expect(haltReasonLabel("circuit_breaker")).toBe("circuit breaker open");
    expect(haltReasonLabel("concurrency_cap")).toBe("concurrency cap reached");
    expect(haltReasonLabel(undefined)).toBeNull();
  });

  it("withholds the reset forecast on mixed or unknown causes", () => {
    expect(breakerResetForecast("mixed").available).toBe(false);
    expect(breakerResetForecast(null).available).toBe(false);
  });

  it("forecasts a single structural cause as failing again until fixed", () => {
    const forecast = breakerResetForecast("publisher_unconfigured");
    expect(forecast.available).toBe(true);
    expect(forecast.text).toContain("fail the same way");
  });
});

describe("patrol status presentation names the halting gate", () => {
  it("appends the gate to a halted patrol only", async () => {
    const { getQaPatrolStatusPresentation } = await import("./qa-patrol-status");
    expect(getQaPatrolStatusPresentation("halted", "circuit_breaker").label).toBe(
      "dispatch halted: circuit breaker open",
    );
    expect(getQaPatrolStatusPresentation("halted").label).toBe("dispatch halted");
    expect(getQaPatrolStatusPresentation("clean", "circuit_breaker").label).toBe("clean");
  });
});
