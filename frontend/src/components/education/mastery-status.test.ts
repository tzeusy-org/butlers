import { describe, expect, it } from "vitest";

import {
  MASTERY_STATUS_COLORS,
  MASTERY_STATUS_TEXT_COLORS,
  masteryStatusBadgeClassName,
} from "./mastery-status";

describe("mastery-status: canonical color map", () => {
  it("uses the AA-safe --amber-text alias for learning's text color", () => {
    expect(MASTERY_STATUS_COLORS.learning).toBe("var(--amber)");
    expect(MASTERY_STATUS_TEXT_COLORS.learning).toBe("var(--amber-text)");
  });

  it("never uses an unsanctioned raw Tailwind color for a known status", () => {
    for (const token of Object.values(MASTERY_STATUS_COLORS)) {
      expect(token).toMatch(/^var\(--/);
    }
  });

  it("falls back to the unseen token for an unrecognized status", () => {
    expect(masteryStatusBadgeClassName("some-unknown-status")).toBe(
      masteryStatusBadgeClassName("unseen"),
    );
  });

  it("builds a bg/text Tailwind arbitrary-value pair per status", () => {
    expect(masteryStatusBadgeClassName("mastered")).toBe(
      "bg-[var(--green)]/10 text-[var(--green)]",
    );
    expect(masteryStatusBadgeClassName("learning")).toBe(
      "bg-[var(--amber)]/10 text-[var(--amber-text)]",
    );
  });

  it("ensures all text colors are WCAG-AA safe (no border-opacity tokens)", () => {
    for (const [status, token] of Object.entries(MASTERY_STATUS_TEXT_COLORS)) {
      expect(
        token,
        `status "${status}" text color should not use border-strong (18%-20% opacity is not text-safe)`,
      ).not.toContain("border-strong");
    }
    // Explicitly verify unseen and diagnosed use text-safe --dim
    expect(MASTERY_STATUS_TEXT_COLORS.unseen).toBe("var(--dim)");
    expect(MASTERY_STATUS_TEXT_COLORS.diagnosed).toBe("var(--dim)");
  });
});
