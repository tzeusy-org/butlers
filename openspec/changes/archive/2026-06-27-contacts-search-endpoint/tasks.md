> NOTE: the retired `public.contact_info` was DROPped (core_115) and its
> non-secret identifiers re-homed to `relationship.entity_facts`; secrets moved
> to `public.entity_info` (`secured = true`). A guardrail contract
> (`tests/contracts/test_contacts_schema_retired.py`) forbids live SQL from
> referencing the dropped tables, so the endpoint matches identifiers against
> `relationship.entity_facts` (active `has-*` literal triples) and never reads
> the secret store.

## 1. Search endpoint

- [x] 1.1 Add `GET /api/contacts/search?q=` (read-only) that returns person entities from the identity layer for contact-link typeahead
- [x] 1.2 Match `q` against `public.entities.canonical_name` and `aliases`, filtered to `entity_type = 'person'` (exclude organizations/places and merged/soft-deleted entities)
- [x] 1.3 Also match `q` against the entity's non-secret channel identifiers (active `has-*` literal triples in `relationship.entity_facts`), joining back to the person entity, and surface the matched identifier in the result for chip rendering
- [x] 1.4 Deduplicate by entity id; cap results with a `limit` (default 20, max 50); deterministic ordering (by display name then id)
- [x] 1.5 Matching is deterministic SQL (`ILIKE`) — no LLM, no embedding service

## 2. Secret-store exclusion

- [x] 2.1 Ensure the secret store `public.entity_info` (`secured = true`) is NOT read, so secret values are neither searched nor returned
- [x] 2.2 Confirm an entity matched only via a secret value does NOT appear in results on the strength of that secret value

## 3. Empty / no-match behavior

- [x] 3.1 Blank/whitespace `q` returns an empty result list (HTTP 200), not an error
- [x] 3.2 A `q` with no matching person entity returns an empty result list (HTTP 200)

## 4. Tests + validation

- [x] 4.1 Unit/integration tests: name/alias match returns the person entity; non-secret identifier match returns the person entity with the matched identifier
- [x] 4.2 Test: secret `public.entity_info` value is excluded (no match, not in results, never leaks)
- [x] 4.3 Test: blank `q` and no-match `q` each return an empty list with HTTP 200
- [x] 4.4 Test: organizations/places and merged entities are not returned (live person-only)
- [x] 4.5 Run `openspec validate contacts-search-endpoint --strict`
- [x] 4.6 Quality gate: `ruff check`/`format --check` + targeted contacts/identity test suite
