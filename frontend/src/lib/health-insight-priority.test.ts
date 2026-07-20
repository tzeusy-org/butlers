import { describe, expect, it } from "vitest";

import { healthInsightSeverity } from "@/lib/health-insight-priority";

describe("healthInsightSeverity", () => {
  it("classifies the canonical backend threshold boundaries", () => {
    expect(healthInsightSeverity(1)).toBe("low");
    expect(healthInsightSeverity(49)).toBe("low");
    expect(healthInsightSeverity(50)).toBe("medium");
    expect(healthInsightSeverity(89)).toBe("medium");
    expect(healthInsightSeverity(90)).toBe("high");
    expect(healthInsightSeverity(100)).toBe("high");
  });

  it("never lowers displayed severity as numeric priority increases", () => {
    expect([1, 49, 50, 89, 90, 100].map(healthInsightSeverity)).toEqual([
      "low",
      "low",
      "medium",
      "medium",
      "high",
      "high",
    ]);
  });
});
