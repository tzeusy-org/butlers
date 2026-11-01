## Amendment scope

RFC 0038 D1, D5, D11, D12, D17 and Hard cutover, and the generalist collision disposition (`re-scope`), govern every task below. Reasoning ingress becomes one deduplicated goal admission with a durable admission receipt; deterministic ingestion stays on the service plane; legacy target receipts stay readable and drain before the switch. No task builds a classifier, decomposition plan, fan-out, delivery outbox, dispatcher or retry worker on the frozen classify-and-route path. Profile names (KEY_ABSENT_STAGE, ISOLATED_CANDIDATE, SWITCHED_RELEASE) follow the generalist change.

## 1. Frozen-lane fixes (KEY_ABSENT_STAGE)

- [ ] 1.1 Replace every `public.ingestion_events` replay branch with the KEY_ABSENT_STAGE matrix in Decision 5: `failed`, `ingested` and `replay_failed` return a specific non-success result; `replay_pending` without queued work is projected as unavailable or conflicting; other statuses conflict. Never set `failed -> ingested` by status alone, reset terminal `message_inbox`, or reclassify. Retain connector `filtered_events` replay-safe policy joins and email semantics. Test each status, duplicate request and store failure. Covers `REQ-ingestion-target-delivery-recovery-006` and `REQ-ingestion-event-registry-002`.
- [ ] 1.2 Project legacy target evidence (`route_inbox` rows, `dispatch_outcomes`, `decomposition_output`, transport outcomes) read-only in the ingestion list and detail APIs, keeping partial legacy fan-out non-complete and unreadable evidence unavailable. Covers `REQ-ingestion-target-delivery-recovery-005` and `REQ-ingestion-event-registry-002`.

## 2. Admission receipt (dark build, ISOLATED_CANDIDATE)

- [ ] 2.1 Bind the admission receipt (source key, canonical envelope digest, decision identity, outcome) to the generalist admission ledger (`admission_decisions`, `admission_attempt_bindings`), committing with the decision and any goal row. Keep the receipt ledger append-only, content-blind and unpruned in v1. On real migrated PostgreSQL, prove concurrent duplicates, lost-response resubmission, same-key changed-digest conflict, inbox-prune-then-duplicate, migration replay and rollback each yield one decision and at most one goal. Covers `REQ-ingestion-target-delivery-recovery-001`, `-002`, `-003`, and `REQ-butler-switchboard-003`.
- [ ] 2.2 Drive buffer-to-admission with bounded jittered retry that reads the decision by source key before retrying; exhaust to visible `admission_failed`. Inject interruption before and after commit and a control-plane restart with a full buffer; prove exactly-once admission and no classification. Covers `REQ-ingestion-target-delivery-recovery-003` and `-004`.
- [ ] 2.3 Wire the post-switch recovery matrix in Decision 5: return the existing receipt, admit once when retained evidence proves no decision, keep stored origin and exposure, and stay idempotent for `replay_pending`. Exercise it through the closed ISOLATED_CANDIDATE harness only. Covers `REQ-ingestion-target-delivery-recovery-006`.

## 3. Projections and owner controls

- [ ] 3.1 Project admission state (`buffered`, `admitted`, `recorded_without_goal`, `refused`, `admission_failed`, unavailable) with decision and goal identities; keep coarse `ingested` distinct from admission and goal completion. Covers `REQ-ingestion-target-delivery-recovery-005` and `REQ-ingestion-event-registry-002`.
- [ ] 3.2 Update `/ingestion` Timeline recovery controls to follow server eligibility, show legacy and admission evidence without a false all-clear, and give immediate, repeat-safe feedback in the existing dot-and-word visual language. Prove keyboard and screen-reader access and cached-row failure behavior. The D19 owner surface replaces this view at the switch. Covers `REQ-dashboard-ingestion-dispatch-console-001` and `-002`.

## 4. Switch drain and historical gate

- [ ] 4.1 Before the switch, stop accepting new routes, let in-flight legacy route-inbox work finish, and record content-blind residue for legacy work not proven accepted, with no automatic resend after `uncertain`, `rejected` or policy refusal. Covers `REQ-butler-switchboard-003` and `REQ-ingestion-target-delivery-recovery-004`.
- [ ] 4.2 Build a read-only content-blind historical dry run over retained event, inbox, buffer, legacy residue and admission evidence, with no cross-butler schema SELECT. Return grouped counts and an exact privately reviewable source-key set; exclude missing envelopes, admitted, legacy-accepted, ambiguous, rejected, policy-denied, pruned and unprovable rows. Prove an empty eligible set is valid and no source cursor or connector queue changes. Covers `REQ-ingestion-target-delivery-recovery-007` and `-008`.
- [ ] 4.3 Add owner-authenticated creation of an immutable exact-key approval record (set/policy digest, preview version, expiry, revocation) and an admission operation that accepts only its opaque ID, rechecks each key, and atomically records one consumed result plus one deduplicated admission per eligible key with stored origin and exposure. Test no record, revoked or expired record, changed evidence, duplicate consumption and partial exclusions without recording message content. The operation refuses every request until the owner creates an approval. Covers `REQ-ingestion-target-delivery-recovery-007`.

## 5. Cross-boundary verification and documentation

- [ ] 5.1 Extend behavior-executing tests at the real ingress-to-admission seam, including buffer fault injection and real PostgreSQL deduplication, and verify Messenger's service-plane delivery endpoint, domain-event subscribers, connector ingress replay and email replay-safe policy remain governed by their existing contracts. Cite each requirement ID at its nearest owning gate. Covers `REQ-ingestion-target-delivery-recovery-008`.
- [ ] 5.2 Update ingestion topology and operator documentation to describe the admission receipt, legacy drain, recovery matrix and historical gate; RFC corpus alignment stays with the post-switch rewrite. Reconcile with active changes and run strict OpenSpec validation, spec-overwrite guard, authoring trace check, applicable targeted tests, and terminal hosted CI on the implementation head.

## Documentation amendment verification

For `bu-mmax8j.10`, reconcile the proposal, design, these tasks and the four remaining deltas as one amendment; the `conversation-decomposition` and `module-pipeline` deltas are removed so generalist task 4.2 applies cleanly. Run `openspec validate --all --strict`, `make check-guards` and `git diff --check`. No code, test, baseline spec, RFC, archive or runtime activation. Tests: +0 ~0 -0.
