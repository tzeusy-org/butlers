# Relationship Butler (Personal CRM)

The Relationship butler is a personal CRM inspired by [Monica CRM](https://www.monicahq.com/). It tracks contacts, relationships, interactions, important dates, reminders, gifts, loans, and more. It runs as a standalone butler daemon on port 40102 with its own dedicated PostgreSQL database (`butler_relationship`). All functionality is implemented via dedicated schema and MCP tools — the Relationship butler has no modules.

## Configuration

```toml
[butler]
name = "relationship"
description = "Personal CRM. Manages contacts, relationships, important dates, interactions, gifts, and reminders."
port = 40102

[butler.db]
name = "butler_relationship"

[[butler.schedule]]
name = "upcoming-dates-check"
cron = "0 8 * * *"
prompt = """
Check for important dates in the next 7 days (birthdays, anniversaries).
For each, draft a reminder message and store it in state for the Switchboard
to deliver via Telegram.
"""

[[butler.schedule]]
name = "relationship-maintenance"
cron = "0 9 * * 1"
prompt = """
Review contacts I haven't interacted with in 30+ days.
Suggest 3 people I should reach out to this week, with context
on our last interaction and any upcoming dates.
"""
```

## Database Schema

```sql
-- Core tables (from framework: state, scheduled_tasks, sessions)

-- Contacts
CREATE TABLE contacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    first_name TEXT,
    last_name TEXT,
    nickname TEXT,
    company TEXT,
    job_title TEXT,
    gender TEXT,
    pronouns TEXT,
    avatar_url TEXT,
    listed BOOLEAN NOT NULL DEFAULT true,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Contact information (email, phone, social, etc.)
CREATE TABLE contact_info (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    type TEXT NOT NULL,             -- 'email', 'phone', 'telegram', 'linkedin', etc.
    value TEXT NOT NULL,
    label TEXT,                     -- 'work', 'personal', 'home'
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Typed, bidirectional relationships between contacts
CREATE TABLE relationships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    related_contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    group_type TEXT NOT NULL,       -- 'love', 'family', 'friend', 'work'
    type TEXT NOT NULL,             -- 'spouse', 'parent', 'child', 'colleague', etc.
    reverse_type TEXT NOT NULL,     -- the other direction: 'child' if type is 'parent'
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(contact_id, related_contact_id, type)
);

-- Important dates (birthdays, anniversaries, etc.)
CREATE TABLE important_dates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    label TEXT NOT NULL,            -- 'birthday', 'anniversary', 'deceased', custom
    day INT,                        -- nullable for partial dates
    month INT,
    year INT,                       -- nullable (e.g., birthday with unknown year)
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Notes per contact
CREATE TABLE notes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    title TEXT,
    body TEXT NOT NULL,
    emotion TEXT,                   -- 'positive', 'neutral', 'negative'
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Interaction log (calls, meetings, messages)
CREATE TABLE interactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    type TEXT NOT NULL,             -- 'call', 'video', 'meeting', 'message', 'email'
    direction TEXT,                 -- 'inbound', 'outbound'
    summary TEXT,
    duration_minutes INT,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata JSONB NOT NULL DEFAULT '{}'
);

-- Reminders (one-time or recurring)
CREATE TABLE reminders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID REFERENCES contacts(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'one_time',  -- 'one_time', 'recurring_yearly', 'recurring_monthly'
    next_trigger_at TIMESTAMPTZ,
    last_triggered_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Gift tracking (idea -> bought -> given pipeline)
CREATE TABLE gifts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'idea',  -- 'idea', 'searched', 'found', 'bought', 'given'
    occasion TEXT,                         -- 'birthday', 'christmas', 'just_because'
    estimated_price_cents INT,
    url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Loans and debts
CREATE TABLE loans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lender_contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    borrower_contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    amount_cents INT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    loaned_at TIMESTAMPTZ,
    settled BOOLEAN NOT NULL DEFAULT false,
    settled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Groups (families, friend circles, teams)
CREATE TABLE groups (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    type TEXT,                      -- 'family', 'couple', 'friends', 'team'
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE group_members (
    group_id UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    role TEXT,                      -- 'parent', 'child', 'partner', etc.
    PRIMARY KEY (group_id, contact_id)
);

-- Labels (color-coded tags)
CREATE TABLE labels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT UNIQUE NOT NULL,
    color TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE contact_labels (
    contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    label_id UUID NOT NULL REFERENCES labels(id) ON DELETE CASCADE,
    PRIMARY KEY (contact_id, label_id)
);

-- Quick facts (key-value per contact: hobbies, food preferences, etc.)
CREATE TABLE quick_facts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    category TEXT NOT NULL,         -- 'hobbies', 'food', 'custom'
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Addresses
CREATE TABLE addresses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    type TEXT,                      -- 'home', 'work', 'other'
    line_1 TEXT,
    line_2 TEXT,
    city TEXT,
    province TEXT,
    postal_code TEXT,
    country TEXT,
    is_current BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Activity feed (polymorphic log of all changes per contact)
CREATE TABLE contact_feed (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    action TEXT NOT NULL,           -- 'note_created', 'interaction_logged', 'gift_added', etc.
    entity_type TEXT,               -- 'note', 'interaction', 'gift', etc.
    entity_id UUID,
    summary TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_contacts_name ON contacts(first_name, last_name);
CREATE INDEX idx_contact_info_type ON contact_info(contact_id, type);
CREATE INDEX idx_important_dates_month ON important_dates(month, day);
CREATE INDEX idx_interactions_contact ON interactions(contact_id, occurred_at DESC);
CREATE INDEX idx_notes_contact ON notes(contact_id, created_at DESC);
CREATE INDEX idx_contact_feed_contact ON contact_feed(contact_id, created_at DESC);
```

## MCP Tools

| Category | Tools |
|----------|-------|
| **Contacts** | `contact_create`, `contact_update`, `contact_get`, `contact_search`, `contact_archive` |
| **Relationships** | `relationship_add`, `relationship_list`, `relationship_remove` |
| **Important Dates** | `date_add`, `date_list`, `upcoming_dates` |
| **Notes** | `note_create`, `note_list`, `note_search` |
| **Interactions** | `interaction_log`, `interaction_list` |
| **Reminders** | `reminder_create`, `reminder_list`, `reminder_dismiss` |
| **Gifts** | `gift_add`, `gift_update_status`, `gift_list` |
| **Loans** | `loan_create`, `loan_settle`, `loan_list` |
| **Groups** | `group_create`, `group_add_member`, `group_list` |
| **Labels** | `label_create`, `label_assign`, `contact_search_by_label` |
| **Quick Facts** | `fact_set`, `fact_list` |
| **Activity Feed** | `feed_get` |

## ADDED Requirements

### Requirement: Relationship butler schema provisioning

The Relationship butler's database tables SHALL be created during butler startup as Alembic revisions in the `relationship` version chain, applied after the core Alembic chain. The schema MUST include the tables `contacts`, `contact_info`, `relationships`, `important_dates`, `notes`, `interactions`, `reminders`, `gifts`, `loans`, `groups`, `group_members`, `labels`, `contact_labels`, `quick_facts`, `addresses`, and `contact_feed`, along with all associated indexes.

#### Scenario: Butler starts with a fresh database

WHEN the Relationship butler starts against a newly provisioned `butler_relationship` database
THEN all Relationship-specific tables MUST exist with the columns and constraints defined in the schema
AND the core tables (`state`, `scheduled_tasks`, `sessions`) MUST also exist
AND all indexes (`idx_contacts_name`, `idx_contact_info_type`, `idx_important_dates_month`, `idx_interactions_contact`, `idx_notes_contact`, `idx_contact_feed_contact`) MUST be created

#### Scenario: Butler starts with no modules

WHEN the Relationship butler starts
THEN it SHALL load zero modules
AND only core MCP tools and Relationship-specific MCP tools SHALL be available

---

### Requirement: Scheduled task bootstrap

The Relationship butler SHALL define two scheduled tasks in its `butler.toml` that are synced to the `scheduled_tasks` table on startup.

#### Scenario: upcoming-dates-check task is bootstrapped

WHEN the Relationship butler starts
THEN the `scheduled_tasks` table MUST contain a row with `name='upcoming-dates-check'`, `source='toml'`, `enabled=true`, and `cron='0 8 * * *'`
AND `next_run_at` MUST be computed from the cron expression

#### Scenario: relationship-maintenance task is bootstrapped

WHEN the Relationship butler starts
THEN the `scheduled_tasks` table MUST contain a row with `name='relationship-maintenance'`, `source='toml'`, `enabled=true`, and `cron='0 9 * * 1'`
AND `next_run_at` MUST be computed from the cron expression

---

### Requirement: contact_create creates a new contact

The `contact_create` MCP tool SHALL accept optional parameters `first_name`, `last_name`, `nickname`, `company`, `job_title`, `gender`, `pronouns`, `avatar_url`, and `metadata`, and insert a new row into the `contacts` table with `listed=true`.

The tool SHALL return a JSON object containing the full contact record including the generated `id`, `created_at`, and `updated_at`.

#### Scenario: Creating a contact with basic fields

WHEN `contact_create(first_name="Alice", last_name="Smith", company="Acme")` is called
THEN a new row MUST be inserted into the `contacts` table with `first_name='Alice'`, `last_name='Smith'`, `company='Acme'`, and `listed=true`
AND the tool MUST return a JSON object containing the `id`, `first_name`, `last_name`, `company`, `listed`, `created_at`, and `updated_at`

#### Scenario: Creating a contact with minimal fields

WHEN `contact_create(first_name="Bob")` is called with only a first name
THEN a new row MUST be inserted with `first_name='Bob'` and all other optional fields as their defaults (NULL or empty)
AND `listed` MUST be `true`
AND the tool MUST return a JSON object containing the generated `id`

#### Scenario: Creating a contact populates the activity feed

WHEN `contact_create(first_name="Alice")` is called
THEN a row MUST be inserted into the `contact_feed` table with `contact_id` set to the new contact's `id`, `action='contact_created'`, `entity_type='contact'`, and `entity_id` set to the new contact's `id`

---

### Requirement: contact_update modifies an existing contact

The `contact_update` MCP tool SHALL accept a `contact_id` (UUID) and optional fields (`first_name`, `last_name`, `nickname`, `company`, `job_title`, `gender`, `pronouns`, `avatar_url`, `metadata`). At least one field besides `contact_id` MUST be provided.

The tool SHALL update only the provided fields and set `updated_at` to the current timestamp. The tool SHALL return the updated contact record as a JSON object.

#### Scenario: Updating a contact's company

WHEN `contact_update(contact_id=<uuid>, company="NewCorp")` is called with a valid contact ID
THEN the contact's `company` MUST be updated to `"NewCorp"`
AND `updated_at` MUST be set to the current timestamp
AND all other fields MUST remain unchanged
AND the tool MUST return the full updated contact as a JSON object

#### Scenario: Updating a non-existent contact

WHEN `contact_update(contact_id=<nonexistent-uuid>, first_name="X")` is called
THEN the tool MUST return an error indicating the contact was not found

---

### Requirement: contact_get retrieves a single contact

The `contact_get` MCP tool SHALL accept a `contact_id` (UUID) and return the full contact record as a JSON object.

#### Scenario: Retrieving an existing contact

WHEN `contact_get(contact_id=<uuid>)` is called with an ID that exists in the `contacts` table
THEN the tool MUST return a JSON object containing all columns of the contact row

#### Scenario: Retrieving a non-existent contact

WHEN `contact_get(contact_id=<nonexistent-uuid>)` is called
THEN the tool MUST return null
AND it MUST NOT raise an error

---

### Requirement: contact_search supports full-text search

The `contact_search` MCP tool SHALL accept a `query` string and return a list of contacts where the query matches against `first_name`, `last_name`, `nickname`, or `company` using case-insensitive partial matching. Only contacts with `listed=true` SHALL be returned.

The tool SHALL accept optional `limit` (default 20) and `offset` (default 0) parameters for pagination.

#### Scenario: Searching by first name

WHEN the `contacts` table contains Alice Smith and Bob Jones
AND `contact_search(query="alice")` is called
THEN the tool MUST return a list containing only Alice Smith
AND each result MUST be a JSON object with the contact's full record

#### Scenario: Searching by company

WHEN the `contacts` table contains Alice (company="Acme") and Bob (company="Globex")
AND `contact_search(query="acme")` is called
THEN the tool MUST return a list containing only Alice

#### Scenario: Search excludes archived contacts

WHEN the `contacts` table contains Alice with `listed=true` and Bob with `listed=false`
AND both have `first_name` matching the query
AND `contact_search(query="b")` is called matching Bob
THEN the tool MUST return an empty list (Bob is archived)

#### Scenario: Search with no results

WHEN `contact_search(query="zzzzz")` is called and no contacts match
THEN the tool MUST return an empty list
AND it MUST NOT raise an error

#### Scenario: Search with pagination

WHEN the `contacts` table contains 30 contacts matching the query
AND `contact_search(query="a", limit=10, offset=10)` is called
THEN the tool MUST return at most 10 contacts, skipping the first 10 matches

---

### Requirement: contact_archive soft-deletes a contact

The `contact_archive` MCP tool SHALL accept a `contact_id` (UUID) and set `listed=false` on the corresponding contact row. This is a soft delete — the row MUST NOT be removed from the database.

#### Scenario: Archiving an existing contact

WHEN `contact_archive(contact_id=<uuid>)` is called with a valid contact ID
THEN the contact's `listed` field MUST be set to `false`
AND `updated_at` MUST be set to the current timestamp
AND the row MUST still exist in the `contacts` table

#### Scenario: Archiving a non-existent contact

WHEN `contact_archive(contact_id=<nonexistent-uuid>)` is called
THEN the tool MUST return an error indicating the contact was not found

#### Scenario: Archived contact excluded from search

WHEN a contact has been archived via `contact_archive`
AND `contact_search` is called with a query matching that contact
THEN the archived contact MUST NOT appear in the results

---

### Requirement: relationship_add creates a bidirectional relationship

The `relationship_add` MCP tool SHALL accept `contact_id`, `related_contact_id`, `group_type`, `type`, and `reverse_type` parameters. It SHALL insert two rows into the `relationships` table: one from `contact_id` to `related_contact_id` with the given `type`, and one from `related_contact_id` to `contact_id` with the given `reverse_type`.

#### Scenario: Adding a parent-child relationship

WHEN `relationship_add(contact_id=<alice>, related_contact_id=<bob>, group_type="family", type="parent", reverse_type="child")` is called
THEN the `relationships` table MUST contain a row with `contact_id=<alice>`, `related_contact_id=<bob>`, `type="parent"`, `reverse_type="child"`, `group_type="family"`
AND it MUST also contain a row with `contact_id=<bob>`, `related_contact_id=<alice>`, `type="child"`, `reverse_type="parent"`, `group_type="family"`

#### Scenario: Adding a symmetric relationship

WHEN `relationship_add(contact_id=<alice>, related_contact_id=<bob>, group_type="friend", type="friend", reverse_type="friend")` is called
THEN both rows MUST be inserted with `type="friend"` and `reverse_type="friend"`

#### Scenario: Duplicate relationship rejected

WHEN a relationship of the same `type` already exists between `contact_id` and `related_contact_id`
AND `relationship_add` is called again with the same `contact_id`, `related_contact_id`, and `type`
THEN the tool MUST return an error indicating the relationship already exists
AND no duplicate rows SHALL be inserted

#### Scenario: Relationship creates activity feed entries

WHEN `relationship_add` is called successfully
THEN a `contact_feed` entry MUST be created for `contact_id` with `action='relationship_added'` and `entity_type='relationship'`
AND a `contact_feed` entry MUST be created for `related_contact_id` with `action='relationship_added'` and `entity_type='relationship'`

---

### Requirement: relationship_list returns relationships for a contact

The `relationship_list` MCP tool SHALL accept a `contact_id` (UUID) and return all relationships where that contact is the `contact_id` in the `relationships` table, as a list of JSON objects.

#### Scenario: Listing relationships for a contact with multiple relationships

WHEN Alice has a "parent" relationship with Bob and a "friend" relationship with Carol
AND `relationship_list(contact_id=<alice>)` is called
THEN the tool MUST return a list containing both relationships
AND each entry MUST include `id`, `contact_id`, `related_contact_id`, `group_type`, `type`, `reverse_type`, and `created_at`

#### Scenario: Listing relationships for a contact with no relationships

WHEN `relationship_list(contact_id=<uuid>)` is called for a contact with no relationships
THEN the tool MUST return an empty list
AND it MUST NOT raise an error

---

### Requirement: relationship_remove deletes a bidirectional relationship

The `relationship_remove` MCP tool SHALL accept a `relationship_id` (UUID) and delete both the specified relationship row and its reverse counterpart from the `relationships` table.

#### Scenario: Removing a relationship

WHEN a parent-child relationship exists between Alice and Bob (two rows in the `relationships` table)
AND `relationship_remove(relationship_id=<alice-to-bob-row-id>)` is called
THEN both the Alice-to-Bob row and the Bob-to-Alice row MUST be deleted from the `relationships` table

#### Scenario: Removing a non-existent relationship

WHEN `relationship_remove(relationship_id=<nonexistent-uuid>)` is called
THEN the tool MUST return an error indicating the relationship was not found

---

### Requirement: date_add creates an important date for a contact

The `date_add` MCP tool SHALL accept `contact_id`, `label`, and optional `day`, `month`, and `year` parameters. Partial dates are supported — `year` MAY be null (e.g., a birthday with unknown birth year).

The tool SHALL return the created important date record as a JSON object.

#### Scenario: Adding a birthday with known year

WHEN `date_add(contact_id=<uuid>, label="birthday", day=14, month=3, year=1990)` is called
THEN a new row MUST be inserted into `important_dates` with all fields set
AND the tool MUST return a JSON object containing the `id`, `contact_id`, `label`, `day`, `month`, `year`, and `created_at`

#### Scenario: Adding a birthday with unknown year

WHEN `date_add(contact_id=<uuid>, label="birthday", day=25, month=12)` is called without a year
THEN a new row MUST be inserted with `year` as NULL
AND the tool MUST return a JSON object with `year` as null

#### Scenario: Adding an important date creates a feed entry

WHEN `date_add` is called successfully
THEN a row MUST be inserted into `contact_feed` with `action='date_added'`, `entity_type='important_date'`, and `entity_id` set to the new date's `id`

---

### Requirement: date_list returns important dates for a contact

The `date_list` MCP tool SHALL accept a `contact_id` (UUID) and return all important dates for that contact as a list of JSON objects.

#### Scenario: Listing dates for a contact with multiple dates

WHEN a contact has a birthday and an anniversary in the `important_dates` table
AND `date_list(contact_id=<uuid>)` is called
THEN the tool MUST return a list containing both date records

#### Scenario: Listing dates for a contact with no dates

WHEN `date_list(contact_id=<uuid>)` is called for a contact with no important dates
THEN the tool MUST return an empty list

---

### Requirement: upcoming_dates finds dates within a time window

The `upcoming_dates` MCP tool SHALL accept an optional `days` parameter (default 7) and return all important dates whose `month` and `day` fall within the next `days` days from the current date. The comparison SHALL use month/day matching regardless of the `year` column, so recurring dates like birthdays are matched every year.

The tool SHALL return a list of JSON objects, each containing the important date record joined with the contact's `first_name`, `last_name`, and `contact_id`.

#### Scenario: Birthday within the next 7 days

WHEN today is February 9
AND a contact has an important date with `label="birthday"`, `month=2`, `day=14`
AND `upcoming_dates(days=7)` is called
THEN the result MUST include that contact's birthday record

#### Scenario: Date outside the window

WHEN today is February 9
AND a contact has an important date with `month=3`, `day=15`
AND `upcoming_dates(days=7)` is called
THEN the result MUST NOT include that date

#### Scenario: Year-end wrap-around

WHEN today is December 29
AND a contact has an important date with `month=1`, `day=3`
AND `upcoming_dates(days=7)` is called
THEN the result MUST include that date (January 3 is within 7 days of December 29)

#### Scenario: Dates with null year are still matched

WHEN a contact has an important date with `month=2`, `day=14`, `year=NULL`
AND `upcoming_dates(days=7)` is called on February 9
THEN the result MUST include that date

#### Scenario: No upcoming dates

WHEN no important dates fall within the next N days
AND `upcoming_dates(days=7)` is called
THEN the tool MUST return an empty list

---

### Requirement: note_create adds a note to a contact

The `note_create` MCP tool SHALL accept `contact_id`, `body`, and optional `title` and `emotion` parameters. Valid values for `emotion` are `'positive'`, `'neutral'`, and `'negative'`.

The tool SHALL return the created note record as a JSON object.

#### Scenario: Creating a note with all fields

WHEN `note_create(contact_id=<uuid>, title="Lunch catch-up", body="Had a great conversation about travel plans.", emotion="positive")` is called
THEN a new row MUST be inserted into the `notes` table with the provided values
AND the tool MUST return a JSON object containing `id`, `contact_id`, `title`, `body`, `emotion`, and `created_at`

#### Scenario: Creating a note with only required fields

WHEN `note_create(contact_id=<uuid>, body="Quick check-in")` is called
THEN a new row MUST be inserted with `title` as NULL and `emotion` as NULL

#### Scenario: Creating a note populates the activity feed

WHEN `note_create` is called successfully
THEN a row MUST be inserted into `contact_feed` with `action='note_created'`, `entity_type='note'`, and `entity_id` set to the new note's `id`

---

### Requirement: note_list returns notes for a contact

The `note_list` MCP tool SHALL accept a `contact_id` (UUID) and optional `limit` (default 20) and `offset` (default 0) parameters. It SHALL return notes ordered by `created_at` descending.

#### Scenario: Listing notes for a contact

WHEN a contact has 5 notes
AND `note_list(contact_id=<uuid>)` is called
THEN the tool MUST return all 5 notes ordered by `created_at` descending
AND each note MUST be a JSON object with all columns

#### Scenario: Listing notes with pagination

WHEN a contact has 25 notes
AND `note_list(contact_id=<uuid>, limit=10, offset=10)` is called
THEN the tool MUST return at most 10 notes, skipping the first 10

---

### Requirement: note_search searches notes by content

The `note_search` MCP tool SHALL accept a `query` string and optional `contact_id` (UUID) parameter. It SHALL return notes where the `body` or `title` contains the query string (case-insensitive partial match). If `contact_id` is provided, results SHALL be scoped to that contact.

#### Scenario: Searching notes across all contacts

WHEN `note_search(query="travel")` is called
AND two notes across different contacts contain "travel" in their body
THEN the tool MUST return both notes

#### Scenario: Searching notes scoped to a contact

WHEN `note_search(query="travel", contact_id=<uuid>)` is called
THEN the tool MUST return only notes belonging to that contact that match the query

#### Scenario: No matching notes

WHEN `note_search(query="xyznonexistent")` is called
THEN the tool MUST return an empty list

---

### Requirement: interaction_log records an interaction with a contact

The `interaction_log` MCP tool SHALL accept `contact_id`, `type`, and optional `direction`, `summary`, `duration_minutes`, `occurred_at`, and `metadata` parameters. Valid values for `type` are `'call'`, `'video'`, `'meeting'`, `'message'`, and `'email'`. Valid values for `direction` are `'inbound'` and `'outbound'`.

If `occurred_at` is not provided, it SHALL default to the current timestamp.

The tool SHALL return the created interaction record as a JSON object.

#### Scenario: Logging a phone call

WHEN `interaction_log(contact_id=<uuid>, type="call", direction="outbound", summary="Discussed project timeline", duration_minutes=30)` is called
THEN a new row MUST be inserted into the `interactions` table with the provided values
AND the tool MUST return a JSON object containing `id`, `contact_id`, `type`, `direction`, `summary`, `duration_minutes`, `occurred_at`, and `metadata`

#### Scenario: Logging an interaction with default occurred_at

WHEN `interaction_log(contact_id=<uuid>, type="message", direction="inbound")` is called without `occurred_at`
THEN `occurred_at` MUST default to approximately the current timestamp

#### Scenario: Logging an interaction populates the activity feed

WHEN `interaction_log` is called successfully
THEN a row MUST be inserted into `contact_feed` with `action='interaction_logged'`, `entity_type='interaction'`, and `entity_id` set to the new interaction's `id`

---

### Requirement: interaction_list returns interactions for a contact

The `interaction_list` MCP tool SHALL accept a `contact_id` (UUID) and optional `type`, `limit` (default 20), and `offset` (default 0) parameters. It SHALL return interactions ordered by `occurred_at` descending.

If `type` is provided, only interactions of that type SHALL be returned.

#### Scenario: Listing all interactions for a contact

WHEN a contact has 3 interactions (1 call, 1 meeting, 1 message)
AND `interaction_list(contact_id=<uuid>)` is called
THEN the tool MUST return all 3 interactions ordered by `occurred_at` descending

#### Scenario: Filtering interactions by type

WHEN a contact has 3 interactions (2 calls, 1 meeting)
AND `interaction_list(contact_id=<uuid>, type="call")` is called
THEN the tool MUST return only the 2 call interactions

#### Scenario: No interactions

WHEN `interaction_list(contact_id=<uuid>)` is called for a contact with no interactions
THEN the tool MUST return an empty list

---

### Requirement: reminder_create creates a reminder

The `reminder_create` MCP tool SHALL accept `label`, `type`, `next_trigger_at`, and optional `contact_id` parameters. Valid values for `type` are `'one_time'`, `'recurring_yearly'`, and `'recurring_monthly'`.

The tool SHALL return the created reminder record as a JSON object.

#### Scenario: Creating a one-time reminder

WHEN `reminder_create(contact_id=<uuid>, label="Send birthday card", type="one_time", next_trigger_at="2026-03-14T08:00:00Z")` is called
THEN a new row MUST be inserted into the `reminders` table with `type='one_time'` and the specified `next_trigger_at`
AND the tool MUST return a JSON object containing all columns

#### Scenario: Creating a recurring yearly reminder

WHEN `reminder_create(contact_id=<uuid>, label="Anniversary reminder", type="recurring_yearly", next_trigger_at="2026-06-15T08:00:00Z")` is called
THEN a new row MUST be inserted into the `reminders` table with `type='recurring_yearly'`

#### Scenario: Creating a reminder without a contact

WHEN `reminder_create(label="General check-in", type="one_time", next_trigger_at="2026-04-01T09:00:00Z")` is called without a `contact_id`
THEN a new row MUST be inserted with `contact_id` as NULL

---

### Requirement: reminder_list returns reminders

The `reminder_list` MCP tool SHALL accept optional `contact_id` and `include_dismissed` (default `false`) parameters. If `contact_id` is provided, only reminders for that contact SHALL be returned. By default, only reminders with `next_trigger_at` in the future (not yet dismissed or triggered) SHALL be returned.

#### Scenario: Listing active reminders for a contact

WHEN a contact has 2 active reminders (future `next_trigger_at`) and 1 past reminder
AND `reminder_list(contact_id=<uuid>)` is called
THEN the tool MUST return only the 2 active reminders

#### Scenario: Listing all reminders including dismissed

WHEN `reminder_list(contact_id=<uuid>, include_dismissed=true)` is called
THEN the tool MUST return all reminders for the contact regardless of `next_trigger_at`

#### Scenario: Listing all reminders across contacts

WHEN `reminder_list()` is called without a `contact_id`
THEN the tool MUST return all active reminders across all contacts

---

### Requirement: reminder_dismiss dismisses a reminder

The `reminder_dismiss` MCP tool SHALL accept a `reminder_id` (UUID). For `one_time` reminders, it SHALL set `last_triggered_at` to the current time and `next_trigger_at` to NULL. For `recurring_yearly` reminders, it SHALL advance `next_trigger_at` by one year. For `recurring_monthly` reminders, it SHALL advance `next_trigger_at` by one month.

#### Scenario: Dismissing a one-time reminder

WHEN `reminder_dismiss(reminder_id=<uuid>)` is called on a `one_time` reminder
THEN `last_triggered_at` MUST be set to approximately the current time
AND `next_trigger_at` MUST be set to NULL

#### Scenario: Dismissing a recurring yearly reminder

WHEN `reminder_dismiss(reminder_id=<uuid>)` is called on a `recurring_yearly` reminder with `next_trigger_at='2026-03-14T08:00:00Z'`
THEN `last_triggered_at` MUST be set to approximately the current time
AND `next_trigger_at` MUST be advanced to `'2027-03-14T08:00:00Z'`

#### Scenario: Dismissing a recurring monthly reminder

WHEN `reminder_dismiss(reminder_id=<uuid>)` is called on a `recurring_monthly` reminder with `next_trigger_at='2026-03-14T08:00:00Z'`
THEN `last_triggered_at` MUST be set to approximately the current time
AND `next_trigger_at` MUST be advanced to `'2026-04-14T08:00:00Z'`

#### Scenario: Dismissing a non-existent reminder

WHEN `reminder_dismiss(reminder_id=<nonexistent-uuid>)` is called
THEN the tool MUST return an error indicating the reminder was not found

---

### Requirement: gift_add adds a gift idea for a contact

The `gift_add` MCP tool SHALL accept `contact_id`, `name`, and optional `description`, `occasion`, `estimated_price_cents`, and `url` parameters. The `status` SHALL default to `'idea'`.

The tool SHALL return the created gift record as a JSON object.

#### Scenario: Adding a gift idea

WHEN `gift_add(contact_id=<uuid>, name="Kindle Paperwhite", occasion="birthday", estimated_price_cents=14000)` is called
THEN a new row MUST be inserted into the `gifts` table with `status='idea'`
AND the tool MUST return a JSON object containing all columns

#### Scenario: Adding a gift populates the activity feed

WHEN `gift_add` is called successfully
THEN a row MUST be inserted into `contact_feed` with `action='gift_added'`, `entity_type='gift'`, and `entity_id` set to the new gift's `id`

---

### Requirement: gift_update_status advances a gift through the pipeline

The `gift_update_status` MCP tool SHALL accept a `gift_id` (UUID) and a `status` parameter. Valid status values are `'idea'`, `'searched'`, `'found'`, `'bought'`, and `'given'`. The tool SHALL update the gift's `status` and set `updated_at` to the current timestamp.

#### Scenario: Moving a gift from idea to searched

WHEN `gift_update_status(gift_id=<uuid>, status="searched")` is called on a gift with `status='idea'`
THEN the gift's `status` MUST be updated to `'searched'`
AND `updated_at` MUST be set to the current timestamp

#### Scenario: Marking a gift as given

WHEN `gift_update_status(gift_id=<uuid>, status="given")` is called
THEN the gift's `status` MUST be updated to `'given'`
AND `updated_at` MUST be set to the current timestamp

#### Scenario: Invalid status value

WHEN `gift_update_status(gift_id=<uuid>, status="invalid_status")` is called
THEN the tool MUST return an error indicating the status value is not valid
AND the gift row MUST NOT be modified

#### Scenario: Updating a non-existent gift

WHEN `gift_update_status(gift_id=<nonexistent-uuid>, status="bought")` is called
THEN the tool MUST return an error indicating the gift was not found

---

### Requirement: gift_list returns gifts for a contact

The `gift_list` MCP tool SHALL accept a `contact_id` (UUID) and optional `status` parameter. If `status` is provided, only gifts with that status SHALL be returned. Results SHALL be ordered by `created_at` descending.

#### Scenario: Listing all gifts for a contact

WHEN a contact has 3 gifts (1 idea, 1 bought, 1 given)
AND `gift_list(contact_id=<uuid>)` is called
THEN the tool MUST return all 3 gifts ordered by `created_at` descending

#### Scenario: Filtering gifts by status

WHEN a contact has 3 gifts (2 ideas, 1 given)
AND `gift_list(contact_id=<uuid>, status="idea")` is called
THEN the tool MUST return only the 2 gifts with `status='idea'`

#### Scenario: No gifts for a contact

WHEN `gift_list(contact_id=<uuid>)` is called for a contact with no gifts
THEN the tool MUST return an empty list

---

### Requirement: loan_create records a loan between contacts

The `loan_create` MCP tool SHALL accept `lender_contact_id`, `borrower_contact_id`, `name`, `amount_cents`, and optional `currency` (default `'USD'`) and `loaned_at` parameters. The `settled` field SHALL default to `false`.

The tool SHALL return the created loan record as a JSON object.

#### Scenario: Creating a loan

WHEN `loan_create(lender_contact_id=<alice>, borrower_contact_id=<bob>, name="Dinner", amount_cents=5000, currency="USD")` is called
THEN a new row MUST be inserted into the `loans` table with `settled=false`
AND the tool MUST return a JSON object containing all columns

#### Scenario: Creating a loan populates the activity feed

WHEN `loan_create` is called successfully
THEN a `contact_feed` entry MUST be created for `lender_contact_id` with `action='loan_created'` and `entity_type='loan'`
AND a `contact_feed` entry MUST be created for `borrower_contact_id` with `action='loan_created'` and `entity_type='loan'`

---

### Requirement: loan_settle marks a loan as settled

The `loan_settle` MCP tool SHALL accept a `loan_id` (UUID) and set `settled=true` and `settled_at` to the current timestamp on the corresponding loan row.

#### Scenario: Settling an unsettled loan

WHEN `loan_settle(loan_id=<uuid>)` is called on a loan with `settled=false`
THEN the loan's `settled` MUST be set to `true`
AND `settled_at` MUST be set to approximately the current timestamp

#### Scenario: Settling an already-settled loan

WHEN `loan_settle(loan_id=<uuid>)` is called on a loan with `settled=true`
THEN the tool MUST return an error indicating the loan is already settled
AND the row MUST NOT be modified

#### Scenario: Settling a non-existent loan

WHEN `loan_settle(loan_id=<nonexistent-uuid>)` is called
THEN the tool MUST return an error indicating the loan was not found

---

### Requirement: loan_list returns loans involving a contact

The `loan_list` MCP tool SHALL accept a `contact_id` (UUID) and optional `settled` (boolean) parameter. It SHALL return all loans where the contact is either the `lender_contact_id` or `borrower_contact_id`. If `settled` is provided, only loans matching that settled status SHALL be returned.

#### Scenario: Listing all loans for a contact

WHEN Alice has 2 loans as lender and 1 loan as borrower
AND `loan_list(contact_id=<alice>)` is called
THEN the tool MUST return all 3 loans

#### Scenario: Filtering unsettled loans

WHEN a contact has 2 unsettled loans and 1 settled loan
AND `loan_list(contact_id=<uuid>, settled=false)` is called
THEN the tool MUST return only the 2 unsettled loans

#### Scenario: No loans for a contact

WHEN `loan_list(contact_id=<uuid>)` is called for a contact with no loans
THEN the tool MUST return an empty list

---

### Requirement: group_create creates a group

The `group_create` MCP tool SHALL accept `name` and optional `type` parameters. Valid values for `type` include `'family'`, `'couple'`, `'friends'`, and `'team'`.

The tool SHALL return the created group record as a JSON object.

#### Scenario: Creating a family group

WHEN `group_create(name="The Smiths", type="family")` is called
THEN a new row MUST be inserted into the `groups` table
AND the tool MUST return a JSON object containing `id`, `name`, `type`, and `created_at`

#### Scenario: Creating a group without a type

WHEN `group_create(name="Book Club")` is called without a `type`
THEN a new row MUST be inserted with `type` as NULL

---

### Requirement: group_add_member adds a contact to a group

The `group_add_member` MCP tool SHALL accept `group_id`, `contact_id`, and optional `role` parameters. It SHALL insert a row into the `group_members` table.

#### Scenario: Adding a member to a group

WHEN `group_add_member(group_id=<uuid>, contact_id=<uuid>, role="parent")` is called
THEN a new row MUST be inserted into `group_members` with the provided values

#### Scenario: Adding a duplicate member

WHEN a contact is already a member of a group
AND `group_add_member` is called with the same `group_id` and `contact_id`
THEN the tool MUST return an error indicating the contact is already a member of the group

#### Scenario: Adding a member to a non-existent group

WHEN `group_add_member(group_id=<nonexistent-uuid>, contact_id=<uuid>)` is called
THEN the tool MUST return an error indicating the group was not found

---

### Requirement: group_list returns groups with optional member details

The `group_list` MCP tool SHALL return all groups as a list of JSON objects. Each group object SHALL include the group's `id`, `name`, `type`, `created_at`, and a `members` array containing the contacts in that group (each with `contact_id`, `role`, `first_name`, and `last_name`).

#### Scenario: Listing groups with members

WHEN there are 2 groups, one with 3 members and one with 1 member
AND `group_list()` is called
THEN the tool MUST return both groups
AND each group's `members` array MUST contain the correct contacts with their roles

#### Scenario: No groups exist

WHEN `group_list()` is called and the `groups` table is empty
THEN the tool MUST return an empty list

---

### Requirement: label_create creates a label

The `label_create` MCP tool SHALL accept `name` and optional `color` parameters. The `name` column has a UNIQUE constraint.

The tool SHALL return the created label record as a JSON object.

#### Scenario: Creating a label

WHEN `label_create(name="VIP", color="#ff0000")` is called
THEN a new row MUST be inserted into the `labels` table
AND the tool MUST return a JSON object containing `id`, `name`, `color`, and `created_at`

#### Scenario: Creating a duplicate label

WHEN a label with `name="VIP"` already exists
AND `label_create(name="VIP")` is called
THEN the tool MUST return an error indicating the label name already exists

---

### Requirement: label_assign assigns a label to a contact

The `label_assign` MCP tool SHALL accept `contact_id` and `label_id` parameters. It SHALL insert a row into the `contact_labels` table.

#### Scenario: Assigning a label to a contact

WHEN `label_assign(contact_id=<uuid>, label_id=<uuid>)` is called
THEN a new row MUST be inserted into `contact_labels`

#### Scenario: Assigning the same label twice

WHEN a contact already has a given label assigned
AND `label_assign` is called with the same `contact_id` and `label_id`
THEN the tool MUST return an error indicating the label is already assigned

---

### Requirement: contact_search_by_label returns contacts with a given label

The `contact_search_by_label` MCP tool SHALL accept a `label_id` (UUID) and return all contacts that have the given label assigned, filtered to only `listed=true` contacts.

#### Scenario: Searching contacts by label

WHEN 3 contacts have the "VIP" label assigned and 1 of them is archived
AND `contact_search_by_label(label_id=<vip-label-id>)` is called
THEN the tool MUST return only the 2 listed contacts with that label

#### Scenario: No contacts with the label

WHEN `contact_search_by_label(label_id=<uuid>)` is called for a label with no contacts
THEN the tool MUST return an empty list

---

### Requirement: fact_set stores a quick fact for a contact

The `fact_set` MCP tool SHALL accept `contact_id`, `category`, and `content` parameters. If a quick fact with the same `contact_id` and `category` already exists, the tool SHALL update the existing row's `content`. Otherwise, it SHALL insert a new row. This is upsert semantics.

#### Scenario: Setting a new quick fact

WHEN `fact_set(contact_id=<uuid>, category="favorite_food", content="Sushi")` is called
AND no quick fact with that `contact_id` and `category` exists
THEN a new row MUST be inserted into the `quick_facts` table

#### Scenario: Updating an existing quick fact

WHEN a quick fact with `category="favorite_food"` already exists for the contact
AND `fact_set(contact_id=<uuid>, category="favorite_food", content="Ramen")` is called
THEN the existing row's `content` MUST be updated to `"Ramen"`
AND no additional row SHALL be inserted

---

### Requirement: fact_list returns quick facts for a contact

The `fact_list` MCP tool SHALL accept a `contact_id` (UUID) and return all quick facts for that contact as a list of JSON objects.

#### Scenario: Listing quick facts

WHEN a contact has 3 quick facts (favorite_food, hobbies, pet_name)
AND `fact_list(contact_id=<uuid>)` is called
THEN the tool MUST return all 3 quick facts
AND each entry MUST include `id`, `contact_id`, `category`, `content`, and `created_at`

#### Scenario: No quick facts

WHEN `fact_list(contact_id=<uuid>)` is called for a contact with no quick facts
THEN the tool MUST return an empty list

---

### Requirement: feed_get returns the activity feed for a contact

The `feed_get` MCP tool SHALL accept a `contact_id` (UUID) and optional `limit` (default 50) and `offset` (default 0) parameters. It SHALL return all entries from the `contact_feed` table for that contact, ordered by `created_at` descending.

#### Scenario: Retrieving a contact's activity feed

WHEN a contact has 10 feed entries (notes created, interactions logged, gifts added, etc.)
AND `feed_get(contact_id=<uuid>)` is called
THEN the tool MUST return all 10 entries ordered by `created_at` descending
AND each entry MUST include `id`, `contact_id`, `action`, `entity_type`, `entity_id`, `summary`, and `created_at`

#### Scenario: Retrieving feed with pagination

WHEN a contact has 100 feed entries
AND `feed_get(contact_id=<uuid>, limit=20, offset=20)` is called
THEN the tool MUST return at most 20 entries, skipping the first 20

#### Scenario: Empty activity feed

WHEN `feed_get(contact_id=<uuid>)` is called for a contact with no feed entries
THEN the tool MUST return an empty list

---

### Requirement: Activity feed auto-population

The activity feed SHALL be automatically populated when entities are created or modified through MCP tools. Each tool that mutates data related to a contact MUST insert a corresponding entry into the `contact_feed` table.

#### Scenario: note_create populates the feed

WHEN `note_create` is called for a contact
THEN a `contact_feed` entry MUST be created with `action='note_created'` and `entity_type='note'`

#### Scenario: interaction_log populates the feed

WHEN `interaction_log` is called for a contact
THEN a `contact_feed` entry MUST be created with `action='interaction_logged'` and `entity_type='interaction'`

#### Scenario: gift_add populates the feed

WHEN `gift_add` is called for a contact
THEN a `contact_feed` entry MUST be created with `action='gift_added'` and `entity_type='gift'`

#### Scenario: date_add populates the feed

WHEN `date_add` is called for a contact
THEN a `contact_feed` entry MUST be created with `action='date_added'` and `entity_type='important_date'`

#### Scenario: relationship_add populates the feed for both contacts

WHEN `relationship_add` is called
THEN a `contact_feed` entry MUST be created for both `contact_id` and `related_contact_id` with `action='relationship_added'` and `entity_type='relationship'`

#### Scenario: loan_create populates the feed for both contacts

WHEN `loan_create` is called
THEN a `contact_feed` entry MUST be created for both `lender_contact_id` and `borrower_contact_id` with `action='loan_created'` and `entity_type='loan'`

#### Scenario: gift_update_status populates the feed

WHEN `gift_update_status` is called successfully
THEN a `contact_feed` entry MUST be created with `action='gift_status_updated'` and `entity_type='gift'`

---

### Requirement: All tools return JSON objects

Every Relationship butler MCP tool SHALL return its results as JSON objects (or JSON arrays of objects for list operations). No tool SHALL return plain text or unstructured output.

#### Scenario: Single-entity tool returns JSON object

WHEN `contact_get`, `contact_create`, `contact_update`, `note_create`, `interaction_log`, `gift_add`, `loan_create`, `reminder_create`, `group_create`, `label_create`, or `date_add` is called successfully
THEN the return value MUST be a JSON object

#### Scenario: List tool returns JSON array

WHEN `contact_search`, `relationship_list`, `date_list`, `upcoming_dates`, `note_list`, `note_search`, `interaction_list`, `reminder_list`, `gift_list`, `loan_list`, `group_list`, `contact_search_by_label`, `fact_list`, or `feed_get` is called
THEN the return value MUST be a JSON array of objects (or an empty JSON array if no results)

---

### Requirement: upcoming-dates-check scheduled task

The `upcoming-dates-check` task SHALL run daily at 8:00 AM (`0 8 * * *`). When triggered, the runtime instance SHALL check for important dates in the next 7 days and prepare reminder messages.

#### Scenario: Scheduled task triggers CC with correct prompt

WHEN the scheduler's `tick()` runs at or after 8:00 AM
AND the `upcoming-dates-check` task is due
THEN the scheduler SHALL dispatch the task's prompt to the LLM CLI spawner
AND the runtime instance SHALL have access to the `upcoming_dates`, `contact_get`, and `state_set` tools to check dates and store reminder messages

---

### Requirement: relationship-maintenance scheduled task

The `relationship-maintenance` task SHALL run weekly on Monday at 9:00 AM (`0 9 * * 1`). When triggered, the runtime instance SHALL review contacts that have not been interacted with in 30+ days and suggest outreach.

#### Scenario: Scheduled task triggers CC with correct prompt

WHEN the scheduler's `tick()` runs at or after 9:00 AM on a Monday
AND the `relationship-maintenance` task is due
THEN the scheduler SHALL dispatch the task's prompt to the LLM CLI spawner
AND the runtime instance SHALL have access to `contact_search`, `interaction_list`, and `upcoming_dates` tools to identify neglected contacts and provide context
