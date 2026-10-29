# Realtime feature specifications: run 23 follow-through

Date: 2026-10-28. Specification baseline: `8465542f4`. Tracking: `bu-okh0fw.1` through `.4`.

## Disposition

The owner adopted shared REQ-event-response-contract-001 through 005 and released specification work. Three concrete feature drafts now supply 21 traced requirements and 63 scenarios: appliance 7/16, comfort 7/19 and Travel 7/28. Twenty requirements are mandatory for their first feature release; Travel's exact recovery choices are explicitly post-v1. The shared spec text is unchanged. These domain drafts and numerical profiles are **proposed**, not adopted, implemented or live-tested.

| Outcome | Complete specification and profile | Current source versus required feature |
| --- | --- | --- |
| Appliance completion through owner handoff | [Spec](../../openspec/changes/specify-home-appliance-handoff/specs/home-appliance-handoff/spec.md), [design/profile](../../openspec/changes/specify-home-appliance-handoff/design.md) | HA transport, Home cache and deterministic safety/maintenance exist. Separate cycle and human-handoff lifecycle, enrollment capability proof and Done/Later/Stop controls are new. |
| Disrupted linked travel plans | [Spec](../../openspec/changes/specify-travel-disruption-review/specs/travel-disruption-review/spec.md), [design/profile](../../openspec/changes/specify-travel-disruption-review/design.md) | Itinerary versions and connection analysis exist. Explicit owner links, accepted trip revision producer and cross-plan review do not. Current flight source derives arrival from departure changes, lacks provider observation time and exact-occurrence matching, and polls only pre-departure legs. |
| Bounded observed comfort | [Spec](../../openspec/changes/specify-home-comfort-sessions/specs/home-comfort-session/spec.md), [design/profile](../../openspec/changes/specify-home-comfort-sessions/design.md) | HA risk gating and setpoint readback exist. Finite sessions, actuator claims, actual-room dwell attainment and session-aware dispatch fencing are new. |

Dedicated capability specs are justified by new durable domain lifecycles, not a new shared platform. Existing `generalist-lead-focused-delegation` Home/Travel identity and tool clauses retain their writer; no duplicate MODIFIED block or new evaluator, perception, budget, approval or delivery service is created.

## Proposed first-release behavior

Appliance starts with at most eight explicitly enrolled washer/dishwasher phase entities. Initial running snapshots cannot invent a cycle. Completed cycles create an owner handoff; only Done completes that handoff. Reminder and observation work has zero model calls/tokens, a 24-hour handoff expiry and finite Later/reminder allowances. A small separate action generation keeps Done available after Stop while old callbacks remain inert. Device source freshness must be demonstrated, not inferred from API connectivity.

Comfort defaults to observe-only, with an optional single exact setpoint through existing approval/risk controls. Five continuous minutes of fresh in-band observations establish attainment. Sessions default to two hours with a twelve-hour maximum; monitoring consumes zero model calls/tokens. Stop, override, uncertainty and expiry retain honest outcomes. Expiry does not restore the thermostat automatically; an unverified action blocks another automatic send and retains an actuator reconciliation hold.

Travel starts read-only on the existing thirty-minute polling population. Unknown provider freshness and departure-derived arrivals remain labelled provisional/unknown. Local provisional-card and optional enrichment targets are45s/150s from acceptance, not physical-disruption-to-owner guarantees. Optional reasoning is capped at12 invocations,144,000 input and18,000 output tokens per UTC day, including retries and superseded work. No children or model escalation. A deterministic review remains available without inference. Future exact Calendar/message-draft choices need separately adopted M7/source-write contracts and remain post-v1.

All numeric values are explicit proposed initial runtime seeds. No supported-device or measured-latency claim is made. The actual appliance and room sensor/actuator are commissioning inputs; a missing capable source refuses activation without changing the spec's required behavior.

## Shared requirement coverage

| Adopted invariant | Appliance owner | Comfort owner | Travel owner |
| --- | --- | --- | --- |
| 001: zero routine inference | Feature005 plus ingestion-to-delivery model-admission test | Feature005 plus sample/control/promotion seam test | Features002/005 plus accepted-writer/admission tests |
| 002: complete finite profile and freshness | Features001/005, appliance-v1 | Features001/003/005, comfort-v1 | Feature003, polling-review-v1 |
| 003: transitions and Stop survive coalescing | Features003/004, durable reducer and action CAS | Features003/004, dwell/crossing and dispatch fence | Features002/006, fixed coalescing and cancellation |
| 004: aggregate inference accounting | Feature005 fixed zero allowance | Feature005 fixed zero allowance | Feature005, canonical reservations across trips/generations/retries |
| 005: honest source/response/outcome evidence | Features002/005/006 | Features002/003/005/006 | Features003/004/006 |

Each design has a requirement-to-behavior-verification matrix, actual owning source/test paths, migration/rollback, privacy/retention, expiry, replay and race cases. Browser/card tests exercise the real owner callbacks; real-Postgres tests own CAS, outbox, reservation and migrated-role guarantees. A source-grep assertion or mocked success receipt is not a substitute. One executing seam suite may cover several related IDs; no test-per-ID quota is proposed.

## Exact integration owners and remaining gaps

Live tracker inspection at drafting time found `bu-dhloi1.19` shared-interface specification closed, `.9` perception representation blocked, and `.11` evaluator, `.13` delivery, `bu-cadcr1` broker, `bu-4c7nvp.1.5` M7 watches, `bu-4c7nvp.1.3` exact-decision producer and `bu-jv1moa.27` exact-decision UI open. Closed documentation is not runtime evidence. Their statuses must be refreshed before implementation allocation.

| Boundary | Implementation responsibility and required proof |
| --- | --- |
| Observation identity and coverage | Existing HA/domain source owners must prove phase/crossing preservation, stable identity/digest and timestamp meaning; Home reducers may own local domain facts but cannot self-register new S3 kinds. |
| Durable watches and reminders | `.11`/M7 `.1.5` retain evaluator ownership. Appliance/comfort adapters cannot expand the first-switch closed predicates or invent a scheduler. |
| Owner actions and delivery | `.13` remains the sole identity/token writer. Domain CAS/outbox bridges must preserve one subject and current action fences; no direct second send/callback family. |
| Physical execution | Existing RFC0028 actuation owns sends and receipts. Comfort adds a session-generation/freshness/expected-state condition at its actual execution fence; a preflight read alone is insufficient. |
| Travel evidence | Existing Travel source writer must provide exact occurrence matching, trip-wide revisions and honest arrival/time provenance. A derived arrival is not promoted to authoritative evidence by the review. |
| Aggregate reasoning budget | `bu-u5s4w8` owns canonical budget enforcement but currently promises per-goal scope. Cross-goal feature reservations remain a concrete required extension to reconcile with that owner before Travel inference activation; do not pretend the existing packet already implements it. |
| Later Travel effects | M7 `.1.3`/`.27` and Calendar's source-owner conditional write must be verified before the post-v1 choices mode. Current Calendar update lacks the required target revision precondition. |

These are cold-start implementation prerequisites captured in the domain packets, not new runnable runtime tasks. Existing runtime work should be extended after exact feature adoption instead of duplicating owners. No original requirement is silently removed because a prerequisite is incomplete.

## Review and verification receipts

Independent review found and the drafts corrected: appliance action-generation validity after Stop/remap; comfort's terminal-report exception, fresh dispatch checks after delayed approval, denied/failed-command observation behavior and terminal-state precedence; Travel trip-wide identity, superseded inference occupancy/reservation accounting and renewed-enrollment identity. Rechecks reported no blockers. Review covers specification semantics only.

Named `openspec validate <change> --strict` passes for all three changes. Full authoring diagnostics against an independently checked-out `8465542f4` baseline retain3,174 existing errors on both trees, add no errors, and add20 expected future-test-citation warnings for the new mandatory requirements. Existing spec and test raw hashes are unchanged. This is a full diagnostic comparison, not a full-tree authoring PASS or implementation verification. Raw receipts are retained outside the repository; the PR records the final static/hosted results. Planning Tests: +0 ~0 -0.

The same documentation PR carries the two Home drafts so their shared conventions cannot diverge between merges. Comfort drafting consumed the completed appliance draft; reconciliation consumed all three independently reviewed drafts. Original dependency edges remain, and task closure must follow child order after merge. New domain schemas and operation names remain explicit proposed interfaces, never claims of current implementation.

## Adoption handoff

The owner review is of these three exact domain specifications and their versioned initial profiles, including the narrow appliance manifesto sentence, comfort's single-action/no-automatic-restore policy, and Travel's read-only first release with finite optional inference. Shared contract adoption does not silently answer these new choices. After adoption, implementation allocation must resolve the named owners and preserve the source/authorization/effect boundaries above. Activation still requires actual supported sources and measured behavior. No runtime feature, migration, provider account or device has changed in this specification run.
