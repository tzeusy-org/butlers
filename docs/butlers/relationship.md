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

## Implementation Notes

- `relationship.facts` is a multi-valued log store: `activity` and `interaction_*` carry many
  active rows per `(entity_id, predicate)`. Contradiction detection in
  `run_fact_retraction_curation` (`roster/relationship/jobs/relationship_jobs.py`) is therefore
  gated to the `_CONTRADICTION_FUNCTIONAL_PREDICATES` allowlist, so a new log predicate can never
  flood approvals. Cardinality cannot be read from `entity_predicate_registry`, which covers only
  the `entity_facts` store.
- `run_interaction_sync_job` reads `switchboard.message_inbox` directly, so `scripts/init-db.sql`
  grants `butler_relationship_rw` read-only access to schema `switchboard` (plus matching default
  privileges).
- `POST /api/relationship/contacts/sync` dispatches to the `contacts_sync_now` MCP tool with
  `{"provider": "google", "mode": "incremental|full"}`. `mode` is strict, and credential failures
  surface as `400` errors pointing at `/api/oauth/google/start`.
- `relationship.facts` and `relationship.predicate_registry` belong to the memory module; domain
  triple-store work uses `relationship.entity_facts` and `relationship.entity_predicate_registry`.
  Interaction facts use `subject='entity:{entity_id}'` (`interaction_log` / `interaction_list`
  still resolve legacy contact UUIDs).
- Active-surface queries share one filter excluding `metadata.archived = true`, `archived_at`,
  `tombstone = true`, `deleted_at` and `merged_into`.
- Dunbar decay counts connector LLM-extraction facts as mentions unless
  `extra_metadata.source == "interaction_sync"`; `email`, `interview` and `calendar_event`
  interactions weigh `0.2`.

## Related Pages

- [Switchboard Butler](switchboard.md) -- routes people-related messages here
- [General Butler](general.md) -- handles freeform data that is not contact-specific
