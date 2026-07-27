## 1. Establish relay evidence and dependency boundary

- [ ] 1.1 Merge and independently verify the exact #3624 dashboard-turn
  authority head before adding terminal-action recovery work.
- [ ] 1.2 After #3624 lands, decide PR #3618 explicitly: rebase it onto the
  landing and independently revalidate its truthful dispatch receipt/UI scope,
  or close it as superseded. Do not implement overlapping UI/API behavior until
  that disposition is recorded.
- [ ] 1.3 Add dashboard-action/effect identities to QA `report_finding`, a
  durable QA receipt store, and an authenticated receipt lookup; keep ordinary
  non-dashboard reports on their existing volatile-buffer path.
- [ ] 1.4 Add durable uniqueness/receipt boundaries for dead-letter capture and
  `conversation_reply` child effects.
- [ ] 1.5 Document and test that an effect without receiver-enforced idempotency
  or a durable receipt becomes ambiguous after an indeterminate attempt rather
  than being retried unsafely.

## 2. Add the durable action journal

- [ ] 2.1 Add a core migration for one singular action per dashboard message,
  immutable kind/canonical payload hash, per-effect receipts, action Stop intent,
  `ambiguous` state vocabulary, lease fencing, deadline/attempt bounds, and
  indexes.
- [ ] 2.2 Add database helpers that create intent with the dashboard-turn claim,
  atomically persist `attempt_started`, record action-level Stop intent, claim
  reconciliation leases, persist effect receipts, and map parent/turn outcomes
  monotonically.
- [ ] 2.3 Backfill every pre-existing `external_action_in_progress` turn to an
  explicit ambiguous action by default; never auto-replay legacy work without a
  newly available exact receipt lookup.
- [ ] 2.4 Add the persisted owner-only
  `terminal_action_reconciler.mode=observe|active` setting. Default to `observe`;
  in that mode the reconciler may inspect/lookup/mark bounded ambiguity but may
  not invoke a missing child effect. Audit promotion and preserve pending rows on
  rollback to observe.

## 3. Journal the terminal lanes and reconcile safely

- [ ] 3.1 Reserve `route_pending`, `bug_report`, or `dead_letter` atomically
  before the first irreversible dashboard dispatch. Promote a route to immutable
  `route` only on a definitive `accepted` or `ok` acknowledgement; allow a
  fenced `route_pending → dead_letter` transition only on definitive
  pre-dispatch/no-side-effect failure.
  Refuse a later conflicting tool call with `dashboard_lane_conflict`; a timeout
  or unknown route outcome SHALL be ambiguous, not dead-lettered or retried.
- [ ] 3.2 Update `file_bug_report` to journal immutable intent, invoke QA with
  stable action/effect identities, and persist each required effect receipt.
- [ ] 3.3 Update dashboard dead-letter capture and `conversation_reply` to use
  their own child-effect identities and idempotency boundaries.
- [ ] 3.4 Implement the Switchboard-owned supervised reconciler: startup catch-up,
  <=60-second cadence, 60-second lease, <=20-second heartbeat, five-attempt and
  15-minute bounds, receipt-before-retry, and ambiguity on unprovable effects.
- [ ] 3.5 Integrate Stop at the action linearization point so a pre-attempt Stop
  cancels without an effect and post-attempt Stop is pending/ambiguous until
  evidence proves the outcome.
- [ ] 3.6 Add targetless-ingress Stop reconciliation: at startup and <=60-second
  cadence inspect durable ingress/request/session evidence without redelivery;
  resolve a proven outcome or persist `ingress_stop_outcome_unknown` ambiguity by
  a deadline no later than 15 minutes after Stop intent.

## 4. Surface durable truth to the owner

- [ ] 4.1 Extend conversation message API models and read queries with the exact
  terminal-action object, child-effect summaries, sanitized reason code, safe
  resolution URL, and a durable dashboard-turn projection including the exact
  ingress state, nullable target kind, cancellation precedence/timestamp,
  safe ingress-recovery boundary, and targetless pending/retryable/rejected
  outcomes as well as route-only pending or ambiguous outcomes.
- [ ] 4.2 Add the owner-only exact-message ingress-recovery endpoint. Reuse the
  existing durable claim fence; allow a retryable error immediately and a
  submitting claim only after its 60-second recovery boundary; create no new
  dashboard message and never automatically re-dispatch Switchboard. Return the
  exact message/conversation identities, semantic recovery outcome, and current
  safe turn projection as JSON, then refetch through the durable query.
- [ ] 4.3 Add owner-only action inspection and manual-resolution endpoints that
  append immutable completed/failed evidence without invoking a relay.
- [ ] 4.4 Update the dashboard chat surfaces to render pending ingress, pending
  reconciliation, retryable/rejected ingress failure, confirmed outcome, failure,
  cancellation, pending cancellation, stale-ingress exact-message recovery, and
  actionable ambiguity without false filed/cancelled copy.
- [ ] 4.5 Ensure reload, reconnect, and bounded pending-state polling refresh the
  same durable status rather than relying on the original SSE stream; stop passive
  pending-ingress polling at the 60-second recovery boundary and require an owner
  action for retry; bound pending Stop polling by the targetless-Stop reconciler
  deadline and render its durable ambiguity without offering redelivery.

## 5. Prove failure boundaries and roll out safely

- [ ] 5.1 Add unit and real-Postgres tests for every child-effect crash boundary:
  before `attempt_started`, after attempt before receipt, after receipt, stale
  lease, and restart catch-up.
- [ ] 5.2 Add coverage for both action kinds, duplicate delivery, QA receipt
  lookup, dead-letter/reply idempotency, Stop at each linearization point,
  legacy ambiguity, manual resolution, and first-lane-wins races including
  route-then-bug and bug-then-route; also cover definitive route failure to
  dead-letter, unknown route outcome, late route success, and
  route-failure-then-bug, plus freshly opened targetless ingress,
  retryable/rejected targetless ingress, Stop during submitting and Stop after
  an ingress error, crash-after-outbound-ingress-plus-Stop/reload, stale ingress
  after a crash/reclaim boundary, exact recovery JSON/client handoff, route
  ambiguity on reload/reconnect, and UI render.
- [ ] 5.3 Run a compose-backed kill/restart canary proving no duplicate effect,
  retained conversation lineage, exact action/turn mappings, and truthful
  owner-visible status before an owner promotes the reconciler from `observe` to
  `active`.
- [ ] 5.4 Add low-cardinality pending/stale/failed/ambiguous metrics, inspect
  existing action rows, and document/test rollback to `observe` (inspection and
  bounded ambiguity only, no automatic invocation) without journal deletion or
  pending-state reset.
