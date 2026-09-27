# Relationship Butler

A personal CRM: contacts, relationships, important dates, interactions, gifts, loans, and
reminders, so the owner never forgets what matters about the people in their life. Data is
entity-first: every contact links to a shared entity, and facts attach to the entity.

- **Identity and scope:** [`roster/relationship/MANIFESTO.md`](../../roster/relationship/MANIFESTO.md)
- **Required behavior:** [`butler-relationship` spec](../../openspec/specs/butler-relationship/spec.md)
- **Schedules, modules, and port:** [`roster/relationship/butler.toml`](../../roster/relationship/butler.toml)
- **Entity model:** [Identity Model](../concepts/identity-model.md)

![Relationship Butler Flows](./relationship-flows.svg)

## Stale-contact source authority

Elapsed time alone does not prove a contact has gone quiet. A contact can be surfaced as overdue
(in insight scans, the weekly maintenance message, the reconnect planner, the Contacts overdue
panel, or the attention rail) only when exactly one server-attested producer was expected to
record the next interaction, and the heartbeat for that exact producer endpoint is healthy. A
healthy sibling endpoint never substitutes; stale, missing, unhealthy, or mixed provenance makes
the contact `unmeasurable` and pauses every nudge. Caller-settable `extra_metadata.source` is not
producer authority. `unmeasurable` describes the instrument, not the owner's behavior, so an
empty overdue list is not an all-clear.

The channel-to-producer mapping lives in the `butler-relationship` spec and
[RFC 0029](../../about/legends-and-lore/rfcs/0029-expected-signals-and-honest-absence.md). For
triage, check the contact's expected-signal state and exact producer, then that same connector
endpoint's heartbeat.

## Related Pages

- [Switchboard Butler](switchboard.md) -- routes people-related messages here
- [General Butler](general.md) -- handles freeform data that is not contact-specific
