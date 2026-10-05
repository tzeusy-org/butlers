# RFC 0032: Fleet Case File

**Status:** Decision record; seven source slices landed, runtime conformance partial.
**Date:** 2026-09-05
**Canonical contract:** [Fleet case file](../../../openspec/specs/fleet-case-file/spec.md)

## Context

The Switchboard insight broker computes correlated multi-butler candidate clusters and synthesizes a summary, then discards both every delivery cycle. That lifecycle cannot carry a situation spanning days. Candidate-scoped urgent bypasses also let several notices of one situation independently break quiet hours.

## Decision

Represent a recognized situation as a durable shared case, so later observations accrete onto its identity instead of reconstructing it from cycle-scoped candidates. Keep case existence, lifecycle, posture and ledger bindings under Switchboard write authority, with contributors recording their own attributed evidence through their sanctioned paths. The canonical specification contains the complete lifecycle, correlation, evidence, read, attention, lapse, backfill and link requirements.

The storage model permits general ledger references, including another-case references. The shipped S7 tool admits the three explicit ledger kinds whose references its existing contribution, attention and backfill call sites can observe. This distinction preserves the original general model without inventing a fourth tool producer.

Switchboard arbitration is a sole-writer, last-write-wins decision. It does not introduce quorum, decay or per-butler voting. Situation-scoped attention records a deduplicated quiet-window bypass; it is not proof of notification delivery or automatic broker wiring.

## Rationale And Alternatives

- Extending expiring `public.insight_candidates` with a cluster or parent pointer would retain the wrong lifecycle. A situation needs durable state, posture and outcome beyond one delivery cycle.
- GRANT/REVOKE alone cannot enforce Switchboard case/link authority because `scripts/init-db.sql` re-widens public privileges. Effective-role row-level security preserves that boundary across bootstrap.
- Historical backfill uses resolved owner-condition episodes. Discarded broker clusters cannot provide honest historical evidence, and a backfill is not authority to create an active situation.

## Source Status And Remaining Obligations

The source contains the seven slices: schema, read API, contribution tools, case attention, lapse sweep, historical backfill and three-ledger binding. There are seven group-gated tools, typed evidence includes `case`, and current backfill repairs missing links on pre-S7 cases. The former S3 “this change” and S6 “no links” descriptions are historical stages.

Source presence does not establish full conformance. Immutable evidence remains mandatory under REQ-fleet-case-file-005, but current broad UPDATE access is unfenced and no test citation establishes mutation refusal. REQ-fleet-case-file-007 requires honest source availability; the current list reader catches genuine failures as clean empty pages, an observed defect rather than required behavior.

Other requirements have partial proof: actual registered transport, post-migration bootstrap replay, exhaustive role/privacy controls, forced open/freshness races, native sweep dispatch, genuine ledger references and old-link SQL recovery remain incomplete. See the [source translation and proof limits](../../../openspec/changes/archive/2026-10-05-document-rfc0032-fleet-case-file/design.md) and [concept guide](../../../docs/concepts/fleet-case-file.md). Existing `bu-yfd9v1` owns the unadopted evidence-grant posture; `bu-h40h2b.8` retains its paired safe-read/availability and larger feature obligations. Source archive closes neither owner’s runtime work.

## Non-goals

This decision adds no automatic insight-broker producer or provider send, dashboard UI/write surface, joint-objective register, or `delegate_act` mandate; commitment and mandate design remains with RFC 0026. Historical backfill stays closed-only. Native Fleet backup/offline restore adoption, private-schema authority, new grant policy and activation are outside this source translation. Existing backup and capture holds remain unchanged.
