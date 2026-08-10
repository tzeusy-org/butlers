// @vitest-environment jsdom
/**
 * Unit tests for bulkEligibility helpers.
 *
 * Verifies that isBulkEligible and bulkIneligibleReason mirror both the
 * backend status guard and the server-authoritative replay safety policy.
 */

import { describe, expect, it } from "vitest";
import type { IngestionEventStatus } from "@/api/index.ts";
import {
  isBulkEligible,
  bulkIneligibleReason,
  type ReplayEligibilityEvent,
} from "./bulkEligibility";

const ELIGIBLE_STATUSES: IngestionEventStatus[] = [
  "ingested",
  "filtered",
  "error",
  "failed",
  "replay_complete",
  "replay_failed",
];

const INELIGIBLE_STATUSES: IngestionEventStatus[] = ["replay_pending", "skipped"];

function event(
  status: IngestionEventStatus,
  replaySafe = true,
): ReplayEligibilityEvent {
  return {
    status,
    replay_safe: replaySafe,
    replay_block_reason: replaySafe ? null : "Email events cannot be replayed safely",
  };
}

describe("isBulkEligible", () => {
  it.each(ELIGIBLE_STATUSES)("returns true for status=%s (backend accepts these)", (status) => {
    expect(isBulkEligible(event(status))).toBe(true);
  });

  it.each(INELIGIBLE_STATUSES)(
    "returns false for status=%s (backend returns conflict)",
    (status) => {
      expect(isBulkEligible(event(status))).toBe(false);
    },
  );
});

describe("bulkIneligibleReason", () => {
  it.each(ELIGIBLE_STATUSES)("returns null for eligible status=%s", (status) => {
    expect(bulkIneligibleReason(event(status))).toBeNull();
  });

  it("returns a reason string for replay_pending", () => {
    const reason = bulkIneligibleReason(event("replay_pending"));
    expect(reason).not.toBeNull();
    expect(typeof reason).toBe("string");
    expect(reason!.length).toBeGreaterThan(0);
  });

  it("returns a reason string for skipped", () => {
    const reason = bulkIneligibleReason(event("skipped"));
    expect(reason).not.toBeNull();
    expect(typeof reason).toBe("string");
    expect(reason!.length).toBeGreaterThan(0);
  });

  it("returns the server-provided reason for replay-unsafe events", () => {
    const unsafeEvent = event("error", false);
    expect(isBulkEligible(unsafeEvent)).toBe(false);
    expect(bulkIneligibleReason(unsafeEvent)).toBe("Email events cannot be replayed safely");
  });
});
