## Why

An ingested event can be classified correctly and still fail delivery to a domain butler. Today the ordinary non-dashboard `route.execute` path has no durable per-target delivery obligation or stable target-acceptance identity; a retry can duplicate acceptance, while the failed-event “replay” action can turn a failed row green without routing anything. The September 2026 fleet outage exposed both gaps across multiple channels.

## What Changes

- Persist the completed classification or conversation decomposition and one delivery intent per target segment before the first target call. Resume delivery from those intents without classifying the event again.
- Give each target delivery a stable identity and an atomic acceptance receipt. Matching duplicate calls and receipt lookups verify the original target and immutable payload digest; changed same-key work is refused instead of receiving a false receipt.
- Recover proven pre-accept failures with bounded retry. Preserve `rejected` as terminal and `uncertain` as ambiguous until the same target acceptance identity supplies affirmative evidence. Preserve accepted targets during partial fan-out.
- Replace every `public.ingestion_events` replay branch that relabels failure or resets `message_inbox` for reclassification with same-plan, same-intent recovery or an explicit refusal. Expose waiting, accepted, ambiguous, and terminal target states without treating coarse source `ingested` status as universal target acceptance or target acceptance as downstream session completion.
- Produce a content-blind historical eligibility dry run. Require owner approval of the exact historical delivery set and late-delivery policy before queuing any old event.
- **BREAKING:** a failed ingestion event can no longer be relabeled `ingested` merely because its replay endpoint was invoked, and `ingested`/`replay_failed` events no longer reset a terminal inbox to trigger fresh classification; legacy clients receive an explicit non-success result when no durable recovery work exists.

## Capabilities

### New Capabilities

- `ingestion-target-delivery-recovery`: durable Switchboard delivery intents, target acceptance identity, bounded recovery, truthful replay, historical recovery, and safe operator projections for non-dashboard ingestion-to-domain `route.execute`.

### Modified Capabilities

- `conversation-decomposition`: persist a classified fan-out plan before dispatch and retain per-segment outcomes without rewriting the plan.
- `module-pipeline`: make non-dashboard classification produce durable delivery decisions before invoking a target route, and make decomposition fan-out retain segment-aware outcomes behind its existing per-butler compatibility projection.
- `butler-switchboard`: bind ordinary ingestion-to-domain routing to the durable intent and stable target acceptance contract while preserving other routing lanes.
- `ingestion-event-registry`: define the complete public-event recovery status matrix and separate coarse source status from target delivery evidence without changing connector-side replay semantics.
- `dashboard-ingestion-dispatch-console`: remove the claim that a failed event can replay directly to `ingested` and present target-level delivery recovery honestly.

## Impact

This affects Switchboard classification and decomposition in `src/butlers/modules/pipeline.py`, route dispatch in `src/butlers/core_tools/_routing.py`, the target `route_inbox` acceptance path, PostgreSQL migrations and permissions, `public.ingestion_events` recovery APIs, and `/ingestion` timeline projections and controls. It preserves the existing dashboard synchronous route contract, Messenger delivery, domain-event fan-out, connector-side ingress replay and email replay-safety policy, and the canonical route transport outcomes `confirmed | rejected | uncertain | not_attempted`. Historical recovery cannot rewind provider cursors or broadly reprocess connector input.
