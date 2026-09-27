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
