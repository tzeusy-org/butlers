# RFC 0023 source translation

This record accompanies the source-only canonicalization authorized by the
owner-released shape-condensation plan, phase 1 (`bu-lsxqb0.14`). It supersedes
neither the accepted contract nor the unchecked operational tasks. Baseline:
`b68bddd6062a751bc9e56743b12338f4f4af872e`. The existing adopted change is synced
and archived; no second recovery capability or runtime behavior is introduced.

The canonical WHAT is `openspec/specs/approval-delivery-intent-recovery/spec.md`.
Existing module, notify, Messenger, and dashboard IDs remain integration-seam
requirements. The RFC retains the accepted decision and trade-offs. The
original RFC sections in the table below refer to the baseline document; their
obligations remain mandatory even where verification is incomplete.

## Clause composition

IDs in this table use prefix `REQ-approval-delivery-intent-recovery-`.
Citation presence proves traceability, not every conjunct of a requirement.

| Accepted source clause | Canonical IDs | Existing executable evidence and limits |
| --- | --- | --- |
| Summary/doctrine: pending status alone gates decisions/execution; deterministic notification-only worker, no model/domain mutation, no peer pools/DSNs/grants | 002, 005 | `tests/integration/test_approval_delivery_worker.py`: expired-action and decision races preserve domain status; `tests/core/test_approval_delivery_worker.py`: lifecycle and lost-heartbeat boundaries. Dependency construction is source-inspected; these tests do not prove a universal future-path authority restriction. |
| §1: atomic action/root/presentation admission, non-null unique FK, stable action key, validated origin, database-time eligibility, rollback, semantic duplicate reuse | 001, 008, 011 | `tests/integration/test_approval_push_on_park.py`: gate admission, injected rollback, concurrent deduplication, serialization-time recomputation; producer contract requires origins. Admission is deliberately conditional on the adopted default-off rollout. |
| §1: direct monotonic presentation identity; independent cohort-owned digest, fourth-member anchor, later collapsed members, eligible-member continuity, empty-cohort replacement without duplicate handoff | 001, 005, 006 | Admission concurrency test plus worker terminal-fourth/fifth continuity, collapsed digest membership, defer and replacement-generation tests. No bare fourth-action surrogate is introduced. |
| §2: complete closed state/reason vocabulary; bounded safe durable/API/audit/metric fields; derived rather than writable stuck | 002, 007 | Real-PostgreSQL schema-vocabulary and attempt-mutation tests; worker current-render memory-only test; API safe detail/backlog tests. Unknown values and private sentinels are planted by the owning tests. |
| §3: daemon lifecycle, narrow dependencies, SKIP LOCKED, token/generation/fence CAS, safe lease succession, reconciliation-only stale started claims | 002, 003, 004 | Core lifecycle/lost-heartbeat tests; real-PostgreSQL distinct claims, stale heartbeat/transition rejection, slow-await renewal, restart matrix, reconcile-only no-resend tests. No deployment proof is claimed. |
| §3: 5-second idle polling, 30-second lease, 15-second exponential six-step retry, deterministic 0–20% jitter, 15-minute cap/SLO, database-time schedule | 003, 007 | Source constants and `_backoff_seconds()` are inspected; missing-owner test proves a future database-time retry, not every numeric backoff/jitter boundary. Stuck-age test proves due-time calculation. Exact defaults/formula coverage remains a follow-up. |
| §4: non-secret subjects/keys, non-null recovery selection, ordinary absent/null compatibility, malformed material fail-closed | 004 | `tests/core/test_routing_contracts.py`; `tests/modules/test_switchboard_module_tools.py`; daemon route null/malformed tests. Test tokens and attestation objects are injected, not issued through a real transport. |
| §4: authenticated registered issuer/schema/mode; independent non-caller-serializable source subject/presentation attestation before any persistence/egress; trusted Switchboard-only Messenger context and tuple ledger without peer reads | 004 | Core scope/client-ID check and daemon mismatch test prove local rejection; Messenger tuple test proves ledger behavior. Server auth provisioning, actual bearer propagation, and independent source admission attestation are not proven or wired by these tests. See pre-activation gap below. |
| §4: durable pre-provider marker; confirmed/safe_retry/ambiguous classes and safe receipts; same-key duplicate suppression, proof-only retry, post-start no-resend, acceptance is not owner-read | 004 | Real-PostgreSQL Messenger tuple/ambiguity and safe-prestart tests; source worker timeout, expired handoff, and restart matrix. Provider mocks exercise accepted result shapes; no real provider/private data is used. |
| §4: Telegram/email/WhatsApp capability inventory; none currently accepts presentation idempotency or exposes reconcile lookup; every uncertain post-start failure remains ambiguous; future proof requires new evidence | 004 | `_routing.py` adapter switch and the three module signatures are source-inspected. Messenger tests cover synthetic Telegram-like receipt, email sent result, WhatsApp explicit error and timeout. They do not certify an external provider capability. |
| §4/§6/§7: no generic notification/outbound message_inbox persistence, including redacted substitutes; no envelope reconstruction, history/LLM readers, retry/escalation/ack/count leakage; future redacted record separately reviewed | 006, 009 | `test_recovery_path_persists_no_generic_or_history_content` plants rendered-message, recipient-thread, and callback sentinels, plus positive ordinary-history controls; checks notifications/message_inbox absence, generic read/count/ack exclusion, reconstruction rejection and three history loaders. Dedicated generic retry/escalate no-control and every future reader remain universal obligations, not established by this one test. |
| §5: every pending-to-terminal writer atomically cancels/fences and marks only its membership ineligible; fixed lock order, both decision/expiry races, late results append-only/no revival | 005 | Real-PostgreSQL decision, expiry, late result, transaction-exit-failure and cohort tests. There is no current source gate exhaustively rejecting future direct SQL terminal writers; the existing admission inventory gate checks INSERTs only. |
| §5: authenticated bounded dashboard defer alone extends expiry and adds one g+1 at now+hours; both race orderings and cohort-member detachment; worker cannot reschedule | 005, 006 | Worker prestart/handoff-first defer, cohort-member defer, replacement-history tests; `tests/api/test_api_approvals.py` bounded-hours and expired-action endpoint tests. These are disposable/mock authority evidence, not a live authenticated rollout. |
| §6: stable key across edits/retries/restarts/duplicates; exact quiet-hours admission/no re-gate; insight-budget isolation; ten-minute burst rules, digest dashboard link/expiry preservation; generic scheduler not recovery authority | 001, 003, 006 | Quiet-hours snapshot and concurrent admission tests; worker exhausted-insight-budget test and membership continuity tests; renderer inspected for dashboard link. Universal future isolation remains binding. |
| §7: safe append-only attempt fields, immutable action/cohort terminal event, unresolved retention, FK cleanup order, event provenance survives mutable pruning | 007, 011 | `tests/modules/test_approvals_retention.py`: ambiguous retention, resolved summary cleanup and terminal graph cascade; worker late result and schema immutable-attempt tests. Selected downgrade categories are tested; every future evidence category is not thereby certified. |
| §7: truthful API/dashboard state, mode/generation, safe reason/count/time, explicit stuck/ambiguous, legacy evidence without never-notified fabrication; safe state/reason/age/lease and scan/claim/handoff observability, no mutation controls | 007 | API safe detail/backlog/legacy-failed state tests and source frontend/read-model inspection. Tests do not certify all dashboard rendered states or every structured scan/claim/handoff metric/log outcome. |
| §8: all listed gate, email/recipient guards, core-notify, calendar, connector, relationship assertion/curation and prepared producers share admission; auto-approved SQL only exception | 008, 010 | `tests/contracts/test_relationship_pending_actions_choke_point.py` scans complete production `src/` and `roster/`, checks gate auto-approved inserts, helper uniqueness, origin supply and relationship delegation. This structural gate proves source admission coverage, not every producer’s end-to-end behavior. |
| §8: prepared relationship reach-out and travel connection-risk doors stay collapsed, standalone, silent, outside burst counts/membership/due work/provider/defer; default-off legacy prepared row only | 010, 011 | `tests/modules/approvals/test_prepared_actions.py` exercises enabled/default-off prepared admission, both producer origins, ordinary burst interaction and defer rejection. |
| Rollout/rollback: additive compatibility first, writer/worker default-off, no historical backfill/replay/dual-send, authorized synthetic canary and gradual activation, legacy-read compatibility, non-destructive binary rollback/guarded nonempty downgrade | 011 | Admission default/absent/invalid configuration tests; lifecycle disabled/unreadable worker tests; migration legacy preservation and selected downgrade-category tests. Canary and activation tasks 7.2/7.3 remain unchecked and were not performed by source archive. |
| Verification: real-PostgreSQL fault/concurrency/race/retention/authority/history negative proof and source admission gate required | 001–011; archived tasks §6 | Existing owning tests remain the verification path. Local Docker socket denial prevents executing the database files here; hosted owning-file evidence is required before merge. A mechanical strict citation pass is never labeled runtime or whole-contract acceptance. |

## Preserved implementation gaps

**[Observed] Pre-activation authority/transport gap.**
`ApprovalRecoveryRuntime` documents dormant construction, and daemon startup
leaves `_approval_delivery_runtime` unset. `authenticated_daemon_name()` checks
an already-supplied FastMCP token's scope, daemon name and client ID, while
`recovery_context_from_request()` builds subject/presentation context from the
caller model. There is no independently server-held source-schema admission
attestation in that helper. `src/butlers/lifecycle.py` constructs FastMCP without
a configured auth provider; this source task does not infer that an injected
AccessToken supplies a mint/verifier or authenticated client wiring. The route test injects `get_access_token()` and
trusted context; the real-PostgreSQL test passes `trusted_source` directly and
mocks the remote route. Neither proves token mint/verifier protection, real
authenticated daemon-to-Switchboard-to-Messenger transport, or independent
source subject/presentation authority. Keep activation fail-closed until a
separately scoped source/runtime follow-up supplies those seams and rejection
evidence without peer pools or grants. This source translation introduces no
signer, secret provisioning, or blanket transport rollout.

**[Observed] Verification gaps.** Exact idle/lease/backoff/jitter defaults,
the universal direct terminal-update choke-point guard, all generic
retry/escalate controls, every future history reader, all structured worker
observability outcomes, real runtime-role isolation, and full rendered dashboard truth are not established
by the cited tests. Preserve their mandatory clauses and pursue a cohesive
contract-coverage follow-up at the existing worker/transport/lifecycle seams,
rather than adding a test per sentence or treating citations as completeness.

## Mechanical maintenance and rollback

Runtime code, migrations, API behavior, provider adapters, and existing test
assertions remain unchanged. Requirement citations are comments beside owning
tests. The structural planning test follows its relocated spec/tasks fixtures;
its fixture-path literals change, with assertions, sentinel terms and test
functions preserved. Its old planning docstring is refreshed. Report that AST
exception separately from the unchanged runtime-test ASTs.

The inherited module delta also carried five Queue and nine Transition
body-loss ratchet entries. Composition preserves the current baseline’s exact
rationale/evidence fields, count/dict shape, human decision provenance,
execution success audit, all-row expiry, and abandonment constraints alongside
the adopted recovery additions. Disappearance of an active ratchet entry on
archive is not credited as proof of preservation; canonical/archive block
parity and a direct baseline clause comparison supply that evidence.

The source archive preserves the unchecked canary/activation boxes and their
gates. It establishes the capability's canonical source home, not operational
completion. Reverting this documentation commit restores the former source
arrangement without touching runtime state or data.
