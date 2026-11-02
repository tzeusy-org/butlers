# Travel Butler

Turns booking confirmations and itinerary emails into a trip container: every leg,
accommodation, reservation, and document belongs to a trip. It detects itinerary changes,
preserves what changed, and alerts ahead of departures, check-ins, and document expiry.

- **Identity and scope:** [`roster/travel/MANIFESTO.md`](../../roster/travel/MANIFESTO.md)
- **Required behavior:** [`butler-travel` spec](../../openspec/specs/butler-travel/spec.md)
- **Schedules, modules, and port:** [`roster/travel/butler.toml`](../../roster/travel/butler.toml)

## Related Pages

- [Switchboard Butler](switchboard.md) -- routes travel-related email and messages here
- [Finance Butler](finance.md) -- owns expense tracking; Travel stores receipts as documents only

## Proposed linked-plan disruption review

The [Travel disruption review specification](../../openspec/changes/specify-travel-disruption-review/proposal.md) proposes a read-only review of reservations and exact Calendar occurrences the owner explicitly links to a trip. It is not implemented or activated. Its shared [event-response requirements](../../openspec/changes/specify-realtime-feature-contracts/specs/event-response-contract/spec.md) are adopted; the Travel-specific profile and retention settings remain proposed.

The initial profile retains the existing thirty-minute source poll and labels upstream freshness as unknown when the provider supplies no trustworthy observation time. A local fetch timestamp or arrival extrapolated from a departure delay cannot establish a confirmed unaffected plan. Routine polls require no model calls; meaningful revisions share finite daily invocation and token limits.

A current review distinguishes affected, unaffected, unknown and not assessed, and supports repeat-safe Review now and Stop monitoring. Later exact Calendar/message-draft choices require separate M7 and source-owner prerequisites; no reservation is rebooked, cancelled or paid, and no message is sent by this feature. See the [design and verification handoff](../../openspec/changes/specify-travel-disruption-review/design.md) for proposed profiles, source limitations and adoption dependencies.
