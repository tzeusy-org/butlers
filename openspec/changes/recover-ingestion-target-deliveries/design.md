## Context

See `proposal.md` for the incident. Today `src/butlers/modules/pipeline.py` can execute a target route during an LLM `route_to_butler` tool call or decomposition fan-out, then persist `message_inbox` classification and dispatch outcomes afterward. `src/butlers/core/route_inbox.py` mints a fresh UUID on each target acceptance. `src/butlers/core/ingestion_events.py::ingestion_event_replay_request` handles a failed ingestion row with a status-only `failed -> ingested` write and handles `ingested`/`replay_failed` by setting `replay_pending` then resetting `message_inbox` to `accepted` for fresh classification. Those seams cannot prove or safely retry a missing handoff.

RFC 0038 replaces the lane rather than repairing it. Switchboard keeps ingest envelopes, deduplication and the durable buffer, loses classification and route dispatch (D1), and admits each envelope that needs reasoning as one goal (D5). Admission is deterministic, reads origin and cause from server-held context, and returns the existing goal for a duplicate ingest envelope. Deterministic ingestion stays on the service plane. The broker never re-dispatches an effect on its own (D12). Before the switch, classification and route dispatch are frozen to fixes, and old buffers drain before the switch (Hard cutover).

The generalist change defines the profiles this amendment uses: KEY_ABSENT_STAGE keeps today's classify-and-route service; ISOLATED_CANDIDATE runs admission only for the closed trusted test harness; SWITCHED_RELEASE applies admission to actual ingress after the owner-authorized switch. Its `Ingest Envelopes Feed Goal Admission` requirement (`REQ-butler-switchboard-007`) and the admission ledger (`admission_decisions`, `admission_attempt_bindings` from `sw_050`/`sw_051`, `REQ-goal-orchestration-005` source dedupe) are the substrate this change binds to. Generalist removes the whole `conversation-decomposition` capability and `module-pipeline`'s `Decomposition-to-Routing Fan-Out` at task 4.2.

## Goals / Non-Goals

**Goals:** Admit each reasoning envelope exactly once, with a durable admission receipt; make recovery and retry return that receipt instead of reclassifying or relabeling; keep legacy target receipts readable and drain legacy routes before the switch; show admission and legacy evidence truthfully; provide a bounded owner-reviewed path for historical late admission.

**Non-Goals:** A per-target classifier, decomposition plan, fan-out, delivery outbox, dispatcher or retry worker on the frozen path; goal execution, effect idempotency and effect receipts (RFC 0038 D11, D12); the D19 owner surface; recovery of Messenger sends, domain-event subscribers, or connector/provider ingress through this change; rewinding source cursors; admitting historically late material without exact owner approval.

## Decisions

### Decision 1: One deduplicated goal admission per reasoning envelope

In ISOLATED_CANDIDATE and SWITCHED_RELEASE, Switchboard submits each accepted envelope that needs reasoning to goal admission once, keyed by its durable source key. Admission writes each attempt's immutable decision and its admission receipt in one PostgreSQL transaction with the goal or goal-inbox row, or with the record-only or refusal outcome. `admitted`, `joined` and `recorded_without_goal` are terminal for the source key: a duplicate ingress, connector redelivery, durable-buffer restart or recovery request reads that decision by source key and returns the same receipt. Any other refusal binds only its attempt, so a retryable refusal can be retried under the same source key (REQ-goal-orchestration-005). A feed envelope that reaches admission without a matching subscription receives the terminal `recorded_without_goal` decision (sw_051). A conversation-history batch is one envelope and one goal; its lead handles every domain signal. Deterministic ingestion (gating outcomes such as skip and metadata-only, channel-bound envelopes, bulk backfill and sync) is never submitted to admission; it goes to its service-plane ingest and creates neither a goal nor an admission receipt.

The receipt reuses the generalist admission ledger rather than adding a table: the decision identity, source key, attempt binding, outcome (`admitted` or `joined` with goal ID, `recorded_without_goal`, or `refused` with a safe reason and any `retry_after`) and server timestamp. `recorded_without_goal` and `refused` are both stored as result `refused`, distinguished by reason. A durable classified plan before route dispatch was the original design; it is rejected because it is new classification work under the freeze and generalist deletes classification at the switch.

### Decision 2: The receipt binds source key and attempt identity

The source key is the deduplication identity Switchboard already derives (channel-specific keys such as Telegram `update_id`, email `Message-ID`, API caller key). The receipt binds each submitting attempt to its decision through the immutable `admission_attempt_bindings` (sw_050), whose composite foreign key ties attempt, source key and decision together. A repeated attempt returns its exact decision; a new attempt for a key with a terminal decision is bound to that decision; an attempt identity already bound to another source key fails as unavailable. The ledger has no envelope digest column and this change adds none. The source key, attempt bindings, decision identity and outcome live in a content-blind, append-only ledger that v1 never prunes, including during migration replay and rollback. Message bodies in `message_inbox` and the buffer follow existing retention; a duplicate is still resolved against the ledger after they age out. `public.ingestion_events.received_at` is not an admission-age fence, because runtime grants permit UPDATE. A future bounded-retention design must first add a database-enforced non-readmission fence. The ledger grows over time and needs capacity monitoring.

### Decision 3: Buffer-to-admission retry is safe because admission is deduplicated

Admission runs in the control plane against the same database as the buffer, so an interrupted attempt resolves by reading the decision bound to its attempt, then any terminal decision for its source key: a committed decision is returned, and the absence of both means admission did not happen and may be attempted again. A retryable refusal is retried with a new attempt identity under the same source key, no earlier than its `retry_after`. The buffer drain retries with jitter, an attempt cap and an age cap; exhausting them leaves the envelope visibly `admission_failed` with a closed safe reason rather than dropping it. Retrying admission never re-dispatches an effect: effects and their receipts belong to the goal ledger (D11, D12), and a goal that fails is never re-admitted because it failed. The admission receipt states admission only, never goal completion.

### Decision 4: Legacy target receipts stay readable and drain before the switch

In KEY_ABSENT_STAGE the classify-and-route lane runs unchanged except for the recovery fix in Decision 5. Its evidence (`route_inbox` rows, `message_inbox.dispatch_outcomes`, `decomposition_output`, routing logs and transport outcomes `confirmed | rejected | uncertain | not_attempted`) stays readable as pre-switch records. Before the switch, Switchboard stops accepting new routes, lets in-flight route-inbox work finish, and records a content-blind residue of legacy work it cannot prove accepted. Residue is never re-routed, and never re-admitted as a goal except through Decision 6. The switch release runs no destructive migration, so legacy rows stay queryable afterward.

### Decision 5: Failed-event recovery never relabels or reclassifies

For `public.ingestion_events`, no recovery branch sets `failed -> ingested` by status alone, resets `message_inbox` to `accepted`, or classifies again.

- In KEY_ABSENT_STAGE, `failed`, `ingested` and `replay_failed` return a specific non-success result with a content-blind reason, because the frozen lane has no durable unaccepted work to queue safely. `replay_pending` with no queued work is projected as unavailable or conflicting. Other statuses conflict.
- In ISOLATED_CANDIDATE and SWITCHED_RELEASE, `failed`, `ingested` and `replay_failed` resubmit the original envelope identity to admission. Admission returns the existing receipt when a decision exists, or admits once when retained buffer evidence proves no decision was made. `replay_pending` returns the same pending identity idempotently. A legacy row without a retained envelope or admission evidence keeps its state and returns an ineligibility reason. Origin and exposure come from the stored evidence, so recovery can never upgrade authorship or reduce exposure.

The status change and any admission write share one transaction. A request cannot report pending unless work is durably queued. For `connectors.filtered_events`, preserve the current connector-policy-gated `filtered | error | replay_failed | ingested | replay_complete -> replay_pending` drain contract; `replay_pending` does not enqueue twice. Preserve email replay safety.

### Decision 6: Historical late admission is a separate exact owner gate

The historical dry run reads retained Switchboard event, inbox, buffer, legacy residue and admission evidence. Legacy target acceptance evidence comes from receipts retained before the switch, never from direct SELECT on another butler's schema; missing, conflicting or unsupported evidence excludes the event as unprovable. The preview groups counts by time bucket, channel and safe failure class, and privately produces an exact candidate set. Only events with a retained envelope, no admission receipt, and either no legacy route or independently proven no-effect legacy outcome are candidates. Admitted, legacy-accepted, ambiguous, rejected, policy-denied, pruned and unprovable events are exclusions. If none qualifies, the eligible set is empty.

The owner reviews the set privately and, only after an explicit decision, creates an immutable server-side approval record through an owner-authenticated operation: exact source keys, bounded age and channel policy, preview evidence version, fixed expiry and canonical digest. A declined decision creates no record. The admission operation accepts only the opaque record ID, rechecks each key, and atomically records one consumed result plus one deduplicated admission per eligible key. Late admission takes origin and exposure from stored evidence; third-party material stays third-party and exposed. Repeated calls return the same result. Revocation or expiry makes an unused record unusable. It never rewinds a provider cursor or replays connector ingestion.

### Decision 7: Recovery evidence stays content-blind

API, log, metric, condition and dry-run projections use allowlisted state and reason codes, bounded identifiers (event ID, source-key digest, decision ID, goal ID) and counts. Raw messages, sender identities, provider payloads, exceptions and credentials never enter these projections. Schema checks reject unknown state or reason values.

## Risks / Trade-offs

- **Undrained legacy work at the switch:** a target that never finishes leaves residue. The switch waits for drain; residue stays visible and needs owner review rather than silent replay.
- **Admission latency:** one admission per envelope replaces classification, so it removes a model step; buffer retry adds bounded delay only on failure.
- **Historical evidence quality:** old rows may lack a retained envelope or proof. The dry run prefers an empty eligible set over inferred safety.
- **Retention:** the content-blind receipt ledger is not pruned in v1. A prune-inbox-then-duplicate real-PostgreSQL regression is required, and compaction needs a separate non-readmission fence.
- **Rollback:** rollback redeploys the previous release (RFC 0038 Rollback). Goal and receipt tables stay and are ignored; nothing replays admitted or legacy-accepted work.

## Migration Plan

1. In KEY_ABSENT_STAGE, ship the Decision 5 recovery fix and read-only legacy projections. Add no classifier, plan, intent, dispatcher or retry worker.
2. In the dark build, bind the admission receipt (source key, attempt binding, outcome) to the generalist admission ledger and prove deduplication, retryable-refusal retry and buffer-restart behavior in ISOLATED_CANDIDATE through the closed test harness.
3. Before the switch, stop new routes, drain in-flight legacy work and record residue. At the switch, route reasoning ingress and recovery through admission as part of the one-release cutover.
4. After the switch, run the content-blind historical dry run. Historical admission stays inactive until an exact owner approval.
