/**
 * Bulk-retry eligibility helpers — mirror of the backend guard in
 * `src/butlers/core/ingestion_events.py :: ingestion_event_replay_request`.
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

import type { IngestionEventStatus } from "@/api/index.ts";

/**
 * Returns true if an event with the given status is eligible for bulk retry.
 * Mirrors the replayable-state guards in `ingestion_event_replay_request`.
 */
export function isBulkEligible(status: IngestionEventStatus): boolean {
  return status !== "replay_pending" && status !== "skipped";
}

/**
 * Returns a human-readable explanation of why a row is ineligible for bulk
 * retry, or null when the status is eligible.  Used for tooltip text.
 */
export function bulkIneligibleReason(status: IngestionEventStatus): string | null {
  if (status === "replay_pending") return "Already queued for replay";
  if (status === "skipped") return "Skipped events cannot be replayed";
  return null;
}
