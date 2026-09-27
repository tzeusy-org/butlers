# Home Butler

Orchestrates the smart home through Home Assistant: comfort preferences per room, scenes composed
in conversation, energy awareness, and device health. It is the only butler allowed to actuate
Home Assistant, and it asks before anything destructive.

- **Identity and scope:** [`roster/home/MANIFESTO.md`](../../roster/home/MANIFESTO.md)
- **Required behavior:** [`butler-home` spec](../../openspec/specs/butler-home/spec.md) and
  [`module-home-assistant` spec](../../openspec/specs/module-home-assistant/spec.md)
- **Schedules, modules, and port:** [`roster/home/butler.toml`](../../roster/home/butler.toml)

## Physical actuation

`ha_call_service` runs under the physical-risk boundary of
[RFC 0028](../../about/legends-and-lore/rfcs/0028-home-physical-actuation-contract.md):
consequential calls park for owner approval, and every attempt writes a receipt to
`home.ha_command_log`, the authoritative actuation ledger. A receipt is `succeeded` only after a
live read-back confirms the requested state; any uncertainty after sending is `unverified` and
needs owner attention. An unverified or crashed approved attempt is never blindly retried under
the same approval; the owner reconciles the device and issues a new approved action.

## Home Assistant source health

`home.ha_entity_snapshot` is a last-known-state cache, not proof that Home Assistant is reachable.
Readers treat the snapshot as unmeasurable whenever the `home.ha_source_health` lease is missing,
in error, or expired: list endpoints keep last-known rows flagged `ha_source_available=false`,
reads that cannot distinguish absence from an outage return 503, presence signals are left
untouched, and the Home briefing reports the outage instead of an all-clear. Operator triage:
[Troubleshooting](../operations/troubleshooting.md#symptom-home-dashboard-says-home-assistant-is-unavailable).

## Private person mappings

The dashboard's **Devices → Person mappings** form, behind dashboard owner-auth, is the only
supported way to associate an exact `person.<slug>` Home Assistant ID with an existing person
entity. Submission is an all-or-nothing batch: exact existing pairs are no-ops and any conflict
refuses the whole batch. It never queries Home Assistant, infers identity from names, creates an
entity, or exposes a mapping read surface, and it returns only an opaque receipt and counts.
Remap, delete, and rollback are separate owner-authorized operations. Route:
`src/butlers/api/routers/home_person_mappings.py`.

## Related Pages

- [Health Butler](health.md) -- reads Home Assistant sensors (read-only) for health correlation
- [Switchboard Butler](switchboard.md) -- routes home automation messages here
