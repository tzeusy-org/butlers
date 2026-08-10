/**
 * Bulk-retry eligibility helpers.
 *
 * The server's replay policy is authoritative. The status guards below mirror
 * `src/butlers/core/ingestion_events.py :: ingestion_event_replay_request`,
 * while `replay_safe` prevents stale or incomplete client data from queuing a
 * connector action that the server would reject.
 *
 * Ineligible statuses (backend returns "conflict"):
 *   - replay_pending — event is already queued; re-queuing has no effect
 *     (neither ingestion_events nor filtered_events accept this transition)
 *   - skipped       — skip-triaged events are never retried
 *
 * All other statuses are accepted by at least one table:
 *   failed / ingested / replay_failed → ingestion_events
 *   filtered / error / replay_complete / replay_failed / ingested → filtered_events
 */

import type { IngestionEventSummary } from "@/api/index.ts";

export type ReplayEligibilityEvent = Pick<
  IngestionEventSummary,
  "status" | "replay_safe" | "replay_block_reason"
>;

/** Returns true only when the server explicitly confirmed connector replay safety. */
export function isReplaySafe(event: ReplayEligibilityEvent): boolean {
  return event.replay_safe === true;
}

/**
 * Returns true if an event with the given status is eligible for bulk retry.
 * Mirrors the replayable-state guards in `ingestion_event_replay_request`.
 */
export function isBulkEligible(event: ReplayEligibilityEvent): boolean {
  return (
    isReplaySafe(event) && event.status !== "replay_pending" && event.status !== "skipped"
  );
}

/**
 * Returns a human-readable explanation of why a row is ineligible for bulk
 * retry, or null when the status is eligible.  Used for tooltip text.
 */
export function bulkIneligibleReason(event: ReplayEligibilityEvent): string | null {
  if (!isReplaySafe(event)) {
    return event.replay_block_reason ?? "Replay safety has not been confirmed for this event";
  }
  if (event.status === "replay_pending") return "Already queued for replay";
  if (event.status === "skipped") return "Skipped events cannot be replayed";
  return null;
}
