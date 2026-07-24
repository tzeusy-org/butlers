/**
 * Named poll-policy tokens (bu-qvnce.14 slice 3).
 *
 * A bare numeric `refetchInterval` value hides intent: is this the PRIMARY
 * update path for this data, or a safety-net reconciliation sweep sitting
 * behind a live event source? Before this, both shapes were spelled the same
 * way (a raw `30_000` or `5 * 60_000` literal), so a reviewer had to go
 * cross-reference event-cache-registry.ts to tell them apart.
 *
 * `POLL_BUS_RECONCILE_MS` names the one token this codebase actually needs
 * today: "this query's cache key is ALSO invalidated by the fleet event bus
 * (see event-cache-registry.ts) — the interval below is a reconciliation
 * sweep for the rare case the bus is down, not the primary path." Fixed at
 * 5 minutes to match the pattern use-sessions.ts / use-approvals.ts /
 * use-spend.ts already established (the "blessed Approvals pattern" the
 * 2026-07-04 JARVIS pursuit doc cites).
 *
 * Not every refetchInterval in the app has been migrated onto a named token
 * yet — see the eslint.config.js `POLL_POLICY_FILES` comment for the exact
 * (currently small) set of files this is enforced on, and the bu-qvnce.14
 * worker report for the follow-up to broaden it.
 */

/**
 * Explicit escape hatch for a query whose hidden-tab freshness is intentional.
 * A hook opting in must import this token (rather than use a bare `true`) and
 * document why its work must continue after the owner leaves the tab.
 */
export const POLL_IN_BACKGROUND = true;

export const POLL_BUS_RECONCILE_MS = 5 * 60_000;

/**
 * `POLL_BUS_DOWN_FALLBACK_MS` -- the fast fallback cadence
 * `useBusAwarePollInterval` (use-bus-aware-poll-interval.ts) applies while
 * the fleet event bus is NOT connected ("connecting" | "reconnecting" |
 * "closed", see EventStreamStatus in use-event-stream.ts).
 *
 * A bus-covered surface's cache key is normally kept fresh by live
 * invalidation (event-cache-registry.ts), with POLL_BUS_RECONCILE_MS above as
 * a 5-minute safety net for the rare case the bus is briefly down. That
 * safety net is only honest while the bus reconnects quickly -- a longer
 * outage would otherwise leave the surface silently stale for up to 5
 * minutes with no visible degradation (the exact gap use-notifications.ts
 * had before bu-01r64.3, with NO refetchInterval at all -- infinite
 * staleness on a dead socket). 30s matches the primary-path cadence already
 * used for non-bus-covered surfaces elsewhere (e.g. BUTLERS_POLL_MS in
 * use-butlers.ts), so a dropped socket degrades to honest polling, not
 * silence.
 */
export const POLL_BUS_DOWN_FALLBACK_MS = 30_000;

/**
 * `POLL_RUNNING_SESSION_MS` — the PRIMARY update path for a single running
 * session's dossier (bu-qvnce.5, pursuit move 5 slice 3).
 *
 * Unlike POLL_BUS_RECONCILE_MS above, this is not a reconciliation safety net
 * sitting behind bus coverage: the fleet event bus only emits a "session"
 * event on start/end (see event-cache-registry.ts's sessionPatch), never per
 * tool-call, so a running session's streaming tool-call tail has no bus event
 * to ride until the session ends. A short poll is the honest primary
 * mechanism while `success === null` (StatusBadge's own "running" test) —
 * once terminal, the query stops polling (see useGlobalSessionDetail) and
 * relies entirely on the bus's end-of-session invalidation, same as every
 * other bus-covered surface.
 */
export const POLL_RUNNING_SESSION_MS = 3_000;
