## ADDED Requirements

### Requirement: Entity-level tab write endpoints

The dashboard API SHALL expose entity-keyed `POST` endpoints for notes,
interactions, and gifts, writing to the same `facts` rows the matching tab GET
endpoints read. Each write MUST set `scope = 'relationship'` and the target
`entity_id`, and MUST NOT create a parallel store, a shadow table, or a
contact-keyed row the entity tab GETs cannot see.

The endpoints are:

| Endpoint | Predicate written | Success |
|---|---|---|
| `POST /api/relationship/entities/{id}/notes` | `contact_note` | 201 with the note shape from the notes tab mapping |
| `POST /api/relationship/entities/{id}/interactions` | `interaction_<type>` | 201 with the interaction shape from the interactions tab mapping |
| `POST /api/relationship/entities/{id}/gifts` | `gift` | 201 with the gift shape from the gifts tab mapping |

All three MUST enforce the Clause 12a owner gate, MUST return 404 when the
entity UUID does not exist in `public.entities`, MUST return 422 when the
required text field is absent or blank after trimming, and MUST return 409 with
`existing_id` when an identical record already exists for that entity inside the
writer's duplicate window. A rejected write MUST NOT create a fact.

Because the readers already tolerate contact-keyed subjects, `note_list` and
`gift_list` MUST match both the `contact:` and `entity:` subject forms so a
record written through the dashboard remains visible to the MCP tools.

#### Scenario: Note write is readable from the notes tab

- **WHEN** an owner calls `POST /api/relationship/entities/{id}/notes` with a non-blank `content`
- **THEN** the response status MUST be 201 with the note shape
- **AND** a `contact_note` fact MUST exist with that entity's `entity_id` and `scope = 'relationship'`
- **AND** `GET /api/relationship/entities/{id}/notes` MUST return it

#### Scenario: Interaction write records the requested type

- **WHEN** an owner calls `POST /api/relationship/entities/{id}/interactions` with `type = "call"`
- **THEN** the stored predicate MUST be `interaction_call`
- **AND** the response `type` MUST be `"call"`

#### Scenario: Non-owner write is refused before any fact is created

- **WHEN** a caller who does not resolve to an owner-role entity calls any of the three endpoints
- **THEN** the response status MUST be 403 with `{ code: 'owner_required' }`
- **AND** no fact MUST be written

#### Scenario: Blank input is rejected, duplicates are reported

- **WHEN** the required text field is blank or whitespace only
- **THEN** the response status MUST be 422 and no fact MUST be written
- **WHEN** the identical record already exists for that entity inside the duplicate window
- **THEN** the response status MUST be 409 and the body MUST carry `existing_id`

### Requirement: Entity operator verb rail

Entity detail and the Plex dossier SHALL each render one operator verb rail
offering `log-interaction`, `gift-idea`, and `note`, writing through the
endpoints above. (A fourth verb, `draft-reach-out`, and entity detail's
accompanying drafts panel, shipped alongside these and were later retired in
bu-2jtfw.11 — see the "Reach-out drafts are drafted, never sent (RETIRED)"
requirement above.)

The rail MUST report the real state of a write and nothing more: a pending write
MUST read as pending rather than as success, a completed write MUST appear only
after the server confirms it, and a refusal MUST surface one plain sentence
naming the actual cause -- duplicate, owner-only, missing entity, or invalid
input -- rather than a raw error payload or a silent no-op.

The draft form MUST carry a permanent statement that nothing is sent, and the
rail MUST offer no send affordance for any verb.

#### Scenario: Verb writes appear only once confirmed

- **WHEN** the owner submits any verb form
- **THEN** the rail MUST show a pending state while the request is in flight
- **AND** MUST NOT show the record as saved until the server confirms it

#### Scenario: Refusals are legible

- **WHEN** a write is refused as a duplicate, for owner-only authorization, for a missing entity, or as invalid input
- **THEN** the rail MUST show one sentence naming that cause
- **AND** MUST NOT render a raw error object or leave the form silently unchanged

## MODIFIED Requirements

### Requirement: Owner-only authorization for entity endpoints

The entity endpoints under `/api/relationship/entities/*` MUST enforce owner-only authorization.
They expose both mutation surfaces that mint, merge, archive, or forget entities AND read
surfaces that return raw contact-fact `object` values (emails, phone numbers, social handles,
addresses) — which are PII. The owner-only authorization gate from
`about/heart-and-soul/security.md:18-22` and `rfcs/0007:309` (`'owner' = ANY(e.roles)`) MUST
apply to both write and PII-bearing read surfaces; one without the other leaves a leak hole.

**Clause 12a — Writes (mutations).** Every `POST/PATCH/DELETE` under
`/api/relationship/entities/*` MUST resolve the caller to an owner-role entity
per the `'owner' = ANY(e.roles)` pattern and return HTTP 403 with the envelope
`{ code: 'owner_required' }` otherwise. The gate applies to the exact endpoint set:

- `POST /api/relationship/entities`
- `POST /api/relationship/entities/{id}/merge`
- `POST /api/relationship/entities/{id}/archive`
- `POST /api/relationship/entities/{id}/promote-tier`
- `DELETE /api/relationship/entities/{id}`
- `POST /api/relationship/entities/queue/dismiss`
- `POST /api/relationship/entities/{id}/contacts`
- `DELETE /api/relationship/entities/{id}/contacts/{pred}/{valueHash}`
- `POST /api/relationship/entities/{id}/notes`
- `POST /api/relationship/entities/{id}/interactions`
- `POST /api/relationship/entities/{id}/gifts`
- `POST /api/relationship/entities/{id}/reach-out-drafts`

**Clause 12b — Reads (PII-bearing).** The same owner-only gate MUST apply to the following
GET endpoints because they return raw contact-fact `object` values (emails / phones /
handles / addresses) or aliased identity links whose exposure through the shared
`DASHBOARD_API_KEY` would leak PII to any caller reaching the API surface:

- `GET /api/relationship/entities/queue`
- `GET /api/relationship/entities/search`
- `GET /api/relationship/entities/{id}/contacts`
- `GET /api/relationship/entities/{id}/neighbours`
- `GET /api/relationship/entities/{id}/activity`
- `GET /api/relationship/plex/halo`

The list-only `GET /api/relationship/entities` and per-entity timeline / notes /
interactions / gifts / loans endpoints (which do NOT surface raw contact-fact `object`
values) inherit the existing dashboard session boundary and are not within scope of this
gate. Any future change that adds raw contact-fact values to those responses MUST extend
the gate to the affected endpoint.

**Clause 12c — Deploy gate.** In any non-`dev` environment, daemon startup MUST fail with
a fatal error if `DASHBOARD_API_KEY` is unset. The dev-time "no API key → auth disabled"
shortcut at `src/butlers/api/app.py:246` is incompatible with shipping the entity endpoints.
A guardrail test (tasks.md §12.8) MUST exercise this invariant.

#### Scenario: Owner request to mutate entity succeeds
- **WHEN** an authenticated request resolves to an entity with `'owner' = ANY(e.roles)` and
  calls `POST /api/relationship/entities/{id}/promote-tier`
- **THEN** the response status MUST be 2xx (per the endpoint's own contract)
- **AND** the gate MUST NOT reject the request

#### Scenario: Non-owner request is rejected with `owner_required`
- **WHEN** an authenticated request resolves to an entity whose `roles` does NOT contain
  `'owner'` and calls any endpoint in clause 12a or 12b
- **THEN** the response status MUST be 403
- **AND** the response body MUST contain `{ code: 'owner_required' }` (envelope form per
  `rfcs/0007:75-87` or unwrapped per relationship-domain convention; the `code` string is
  binding)
- **AND** no mutation MUST be applied
- **AND** no PII MUST be returned

#### Scenario: Missing `DASHBOARD_API_KEY` in production refuses startup
- **WHEN** the daemon starts with `BUTLERS_ENV != 'dev'` and `DASHBOARD_API_KEY` unset
- **THEN** startup MUST fail with a fatal error referencing the missing key
- **AND** no entity endpoint MUST become reachable
