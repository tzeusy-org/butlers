import { describe, expect, it } from "vitest";

import { formatDurationCompact, formatDurationMs, formatDurationTicks } from "./format-duration";

describe("formatDurationMs", () => {
  it("renders null/undefined as an em dash", () => {
    expect(formatDurationMs(null)).toBe("—");
    expect(formatDurationMs(undefined)).toBe("—");
  });

  it("renders sub-second durations in ms", () => {
    expect(formatDurationMs(450)).toBe("450ms");
  });

  it("renders whole seconds without a decimal", () => {
    expect(formatDurationMs(5000)).toBe("5s");
  });

  it("renders fractional seconds to one decimal place", () => {
    expect(formatDurationMs(1500)).toBe("1.5s");
  });

  it("renders whole minutes alone when there is no remaining seconds component", () => {
    expect(formatDurationMs(120_000)).toBe("2m");
  });

  it("renders minutes plus remaining whole seconds", () => {
    expect(formatDurationMs(125_000)).toBe("2m 5s");
  });
});

describe("formatDurationCompact", () => {
  it("renders sub-minute durations as rounded seconds", () => {
    expect(formatDurationCompact(45_000)).toBe("45s");
  });

  it("renders sub-hour durations as rounded minutes", () => {
    expect(formatDurationCompact(150_000)).toBe("3m");
  });

  it("renders hour-scale durations without a remaining minutes component", () => {
    expect(formatDurationCompact(7_200_000)).toBe("2h");
  });

  it("renders hour-scale durations with a remaining minutes component", () => {
    expect(formatDurationCompact(7_500_000)).toBe("2h 5m");
  });
});

describe("formatDurationTicks", () => {
  it("renders null/undefined/negative as an em dash", () => {
    expect(formatDurationTicks(null)).toBe("—");
    expect(formatDurationTicks(undefined)).toBe("—");
    expect(formatDurationTicks(-5)).toBe("—");
  });

  it("renders sub-second durations in ms", () => {
    expect(formatDurationTicks(450)).toBe("450ms");
  });

  it("renders sub-minute durations to one decimal place in seconds", () => {
    expect(formatDurationTicks(12_300)).toBe("12.3s");
  });

  it("renders minute-scale durations to one decimal place in minutes, with no combined m+s tier", () => {
    expect(formatDurationTicks(90_000)).toBe("1.5m");
  });
});
