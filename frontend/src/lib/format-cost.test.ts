import { describe, expect, it } from "vitest";

import { formatCostUsd, formatCostUsdPrecise } from "./format-cost";

// ---------------------------------------------------------------------------
// bu-dm6kh (PR #2871 review follow-up): formatCostUsd previously clamped any
// magnitude below one cent to "<$0.01" regardless of sign, so a negative
// spend (e.g. a refund/credit) below one cent in magnitude rendered as
// "<$0.01" instead of a signed figure — and any negative amount at or above
// one cent lost its sign entirely (amount.toFixed(2) on a negative number
// does carry the "-", but the early `amount < 0.01` branch swallowed small
// negative magnitudes into the unsigned "<$0.01" string). Pin the honest
// signed behavior for all magnitudes.
// ---------------------------------------------------------------------------

describe("formatCostUsd", () => {
  it("renders exactly zero as $0.00", () => {
    expect(formatCostUsd(0)).toBe("$0.00");
  });

  it("never renders a nonzero positive spend as $0.00", () => {
    expect(formatCostUsd(0.004)).toBe("<$0.01");
  });

  it("renders ordinary positive amounts to 2 decimal places", () => {
    expect(formatCostUsd(1.5)).toBe("$1.50");
  });

  it("preserves the sign for a negative amount at or above one cent in magnitude", () => {
    expect(formatCostUsd(-1.5)).toBe("-$1.50");
  });

  it("preserves the sign for a negative amount below one cent in magnitude", () => {
    expect(formatCostUsd(-0.004)).toBe("-<$0.01");
  });

  it("treats non-finite input the same as zero", () => {
    expect(formatCostUsd(Number.NaN)).toBe("$0.00");
    expect(formatCostUsd(Number.POSITIVE_INFINITY)).toBe("$0.00");
  });
});

describe("formatCostUsdPrecise", () => {
  it("renders null/undefined as an em dash", () => {
    expect(formatCostUsdPrecise(null)).toBe("—");
    expect(formatCostUsdPrecise(undefined)).toBe("—");
  });

  it("renders exactly zero as $0.00", () => {
    expect(formatCostUsdPrecise(0)).toBe("$0.00");
  });

  it("renders a nonzero magnitude below $0.001 as <$0.001", () => {
    expect(formatCostUsdPrecise(0.0004)).toBe("<$0.001");
  });

  it("renders ordinary amounts to 4 decimal places", () => {
    expect(formatCostUsdPrecise(0.0125)).toBe("$0.0125");
  });
});
