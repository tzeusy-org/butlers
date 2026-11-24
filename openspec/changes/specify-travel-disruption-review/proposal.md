## Why

A changed flight can invalidate an owner-linked hotel arrival, pickup or calendar occurrence while today's connection check only assesses adjacent flight legs. The owner needs one truthful review of those linked plans without every status poll launching reasoning or silently changing a reservation.

## What Changes

- Specify owner-confirmed trip dependencies and a revision-bound read-only impact review, with affected, unaffected and unknown results.
- Bind the feature to adopted REQ-event-response-contract-001 through 005, including a concrete proposed `polling-review-v1` profile and strict aggregate inference allowance.
- Define Stop, source expiry, idempotent coalescing and accessible review behavior; distinguish fetched evidence, derived arrivals and authoritative observations.
- Specify later exact calendar/message-draft choices as post-v1, behind existing M7 producers, owner approvals and source-owner conditional-write support. Initial release is `review_only`.

Status: proposed Travel feature specification for `bu-okh0fw.2`, baseline `8465542f4`. The shared event-response requirements are already owner-adopted; these domain semantics, retention and initial numeric settings are not. No runtime code, provider subscription, schema migration or activation is changed by merging these documents.

## Capabilities

### New Capabilities

- `travel-disruption-review`: Durable review of explicitly owner-linked non-flight plans against accepted itinerary revisions, including its finite runtime profile and later exact-choice contract.

### Modified Capabilities

None. `butler-travel` continues to own booking identity and adjacent-leg connection integrity. The active `generalist-lead-focused-delegation` change owns goal admission, grants and runtime conversion; this specification is a new domain lifecycle consuming those interfaces, not a competing writer for its requirements. The prior exploratory packet's suggested butler-travel delta is refined into a dedicated capability because dependency enrollment, impact revisions and review disposition are distinct from the existing role and connection contracts.

## Impact

Future owning surfaces: `roster/travel/tools/{bookings,connections,trips}.py`, new Travel-owned impact tools/reducer and migrations, `roster/travel/api/{router,models}.py`, and the canonical goal/owner-delivery consumer. Calendar and recipient details remain broker reads from their owners. Existing connection-risk acknowledgement remains acknowledgement only.

Non-goals: new flight transport/provider, inferred trip links, automatic rebooking, payment, commercial cancellation, autonomous message sending, Home actuation, health/legal advice, generic graph/evaluator/scheduler/approval infrastructure, or implementation on classify-and-route. A provider delay is evidence, not owner intent or mutation authority. The existing Travel manifesto covers read-only itinerary impact assessment; no claim that polling beats airline announcements is made.

## Funnel disposition

Large cross-domain contract. All five knowledge pillars exist. Doctrine alignment: vision rules 3, 4, 5, 6 and 7; Travel manifesto's coherent trip view. Topology: Travel owns links and projections; source owners retain records; control plane owns goals, budgets, approvals and delivery. `design.md` supplies state/wire contracts, source limitations, initial profile, verification and prerequisites. Review must inspect every unknown/expiry, stale-version and budget path. No unresolved owner-only input prevents drafting; actual trip enrollment, grants and activation measurements are future runtime evidence, not values to invent now. Exact domain-spec signoff remains pending.
