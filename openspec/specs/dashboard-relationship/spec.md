## MODIFIED Requirements

### Requirement: Contact detail API

The dashboard API SHALL expose `GET /api/butlers/relationship/contacts/:id` which returns a single contact's full record with joined data from related tables via a direct database read. The query MUST read from `shared.contacts` (not `relationship.contacts`).

The response MUST include:
- All columns from the `shared.contacts` table (`id`, `first_name`, `last_name`, `nickname`, `company`, `job_title`, `gender`, `pronouns`, `avatar_url`, `listed`, `metadata`, `roles`, `entity_id`, `created_at`, `updated_at`)
- `info` (array) -- all rows from `shared.contact_info` for this contact, each containing `id`, `type`, `value` (masked as `"********"` if `secured = true`), `label`, `secured`, `created_at`
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

## ADDED Requirements

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
