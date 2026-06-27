## ADDED Requirements

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
