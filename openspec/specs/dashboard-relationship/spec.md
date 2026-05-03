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

The frontend SHALL render a contact detail page at `/butlers/relationship/contacts/:id` displaying the full contact record with all related data.

The page MUST contain the following sections:

1. **Header card** -- displaying the contact's full name (`first_name` + `last_name`), `company`, `job_title`, `pronouns`, `avatar_url` (as an image if present, or initials fallback), and `roles` as colored role badges (e.g., "owner" badge in a distinct color). Assigned labels SHALL be rendered as colored badges. The `nickname` SHALL be displayed in parentheses after the name if present.

2. **Contact info section** -- displaying all `contact_info` entries grouped by `type` (email, phone, social, etc.). Each entry SHALL show the `label` (e.g., "work", "personal") and the `value`. Entries with `secured = true` SHALL display the value as `"********"` with a click-to-reveal button that fetches the actual value from `GET /api/contacts/{id}/secrets/{info_id}`. Values of type `email` SHALL be rendered as `mailto:` links. Values of type `phone` SHALL be rendered as `tel:` links.

3. **Important dates section** -- displaying all important dates with a countdown indicator. Each date SHALL show the `label`, the date (`day`/`month`/`year` formatted, with "unknown year" if `year` is null), and the number of days until the next occurrence. Dates occurring within the next 7 days SHALL be visually highlighted (e.g., with a colored badge or accent).

4. **Quick facts section** -- displaying all quick facts, grouped or tagged by `category`. Each fact SHALL show the `category` label and the `content` text.

5. **Relationships section** -- displaying all relationships as a list. Each entry SHALL show the `type` label (e.g., "Parent", "Friend"), the related contact's name as a clickable link to their detail page, and the `group_type`.

6. **Tabbed content area** -- containing four tabs:
   - **Notes** -- paginated list of notes loaded from `/contacts/:id/notes`. Each note SHALL display `title` (if present), `body` text, `emotion` as a colored indicator (green for positive, gray for neutral, red for negative), and `created_at` as a relative timestamp.
   - **Interactions** -- paginated list of interactions loaded from `/contacts/:id/interactions`. Each interaction SHALL display `type` as an icon or badge, `direction` (inbound/outbound), `summary`, `duration_minutes` (if present), and `occurred_at` as a relative timestamp. A type filter dropdown SHALL allow filtering by interaction type.
   - **Gifts** -- paginated list of gifts loaded from `/contacts/:id/gifts`. Each gift SHALL display `name`, `description`, `status` as a colored badge (pipeline stages), `occasion`, `estimated_price_cents` formatted as currency, `url` as a clickable link (if present), and `created_at`.
   - **Loans** -- paginated list of loans loaded from `/contacts/:id/loans`. Each loan SHALL display `name`, `amount_cents` formatted as currency with `currency` code, the other party's name (lender or borrower), `settled` status as a badge, `loaned_at`, and `settled_at` (if settled).

7. **Activity feed sidebar or section** -- displaying the contact's activity feed loaded from `/contacts/:id/feed`, ordered by `created_at` descending. Each entry SHALL display the `action` as a human-readable label, `summary` text, and `created_at` as a relative timestamp. The feed SHALL support "load more" pagination.

#### Scenario: Contact detail page loads with roles and secured info

- **WHEN** a user navigates to `/butlers/relationship/contacts/abc-123-uuid` for the owner contact
- **THEN** the header card MUST display the contact's name with an "owner" role badge
- **AND** contact info entries with `secured = true` MUST display masked values with reveal buttons

#### Scenario: Click-to-reveal secured credential

- **WHEN** a user clicks the reveal button on a secured `contact_info` entry
- **THEN** the frontend MUST call `GET /api/contacts/{id}/secrets/{info_id}`
- **AND** the masked value MUST be replaced with the actual value
- **AND** a hide button MUST appear to re-mask the value

#### Scenario: Contact with no data in some sections

- **WHEN** a user navigates to a contact's detail page and the contact has no notes, interactions, gifts, or loans
- **THEN** each empty tab MUST display an appropriate empty state message (e.g., "No notes yet", "No interactions recorded", "No gift ideas", "No loans")
- **AND** the page MUST render without errors

#### Scenario: Email and phone values are clickable

- **WHEN** a contact has a `contact_info` entry with `type='email'` and `value='alice@example.com'`
- **THEN** the value MUST be rendered as a clickable `mailto:alice@example.com` link
- **WHEN** a contact has a `contact_info` entry with `type='phone'` and `value='+1-555-0100'`
- **THEN** the value MUST be rendered as a clickable `tel:+1-555-0100` link

#### Scenario: Contact not found

- **WHEN** a user navigates to `/butlers/relationship/contacts/nonexistent-uuid` and the contact does not exist
- **THEN** the page MUST display a 404 message (e.g., "Contact not found")

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
