## 1. Search endpoint

- [ ] 1.1 Add `GET /api/contacts/search?q=` (read-only) that returns person entities from the identity layer for contact-link typeahead
- [ ] 1.2 Match `q` against `public.entities.canonical_name` and `aliases`, filtered to `entity_type = 'person'` (exclude organizations/places and merged entities)
- [ ] 1.3 Also match `q` against `public.contact_info.value` for **non-secured** rows (`secured = false`), joining back to the person entity, and surface the matched identifier in the result for chip rendering
- [ ] 1.4 Deduplicate by entity id; cap results with a `limit` (sane default, bounded max); deterministic ordering for ties
- [ ] 1.5 Matching is deterministic SQL (`ILIKE`) — no LLM, no embedding service

## 2. Secured-info exclusion

- [ ] 2.1 Ensure rows with `secured = true` in `public.contact_info` are NOT searched and NOT included in the response
- [ ] 2.2 Confirm an entity matched only via a secured identifier does NOT appear in results on the strength of that secured value

## 3. Empty / no-match behavior

- [ ] 3.1 Blank/whitespace `q` returns an empty result list (HTTP 200), not an error
- [ ] 3.2 A `q` with no matching person entity returns an empty result list (HTTP 200)

## 4. Tests + validation

- [ ] 4.1 Unit/integration tests: name/alias match returns the person entity; non-secured `contact_info` match returns the person entity with the matched identifier
- [ ] 4.2 Test: secured `contact_info` value is excluded (no match, not in results)
- [ ] 4.3 Test: blank `q` and no-match `q` each return an empty list with HTTP 200
- [ ] 4.4 Test: organizations/places are not returned (person-only)
- [ ] 4.5 Run `openspec validate contacts-search-endpoint --strict`
- [ ] 4.6 Quality gate: `ruff check`/`format --check` + targeted contacts/identity test suite
