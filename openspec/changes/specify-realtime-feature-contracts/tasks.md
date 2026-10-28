## Tracking and adoption

Execution tracking remains in Beads; this document is the verification handoff, not a second task queue. The owner adopted the exact shared contract and released specification work on 2026-10-28; `proposal.md` records the approval and immutable source revision. `bu-69cc1o` is satisfied. Domain feature specifications and operational profiles remain to be completed and adopted.

OpenSpec archive gates mirror the Beads deliverables below and remain incomplete until those deliverables and their ensuing implementation evidence are accepted:

- [x] 1. Adopt the exact shared contract and release specification work (owner approval 2026-10-28, recorded in proposal.md).
- [ ] 2. Complete and adopt the feature profiles in `bu-okh0fw.1`, `.2` and `.3`.
- [ ] 3. Reconcile enforcement ownership and verification coverage in `bu-okh0fw.4`.
- [ ] 4. Supply implementation and live response evidence for enrolled features before archiving this contract as delivered.

| Existing work item | Required specification deliverable |
| --- | --- |
| `bu-okh0fw.1` | Appliance profile and behavior scenarios incorporating REQ-event-response-contract-001 through 005 and the appliance conformance cases in design.md |
| `bu-okh0fw.2` | Travel profile and behavior scenarios incorporating REQ-event-response-contract-001 through 005, with provider freshness and aggregate review admission |
| `bu-okh0fw.3` | Comfort profile and behavior scenarios incorporating REQ-event-response-contract-001 through 005, with measured-room outcome, sensor expiry and deterministic stop |
| `bu-okh0fw.4` | Reconcile each complete profile, requirement-to-verification map, actual consumer dependencies and still-pending adoption/activation gates; refuse an uncovered alternate model route |

## Verification obligations for ensuing implementation

Before any enrolled feature activates, its adopted domain spec must supply the finite runtime profile and an explicit implementation owner for every shared enforcement seam. Behavioral evidence must demonstrate zero routine-event invocations, preserved transitions, stale/unknown handling, aggregate reservation limits, stop/expiry under exhaustion and honest response metrics. Tests cite the shared requirement IDs and exercise the real owning seam. Existing RFC 0038 activation dependencies cannot be discharged by this proposal.

Run targeted OpenSpec strict validation and authoring trace validation for this proposal. Independent review covers source compatibility, authority, lifecycle, concurrency and cost bounds. Prose validation does not prove any runtime guarantee.

## Concrete domain specification drafts

The shared requirements remain adopted and unchanged. The following domain profiles are proposed for exact-text review; their completion as documents does not complete the implementation/archive gates above.

- Appliance: [requirements](../specify-home-appliance-handoff/specs/home-appliance-handoff/spec.md) and [appliance-v1 profile](../specify-home-appliance-handoff/design.md).
- Travel: [requirements](../specify-travel-disruption-review/specs/travel-disruption-review/spec.md) and [polling-review-v1 profile](../specify-travel-disruption-review/design.md).
- Comfort: [requirements](../specify-home-comfort-sessions/specs/home-comfort-session/spec.md) and [comfort-v1 profile](../specify-home-comfort-sessions/design.md).
