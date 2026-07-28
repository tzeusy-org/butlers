## 1. Establish relay evidence and dependency boundary

- [ ] 1.1 Independently review and merge the exact current-base #3624
  dashboard-turn authority head before adding terminal-action recovery work. On
  its exact landing commit, rebase this change and fully reconcile every
  replacement clause of `dashboard-chat-ui` → `SSE Client Integration` against
  the landed #3624 and `chat-stop-button-server-cancel` contract: immutable
  pre-conversation `message_id`, accessible pending Stop, persisted intent before
  later ingress/runtime work, runtime cancellation, terminal
  `SESSION_CANCELLED` SSE, and truthful non-calm failure. Preserve each clause
  or record an explicit owner-approved supersession before this change is signed
  off; do not rely on a prior green head from an older base.
  Before that merge, #3624 SHALL repair the claim-to-anchor lease-loss race and
  prove a reclaimed worker cannot invoke, reconcile dashboard-only no-replay
  recovery and the canonical message-scoped cancel endpoint with RFC 0001, RFC
  0003, and the dashboard API inventory, and cover both no-resubmit retry after
  accepted ingress and observer-side `SESSION_CANCELLED` rendering.
- [ ] 1.2 After #3624 lands, decide PR #3618 explicitly: rebase it onto the
  landing and independently revalidate its truthful dispatch receipt/UI scope,
  or close it as superseded only after an owner-approved per-guarantee
  disposition. That record SHALL cover its `dispatch_accepted` route-versus-
  targetless receipt and accessible announcement, accountable routed-butler
  link, and non-destructive conversation-list/history read recovery. After
  #3618 no longer actively modifies the same main requirements, transplant every
  retained guarantee into the surviving changeset and independently validate it;
  an omitted guarantee requires an explicit owner rejection. This is a
  pre-signoff HOLD gate for this changeset, not merely an implementation
  sequencing note: do not approve competing dashboard Stop/SSE requirements or
  silently discard truthful UI behavior.
- [ ] 1.3 After `reconcile-dashboard-conversation-contracts` lands its RFC 0003
  vocabulary/provenance authority, rebase on that exact head and amend only the
  recovery guidance: a dashboard route-inbox row recovered from `processing`
  with its immutable dashboard message identity and no durable route proof
  becomes owner-visible route ambiguity. Preserve safe replay for dashboard
  `accepted` pre-dispatch rows and all non-dashboard rows. Add regression
  coverage for both no-replay-after-unproven-processing and
  safe-replay-before-dispatch.
- [ ] 1.4 Add a dedicated Switchboard-router bearer credential in the existing
  credential store, have its cached QA MCP client present it, and configure a QA
  FastMCP auth provider to validate its subject/client and QA audience from
  request/access-token context before dashboard-mode writes or lookups. Never
  authorize from caller-supplied `source_butler`; cover anonymous,
  wrong-subject, wrong-audience, spoofed-source, and partial-identity rejection.
- [ ] 1.5 Add dashboard-action/effect identities to QA `report_finding` and a
  durable dashboard-report inbox/receipt store. Extend the existing
  `butler_reports` source with a fenced durable
  `pending -> claimed -> acknowledged` claim/ack lifecycle that emits and links
  one patrol-owned finding after restart; enforce one inbox-to-finding mapping,
  canonical payload/idempotency validation, `not_found`-only same-key redelivery,
  and bounded `unavailable` ambiguity. Reject new dashboard delivery while the
  source is disabled, but retain accepted inbox evidence for a later enabled
  claim; keep ordinary non-dashboard reports on their existing volatile-buffer
  path.
- [ ] 1.6 Add the authenticated receipt lookup on the same QA principal
  boundary; prove direct callers cannot enumerate receipts and mismatched
  duplicate idempotency keys cannot overwrite the canonical record.
- [ ] 1.7 Amend RFC 0015 and `roster/qa/MANIFESTO.md` in this implementation
  change to replace the dashboard-report volatility deferral with the
  authenticated durable-inbox exception and document ingestion, triage
  ownership, permissions, recovery, and service-level expectations.
- [ ] 1.8 Add durable uniqueness/receipt boundaries for dead-letter capture and
  `conversation_reply` child effects.
- [ ] 1.9 Document and test that an effect without receiver-enforced idempotency
  or a durable receipt becomes ambiguous after an indeterminate attempt rather
  than being retried unsafely.

## 2. Add the durable action journal

- [ ] 2.1 Add a core migration for one singular action per dashboard message,
  immutable kind/canonical payload hash, per-effect receipts, action Stop intent,
  `ambiguous` state vocabulary, `cancelled` child reason codes,
  one-time immutable owner-resolution overlays, lease fencing, deadline/attempt
  bounds, and indexes.
- [ ] 2.2 Add database helpers that create intent with the dashboard-turn claim,
  atomically and reciprocally fence `planned -> attempt_started` against
  Stop-driven `planned -> cancelled(suppressed_by_stop)`, record action-level
  Stop intent, claim reconciliation leases, persist effect receipts, and map
  parent/turn outcomes monotonically.
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
  `route` only on a definitive `accepted` acknowledgement; allow a
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
  evidence proves the outcome. If a primary child completed first, suppress an
  unstarted acknowledgement as `cancelled/suppressed_by_stop`, return
  `pending_reconciliation` until the failed partial-effect projection is durable,
  and never call that outcome `cancelled`.
- [ ] 3.6 Add targetless-ingress Stop reconciliation: at startup and <=60-second
  cadence inspect durable ingress/request/session evidence without redelivery;
  resolve a proven outcome or persist `ingress_stop_outcome_unknown` ambiguity by
  a deadline no later than 15 minutes after Stop intent.
- [ ] 3.7 Amend `roster/switchboard/MANIFESTO.md` in this implementation change
  to name the authenticated QA relay, Switchboard's terminal-action reconciler
  ownership, credential/receipt permissions, observe/active control, recovery
  bounds, and owner-visible service-level expectations.

## 4. Surface durable truth to the owner

- [ ] 4.1 Extend conversation message API models and read queries with the exact
  terminal-action object, child-effect summaries, sanitized reason code, safe
  resolution URL, immutable owner-resolution overlay/read shape and repeat
  semantics, and a durable dashboard-turn projection including the exact
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
  append exactly one immutable completed/failed assessment with a bounded
  sanitized note, return an identical repeat idempotently, conflict a changed
  repeat, and never invoke a relay or alter the ambiguous parent/turn state.
- [ ] 4.4 Update the dashboard chat surfaces to render pending ingress, pending
  reconciliation, retryable/rejected ingress failure, confirmed outcome, failure,
  cancellation, pending cancellation, stale-ingress exact-message recovery, and
  actionable ambiguity without false filed/cancelled copy. Introduce the
  outcome-only message-scoped cancel endpoint, migrate `api/client.ts`,
  `FloatingChatWidget`, `ChatPanel`, their types/tests and the dashboard API
  inventory, prove no repository-owned caller remains, then delete the
  conversation-scoped endpoint, boolean response model/type, client alias, and
  compatibility assertions in this same implementation change. Any exception
  requires an owner-approved amendment naming a verified consumer, accountable
  owner, and dated sunset.
- [ ] 4.5 Ensure reload, reconnect, and bounded pending-state polling refresh the
  same durable status rather than relying on the original SSE stream; stop passive
  pending-ingress polling at the 60-second recovery boundary and require an owner
  action for retry; bound pending Stop polling by the targetless-Stop reconciler
  deadline and render its durable ambiguity without offering redelivery.

## 5. Prove failure boundaries and roll out safely

- [ ] 5.1 Add unit and real-Postgres tests for every child-effect crash boundary:
  before `attempt_started`, after attempt before receipt, after receipt, stale
  lease, and restart catch-up.
- [ ] 5.2 Add coverage for both action kinds, duplicate delivery, authenticated
  QA receipt lookup and direct/spoofed-call rejection, inbox
  lifecycle/reclaim/source-disabled behavior, dead-letter/reply idempotency,
  Stop at each linearization point, legacy ambiguity, manual
  resolution read/repeat/conflict semantics, and first-lane-wins races including
  route-then-bug and bug-then-route; also cover definitive route failure to
  dead-letter, unknown route outcome, late route success, and
  route-failure-then-bug, plus freshly opened targetless ingress,
  retryable/rejected targetless ingress, Stop during submitting and Stop after
  an ingress error, crash-after-outbound-ingress-plus-Stop/reload, stale ingress
  after a crash/reclaim boundary, exact recovery JSON/client handoff, route
  ambiguity on reload/reconnect, and UI render.
- [ ] 5.2a Add coverage for Stop between child effects: preserve the completed
  primary receipt, use reciprocal atomic fencing to cancel the unstarted
  acknowledgement with `suppressed_by_stop` without invocation, return pending
  reconciliation until the parent failure is durable, and project
  `stopped_after_partial_effect` truthfully in API and UI.
- [ ] 5.3 Run a compose-backed kill/restart canary proving no duplicate effect,
  retained conversation lineage, exact action/turn mappings, and truthful
  owner-visible status before an owner promotes the reconciler from `observe` to
  `active`.
- [ ] 5.4 Add low-cardinality pending/stale/failed/ambiguous metrics, inspect
  existing action rows, and document/test rollback to `observe` (inspection and
  bounded ambiguity only, no automatic invocation) without journal deletion or
  pending-state reset.
