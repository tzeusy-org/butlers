# Dashboard Relationship

## Purpose

Defines the dashboard surfaces for the Relationship butler: the contact detail API, contact detail page, secured credential reveal, owner identity setup, pending identity disambiguation queue, roles management, and the bidirectional bridge between the memory entity pages and the relationship-scoped entity activity page. Together these form the complete operator-facing contract for viewing, managing, and navigating relationship data through the Butlers dashboard.
## Requirements
### Requirement: Contact detail API

The dashboard API SHALL expose `GET /api/butlers/relationship/contacts/:id` which returns a single contact's full record with joined data from related tables via a direct database read. The query MUST read from `public.contacts` (not `relationship.contacts`).

The response MUST include:
- All columns from the `public.contacts` table (`id`, `first_name`, `last_name`, `nickname`, `company`, `job_title`, `gender`, `pronouns`, `avatar_url`, `listed`, `metadata`, `roles`, `entity_id`, `created_at`, `updated_at`)
- `info` (array) -- all rows from `public.contact_info` for this contact, each containing `id`, `type`, `value` (masked as `"********"` if `secured = true`), `label`, `secured`, `created_at`
- `addresses` (array) -- all rows from `addresses` for this contact, each containing `id`, `type`, `line_1`, `line_2`, `city`, `province`, `postal_code`, `country`, `is_current`, `created_at`
- `important_dates` (array) -- all rows from `important_dates` for this contact, each containing `id`, `label`, `day`, `month`, `year`, `created_at`
- `quick_facts` (array) -- all rows from `quick_facts` for this contact, each containing `id`, `category`, `content`, `created_at`
- `relationships` (array) -- all rows from `relationships` where `contact_id` matches, each containing `id`, `related_contact_id`, `group_type`, `type`, `reverse_type`, `created_at`, and a nested `related_contact` object with `id`, `first_name`, `last_name`, `company`
- `labels` (array) -- all labels assigned to this contact via `contact_labels`, each containing `id`, `name`, `color`

#### Scenario: Fetch an existing contact with full detail

- **WHEN** `GET /api/butlers/relationship/contacts/abc-123-uuid` is called and the contact exists
- **THEN** the API MUST return the complete contact record including `roles` and `entity_id` fields
- **AND** the `info` array MUST contain all `contact_info` rows with secured values masked
- **AND** the response status MUST be 200

#### Scenario: Secured contact_info values are masked

- **WHEN** a contact has a `contact_info` entry with `secured = true` and `value = 'secret-token-123'`
- **THEN** the `info` array entry MUST have `value = "********"` and `secured = true`

#### Scenario: Contact does not exist

- **WHEN** `GET /api/butlers/relationship/contacts/nonexistent-uuid` is called and no contact with that ID exists
- **THEN** the API MUST return a 404 response with an error message indicating the contact was not found

#### Scenario: Contact with no related data

- **WHEN** `GET /api/butlers/relationship/contacts/abc-123-uuid` is called for a contact that has no contact_info, addresses, important_dates, quick_facts, relationships, or labels
- **THEN** the API MUST return the contact record with all joined arrays as empty arrays (`[]`)
- **AND** the response status MUST be 200

#### Scenario: Relationships include related contact details

- **WHEN** a contact has a relationship of type `"parent"` with another contact named "Bob Smith" at "Acme Corp"
- **THEN** the relationship entry in the `relationships` array MUST include a `related_contact` object with `id`, `first_name` set to `"Bob"`, `last_name` set to `"Smith"`, and `company` set to `"Acme Corp"`

---

### Requirement: Contact detail page

The frontend SHALL render a contact detail page at the canonical route `/contacts/:contactId` (per the shipped `dashboard-relationship` spec Requirement: Contact detail page canonical route) displaying the contact's channel and identity record. The legacy path `/butlers/relationship/contacts/:id` continues to redirect to the canonical route per that spec; all narrative in this change refers to the canonical path. The page is intentionally scoped to contact-bound concerns (the channels by which the contact is reached, their CRM-style attributes, and their typed relationships to other contacts). Activity history (notes, interactions, gifts, loans, timeline) lives on the entity detail page (`/butlers/relationship/entities/:id`) and is reachable via a prominent link from the header.

The page MUST contain the following sections:

1. **Header card** — displaying the contact's full name (`first_name` + `last_name`), `company`, `job_title`, `pronouns`, `avatar_url` (as an image if present, or initials fallback), and `roles` as colored role badges (e.g., "owner" badge in a distinct color). Assigned labels SHALL be rendered as colored badges. The `nickname` SHALL be displayed in parentheses after the name if present. The header MUST include a prominent "View entity activity →" link that deep-links to `/butlers/relationship/entities/:entity_id` where `entity_id` is the contact's `entity_id`. If the contact has no `entity_id` (a degenerate state that should not occur post-`core_014`), the link MUST be omitted and a non-blocking warning rendered.

2. **Contact info section** — displaying all `contact_info` entries grouped by `type` (email, phone, social, etc.). Each entry SHALL show the `label` (e.g., "work", "personal") and the `value`. Entries with `secured = true` SHALL display the value as `"********"` with a click-to-reveal button that fetches the actual value from `GET /api/contacts/{id}/secrets/{info_id}`. Values of type `email` SHALL be rendered as `mailto:` links. Values of type `phone` SHALL be rendered as `tel:` links.

3. **Important dates section** — displaying all important dates with a countdown indicator. Each date SHALL show the `label`, the date (`day`/`month`/`year` formatted, with "unknown year" if `year` is null), and the number of days until the next occurrence. Dates occurring within the next 7 days SHALL be visually highlighted (e.g., with a colored badge or accent).

4. **Quick facts section** — displaying all quick facts, grouped or tagged by `category`. Each fact SHALL show the `category` label and the `content` text.

5. **Relationships section** — displaying all relationships as a list. Each entry SHALL show the `type` label (e.g., "Parent", "Friend"), the related contact's name as a clickable link to their detail page, and the `group_type`. The `relationships` array on the contact detail API response is preserved unchanged from the prior spec; re-anchoring typed contact-to-contact relationships to entity edge facts is explicitly out of scope for this change.

The page MUST NOT contain a tabbed content area for notes, interactions, gifts, or loans, and MUST NOT contain an activity feed sidebar or section. These sections — previously bullets 6 ("Tabbed content area") and 7 ("Activity feed sidebar or section") of this requirement — are removed in this change. They are replaced by the entity detail page and its tab APIs (see "Entity detail page" and "Entity-level tab APIs" below). The contact detail API response SHALL no longer include any of `notes`, `interactions`, `gifts`, `loans`, `activity_feed` arrays as embedded sub-resources, and the five sub-resource endpoints `GET /api/butlers/relationship/contacts/{id}/{notes,interactions,gifts,loans,feed}` SHALL be removed.

#### Scenario: Contact detail page loads with roles, entity link, and secured info

- **WHEN** a user navigates to `/contacts/abc-123-uuid` for the owner contact and the contact has `entity_id = ent-456-uuid`
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

- **WHEN** a user navigates to `/contacts/nonexistent-uuid` and the contact does not exist
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

### Requirement: Contact detail page canonical route is /contacts/:contactId

The contact detail page SHALL be served exclusively at the canonical route
`/contacts/:contactId`. The legacy route `/butlers/relationship/contacts/:id` (specified
in the existing "Contact detail page" requirement) is deprecated and MUST NOT be treated
as a normative route.

**Route duplication resolution:**

- **Canonical route:** `/contacts/:contactId` (renders `ContactDetailPage`; matches the
  parameter name already registered at `frontend/src/router.tsx` line 84)
- **Legacy route:** `/butlers/relationship/contacts/:id` — MUST redirect to the canonical
  path via a client-side React Router entry using `<Navigate replace />`, following the
  `RelationshipEntityRedirect` pattern already present at `frontend/src/router.tsx`
  lines 57–64. This is a client-side redirect; for external HTTP-level bookmarks a
  hosting-level redirect would be needed separately (out of scope for this change).
- Any internal navigation link (breadcrumb, "View contact" button, notification link,
  email) that currently targets `/butlers/relationship/contacts/:id` MUST be updated
  to target `/contacts/:contactId`.

**Verification:**

The router at `frontend/src/router.tsx` line 84 already registers `/contacts/:contactId`
as the active route. The legacy path `/butlers/relationship/contacts/:id` is not
registered. This requirement formalizes what the router already does and adds the
redirect requirement for any external links that may have been bookmarked.

#### Scenario: Canonical URL for contact detail

- **WHEN** a user navigates to `/contacts/abc-123-uuid`
- **THEN** the contact detail page MUST render for contact `abc-123-uuid`
- **AND** the URL in the address bar MUST remain `/contacts/abc-123-uuid`

#### Scenario: Legacy URL redirects to canonical

- **WHEN** a user navigates to `/butlers/relationship/contacts/abc-123-uuid`
- **THEN** the client-side router MUST redirect to `/contacts/abc-123-uuid`
- **AND** the contact detail page MUST render for contact `abc-123-uuid`

#### Scenario: Internal contact links use canonical route

- **WHEN** a contact name is rendered as a navigation link anywhere in the dashboard
  (e.g., in the contacts table, in a relationship entry, in a notification)
- **THEN** the link target MUST be `/contacts/{contactId}`, not `/butlers/relationship/contacts/{id}`

---

### Requirement: Contact detail page conforms to the detail-page archetype

The contact detail page at `/contacts/:contactId` SHALL conform to the detail-page archetype
defined in the `detail-page-archetype` spec.

**Changes from the existing requirement (§Requirement: Contact detail page):**

1. **Shell adoption.** The page MUST use `<Page archetype="detail">` as its outer
   shell. The existing breadcrumbs block MUST be passed via the `breadcrumbs` prop.
   The inline three-skeleton loading block and the inline destructive-text error block
   MUST be removed from the page body and delegated to the `loading` and `error` props
   on `<Page>`.

2. **Title.** The `title` prop on `<Page>` MUST be the contact's full name
   (`first_name + " " + last_name`), consistent with the H1 already rendered inside
   `ContactDetailView`. If the contact has a `nickname`, it MUST be appended in
   parentheses: `"Alice Johnson (Allie)"`.

3. **Actions.** The edit and delete buttons currently inside `ContactDetailView`'s
   header (`ContactDetailView.tsx` lines 864–898) MUST be migrated to the `actions`
   prop on `<Page>` so they appear in the page header row. The `ContactDetailView`
   component body retains all other content.

4. **Body layout.** The `<ContactDetailView>` component output (minus the header
   card's edit/delete buttons) becomes the `primary` body slot inside the shell.

5. **Token cleanup status.** The hex-literal color palettes previously at
   `ContactDetailView.tsx` lines 53–62 and 69–77 have already been replaced with
   CSS custom properties (`var(--category-*)` and `var(--role-*)`) as of the migration
   in ce185209 (role badge hex → CSS tokens). No token-cleanup prerequisite remains
   for this migration step. Implementers should verify no new hex literals were
   introduced during the archetype migration.

#### Scenario: Contact detail uses shell loading state

- **WHEN** `GET /api/butlers/relationship/contacts/:id` is in flight
- **THEN** the `<Page>` shell MUST show `DetailSkeleton`
- **AND** no inline `<Skeleton>` blocks MUST be rendered by the page at the page layer

#### Scenario: Contact detail uses shell error state

- **WHEN** the contact fetch fails
- **THEN** the `<Page>` shell MUST render the destructive error card
- **AND** no inline destructive-text block MUST be rendered at the page layer

#### Scenario: Contact detail title shows full name with nickname

- **WHEN** a contact has `first_name = "Alice"`, `last_name = "Johnson"`, and
  `nickname = "Allie"`
- **THEN** the `<h1>` rendered by the shell MUST read "Alice Johnson (Allie)"

#### Scenario: Contact detail title shows full name without nickname

- **WHEN** a contact has `first_name = "Bob"`, `last_name = "Smith"`, and no nickname
- **THEN** the `<h1>` rendered by the shell MUST read "Bob Smith"

#### Scenario: Contact edit and delete actions in page header

- **WHEN** a contact detail page renders a resolved contact
- **THEN** the edit button and the delete button MUST appear in the page header row
  (via the `actions` prop), visible without scrolling
- **AND** they MUST NOT appear only inside the `<ContactDetailView>` card body

#### Scenario: No hex literals for role badge colors

- **WHEN** a contact has a role badge (e.g., "owner") rendered on the detail page
- **THEN** the badge color MUST use a CSS custom property or Tailwind semantic token
- **AND** the badge MUST NOT be styled with an inline `style={{ backgroundColor: "#..." }}`

---

### Requirement: Secured contact info reveal API

The dashboard API SHALL expose `GET /api/contacts/{id}/secrets/{info_id}` which returns the unmasked value of a secured `contact_info` entry.

#### Scenario: Reveal a secured value

- **WHEN** `GET /api/contacts/abc-123/secrets/info-456` is called for a `contact_info` entry with `secured = true`
- **THEN** the API MUST return the actual `value` of the entry
- **AND** the response status MUST be 200

#### Scenario: Non-secured entry returns normally

- **WHEN** `GET /api/contacts/abc-123/secrets/info-456` is called for a `contact_info` entry with `secured = false`
- **THEN** the API MUST return the `value` as-is
- **AND** the response status MUST be 200

#### Scenario: Entry does not exist

- **WHEN** `GET /api/contacts/abc-123/secrets/nonexistent-id` is called
- **THEN** the API MUST return a 404 response

#### Scenario: Entry belongs to different contact

- **WHEN** `GET /api/contacts/abc-123/secrets/info-456` is called but `info-456` belongs to contact `def-789`
- **THEN** the API MUST return a 404 response

---

### Requirement: Owner identity and credential management via contact detail page

The contact detail page (`/butlers/relationship/contacts/:id`) SHALL be the primary mechanism for configuring owner identity fields and credentials. The "Add contact info" form on the contact detail page MUST support all identity and credential types, including secured types (`email_password`, `telegram_api_id`, `telegram_api_hash`).

When a secured type is selected, the form MUST:
- Use a password input field to mask the value during entry
- Automatically set `secured = true` on the created `contact_info` entry
- Hide the "Primary" checkbox (not applicable to credential entries)

The form MUST display human-friendly labels for all types (e.g., "Email password", "Telegram API ID", "Telegram API hash", "Telegram chat ID").

#### Scenario: Add a secured credential from the contact detail page

- **WHEN** a user opens the owner contact's detail page and clicks "Add contact info"
- **AND** selects "Email password" from the type dropdown and enters a value
- **THEN** the input field MUST be a password field (masked)
- **AND** the created `contact_info` entry MUST have `secured = true`
- **AND** the entry MUST appear in the contact info list with a masked value and a "Reveal" button

#### Scenario: Add a non-secured identity field from the contact detail page

- **WHEN** a user adds a `telegram` or `email` entry via the contact detail page form
- **THEN** the input field MUST be a text field (not masked)
- **AND** the created `contact_info` entry MUST have `secured = false`

---

### Requirement: Owner identity setup banner

The dashboard SHALL display a persistent banner on the contacts page (`/butlers/relationship/contacts`) when the owner contact is missing key identity fields (name, email, telegram handle, or telegram chat ID). The banner provides a one-time onboarding dialog as a convenience; the contact detail page is the canonical location for ongoing identity and credential management.

#### Scenario: Banner shown when owner has missing identity fields

- **WHEN** a user navigates to the contacts page and the owner contact is missing any of: name, email, telegram handle, or telegram chat ID
- **THEN** a banner MUST be displayed indicating which fields are missing
- **AND** a "Set Up Identity" button MUST open a dialog for filling in missing fields

#### Scenario: Banner hidden when all identity fields are configured

- **WHEN** the owner contact has name, email, telegram handle, and telegram chat ID configured
- **THEN** the setup banner MUST NOT be displayed

#### Scenario: Banner dialog includes credentials section

- **WHEN** the owner setup dialog is opened
- **THEN** a collapsible "Credentials" section MUST be available for optionally setting email password, Telegram API ID, and Telegram API hash
- **AND** these credential fields MUST create secured `contact_info` entries

---

### Requirement: Pending identities queue on contacts page

The contacts page SHALL display a "Pending Identities" section listing all contacts with `metadata.needs_disambiguation = true`. This section MUST appear above the main contacts table when pending contacts exist.

#### Scenario: Pending identities displayed

- **WHEN** a user navigates to `/butlers/relationship/contacts` and 2 temporary contacts exist with `metadata.needs_disambiguation = true`
- **THEN** a "Pending Identities" section MUST appear above the contacts table
- **AND** each pending contact MUST display the contact's name, source channel, source value, and creation date

#### Scenario: Merge action on pending identity

- **WHEN** the user clicks "Merge" on a pending identity
- **THEN** a dialog MUST open with a contact search/select input
- **AND** the user MUST be able to search existing contacts by name
- **AND** selecting a contact and confirming MUST call the merge API
- **AND** the pending identity MUST disappear from the queue after successful merge

#### Scenario: Confirm as new action on pending identity

- **WHEN** the user clicks "Confirm as new" on a pending identity
- **THEN** the `needs_disambiguation` flag MUST be removed from the contact's metadata
- **AND** the contact MUST move to the main contacts table

#### Scenario: Archive action on pending identity

- **WHEN** the user clicks "Archive" on a pending identity
- **THEN** the contact's `listed` MUST be set to `false`
- **AND** the pending identity MUST disappear from the queue

#### Scenario: No pending identities

- **WHEN** no contacts have `metadata.needs_disambiguation = true`
- **THEN** the "Pending Identities" section MUST NOT be displayed

---

### Requirement: Dashboard roles management API

The dashboard API SHALL expose `PATCH /api/contacts/{id}` which allows updating contact fields including `roles`. This is the sole endpoint through which `roles` can be modified.

#### Scenario: Update contact roles

- **WHEN** `PATCH /api/contacts/abc-123` is called with `{"roles": ["owner"]}`
- **THEN** the contact's `roles` column MUST be updated to `['owner']`
- **AND** the response MUST include the updated contact with the new roles
- **AND** the response status MUST be 200

#### Scenario: Update non-role fields

- **WHEN** `PATCH /api/contacts/abc-123` is called with `{"first_name": "Alice"}`
- **THEN** the contact's `first_name` MUST be updated
- **AND** the `roles` column MUST NOT be modified

#### Scenario: Contact does not exist

- **WHEN** `PATCH /api/contacts/nonexistent-uuid` is called
- **THEN** the API MUST return a 404 response

---

### Requirement: Memory entity page links to relationship activity

The memory butler entity surfaces (`/entities` and `/entities/:id`, rendered by `EntitiesPage.tsx` and `EntityDetailPage.tsx`) SHALL surface a navigation link to `/butlers/relationship/entities/:id` for every entity whose `entity_type` is `'person'`. This closes the bidirectional bridge with the existing `EntityDetailView → /entities/:id` "View identity →" link mandated for the relationship-scoped entity page. The relationship butler writes facts only against `person` entities (every relationship-domain row in `predicate-taxonomy.md` Part 2 columns its target entity type as `person`); for `organization`, `place`, and `other` entity types the bridge MUST NOT direct users to a page that will render with all five tabs empty.

The pages MUST render the link as follows:

1. **On `/entities/:id` (entity detail page)** — the link MUST appear inside the main entity card, positioned after the Roles block and before the Source Provenance block. The link text MUST be "View relationship activity →" and MUST point to `/butlers/relationship/entities/:entityId`. Styling MUST mirror the `ContactDetailView` "View entity activity →" link (`text-primary text-sm font-medium hover:underline`). The link MUST be rendered only when `entity.entity_type === 'person'`; for other entity types the link MUST be omitted (matching the conditional-render convention used by the "Mark as confirmed" CTA on the same card).

2. **On `/entities` (entity listing page)** — every table row MUST include an icon button in the row-actions cluster, positioned immediately adjacent to the existing Edit action. The button MUST be enabled with tooltip "Open relationship activity" when the row's `entity_type === 'person'`, and rendered disabled with tooltip "Not a person entity" otherwise. Activating the enabled button MUST navigate to `/butlers/relationship/entities/:id` for that row's entity. This always-rendered, conditionally-disabled pattern MUST mirror the existing "View contact" UserIcon button on the same row.

#### Scenario: Entity detail page surfaces relationship activity link for a person

- **WHEN** a user navigates to `/entities/ent-456-uuid` and the entity has `entity_type = 'person'`
- **THEN** the main entity card MUST include a "View relationship activity →" link
- **AND** the link target MUST be `/butlers/relationship/entities/ent-456-uuid`
- **AND** the link MUST be positioned after the Roles block and before the Source Provenance block

#### Scenario: Entity detail page omits link for organization entity

- **WHEN** a user navigates to `/entities/org-789-uuid` and the entity has `entity_type = 'organization'`
- **THEN** the main entity card MUST NOT contain a "View relationship activity →" link
- **AND** the rest of the page MUST render normally

#### Scenario: Entity detail page omits link for place entity

- **WHEN** a user navigates to `/entities/loc-111-uuid` and the entity has `entity_type = 'place'`
- **THEN** the main entity card MUST NOT contain a "View relationship activity →" link
- **AND** the rest of the page MUST render normally

#### Scenario: Entity detail page omits link for other entity

- **WHEN** a user navigates to `/entities/oth-222-uuid` and the entity has `entity_type = 'other'`
- **THEN** the main entity card MUST NOT contain a "View relationship activity →" link
- **AND** the rest of the page MUST render normally

#### Scenario: Entities listing renders enabled action for person row

- **WHEN** a user navigates to `/entities` and a row in the table has `entity_type = 'person'`
- **THEN** the row's actions cluster MUST contain an enabled icon button
- **AND** the button's tooltip MUST read "Open relationship activity"
- **AND** clicking the button MUST navigate to `/butlers/relationship/entities/:id` for that row's entity

#### Scenario: Entities listing renders disabled action for non-person row

- **WHEN** a user navigates to `/entities` and a row in the table has `entity_type` other than `'person'` (e.g., `'organization'`, `'place'`, `'other'`)
- **THEN** the row's actions cluster MUST still contain the icon button (rendered, not omitted)
- **AND** the button MUST be disabled
- **AND** the button's tooltip MUST read "Not a person entity"
- **AND** clicking the disabled button MUST NOT navigate

### Requirement: Entity detail page

The frontend SHALL render an entity detail page at `/butlers/relationship/entities/:id` displaying the entity's identity header and a unified activity stream. This is the canonical surface for browsing notes, interactions, gifts, loans, and life events for any entity in `public.entities`. This surface coexists with the memory butler's identity-focused entity detail page at `/entities/:id`; the two pages serve different audiences (relationship browsing vs. credential and identity admin) and deep-link to each other.

The page MUST contain:

1. **Header card** — displaying `canonical_name`, `entity_type`, `aliases` (as chips), and `roles` as colored badges. If the entity has `metadata->>'unidentified' = 'true'`, an "Unidentified" badge MUST be shown. The header MUST include a "View identity →" link to `/entities/:id`.

2. **Linked contacts section** — listing all rows in `public.contacts` where `entity_id` matches, with each row showing `first_name + last_name`, primary `contact_info` entries (one email/phone), and a link to the contact detail page.

3. **Unified ActivityTimeline** — a single vertically-scrolling event stream sourced from the entity timeline endpoint (`GET /api/butlers/relationship/entities/{id}/timeline`). The stream MUST display all supported event kinds: interactions, notes, gifts, loans, and life events. Filter pills at the top of the stream allow the user to narrow the view to a single event kind. The active filter is single-select: pills are: **All**, **Interactions**, **Notes**, **Gifts**, **Loans**, **Life events**. Selecting a pill hides all other kinds in the stream (client-side filtering; no additional API call). Empty stream state MUST display an appropriate message (e.g., "No activity recorded yet." when All is active, or "No interactions yet." when a specific kind pill is active). The stream MUST be sorted `valid_at DESC` with `created_at DESC` as a tie-break, consistent with the timeline endpoint sort contract.

4. **Gifts panel** — a structured display of gift-scoped facts (gifts with occasion, status, and description). This panel MAY be hidden when empty. This is a separate surface complementing the unified ActivityTimeline above; the timeline includes gifts mixed with other event kinds, whereas this panel dedicates focus to gifts alone.

5. **Loans panel** — a structured display of loan-scoped facts (loans with amount, direction, settlement status, and description). This panel MAY be hidden when empty. Like the Gifts panel above, this surface coexists with the unified ActivityTimeline.

#### Scenario: Entity detail page renders with unified timeline

- **WHEN** a user navigates to `/butlers/relationship/entities/ent-456-uuid` and the entity exists
- **THEN** the header card MUST display the entity's `canonical_name`, `entity_type`, and any `roles`
- **AND** the linked contacts section MUST list all contacts whose `entity_id` matches
- **AND** the ActivityTimeline MUST be rendered with "All" pill active and all event kinds visible

#### Scenario: Filter pill narrows the stream

- **WHEN** a user activates the "Interactions" pill on the ActivityTimeline
- **THEN** only events with `kind = "interaction"` MUST be visible in the stream
- **AND** no additional API call MUST be issued (filtering is client-side)
- **AND** the "Interactions" pill MUST appear in the active state and all other pills MUST appear inactive

#### Scenario: Entity not found

- **WHEN** a user navigates to `/butlers/relationship/entities/nonexistent-uuid`
- **THEN** the page MUST display a 404 message (e.g., "Entity not found")

#### Scenario: Entity with no facts

- **WHEN** a user navigates to an entity that has zero matching facts
- **THEN** the ActivityTimeline MUST display the empty-state message "No activity recorded yet."
- **AND** the page MUST render without errors

#### Scenario: Unidentified entity badge

- **WHEN** an entity has `metadata->>'unidentified' = 'true'`
- **THEN** the header card MUST display an "Unidentified" badge
- **AND** the rest of the page MUST render normally

---

> **Design history:** Originally specced as five separate tabs (Notes / Interactions / Gifts / Loans / Timeline); consolidated into one filterable stream per shipped UX. See bu-afx6k for the design-update decision.

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

---

