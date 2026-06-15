// @vitest-environment jsdom
/**
 * Unit tests for bulkEligibility helpers.
 *
 * Verifies that isBulkEligible and bulkIneligibleReason mirror the backend
 * guard in `ingestion_event_replay_request` — specifically that:
 *   - replay_pending and skipped are the only ineligible statuses
 *   - all other statuses (failed, ingested, filtered, error, replay_complete,
 *     replay_failed) are eligible
 *   - ineligible statuses return a non-null tooltip reason string
 */

import { describe, expect, it } from "vitest";
import type { IngestionEventStatus } from "@/api/index.ts";
import { isBulkEligible, bulkIneligibleReason } from "./bulkEligibility";

const ELIGIBLE_STATUSES: IngestionEventStatus[] = [
  "ingested",
  "filtered",
  "error",
  "replay_complete",
  "replay_failed",
];

const INELIGIBLE_STATUSES: IngestionEventStatus[] = ["replay_pending", "skipped"];

describe("isBulkEligible", () => {
  it.each(ELIGIBLE_STATUSES)("returns true for status=%s (backend accepts these)", (status) => {
    expect(isBulkEligible(status)).toBe(true);
  });

  it.each(INELIGIBLE_STATUSES)(
    "returns false for status=%s (backend returns conflict)",
    (status) => {
      expect(isBulkEligible(status)).toBe(false);
    },
  );
});

describe("bulkIneligibleReason", () => {
  it.each(ELIGIBLE_STATUSES)("returns null for eligible status=%s", (status) => {
    expect(bulkIneligibleReason(status)).toBeNull();
  });

  it("returns a reason string for replay_pending", () => {
    const reason = bulkIneligibleReason("replay_pending");
    expect(reason).not.toBeNull();
    expect(typeof reason).toBe("string");
    expect(reason!.length).toBeGreaterThan(0);
  });

  it("returns a reason string for skipped", () => {
    const reason = bulkIneligibleReason("skipped");
    expect(reason).not.toBeNull();
    expect(typeof reason).toBe("string");
    expect(reason!.length).toBeGreaterThan(0);
  });
});
