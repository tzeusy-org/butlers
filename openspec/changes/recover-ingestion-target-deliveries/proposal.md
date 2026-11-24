## Why

An ingested event can be accepted and still never reach the work it asked for. The September 2026 fleet outage showed two gaps in the ordinary non-dashboard `route.execute` lane: no durable identity proved whether a target accepted the work, so a retry could duplicate acceptance, and the failed-event "replay" action could turn a failed row green without routing anything, or reset a terminal inbox row so that it was classified again.

RFC 0038 now governs the target. Switchboard becomes the control plane; message classification and route dispatch have no successor (D1). Ingress that needs reasoning becomes one goal through deterministic, deduplicated admission (D5), and deterministic ingestion stays on the service plane. Until the switch, classification and route dispatch are frozen to fixes. This amendment applies the `re-scope` row for this change in the [generalist collision dispositions](../generalist-lead-focused-delegation/collision-dispositions.md) before either change archives. It changes no RFC 0038 decision, profile or activation gate.

## What Changes

- Admit each accepted envelope that needs reasoning as exactly one goal, deduplicated by its durable source key. Each attempt's admission decision and its durable admission receipt commit together. Once the source key has a terminal decision (`admitted`, `joined` or `recorded_without_goal`), a duplicate envelope, connector redelivery, buffer restart or recovery request returns the same receipt and never admits a second goal. A retryable refusal, such as a transient quota refusal, binds only its attempt and can be retried under the same source key. A conversation-history batch is one goal; there is no per-target classifier, decomposition plan, fan-out or delivery outbox.
- Bind the receipt to the source key and attempt identity through the goal ledger's immutable attempt-to-decision bindings. An attempt identity cannot be rebound to another source key. The content-blind receipt ledger is append-only, so retention of message bodies cannot reopen admission.
- Keep deterministic ingestion (gating rules, channel-bound envelopes, bulk backfill and sync) on the service plane. It creates no goal and no admission receipt.
- Keep legacy target receipts readable. Before the switch, Switchboard stops accepting new routes and lets in-flight legacy work drain. Residual unresolved legacy work stays visible and enters a goal only through the owner-gated historical path.
- Replace every `public.ingestion_events` replay branch that relabels failure or resets `message_inbox` for reclassification. Before the switch, recovery returns an explicit refusal when no legacy work is outstanding. After the switch, recovery resubmits the original envelope identity to admission, which returns the existing receipt or admits once.
- Project admission state, not target-delivery state, in the ingestion API and Timeline. Coarse `ingested` status is not admission evidence, and an admission receipt is not goal completion.
- Produce a content-blind historical eligibility dry run. Require owner approval of the exact historical set and late-admission policy before admitting any old event.
- **BREAKING:** a failed ingestion event can no longer be relabeled `ingested` merely because its replay endpoint was invoked, and `ingested`/`replay_failed` events no longer reset a terminal inbox to trigger fresh classification; legacy clients receive an explicit non-success result when no recovery can run.

## Capabilities

### New Capabilities

- `ingestion-target-delivery-recovery`: one deduplicated goal admission per reasoning envelope with a durable admission receipt, the legacy target-receipt drain, truthful recovery, owner-gated historical late admission, and content-blind projections. The capability keeps its original name so existing beads and requirement IDs stay stable; its requirements are stated in goal-runtime terms.

### Modified Capabilities

- `butler-switchboard`: bind reasoning ingress to one receipted goal admission and drain legacy target routes before the switch, while deterministic ingestion stays on the service plane.
- `ingestion-event-registry`: project admission and legacy evidence, and define the public-event recovery status matrix, without changing connector-side replay semantics.
- `dashboard-ingestion-dispatch-console`: remove the claim that a failed event can replay directly to `ingested`, and present recovery and admission evidence honestly.

The amendment removes this change's deltas on `conversation-decomposition` (`Decomposition Output Storage`) and `module-pipeline` (`Decomposition-to-Routing Fan-Out`, and the added classification-before-dispatch requirement). Generalist removes those blocks and the decomposition capability at task 4.2, and a durable classified plan is new classification work that the freeze forbids.

## Impact

Before the switch, this affects only the failed-event recovery branches in `src/butlers/core/ingestion_events.py`, read-only legacy projections, and the drain step. It builds no classifier, plan, intent table, dispatcher or retry worker on the frozen path. At the switch, it binds Switchboard ingress and the durable buffer to the generalist admission ledger (`admission_decisions` and `admission_attempt_bindings`), `public.ingestion_events` recovery APIs, and `/ingestion` projections until the D19 owner surface replaces them. It preserves Messenger's service-plane delivery endpoint, domain-event fan-out, connector-side ingress replay, email replay-safety policy, and the canonical transport outcomes `confirmed | rejected | uncertain | not_attempted` for draining legacy work. Historical recovery cannot rewind provider cursors or broadly reprocess connector input.

## Re-scope and delivery boundary

All eight `ingestion-target-delivery-recovery` requirement IDs, `REQ-butler-switchboard-003`, `REQ-ingestion-event-registry-002` and both dashboard requirement IDs remain; their headings and bodies now state goal-runtime behavior. `REQ-conversation-decomposition-002`, `REQ-module-pipeline-001` and `REQ-module-pipeline-002` leave this change. Goal execution, effects, effect idempotency (RFC 0038 D11, D12) and the D19 owner surface stay with their own owners. No code, tests, baseline spec, RFC, archive or runtime activation belongs to this documentation amendment. Tests: +0 ~0 -0.
