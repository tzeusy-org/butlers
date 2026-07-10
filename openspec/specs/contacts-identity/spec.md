## Purpose

Defines the residual contacts-identity capability after the table-based identity
model was retired. The `public.contacts` and `public.contact_info` tables this
spec originally described have been dropped (`public.contact_info` by
`core_115_drop_contact_info`, `public.contacts` by
`core_134_drop_public_contacts`), and the drop is guarded by
`tests/contracts/test_contacts_schema_retired.py`. The live identity model is
the entity graph: `public.entities` is the identity anchor and roles source,
non-secret channel identifiers are `relationship.entity_facts` triples (Telegram
handles stored prefixed `telegram:<id>` under `has-handle`), and
`public.entity_info` is a secrets-only store (RFC 0004 Amendment 3).
`src/butlers/identity.py` performs entity-graph resolution and always returns
`contact_id = None`.

The `entity-identity` and `relationship-facts` specs are the authoritative
contracts for the live identity model. The table-centric requirements this spec
formerly carried were archived by the `retire-contacts-table-specs` change (per
owner decision on bead bu-qtsy4: archive, do not rewrite); their `Migration`
notes in that change point at the authoritative replacements. Two requirements
survive here because they are not table-centric: the owner-identity secret-key
rename contract ("Secret key renames") and the live entity-graph contact-search
endpoint ("[TARGET-STATE] Contact search endpoint for typeahead", `GET
/api/contacts/search`, implemented in `src/butlers/api/routers/contacts.py`). The
module tool-naming contract that formerly lived here ("I/O model removal") was
relocated to the `core-modules` spec ("Module Tool Naming Convention"), which is
its proper owner.
## Requirements
### Requirement: Secret key renames

Owner-identity secret keys SHALL be renamed for consistency. The following renames MUST be applied:

| Old key | New key |
|---|---|
| `BUTLER_TELEGRAM_CHAT_ID` | `TELEGRAM_CHAT_ID` |

Secret keys that are not identity-bound (API keys, webhook URLs, service tokens) SHALL remain in `butler_secrets` and are not affected by this change.

#### Scenario: Legacy secret key resolves via fallback

- **WHEN** code references `BUTLER_TELEGRAM_CHAT_ID` and the key has been migrated to `TELEGRAM_CHAT_ID`
- **THEN** the credential resolver MUST check the new key first, then fall back to the legacy key during the transition period

### Requirement: [TARGET-STATE] Contact search endpoint for typeahead

The system SHALL provide a read-only `GET /api/contacts/search?q=` endpoint that
returns **person** entities from the identity layer for contact-link typeahead
(e.g. the calendar "People" field). It SHALL match the query string `q` against
`public.entities.canonical_name` and `aliases` (filtered to `entity_type =
'person'`, excluding merged and soft-deleted entities) and against the entity's
**non-secret channel identifiers** — active `has-*` literal triples
(`has-email`, `has-phone`, `has-website`, `has-handle`) in
`relationship.entity_facts`, joined back to the person entity via
`entity_facts.subject = entities.id`. Matching SHALL be deterministic SQL
(`ILIKE`) with no LLM or embedding service. Results SHALL carry, per entity, the
entity id, the display name, and the matched non-secret identifier (its kind and
value) when the match came from an identifier.

Secret credentials live in `public.entity_info` with `secured = true` (the
retired `public.contact_info` table was dropped in core_115 and its non-secret
identifiers re-homed to `relationship.entity_facts`; see
`tests/contracts/test_contacts_schema_retired.py`). This endpoint SHALL NOT read
`public.entity_info`, so secret values are never searched and never appear in
results. The endpoint SHALL NOT perform any write and SHALL NOT require a
migration. It is distinct from `GET /api/relationship/entities/search` and does
not modify it.

#### Scenario: Name match returns the person entity

- **WHEN** `GET /api/contacts/search?q=ali` is called
- **AND** a `public.entities` row with `entity_type = 'person'` has `canonical_name = 'Alice Anderson'`
- **THEN** the response MUST be HTTP 200 with a result for that entity
- **AND** the result MUST include the entity `id` and the display name
- **AND** the result's `matched_identifier` MUST be null because it matched by name, not by an identifier

#### Scenario: Non-secret identifier value matches

- **WHEN** `GET /api/contacts/search?q=alice@work.com` is called
- **AND** a person entity has an active `has-email` literal triple in `relationship.entity_facts` with object `alice@work.com`
- **THEN** the response MUST include that person entity
- **AND** the result MUST surface the matched identifier (`{ "type": "email", "value": "alice@work.com" }`) for chip rendering

#### Scenario: Secret credential excluded from matching and results

- **WHEN** `GET /api/contacts/search?q=topsecret` is called with a query that matches only a `public.entity_info` row whose `secured = true` (e.g. a `google_oauth_refresh` token value)
- **THEN** the secret row MUST NOT be searched
- **AND** the linked entity MUST NOT appear in the response on the strength of that secret value
- **AND** the secret `value` MUST NOT appear anywhere in the response

#### Scenario: No matching person returns an empty list

- **WHEN** `GET /api/contacts/search?q=zzzzz` is called and no person entity (by name, alias, or non-secret identifier) matches
- **THEN** the response MUST be HTTP 200 with an empty result list
- **AND** the response MUST NOT be an error

#### Scenario: Blank query returns an empty list

- **WHEN** `GET /api/contacts/search?q=` is called with an empty or whitespace-only `q`
- **THEN** the response MUST be HTTP 200 with an empty result list

#### Scenario: Only person entities are returned

- **WHEN** `GET /api/contacts/search?q=alice` is called
- **AND** an organization entity `'Alice Industries'` and a merged person entity `'Alice Ghost'` both match the query by name
- **THEN** neither the organization nor the merged entity MUST appear in the results (live `entity_type='person'` entities only)

