## Why

The calendar create/edit dialog is gaining a "People" autocomplete so that
butler-authored events can be linked to known contacts (`calendar_create_event`
already accepts `entity_ids[]` and writes `calendar_event_entities` — only the
form lacks a people field). That typeahead needs a server-side lookup over the
identity layer: as the owner types, return matching **person** entities to render
as chips. Today the only contact-shaped search surface is
`GET /api/relationship/entities/search`, which is owner-role-gated, scored over
the relationship RDF fact store (`relationship.entity_facts`), and returns
**all** entity types (person, organization, place). It is the wrong contract for
a cross-butler typeahead that wants *only people* and must never leak credential
material.

Identity is stored across `public.entities` (the canonical person/org graph) and
`relationship.entity_facts` (the non-secret channel-identifier triples —
`has-email`, `has-phone`, `has-website`, `has-handle` — keyed by entity id). The
retired `public.contacts` / `public.contact_info` tables were dropped (core_134 /
core_115) and re-pointed onto `relationship.entity_facts`; secret credentials
(passwords, tokens, OAuth refresh tokens) were carved out to `public.entity_info`
with `secured = true`, which `contacts-identity` requires to be masked in
dashboard responses. A guardrail contract
(`tests/contracts/test_contacts_schema_retired.py`) fails RED if live SQL
references the retired tables. A small read-only endpoint scoped to person
entities, matching on name/aliases and on the **non-secret** identifier triples,
gives the People field exactly what it needs without ever touching the secret
store.

## What Changes

- **NEW `GET /api/contacts/search?q=`** — a read-only typeahead endpoint that
  returns person entities from the identity layer for the calendar People field
  (and any future contact-link typeahead). It matches the query `q` against
  `public.entities.canonical_name`/`aliases` (filtered to `entity_type =
  'person'`, excluding merged/soft-deleted) and against the entity's non-secret
  channel identifiers — active `has-*` literal triples in
  `relationship.entity_facts` — joining back to the person entity. Each result
  carries the entity id, the display name, and the matched non-secret identifier
  (if any) for chip rendering.
- **Secret credentials are excluded from matching and from results.** The secret
  store `public.entity_info` (`secured = true`) is NEVER read by this endpoint,
  so secret values are neither searched nor returned — consistent with the
  existing `contacts-identity` masking requirement ("Secured contact info
  entries").
- **Empty / no-match behavior is well-defined.** A blank `q` and a `q` with no
  matches both return an empty result list (HTTP 200), never an error — the
  typeahead simply shows nothing.

This change is **endpoint-only**. The calendar People-field UI itself stays
under `module-calendar` and is front-end-only over the already-supported
`entity_ids[]` contract. **No migration** — the endpoint reads existing tables.
No LLM, no embedding service: matching is deterministic SQL (`ILIKE`).

## Capabilities

### New Capabilities

_None — this adds a read endpoint to an existing capability._

### Modified Capabilities

- `contacts-identity`: adds a read-only `GET /api/contacts/search?q=` endpoint
  that returns person entities from the identity layer (`public.entities` +
  non-secret `has-*` triples in `relationship.entity_facts`) for contact-link
  typeahead, with secret credentials (`public.entity_info`, `secured = true`)
  excluded from both matching and results.

## Impact

- **Contacts API:** a new read-only route (`GET /api/contacts/search`). No
  write paths, no mutation of identity data.
- **Spec (`openspec/specs/contacts-identity/spec.md`):** one ADDED requirement
  ("Contact search endpoint for typeahead").
- **No DB schema change / no migration.** Reads `public.entities` and
  `relationship.entity_facts` as they already exist.
- **No front-end change in this delta.** The calendar People-field UI lands
  under `module-calendar` (FE-only) and consumes this endpoint.

## Out of Scope

- The calendar create/edit People-field UI, chip rendering, relationship
  letter-marks, "add as new contact" affordance, and avatar pills on the event
  pill — front-end-only under `module-calendar`.
- Any change to `GET /api/relationship/entities/search` (the owner-gated,
  fact-scored, all-entity-type finder) — left untouched.
- Returning organizations or places from the new endpoint (person entities
  only).
- Revealing or returning secret `public.entity_info` values (those remain behind
  the existing dedicated reveal endpoint and are out of this endpoint's
  contract).
