/**
 * Declarative event -> cache-patch registry for the fleet event bus
 * (WS /api/events/stream, bu-86c4c.8 §JARVIS audit move 5).
 *
 * The multiplexed socket (see useEventStream in use-event-stream.ts) carries
 * every dashboard-relevant event type in one envelope:
 *
 *   {"type": "session" | "notification" | "ingestion" | "issue"
 *           | "approval" | "spend" | "heartbeat", "ts": <unix float>, "data": {...}}
 *
 * Rather than each consumer hand-rolling its own invalidation logic (the
 * pattern that was built three times for approvals/spend/settings-console —
 * see use-approvals-stream.ts:146-152), this module maps each event *type*
 * to a single targeted cache-patch function once. Adding a new live-updating
 * surface means adding one entry here, not a bespoke WS hook.
 *
 * Each patch targets the SAME query keys the corresponding data hooks
 * already use (see use-approvals.ts / ApprovalsPage.tsx, use-spend.ts,
 * use-sessions.ts, use-issues.ts, use-butler-status-board.ts,
 * use-messenger.ts, use-ingestion-events.ts) — this is invalidation, not a
 * blanket refetch of the whole app.
 */

import type { QueryClient } from "@tanstack/react-query";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** One envelope received on the multiplexed fleet event bus. */
export interface FleetEvent {
  type: string;
  ts: number;
  data: Record<string, unknown>;
}

type CachePatch = (qc: QueryClient, event: FleetEvent) => void;

function asString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

// ---------------------------------------------------------------------------
// Per-type cache patches
// ---------------------------------------------------------------------------

/**
 * approval — mirrors the invalidation useApprovalsStream already performs
 * (ApprovalsPage.tsx's ["approvals", "flat" | "history" | "detail"] keys),
 * plus ["approvals", "metrics"] (use-approvals.ts's useApprovalMetrics) since
 * every state-transition kind (created/approved/rejected/executed/expired)
 * also changes the aggregate counts that endpoint serves.
 */
const approvalPatch: CachePatch = (qc, event) => {
  qc.invalidateQueries({ queryKey: ["approvals", "flat"] });
  qc.invalidateQueries({ queryKey: ["approvals", "history"] });
  qc.invalidateQueries({ queryKey: ["approvals", "metrics"] });
  const approvalId = asString(event.data.approval_id);
  if (approvalId) {
    qc.invalidateQueries({ queryKey: ["approvals", "detail", approvalId] });
  }
};

/** spend — mirrors use-spend.ts's cost-summary / daily-costs / top-sessions keys. */
const spendPatch: CachePatch = (qc) => {
  qc.invalidateQueries({ queryKey: ["cost-summary"] });
  qc.invalidateQueries({ queryKey: ["daily-costs"] });
  qc.invalidateQueries({ queryKey: ["top-sessions"] });
};

/**
 * session (started|ended) — the strongest "a butler just did something"
 * signal. Invalidates the session lists (use-sessions.ts) AND the roster
 * board (use-butler-status-board.ts's ["butlers", "board"]) so the shell's
 * liveness view updates in the same beat, not on its next 30s poll — this is
 * the mechanism behind "the owner must never see a butler finish in
 * Telegram before the dashboard notices."
 */
const sessionPatch: CachePatch = (qc, event) => {
  qc.invalidateQueries({ queryKey: ["sessions"] });
  qc.invalidateQueries({ queryKey: ["session-aggregate"] });
  qc.invalidateQueries({ queryKey: ["butler-sessions"] });
  qc.invalidateQueries({ queryKey: ["butlers", "board"] });
  // The fleet chronicle (/timeline, bu-86c4c.10) folds sessions into its
  // event stream — a session starting/ending is exactly the kind of event
  // its live tail must reflect without waiting for the next 30s poll.
  qc.invalidateQueries({ queryKey: ["timeline"] });
  const butler = asString(event.data.butler);
  const sessionId = asString(event.data.session_id);
  if (butler && sessionId) {
    qc.invalidateQueries({ queryKey: ["session-detail", butler, sessionId] });
  }
};

/**
 * notification — a notify() delivery attempt; refreshes the messenger health
 * surfaces and the fleet chronicle's timeline (notification is one of its
 * event sources — see sessionPatch's comment above).
 */
const notificationPatch: CachePatch = (qc) => {
  qc.invalidateQueries({ queryKey: ["messenger-delivery-stats"] });
  qc.invalidateQueries({ queryKey: ["messenger-queue-depth"] });
  qc.invalidateQueries({ queryKey: ["timeline"] });
};

/**
 * issue — a new audit-log error landed; the /api/issues feed groups exactly
 * these rows (see api/routers/issues.py::_list_audit_error_issues), so
 * invalidate both the active and dismissed views (use-issues.ts keys them
 * ["issues", {dismissed}] — this prefix covers both).
 */
const issuePatch: CachePatch = (qc) => {
  qc.invalidateQueries({ queryKey: ["issues"] });
};

/**
 * ingestion — a new ingestion_events row landed (emitted from ingest_v1's
 * insert transaction, bu-h8ioq). Invalidates the timeline list/detail keys
 * (ingestionEventKeys.all == ["ingestion", "events"], which prefixes list,
 * sessions, rollup, replays, sender-contact, detail, and payload) plus the
 * window-rollup and histogram keys, which live under separate prefixes
 * (["ingestion", "window-rollup", ...] / ["ingestion", "events-histogram",
 * ...]) and would otherwise miss this invalidation — see
 * use-ingestion-events.ts's ingestionEventKeys.
 */
const ingestionPatch: CachePatch = (qc) => {
  qc.invalidateQueries({ queryKey: ["ingestion", "events"] });
  qc.invalidateQueries({ queryKey: ["ingestion", "window-rollup"] });
  qc.invalidateQueries({ queryKey: ["ingestion", "events-histogram"] });
};

/**
 * heartbeat — no cache effect. Consumed by useEventStream purely for
 * connection-health tracking (lastEventAt), not cache patching.
 */
const heartbeatPatch: CachePatch = () => {
  // Intentionally a no-op.
};

// ---------------------------------------------------------------------------
// Registry
// ---------------------------------------------------------------------------

/**
 * Declarative type -> cache-patch map. Keep this in sync with
 * `EVENT_TYPES` in `src/butlers/api/routers/events.py`.
 */
export const EVENT_CACHE_REGISTRY: Record<string, CachePatch> = {
  approval: approvalPatch,
  spend: spendPatch,
  session: sessionPatch,
  notification: notificationPatch,
  issue: issuePatch,
  ingestion: ingestionPatch,
  heartbeat: heartbeatPatch,
};

/** Apply one fleet event's cache patch, if a registry entry exists for its type. */
export function applyFleetEvent(qc: QueryClient, event: FleetEvent): void {
  const patch = EVENT_CACHE_REGISTRY[event.type];
  patch?.(qc, event);
}
