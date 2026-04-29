## MODIFIED Requirements

### Requirement: Contact detail page

The frontend SHALL render a contact detail page at `/butlers/relationship/contacts/:id` displaying the contact's channel and identity record. The page is intentionally scoped to contact-bound concerns (the channels by which the contact is reached, their CRM-style attributes, and their typed relationships to other contacts). Activity history (notes, interactions, gifts, loans, timeline) lives on the entity detail page (`/butlers/relationship/entities/:id`) and is reachable via a prominent link from the header.

The page MUST contain the following sections:

1. **Header card** — displaying the contact's full name (`first_name` + `last_name`), `company`, `job_title`, `pronouns`, `avatar_url` (as an image if present, or initials fallback), and `roles` as colored role badges (e.g., "owner" badge in a distinct color). Assigned labels SHALL be rendered as colored badges. The `nickname` SHALL be displayed in parentheses after the name if present. The header MUST include a prominent "View entity activity →" link that deep-links to `/butlers/relationship/entities/:entity_id` where `entity_id` is the contact's `entity_id`. If the contact has no `entity_id` (a degenerate state that should not occur post-`core_014`), the link MUST be omitted and a non-blocking warning rendered.

2. **Contact info section** — displaying all `contact_info` entries grouped by `type` (email, phone, social, etc.). Each entry SHALL show the `label` (e.g., "work", "personal") and the `value`. Entries with `secured = true` SHALL display the value as `"********"` with a click-to-reveal button that fetches the actual value from `GET /api/contacts/{id}/secrets/{info_id}`. Values of type `email` SHALL be rendered as `mailto:` links. Values of type `phone` SHALL be rendered as `tel:` links.

3. **Important dates section** — displaying all important dates with a countdown indicator. Each date SHALL show the `label`, the date (`day`/`month`/`year` formatted, with "unknown year" if `year` is null), and the number of days until the next occurrence. Dates occurring within the next 7 days SHALL be visually highlighted (e.g., with a colored badge or accent).

4. **Quick facts section** — displaying all quick facts, grouped or tagged by `category`. Each fact SHALL show the `category` label and the `content` text.

5. **Relationships section** — displaying all relationships as a list. Each entry SHALL show the `type` label (e.g., "Parent", "Friend"), the related contact's name as a clickable link to their detail page, and the `group_type`. The `relationships` array on the contact detail API response is preserved unchanged from the prior spec; re-anchoring typed contact-to-contact relationships to entity edge facts is explicitly out of scope for this change.

The page MUST NOT contain a tabbed content area for notes, interactions, gifts, or loans, and MUST NOT contain an activity feed sidebar or section. These sections — previously bullets 6 ("Tabbed content area") and 7 ("Activity feed sidebar or section") of this requirement — are removed in this change. They are replaced by the entity detail page and its tab APIs (see "Entity detail page" and "Entity-level tab APIs" below). The contact detail API response SHALL no longer include any of `notes`, `interactions`, `gifts`, `loans`, `activity_feed` arrays as embedded sub-resources, and the five sub-resource endpoints `GET /api/butlers/relationship/contacts/{id}/{notes,interactions,gifts,loans,feed}` SHALL be removed.

#### Scenario: Contact detail page loads with roles, entity link, and secured info

- **WHEN** a user navigates to `/butlers/relationship/contacts/abc-123-uuid` for the owner contact and the contact has `entity_id = ent-456-uuid`
- **THEN** the header card MUST display the contact's name with an "owner" role badge
- **AND** the header MUST display a "View entity activity →" link pointing to `/butlers/relationship/entities/ent-456-uuid`
- **AND** contact info entries with `secured = true` MUST display masked values with reveal buttons

#### Scenario: Click-to-reveal secured credential

- **WHEN** a user clicks the reveal button on a secured `contact_info` entry
- **THEN** the frontend MUST call `GET /api/contacts/{id}/secrets/{info_id}`
- **AND** the masked value MUST be replaced with the actual value
- **AND** a hide button MUST appear to re-mask the value

#### Scenario: Contact with sparse channel data

- **WHEN** a user navigates to a contact's detail page and the contact has no `contact_info`, `addresses`, `important_dates`, `quick_facts`, or relationships
- **THEN** each empty section MUST display an appropriate empty state message (e.g., "No contact info", "No relationships")
- **AND** the page MUST render without errors
- **AND** the "View entity activity →" link MUST still be present in the header

#### Scenario: Email and phone values are clickable

- **WHEN** a contact has a `contact_info` entry with `type='email'` and `value='alice@example.com'`
- **THEN** the value MUST be rendered as a clickable `mailto:alice@example.com` link
- **WHEN** a contact has a `contact_info` entry with `type='phone'` and `value='+1-555-0100'`
- **THEN** the value MUST be rendered as a clickable `tel:+1-555-0100` link

#### Scenario: Contact not found

- **WHEN** a user navigates to `/butlers/relationship/contacts/nonexistent-uuid` and the contact does not exist
- **THEN** the page MUST display a 404 message (e.g., "Contact not found")

#### Scenario: Contact missing entity link (degenerate state)

- **WHEN** a contact has `entity_id IS NULL`
- **THEN** the "View entity activity →" link MUST be omitted from the header
- **AND** a non-blocking warning MUST be rendered indicating the contact is not linked to an entity
- **AND** the rest of the page MUST render normally

#### Scenario: Legacy tab endpoints removed

- **WHEN** any of `GET /api/butlers/relationship/contacts/{id}/{notes,interactions,gifts,loans,feed}` is called
- **THEN** the response MUST be a 404 (route not found)
- **AND** the legacy tables `relationship.{notes, interactions, gifts, loans, activity_feed}` MUST NOT exist in the database after this change is applied

---

## ADDED Requirements

### Requirement: Entity detail page

The frontend SHALL render an entity detail page at `/butlers/relationship/entities/:id` displaying the entity's identity header and activity tabs. This is the canonical surface for browsing notes, interactions, gifts, loans, and the unified timeline for any entity in `public.entities`. This surface coexists with the memory butler's identity-focused entity detail page at `/entities/:id`; the two pages serve different audiences (relationship browsing vs. credential and identity admin) and deep-link to each other.

The page MUST contain:

1. **Header card** — displaying `canonical_name`, `entity_type`, `aliases` (as chips), and `roles` as colored badges. If the entity has `metadata->>'unidentified' = 'true'`, an "Unidentified" badge MUST be shown. The header MUST include a "View identity →" link to `/entities/:id`.

2. **Linked contacts section** — listing all rows in `public.contacts` where `entity_id` matches, with each row showing `first_name + last_name`, primary `contact_info` entries (one email/phone), and a link to the contact detail page.

3. **Tabbed content area** — five tabs in this order: Notes, Interactions, Gifts, Loans, Timeline. Each tab is paginated and loads its data from the corresponding entity-level API endpoint (see "Entity-level tab APIs"). Empty tabs MUST display an appropriate empty-state message (e.g., "No notes for this entity yet").

#### Scenario: Entity detail page renders with tabs

- **WHEN** a user navigates to `/butlers/relationship/entities/ent-456-uuid` and the entity exists
- **THEN** the header card MUST display the entity's `canonical_name`, `entity_type`, and any `roles`
- **AND** the linked contacts section MUST list all contacts whose `entity_id` matches
- **AND** the five tabs MUST be rendered, each loading data from its respective endpoint

#### Scenario: Entity not found

- **WHEN** a user navigates to `/butlers/relationship/entities/nonexistent-uuid`
- **THEN** the page MUST display a 404 message (e.g., "Entity not found")

#### Scenario: Entity with no facts

- **WHEN** a user navigates to an entity that has zero matching facts across all five predicates
- **THEN** all five tabs MUST display empty-state messages
- **AND** the page MUST render without errors

#### Scenario: Unidentified entity badge

- **WHEN** an entity has `metadata->>'unidentified' = 'true'`
- **THEN** the header card MUST display an "Unidentified" badge
- **AND** the rest of the page MUST render normally

---

### Requirement: Entity-level tab APIs

The dashboard API SHALL expose five entity-keyed endpoints for tab data, each reading from `facts` filtered by predicate. All five endpoints MUST scope queries to `validity = 'active' AND scope = 'relationship'`. All five endpoints MUST support pagination via query parameters `?limit=` (default 50, max 200) and `?offset=` (default 0). All five endpoints MUST return 404 if the requested entity UUID does not exist in `public.entities`.

The endpoints are:

| Endpoint | Predicate filter | Sort order |
|---|---|---|
| `GET /api/butlers/relationship/entities/{id}/notes` | `predicate = 'contact_note'` | `valid_at DESC` |
| `GET /api/butlers/relationship/entities/{id}/interactions` | `predicate LIKE 'interaction_%'` | `valid_at DESC` |
| `GET /api/butlers/relationship/entities/{id}/gifts` | `predicate = 'gift'` | `created_at DESC` |
| `GET /api/butlers/relationship/entities/{id}/loans` | `predicate = 'loan'` | `created_at DESC` |
| `GET /api/butlers/relationship/entities/{id}/timeline` | `predicate IN ('contact_note','life_event','gift','loan','dunbar_tier_override') OR predicate LIKE 'interaction_%'` | `valid_at DESC NULLS LAST, created_at DESC` |

The Timeline endpoint excludes the legacy `activity` predicate. The `_log_activity()` write path is removed in this change; historical `activity` facts (if any survive) are not surfaced on Timeline (they are duplicates of primary facts already included via their own predicates) but remain queryable via the `feed_get` MCP tool.

Response field shapes MUST follow the wrapper mappings in `predicate-taxonomy.md` §5.2 with the following per-tab shapes:

- **notes** entries: `{ id: fact.id, content: fact.content, emotion: fact.metadata->>'emotion', created_at: fact.valid_at }`
- **interactions** entries: `{ id: fact.id, type: <predicate suffix>, summary: fact.content, occurred_at: fact.valid_at, direction: fact.metadata->>'direction', group_size: fact.metadata->>'group_size' }`. The `type` field is extracted from the predicate suffix: `predicate='interaction_meeting'` yields `type='meeting'`. The `direction` and `group_size` fields are populated by the passive interaction sync job (`passive-interaction-sync` spec) and may be null for facts written via direct `interaction_log()` calls without those metadata keys.
- **gifts** entries: `{ id: fact.id, description: fact.content, occasion: fact.metadata->>'occasion', status: fact.metadata->>'status', created_at: fact.created_at }`
- **loans** entries: `{ id: fact.id, description: fact.content, amount_cents: fact.metadata->>'amount_cents', currency: fact.metadata->>'currency', direction: fact.metadata->>'direction', settled: fact.metadata->>'settled', settled_at: fact.metadata->>'settled_at', created_at: fact.created_at }`
- **timeline** entries: `{ kind: <predicate-family>, id: fact.id, content: fact.content, valid_at: fact.valid_at, predicate: fact.predicate, metadata: fact.metadata }` where `kind` is one of `note`, `interaction`, `gift`, `loan`, `life_event`, `dunbar_tier_override`.

When a metadata field referenced above is absent from a fact's JSONB, the response value MUST be `null` (not omitted; not a default). Clients MUST be able to render rows with missing metadata fields without errors.

#### Scenario: Notes endpoint returns facts for entity

- **WHEN** `GET /api/butlers/relationship/entities/ent-456/notes` is called and three `contact_note` facts exist with `entity_id = ent-456`, `validity = 'active'`, `scope = 'relationship'`
- **THEN** the response status MUST be 200
- **AND** the response body MUST be a list of three entries shaped per the notes mapping
- **AND** entries MUST be ordered by `valid_at DESC`

#### Scenario: Interactions endpoint merges interaction subtypes

- **WHEN** `GET /api/butlers/relationship/entities/ent-456/interactions` is called and the entity has interaction facts with predicates `interaction_meeting`, `interaction_message`, and `interaction_call`
- **THEN** the response MUST include all three with `type` field set to `"meeting"`, `"message"`, and `"call"` respectively (the predicate suffix)
- **AND** entries MUST be ordered by `valid_at DESC` regardless of subtype

#### Scenario: Mixed-channel interactions are merged across linked contacts

- **WHEN** an entity has two contacts (one Telegram, one email) and interaction facts exist via both channels
- **THEN** `GET /api/butlers/relationship/entities/{id}/interactions` MUST return all facts where `entity_id = $1` regardless of which contact's tools created them
- **AND** the response MUST NOT deduplicate by `(predicate, valid_at)` — facts from different channels are surfaced separately

#### Scenario: Timeline orders by valid_at across all six predicate families

- **WHEN** `GET /api/butlers/relationship/entities/{id}/timeline` is called and the entity has facts of every supported predicate family
- **THEN** the response MUST include facts from `interaction_*`, `contact_note`, `life_event`, `gift`, `loan`, and `dunbar_tier_override`
- **AND** entries MUST be ordered by `valid_at DESC` with `NULLS LAST` semantics, falling back to `created_at DESC` for property facts (gift, loan, dunbar_tier_override)
- **AND** each entry MUST include a `kind` field identifying the predicate family

#### Scenario: Timeline excludes legacy activity facts

- **WHEN** `GET /api/butlers/relationship/entities/{id}/timeline` is called and the entity has facts with `predicate = 'activity'`
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
