## Status and scope

Owner-adopted shared specification as of 2026-10-28; see the exact approval and source revision in `proposal.md`. Gate `bu-69cc1o` is released for specification work under epic `bu-okh0fw`. Each domain specification must incorporate this binding contract before its own adoption. `v1-mandatory` means mandatory for the first release of an enrolled feature, not a claim that today's sources already conform. Concrete profiles, implementation and activation remain separate deliverables.

## Existing behavior and gaps

Static inspection at base `ddda7a87d`: HA has WebSocket ingestion with a 0.5-second reorder window and 60-second REST fallback; Home's shared presence schedule is ten minutes. Gmail and Calendar default to 60-second polling; Travel flight status runs every thirty minutes. These are configured code defaults, not measured source-to-owner bounds. The HA skip rule and deterministic Home cache avoid some inference, but wellness promotion defaults on and can still invoke Health. Connector discretion is not proof that every downstream path is free.

References: `src/butlers/connectors/home_assistant.py`, `home_assistant_pipeline.py`, `gmail.py`, `google_calendar.py`; `roster/home/butler.toml`; `roster/travel/butler.toml`; `roster/switchboard/migrations/010_switchboard_home_assistant_skip.py` and `007_wellness_route_rule.py`; `roster/home/modules/__init__.py` and `actuation.py`.

## Ownership and composition

This new capability constrains cross-feature behavior before and after goal admission. Existing `goal-orchestration` owns goals and per-goal limits; connector contracts own delivery; domain reducers own meaning; RFC 0038 owns perception, conditions, authority and owner delivery. A shared response profile is not a new executable predicate language. Features requiring advanced watches remain behind RFC 0038 M7; this proposal grants no first-switch expansion. Do not implement a parallel event bus, perception table, budget service or notification service.

Use domain-owned source observations to evaluate domain thresholds. A generic numeric delta filter is unsuitable if it loses an appliance edge or cumulative comfort-band crossing. Coalesce only replaceable levels; preserve bounded transition identities or expose a gap. Do not retain new raw streams just to produce telemetry; use existing provenance, labelled timestamps, counters and bounded receipts under existing visibility and retention policies.

## Required profile contents

Each feature packet must name its stable accounting identity and initial runtime configuration, with finite values for:

| Field group | Required declaration |
| --- | --- |
| Source | Exact source/entity population, timestamp trust, maximum age, sampling/polling and fallback limits; stale/unknown behavior |
| Transition | Deterministic named trigger, ordering and duplicate identity, replaceable samples, debounce/dwell, gap/overflow disposition |
| Response | Start and end of each deadline, supported operating conditions, finite missed-deadline detection bound and disposition, healthy and degraded modes, stop deadline, physical-outcome deadline where applicable |
| Cost | Fixed accounting-window rule, invocation/input/output caps, concurrency reservation and settlement, model context/output ceilings, all promotion routes |
| Evidence | Measurement stages, denominators, failed/held/missing outcomes, supported deployment and source population |

An explicit zero reasoning allowance is valid for deterministic-only profiles. Infinite, missing or unenforceable values are invalid. Use fixed UTC accounting windows, capturing window identity at admission as in `REQ-goal-orchestration-005`; reservations and late settlements stay with that window. Token budgets count gross input including cached input once, and all provider-reported output including reasoning tokens where applicable; money accounting remains a separate existing concern. Budget enforcement must reserve the runtime's maximum total input over an invocation's tool turns, not just its initial prompt. If that maximum cannot be enforced, the profile cannot admit that invocation. All existing goal/fleet limits continue to apply. Explicit budget increases follow existing owner-authorized operational controls; version changes alone never reset usage.

Numerical settings belong in the subsequent feature profile, supported by source capability and measurements. A candidate five-second local processing target cannot be advertised as five-second physical-event-to-owner delivery when the source polls every thirty minutes. This proposal deliberately makes complete finite profiles a release requirement instead of inventing unmeasured universal timings.

## Application to the three released feature packets

| Packet | Required feature-specific contract | Acceptance evidence |
| --- | --- | --- |
| `bu-okh0fw.1` Appliance completion through owner handoff | Explicit HA cycle identity/states; distinct appliance completion and owner chore completion; routine samples, normal completion and fixed reminders use zero reasoning. Unknown cycle history cannot invent a completed cycle. | A burst of 10,000 redundant observations plus one valid cycle creates one handoff and zero model invocations. Replay/reordering creates no second handoff. A start/completion inside one coalescing window survives. Source silence shows stale status. |
| `bu-okh0fw.2` Disrupted linked travel plans | One bounded review admission per meaningful accepted disruption revision; only owner-linked plans and authorized reads. Declare actual provider freshness and review deadline. Coalesced revisions retain the latest valid evidence; stale options cannot authorize mutations. | Duplicate revisions do not restart review. Concurrent revisions obey the same finite window budget. Slow polling remains visible in freshness reporting. Review results refer to the accepted itinerary revision; a changed revision invalidates an effect option. |
| `bu-okh0fw.3` Bounded comfort sessions | Deterministic sampling and already permitted corrections; separate command acceptance from observed room outcome; explicit sensor age, dwell, hysteresis, expiry, manual override and stop bounds. No device authority comes from this contract. | Many tiny changes crossing the comfort band remain detectable with zero per-sample inference. Sensor silence cannot yield comfort success. Concurrent work cannot exceed budget. Stop/manual override and expiry work during inference exhaustion and overload. |

All five requirements apply to each packet; these examples supplement its existing safety, identity, source and approval requirements. The ten-thousand-event fixture is a conformance workload, not a production throughput claim. Test fixture budget values may be small to expose boundaries; they do not become operational defaults.

## Verification and release evidence

Specification review must cover failures, ordering, concurrency, authorization and budget attribution across alternate consumers. Behavioral tests should exercise actual ingestion-to-consumer and admission seams, cite the requirement IDs, and use deterministic clocks/fake providers where appropriate. A source scan asserting that no model call appears in one file is insufficient. Reuse one seam test for multiple related requirements rather than multiplying tests by requirement count.

Each feature needs replay/burst and gap tests, aggregate admission/settlement tests where inference is permitted, and evidence for owner stop and expiry under overload. At implementation closeout, verify the selected deployment's timestamps, source cadence, p95 response and failure denominator; preserve raw receipt provenance without copying personal sensor data into PRs. Until then, all numerical performance statements remain targets or observations with explicitly limited scope.

No source, schema, manifesto, UI component, provider subscription or retained-data change is made here. Future domain specification work owns any such changes and their migration/rollback checks. The shared-spec adoption and specification-release gate are satisfied; feature-specific adoption and deployment gates remain intact.

Proposal-stage validation is allocated to this complete changeset using affected authoring plus full-diagnostic nonregression against base `ddda7a87d`. The repository-wide trace checker has pre-existing authoring debt; its full-tree result must remain reported separately. This scoped receipt is not full authoring signoff, implementation evidence or merge permission.
