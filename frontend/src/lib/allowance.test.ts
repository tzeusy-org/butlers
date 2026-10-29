import { describe, expect, it } from "vitest";

import type { AllowanceAccount } from "@/api/types.ts";
import {
  allowanceSourceLabel,
  formatAllowanceCountdown,
  readAllowance,
} from "@/lib/allowance";

const NOW = Date.parse("2026-10-18T12:00:00Z");

function account(over: Partial<AllowanceAccount>): AllowanceAccount {
  return {
    account_key: "acct-a",
    state: "exhausted",
    reset_at: "2026-10-18T14:05:00Z",
    reset_source: "parsed",
    first_seen_at: "2026-10-18T11:00:00Z",
    updated_at: "2026-10-18T11:00:00Z",
    entries: [],
    ...over,
  };
}

describe("allowance helpers", () => {
  it("labels the reset source the way the owner reads it", () => {
    expect(allowanceSourceLabel("parsed")).toBe("provider said");
    expect(allowanceSourceLabel("default_window")).toBe("estimated");
    expect(allowanceSourceLabel("unknown")).toBe("unknown");
  });

  it("formats countdowns and never goes negative", () => {
    expect(formatAllowanceCountdown(125 * 60_000)).toBe("2h 05m");
    expect(formatAllowanceCountdown(14 * 60_000)).toBe("14m");
    expect(formatAllowanceCountdown(30 * 60 * 60_000)).toBe("1d 6h");
    expect(formatAllowanceCountdown(-5_000)).toBe("<1m");
  });

  it("reads an exhausted account as a labelled countdown", () => {
    expect(readAllowance(account({}), NOW)).toEqual({
      kind: "countdown",
      msRemaining: 125 * 60_000,
      label: "provider said",
    });
  });

  it("says should-be-back once reset_at has passed", () => {
    const past = account({ reset_at: "2026-10-18T11:00:00Z" });
    expect(readAllowance(past, NOW)).toEqual({ kind: "should-be-back" });
  });

  it("keeps unknown and available distinct", () => {
    expect(readAllowance(account({ state: "unknown", reset_at: null }), NOW)).toEqual({
      kind: "unknown",
    });
    expect(readAllowance(account({ state: "available", reset_at: null }), NOW)).toEqual({
      kind: "available",
    });
    expect(readAllowance(account({ reset_at: null }), NOW)).toEqual({
      kind: "exhausted-no-reset",
    });
  });
});
