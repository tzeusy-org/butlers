## 1. Versioned durable protocol foundation

- [ ] 1.1 Add guarded migrations for origin-local quiet-hours hold provenance, admission sequencing, fenced reservation states, and legacy-row preservation.
- [ ] 1.2 Add guarded migrations for Switchboard accepted-event snapshots, owner/window runs, participant snapshots, run fences, and composition/action digests.
- [ ] 1.3 Add migrated-database tests for immutability, unique accepted-event and owner/window claims, stale-fence rejection, and no direct cross-schema queue access.

## 2. Strict ingress authority and coordinator admission

- [ ] 2.1 Implement post-commit Switchboard qualification of canonical-owner direct Telegram-bot native-text events and persist only the specified provenance.
- [ ] 2.2 Add explicit, timezone-bound Owner Attention Policy release-floor configuration and fail-closed validation with no guessed default or timer.
- [ ] 2.3 Add ingress tests rejecting raw callbacks, duplicates, non-owner identities, user-client/group/forum/callback/media/caption events, HA/OwnTracks/location events, and pre-floor DMs.

## 3. Origin cohort preparation and ordinary scheduler fencing

- [ ] 3.1 Persist wake-recovery provenance atomically with newly eligible owner-attention quiet-hours holds while preserving full resolved envelopes in their origin schema.
- [ ] 3.2 Implement origin-local prepare, deterministic cutoff, same-fence replay, reason-specific abort, and commit transitions without exposing queue tables to Switchboard; retain the zero-cohort pre-durable-prepare `aborted_preprepare` path, and bind an all-uncommitted `ordinary_precommit_cancel` only after every participant has supplied a compatible durable prepare result for the complete current-fence cohort, through the landed `durable-precommit-cancellation-admission` contract.
- [ ] 3.3 Restrict the ordinary deferred scheduler to pending rows and test the zero-cohort pre-prepare path, the referenced complete-cohort cancellation-return boundary, retained/committed states, legacy, context-only, retry, partial-prepare, and post-cutoff late-row behavior without re-gating stored holds or exposing protocol-bound cohorts as ordinary work.

## 4. Health context and cross-origin MCP contract

- [ ] 4.1 Define authenticated versioned Switchboard-mediated prepare, commit, abort, and release tool schemas with participant digest, run/fence, and replay semantics; consume the landed canonical DND-generation and durable cancellation-admission contracts by reference without redefining their fields, locks, or internal transitions.
- [ ] 4.2 Implement Health validation and fenced commit that supersedes only the matching deterministic Owner-Attention-Policy sleep context; preserve all non-policy contexts.
- [ ] 4.3 Add concurrency and transition tests for DND observed before prepare or commit, the consumed DND-generation and all-cohort cancellation-admission boundaries, durable `blocked_dnd` / retained outcome, a required later qualifying DM after DND clear, exact-target mismatch, unavailable participant, oversize cohort, and the all-zero empty terminal result. Keep canonical-writer and cancellation-internal state-machine coverage in their landed prerequisite test matrices.

## 5. Exact-target composition and Messenger egress recovery

- [ ] 5.1 Implement deterministic all-participant composition using only matching fully resolved Telegram endpoint/chat/thread targets and fixed item/byte limits.
- [ ] 5.2 Persist the stable release action key through Switchboard, origin commit receipts, `notify.v1` release metadata, Messenger action/attempt/receipt records, and audit links.
- [ ] 5.3 Implement Messenger prepare/commit/release admission matching the persisted action state, canonical DND-guarded final egress admission, pre-call send-start persistence, confirmed receipt replay, and non-retryable `egress_ambiguous` recovery; consume rather than duplicate the landed cancellation-admission contract.
- [ ] 5.4 Add fault-injection tests for coordinator, origin, Health, and Messenger crashes at each durable state, including replay-safe committed/delivered/ambiguous abort outcomes, no scheduler fallback, and no second provider send after an uncertain post-start outcome.

## 6. Least privilege, end-to-end verification, and rollout gate

- [ ] 6.1 Add minimal migration-managed role grants and caller authentication checks; prove Switchboard cannot SQL-read origin queues and no participant gains direct peer/provider authority.
- [ ] 6.2 Add end-to-end tests for duplicate accepted events, window fencing, exact-target one-message composition, late rows, zero-cohort pre-prepare abort eligibility, the referenced complete-cohort post-prepare cancellation boundary, all-or-nothing retained recovery, and receipt/ambiguity reconciliation. Verify the parent consumes the landed DND and cancellation receipts without reopening their canonical writer or internal admission test matrices.
- [ ] 6.3 Run an owner-authorized staging drill with a configured release floor and real participant availability before proposing live enablement; record the reconciliation and rollback procedure.
