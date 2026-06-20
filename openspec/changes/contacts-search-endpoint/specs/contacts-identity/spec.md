## ADDED Requirements

### Requirement: [TARGET-STATE] Contact search endpoint for typeahead

The system SHALL provide a read-only `GET /api/contacts/search?q=` endpoint that
returns **person** entities from the identity layer for contact-link typeahead
(e.g. the calendar "People" field). It SHALL match the query string `q` against
`public.entities.canonical_name` and `aliases` (filtered to `entity_type =
'person'`) and against `public.contact_info.value` for **non-secured** rows
(`secured = false`), joining back to the person entity. Matching SHALL be
deterministic SQL (`ILIKE`) with no LLM or embedding service. Results SHALL
carry, per entity, the entity id, the display name, and the matched non-secured
identifier (when the match came from `contact_info`). Rows with `secured = true`
SHALL NOT be searched and SHALL NOT appear in results, consistent with the
"Secured contact info entries" requirement. This endpoint is distinct from
`GET /api/relationship/entities/search` and does not modify it.

#### Scenario: Name match returns the person entity

- **WHEN** `GET /api/contacts/search?q=ali` is called
- **AND** a `public.entities` row with `entity_type = 'person'` has `canonical_name = 'Alice Smith'`
- **THEN** the response MUST be HTTP 200 with a result for that entity
- **AND** each result MUST include the entity `id` and the display name

#### Scenario: Non-secured contact_info value matches

- **WHEN** `GET /api/contacts/search?q=alice@work.com` is called
- **AND** a `public.contact_info` row with `type = 'email'`, `value = 'alice@work.com'`, `secured = false` is linked to a person entity
- **THEN** the response MUST include that person entity
- **AND** the result MUST surface the matched identifier (`alice@work.com`) for chip rendering

#### Scenario: Secured contact_info excluded from matching and results

- **WHEN** `GET /api/contacts/search?q=` is called with a query that matches only a `public.contact_info` row whose `secured = true` (e.g. a `google_oauth_refresh` token value)
- **THEN** the secured row MUST NOT be searched
- **AND** the linked entity MUST NOT appear in the response on the strength of that secured value
- **AND** the secured `value` MUST NOT appear anywhere in the response

#### Scenario: No matching person returns an empty list

- **WHEN** `GET /api/contacts/search?q=zzzzz` is called and no person entity (by name, alias, or non-secured contact_info) matches
- **THEN** the response MUST be HTTP 200 with an empty result list
- **AND** the response MUST NOT be an error

#### Scenario: Blank query returns an empty list

- **WHEN** `GET /api/contacts/search?q=` is called with an empty or whitespace-only `q`
- **THEN** the response MUST be HTTP 200 with an empty result list

#### Scenario: Only person entities are returned

- **WHEN** `GET /api/contacts/search?q=acme` is called and `'Acme Corp'` exists as an `entity_type = 'organization'` entity
- **THEN** the organization MUST NOT appear in the results (person entities only)
