## MODIFIED Requirements

### Requirement: Entity index page (`/entities/index`)

The frontend SHALL render an entity index at `/entities/index` (NOT
`/butlers/relationship/entities`). The `/entities` landing itself is the Plex (see
Requirement: Entity Plex view); the Index is the tabular curation surface one tab away,
home for list-filter-and-curate workflows over the same population. The Index MUST
consist of:

1. **Tabular list (left/main column)** — one row per entity, neutral hairline-on-neutral.
   Columns: entity-mark glyph (type indicator: `P / O / L / X / @ / E / G`), canonical_name +
   nicknames, tier badge (Dunbar), `last_seen`, contact-fact count pill, aliases. Rows MUST NOT
   carry state colour; the EntityMark glyph carries type, not hue.
   Rows use the index-row padding from `dashboard-design-language` "Density and Spacing", never card padding.
2. **Filter chips** — type pills (`person/organization/location/product/...`), `has=contact`
   chip (replaces legacy `/contacts` page), state chips (`unidentified`, `duplicate-candidate`,
   `stale`), tier chips. The `has=contact` chip MUST surface all entities with at least one
   `has-email | has-phone | has-handle | has-address` triple.
3. **Curation queue (right rail)** — see Requirement: Entity curation queue.
4. **SubpageTabs** — horizontal nav strip linking Plex / Index / Concentration.
   Active tab is `/entities/index`.
5. **Cmd-K affordance** — visible mono kbd capsule (`⌘K`) in the header.

The Index page MUST render inside `<Page archetype="overview">` with breadcrumb `Entities`.

#### Scenario: Index renders with neutral rows and queue rail
- **WHEN** a user navigates to `/entities/index` with at least one entity in `public.entities`
- **THEN** the rows MUST render neutral hairline-on-neutral (no amber/red fills)
- **AND** the curation queue rail MUST be present (collapsed to single serif italic line if empty)
- **AND** the SubpageTabs strip MUST mark Index as active

#### Scenario: has=contact filter chip lists every entity with a contact triple
- **WHEN** `?has=contact` query is applied
- **THEN** the result set MUST be exactly the entities with at least one triple in
  `relationship.entity_facts` whose predicate matches `has-email | has-phone | has-handle |
  has-address | has-birthday | has-website`

#### Scenario: `/contacts` index redirects to `/entities/index?has=contact`
- **WHEN** a request reaches the contacts INDEX path `/contacts` (no `:contactId` param)
- **THEN** the client MUST replace-navigate to `/entities/index?has=contact`
- **AND** no functional regression MUST occur for any prior `/contacts` index workflow
- **AND** the contact-detail compatibility path `/contacts/:contactId` MUST also
  replace-navigate to `/entities/index?has=contact`, as defined by Requirement: Contact
  routes are compatibility aliases
- **AND** canonical single-record navigation MUST start from the entity index and target
  `/entities/:entityId`

### Requirement: Entity detail Editorial / Workbench mode toggle

The entity detail page at `/entities/:entityId` SHALL render in one of two modes: **Editorial** (default) or **Workbench**.
The unified ActivityTimeline is present in Editorial mode. In Workbench mode
it is replaced by the ProvenanceGrid, which surfaces every provenance column in
a dense, sortable grid. The toggle also changes how the header and contact
facts are rendered.

**Editorial mode** is the default and MUST:
- Use `<Page archetype="editorial">`, rendering the entity canonical_name in
  the Display tier of the `dashboard-design-language` Type System.
- Hide provenance metadata (`conf`, `src`, `weight`, `verified`, `primary`)
  from row chrome. Provenance is still loaded into the response; only the
  visual rendering hides it.
- Render contacts grouped by predicate (`has-email`, `has-phone`, ...). A
  person with three emails MUST render three rows, primary first; never
  collapsed to "the email."
- Render the voice gloss in `Source Serif 4` italic, one line under the
  canonical name. **The gloss text MUST be a canned string** selected by
  `(tier, state, category)` — see Requirement: Detail-page voice gloss source.

**Workbench mode** MUST:
- Use `<Page archetype="overview">` with the standard page heading; the
  Display tier is forbidden in this mode.
- Surface every provenance column (`conf`, `src`, `lastSeen`, `weight`,
  `verified`, `primary`) on every row. The same data record drives both
  modes.
- Render contacts as a dense predicate+value+provenance grid; sortable by
  any column.

**Mode persistence and toggle UI:**
- The mode toggle is an icon button in the Page shell's actions slot.
- The mode persists in `localStorage` under the key `entities.detail.mode`.
- Missing, invalid, or unsupported values in `localStorage` MUST default to
  `editorial`.
- `?mode=workbench` URL parameter overrides `localStorage` for the current
  page load only; toggling via the UI updates both URL and `localStorage`.

**Forget affordance (binding):**
- Both modes MUST surface a "Forget" action (accessible name "Forget this
  entity") in the Page header actions (NOT a kebab menu). Clicking opens a
  "Forget this entity?" confirm dialog stating that forgetting retracts all
  associated facts, permanently removes the entity, and cannot be undone,
  before the destructive request.

#### Scenario: Editorial is default, mode persists

- **WHEN** a user lands on `/entities/<uuid>` with no `localStorage` value
- **THEN** Editorial MUST render with the Display-tier headline
- **WHEN** the user toggles to Workbench
- **THEN** `localStorage["entities.detail.mode"]` MUST be set to `workbench`
- **AND** subsequent loads MUST render Workbench until toggled back

#### Scenario: Three emails render three rows in both modes

- **WHEN** an entity has three `has-email` triples (primary + two secondary)
- **THEN** Editorial MUST render three rows under the "Email" predicate group,
  primary first
- **AND** Workbench MUST render three rows in the contacts grid, sorted by
  `primary DESC`
- **AND** neither mode MUST collapse to a single "Email" row

### Requirement: Dispatch design language token discipline

All six entity routes (`/entities`, `/entities/hop`, `/entities/columns`, `/entities/concentration`, `/entities/social-map`, `/entities/:entityId`) SHALL follow the token, colour and font rules of `dashboard-design-language`. In particular they MUST NOT define CSS custom properties outside `frontend/src/index.css`, and MUST NOT use hex colour literals in relationship or entity-page components, except in `frontend/src/lib/entity-model.ts` and the predicate-catalog UI.

#### Scenario: Token discipline applies to canonical entity detail route

- **WHEN** code review compares any component rendered at `/entities/:entityId`
- **THEN** the component MUST NOT introduce new CSS custom properties outside `frontend/src/index.css`
- **AND** the component MUST NOT use hex color literals in `frontend/src/components/relationship/*` or `frontend/src/pages/entities/*`

### Requirement: Owner identity and credential management

The Secrets page (`/secrets`) SHALL be the primary mechanism for configuring
owner identity credentials. The entity detail page (`/entities/:entityId`) SHALL
surface identity-bound credentials by displaying a prominent link to the Secrets
page (`/secrets` → User tab) where the owner entity's credentials can be viewed,
entered, and managed.

The entity detail contact-channel card MUST NOT include secured credential types
(`email_password`, `telegram_api_id`, `telegram_api_hash`,
`home_assistant_token`) in its "Add contact info" type dropdown. The
`AddChannelInfoForm` MUST support the following non-secured contact types only:
`email`, `phone`, `telegram`, `website`, `other`. The form MUST display
human-friendly labels for all supported types.

The User tab on the Secrets page MUST support all secured credential types for
the owner entity (`email_password`, `telegram_api_id`, `telegram_api_hash`,
`home_assistant_token`). Telegram API hash entry MUST remain exclusive to the
guided Telegram session setup; the generic raw-credential mutation MUST NOT
receive it.

#### Scenario: Add a non-secured channel entry from the entity detail contact-channel card

- **WHEN** a user opens the owner entity's detail page at `/entities/:entityId`
  and clicks "Add contact info" in the contact-channel card
- **AND** selects "Email" or "Telegram" from the type dropdown and enters a
  value
- **THEN** the input field MUST be a text field (not masked)
- **AND** the created entry MUST have `secured = false`

#### Scenario: Secured credentials are managed via the Secrets page

- **WHEN** a user needs to enter or update a secured credential (e.g., email
  password, Telegram API ID/hash)
- **THEN** the user MUST navigate to the Secrets page at `/secrets` (linked
  from the entity detail page)
- **AND** the User tab MUST display and allow management of the owner entity's
  secured credentials
- **AND** the entity detail contact-channel card MUST NOT offer secured
  credential types in its add form

### Requirement: Owner identity setup banner

The dashboard SHALL display a persistent banner on the entity detail page
(`/entities/:entityId`) when the owner entity is missing key identity
fields (name, telegram handle, or telegram chat ID). The banner appears
inside the practical drawer, which is forced open when the owner has not
completed identity setup. The entity detail contact-channel card at
`/entities/:entityId` is the canonical location for ongoing identity and
credential management.

#### Scenario: Banner shown when owner has missing identity fields

- **WHEN** a user navigates to `/entities/:entityId` for the owner entity and
  the owner is missing any of: name, telegram handle, or telegram chat ID
- **THEN** a banner MUST be displayed inside the practical drawer indicating
  which fields are missing
- **AND** the practical drawer MUST be forced open when the banner is active
- **AND** a "Set Up Identity" button MUST open a dialog for filling in missing
  fields

#### Scenario: Banner hidden when all identity fields are configured

- **WHEN** the owner entity has name, telegram handle, and telegram chat ID
  configured
- **THEN** the setup banner MUST NOT be displayed

#### Scenario: Banner dialog includes credentials section

- **WHEN** the owner setup dialog is opened
- **THEN** a collapsible "Credentials" section MUST be available for
  optionally setting Telegram API ID, Home Assistant URL, and Home Assistant
  token
- **AND** the Telegram API hash MUST NOT be accepted by this generic dialog;
  it is entered only through the guided Telegram session setup
- **AND** credential fields (API ID and Home Assistant token) MUST
  create secured `entity_info` entries

### Requirement: Entity-level tab APIs

The dashboard API SHALL expose five entity-keyed endpoints for tab data, each reading from `facts` filtered by predicate. All five endpoints MUST scope queries to `validity = 'active' AND scope = 'relationship'`. All five endpoints MUST support pagination via query parameters `?limit=` (default 50, max 200) and `?offset=` (default 0). All five endpoints MUST return 404 if the requested entity UUID does not exist in `public.entities`.

The endpoints are:

| Endpoint | Predicate filter | Sort order |
|---|---|---|
| `GET /api/relationship/entities/{id}/notes` | `predicate = 'contact_note'` | `valid_at DESC` |
| `GET /api/relationship/entities/{id}/interactions` | `predicate LIKE 'interaction_%'` | `valid_at DESC` |
| `GET /api/relationship/entities/{id}/gifts` | `predicate = 'gift'` | `created_at DESC` |
| `GET /api/relationship/entities/{id}/loans` | `predicate = 'loan'` | `created_at DESC` |
| `GET /api/relationship/entities/{id}/timeline` | `predicate IN ('contact_note','life_event','gift','loan','dunbar_tier_override') OR predicate LIKE 'interaction_%'` | `valid_at DESC NULLS LAST, created_at DESC` |

The Timeline endpoint excludes the legacy `activity` predicate. The relationship butler no longer writes `activity` facts; historical `activity` facts (if any survive) are not surfaced on Timeline (they are duplicates of primary facts already included via their own predicates) but remain queryable via the `feed_get` MCP tool.

Response field shapes MUST be the following per-tab shapes:

- **notes** entries: `{ id: fact.id, content: fact.content, emotion: fact.metadata->>'emotion', created_at: fact.valid_at }`
- **interactions** entries: `{ id: fact.id, type: <predicate suffix>, summary: fact.content, occurred_at: fact.valid_at, direction: fact.metadata->>'direction', group_size: fact.metadata->>'group_size' }`. The `type` field is extracted from the predicate suffix: `predicate='interaction_meeting'` yields `type='meeting'`. The `direction` and `group_size` fields are populated by the passive interaction sync job (`passive-interaction-sync` spec) and may be null for facts written via direct `interaction_log()` calls without those metadata keys.
- **gifts** entries: `{ id: fact.id, description: fact.content, occasion: fact.metadata->>'occasion', status: fact.metadata->>'status', created_at: fact.created_at }`
- **loans** entries: `{ id: fact.id, description: fact.content, amount_cents: fact.metadata->>'amount_cents', currency: fact.metadata->>'currency', direction: fact.metadata->>'direction', settled: fact.metadata->>'settled', settled_at: fact.metadata->>'settled_at', created_at: fact.created_at }`
- **timeline** entries: `{ kind: <predicate-family>, id: fact.id, content: fact.content, valid_at: fact.valid_at, predicate: fact.predicate, metadata: fact.metadata }` where `kind` is one of `note`, `interaction`, `gift`, `loan`, `life_event`, `dunbar_tier_override`.

When a metadata field referenced above is absent from a fact's JSONB, the response value MUST be `null` (not omitted; not a default). Clients MUST be able to render rows with missing metadata fields without errors.

These five endpoints read the shared `facts` table under `scope='relationship'`. `relationship.entity_facts` (see `relationship-facts`) is the canonical entity triple store; the entity-redesign endpoints read it directly, and these five endpoints SHALL be re-pointed to it at the read-path cut-over.

#### Scenario: Notes endpoint returns facts for entity

- **WHEN** `GET /api/relationship/entities/ent-456/notes` is called and three `contact_note` facts exist with `entity_id = ent-456`, `validity = 'active'`, `scope = 'relationship'`
- **THEN** the response status MUST be 200
- **AND** the response body MUST be a list of three entries shaped per the notes mapping
- **AND** entries MUST be ordered by `valid_at DESC`

#### Scenario: Interactions endpoint merges interaction subtypes

- **WHEN** `GET /api/relationship/entities/ent-456/interactions` is called and the entity has interaction facts with predicates `interaction_meeting`, `interaction_message`, and `interaction_call`
- **THEN** the response MUST include all three with `type` field set to `"meeting"`, `"message"`, and `"call"` respectively (the predicate suffix)
- **AND** entries MUST be ordered by `valid_at DESC` regardless of subtype

#### Scenario: Mixed-channel interactions are merged across linked contacts

- **WHEN** an entity has two contacts (one Telegram, one email) and interaction facts exist via both channels
- **THEN** `GET /api/relationship/entities/{id}/interactions` MUST return all facts where `entity_id = $1` regardless of which contact's tools created them
- **AND** the response MUST NOT deduplicate by `(predicate, valid_at)` — facts from different channels are surfaced separately

#### Scenario: Timeline orders by valid_at across all six predicate families

- **WHEN** `GET /api/relationship/entities/{id}/timeline` is called and the entity has facts of every supported predicate family
- **THEN** the response MUST include facts from `interaction_*`, `contact_note`, `life_event`, `gift`, `loan`, and `dunbar_tier_override`
- **AND** entries MUST be ordered by `valid_at DESC` with `NULLS LAST` semantics, falling back to `created_at DESC` for property facts (gift, loan, dunbar_tier_override)
- **AND** each entry MUST include a `kind` field identifying the predicate family

#### Scenario: Timeline excludes legacy activity facts

- **WHEN** `GET /api/relationship/entities/{id}/timeline` is called and the entity has facts with `predicate = 'activity'`
- **THEN** those facts MUST NOT appear in the response

#### Scenario: Empty entity returns empty arrays

- **WHEN** any of the five endpoints is called for an entity that has zero matching facts
- **THEN** the response status MUST be 200
- **AND** the response body MUST be `[]`

#### Scenario: Entity does not exist

- **WHEN** any of the five endpoints is called with an entity UUID that does not exist in `public.entities`
- **THEN** the response status MUST be 404
- **AND** the response body MUST contain an error message indicating the entity was not found

#### Scenario: Retracted facts are excluded

- **WHEN** an entity has facts with `validity = 'retracted'` or `validity = 'superseded'`
- **THEN** none of the five endpoints MUST include those facts in their responses
- **AND** only `validity = 'active'` facts MUST be returned

#### Scenario: Pagination defaults and limits

- **WHEN** any of the five endpoints is called without `limit` or `offset` query parameters
- **THEN** the response MUST return at most 50 entries
- **AND** the offset MUST be 0

#### Scenario: Pagination max enforced

- **WHEN** any of the five endpoints is called with `?limit=500`
- **THEN** the response MUST be limited to 200 entries (the maximum)

#### Scenario: Cross-scope facts excluded

- **WHEN** an entity has facts with `scope = 'health'` or `scope = 'finance'`
- **THEN** the relationship-domain endpoints MUST NOT include those facts in any response
- **AND** only facts with `scope = 'relationship'` MUST be returned

#### Scenario: Sparse metadata fields render as null

- **WHEN** a fact has `metadata = '{}'` or is missing one of the documented metadata fields
- **THEN** the response entry MUST include the field with value `null`
- **AND** the endpoint MUST NOT raise an error

### Requirement: Finder is deterministic — no LLM ranking

`GET /api/relationship/entities/search` MUST use rule-based ranking only (no embedding service,
no reranker LLM in v1). The rule set is defined in
Requirement: App-wide Cmd-K Finder above. No model call MAY appear in the request handler path
of `/api/relationship/entities/search`.

#### Scenario: Finder handler issues zero LLM calls
- **WHEN** a Finder query is processed
- **THEN** the handler MUST NOT call any LLM provider
- **AND** the handler MUST NOT call any embedding service
- **AND** ranking MUST be computed purely from string-matching and `last_seen / tier` tie-breaks

### Requirement: Entity operator verb rail

Entity detail and the Plex dossier SHALL each render one operator verb rail
offering `log-interaction`, `gift-idea`, and `note`, writing through the
endpoints above.

The rail MUST report the real state of a write and nothing more: a pending write
MUST read as pending rather than as success, a completed write MUST appear only
after the server confirms it, and a refusal MUST surface one plain sentence
naming the actual cause -- duplicate, owner-only, missing entity, or invalid
input -- rather than a raw error payload or a silent no-op.

The rail MUST offer no send affordance for any verb.

#### Scenario: Verb writes appear only once confirmed

- **WHEN** the owner submits any verb form
- **THEN** the rail MUST show a pending state while the request is in flight
- **AND** MUST NOT show the record as saved until the server confirms it

#### Scenario: Refusals are legible

- **WHEN** a write is refused as a duplicate, for owner-only authorization, for a missing entity, or as invalid input
- **THEN** the rail MUST show one sentence naming that cause
- **AND** MUST NOT render a raw error object or leave the form silently unchanged
