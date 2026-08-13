## 1. Contract and additive persistence

- [ ] 1.1 Confirm the owner-approved RFC 0023 constants: closed root/presentation/cohort state and reason vocabulary, logical action/cohort-subject and presentation-key formats, defer-generation/cohort replacement bounds, lease/backoff/stuck SLOs, authenticated transport-principal contract, and the per-provider idempotency/reconciliation capability inventory.
- [ ] 1.2 Add guarded Approvals migrations for `approval_delivery_intents`, monotonic `approval_delivery_presentations`, durable burst cohorts/membership, append-only attempts, safe terminal event vocabulary, due/lease indexes, foreign keys, and fail-closed downgrade checks; do not backfill legacy rows.
- [ ] 1.3 Add the narrowly wired Messenger approval-handoff migration keyed by trusted `(issuer, owning_schema, presentation_key, mode)`, with pre-provider-start persistence and non-destructive downgrade checks; store no recovery envelope/callback material in generic notification tables.
- [ ] 1.4 Add real-PostgreSQL migration tests proving fresh/upgrade shape, generation/cohort uniqueness and constraints, legacy `approval_push_emissions` preservation, no historical intent creation, safe closed vocabularies, and downgrade refusal while recovery data exists.

## 2. Atomic parking admission and producer conversion

- [ ] 2.1 Refactor `src/butlers/modules/approvals/park.py` into the sole typed transaction helper that validates origin/dossier inputs, inserts or resolves the action, computes RFC 0021 admission, and inserts one logical intent/action key plus the required direct presentation, cohort-owned digest, or collapsed membership atomically.
- [ ] 2.2 Move burst reservation from independent `approval_push_emissions` work into the helper's same-transaction admission path, preserving first-three / cohort-owned digest / collapsed membership semantics and leaving legacy emissions read-only.
- [ ] 2.3 Convert the gate and all three recipient/email guard paths to the new helper without changing their fail-closed approval decision behavior.
- [ ] 2.4 Convert the core notify missing-identifier path, daemon calendar-overlap path, and dashboard connector-disconnect path to the new helper.
- [ ] 2.5 Convert relationship assertion plus memory fact retraction, entity merge, email identity enrichment, and memory reclassification curation paths to the new helper, preserving each producer's semantic deduplication contract.
- [ ] 2.6 Add a source-level producer inventory/AST contract that rejects every direct `pending_actions(status='pending')` insert outside the helper while allowing explicitly auto-approved paths.

## 3. Fenced notification-only recovery worker

- [ ] 3.1 Implement the schema-local root/presentation/cohort repository and state machine with due presentation selection, `FOR UPDATE SKIP LOCKED` claims, token/fence/lease CAS transitions, safe attempt recording, and deterministic database-time backoff.
- [ ] 3.2 Add a daemon-owned approval-delivery lifecycle loop that starts only for active Approvals schemas and receives a narrow notification runtime, never approval operations, executor, entity/fact, or generic deferred-queue authority.
- [ ] 3.3 Render `single` and cohort-owned `burst_digest` presentations deterministically at dispatch time using current verified owner resolution and callback-secret lookup in memory only; make collapsed action presentations terminal without a provider call.
- [ ] 3.4 Implement stale-claim recovery, `handoff_started` reconciliation, derived stuck status, safe no-retry handling for unknown post-start outcomes, and a pre-start-only deferred generation successor; no worker may defer or schedule a new presentation.

## 4. End-to-end handoff idempotency

- [ ] 4.1 Extend `NotifyDeliveryV1`, Switchboard forwarding, and normalized response models with a recovery-only presentation mode, direct-action/cohort-subject and presentation correlation keys, and `confirmed` / `safe_retry` / `ambiguous` result classes while preserving ordinary `notify.v1` compatibility.
- [ ] 4.2 Establish the authenticated daemon-to-Switchboard/Messenger source-principal boundary and a non-caller-serializable source-schema subject/presentation attestation; derive issuer/owning schema from it, validate registered ownership and mode before generic logging, ledger persistence, or provider egress, reject any field/key/prefix mismatch fail-closed, and add no peer pool or cross-schema grant.
- [ ] 4.3 Implement Messenger's real route.execute/provider-boundary handoff ledger keyed by trusted issuer/schema/presentation/mode, same-tuple reconciliation, receipt/duplicate-safe response, and explicit ambiguity handling for adapters without proof-bearing idempotency.
- [ ] 4.4 Wire the source worker to persist a fenced pre-provider marker, send/reconcile only the current immutable presentation key, and forbid a fresh key or provider call after an ambiguous outcome.
- [ ] 4.5 Branch recovery-mode `notify.v1` before generic `log_notification()` and `_write_outbound_message_inbox()` persistence; insert no outbound `switchboard.message_inbox` row, including a redacted substitute, and expose only the safe approval delivery projection.
- [ ] 4.6 Exclude recovery delivery from generic list/history/read/stats/acknowledge/retry/escalate/stored-envelope reconstruction and all generic conversation/LLM-history readers; do not persist rendered text, recipient-derived thread identity, or callback material there.
- [ ] 4.7 Add adapter and negative-authority tests showing confirmed same-tuple duplicate suppression, safe pre-start retry, Telegram-style unknown post-start no-resend, spoofed issuer/schema/mode/key rejection before egress, and no generic recovery replay or envelope disclosure. Add the real-PostgreSQL negative integration test with rendered-text, recipient-derived thread-identity, and callback-material sentinels; prove no `switchboard.message_inbox` row and no `_load_realtime_history`, `_load_email_history`, or `_load_conversation_history` result exposes a sentinel.

## 5. Decision, expiry, retention, and operator truth

- [ ] 5.1 Introduce a shared action-then-intent/presentation terminal transition helper and use it in approve, reject, explicit/stale expiry, and any future pending-to-terminal path; preserve executor behavior, cancel the action's unsent current/future presentations, and mark only that action's cohort membership ineligible without cancelling an eligible shared digest.
- [ ] 5.2 Implement the authenticated dashboard defer transaction as the sole nonterminal scheduling writer: for each successful defer, extend expiry, supersede a pre-start generation or record a handoff-first historical result, and append exactly one `now + hours` successor under the shared lock; for a fourth cohort anchor or later collapsed member, mark only that membership ineligible for an unstarted cohort digest and create its direct successor.
- [ ] 5.3 Ensure the worker only observes action eligibility and writes presentation/attempt evidence; cover send-start-versus-decision and defer-versus-send-start linearization races plus the late-result no-revival rule.
- [ ] 5.4 Extend approval retention and immutable safe delivery-summary audit events so unresolved/ambiguous roots, presentations, and cohorts survive while their action or cohort membership is pending and terminal cleanup follows the established provenance contract.
- [ ] 5.5 Extend approval API models, router joins, frontend types, and Approvals page rendering with safe delivery-state truth, legacy evidence labeling, generation/cohort status, and no “never attempted” fabrication.
- [ ] 5.6 Add safe metrics/logs/read-model aggregation for due age, retry/lease backlog, ambiguous/stuck counts, cohort replacement, and closed reason dimensions without sensitive labels or action-mutating controls.

## 6. Real-PostgreSQL fault, concurrency, and compatibility verification

- [ ] 6.1 Add real-PostgreSQL transaction tests for action-plus-intent/presentation rollback, semantic-key duplication, concurrent burst admission, terminalized-fourth/fifth-member cohort continuity, and complete production callsite coverage.
- [ ] 6.2 Add multi-worker real-PostgreSQL tests for `SKIP LOCKED` claims, stale lease fencing, token mismatch, heartbeat/terminal-write rejection, and restart recovery before send start.
- [ ] 6.3 Add crash-injection tests at claim, pre-handoff marker, Messenger handoff persistence, provider acceptance before source result, terminal-result persistence, and daemon restart boundaries.
- [ ] 6.4 Add decision/expiry/defer race tests proving cancellation before handoff prevents send, handoff-first blocks only future recovery, each successful defer creates exactly one fenced `now + hours` successor, cohort-member defer preserves other eligible digest members, and the worker never mutates a parked domain action.
- [ ] 6.5 Add RFC 0021 regression tests for exact quiet-hours release/no re-gate, control-plane budget isolation, and concurrent first-three/digest/collapsed behavior outside generic deferred delivery.
- [ ] 6.6 Add generic-notification exclusion plus approval API/frontend redaction/truthful-state tests, retention/downgrade/stuck-observability tests, and exact-head focused/broader quality gates.

## 7. Additive rollout and rollback gate

- [ ] 7.1 Ship additive schema/read compatibility and metrics with new writers/workers disabled; verify legacy rows are not backfilled, replayed, or dual-sent.
- [ ] 7.2 Execute an owner-authorized staging/canary drill using synthetic newly parked actions only, including worker restart, provider ambiguity, quiet-hours, and decision-race reconciliation.
- [ ] 7.3 Enable new writers/workers schema-by-schema only after the canary evidence and dashboard truth review; monitor stuck/ambiguous/oldest-due metrics.
- [ ] 7.4 Document binary rollback as additive and block schema downgrade/drop while any root/presentation/cohort/attempt/audit data exists; do not delete, replay, approve, execute, or backfill historical actions.
