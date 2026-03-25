# Home Maintenance Scheduling

## Purpose

Track recurring home maintenance items (filter replacements, HVAC service, appliance warranties) with interval-based due-date computation and proactive Telegram reminders when items are due or overdue.

## Requirements

### Requirement: Maintenance Items Table

Recurring maintenance items are stored in the `home.maintenance_items` database table.

#### Scenario: Table schema

- **WHEN** the Alembic migration for maintenance items runs
- **THEN** it SHALL create table `home.maintenance_items` with columns:
  - `id` (UUID, primary key, default `gen_random_uuid()`)
  - `name` (TEXT, NOT NULL, UNIQUE)
  - `category` (TEXT, NOT NULL — one of `filter`, `hvac`, `appliance`, `plumbing`, `electrical`, `general`)
  - `interval_days` (INTEGER, NOT NULL)
  - `last_completed_at` (TIMESTAMPTZ, nullable)
  - `next_due_at` (TIMESTAMPTZ, nullable — computed as `last_completed_at + interval_days * interval '1 day'`, or NULL if never completed)
  - `notes` (TEXT, nullable)
  - `created_at` (TIMESTAMPTZ, NOT NULL, default `now()`)
  - `updated_at` (TIMESTAMPTZ, NOT NULL, default `now()`)

#### Scenario: Migration branch label

- **WHEN** the migration revision is created
- **THEN** it SHALL use branch label `"home_maintenance"`
- **AND** it SHALL depend on the existing home schema migration chain

### Requirement: Maintenance Schedule Check Job

The `maintenance_schedule_check` deterministic job checks all maintenance items for due or overdue status and sends reminders.

#### Scenario: Due item detection

- **WHEN** the `maintenance_schedule_check` job runs
- **THEN** it SHALL query `home.maintenance_items` for items where `next_due_at <= now()` or `next_due_at IS NULL AND last_completed_at IS NULL`
- **AND** items with `next_due_at IS NULL AND last_completed_at IS NULL` SHALL be treated as "never completed — initial setup needed"

#### Scenario: Overdue classification

- **WHEN** a maintenance item has `next_due_at` in the past
- **THEN** it SHALL be classified by overdue severity:
  - `due` if overdue by 0-7 days
  - `overdue` if overdue by 8-30 days
  - `critical` if overdue by more than 30 days

#### Scenario: Upcoming items lookahead

- **WHEN** the job checks for due items
- **THEN** it SHALL also identify items due within the next 7 days as `upcoming`
- **AND** upcoming items SHALL be included in the notification as informational

#### Scenario: Reminder notification

- **WHEN** one or more items are due, overdue, or upcoming
- **THEN** the job SHALL send a Telegram notification via the notify helper with `intent="send"`
- **AND** the message SHALL list items grouped by status (critical overdue first, then overdue, then due, then upcoming)
- **AND** each item SHALL show name, category, days overdue or days until due

#### Scenario: No action needed

- **WHEN** no items are due, overdue, or upcoming within 7 days
- **THEN** the job SHALL NOT send a notification
- **AND** it SHALL return `{"items_checked": N, "reminders_sent": 0}`

#### Scenario: Job return value

- **WHEN** the job completes
- **THEN** it SHALL return a dict with keys `items_checked` (int), `due_count` (int), `overdue_count` (int), `upcoming_count` (int), `reminders_sent` (0 or 1)

### Requirement: Maintenance Item Management via MCP Tools

The Home butler provides MCP tools for creating, completing, listing, and removing maintenance items.

#### Scenario: Create maintenance item

- **WHEN** `ha_maintenance_create(name, category, interval_days, notes=None)` is called
- **THEN** a new row SHALL be inserted into `home.maintenance_items` with the given values
- **AND** `next_due_at` SHALL be NULL (never completed)
- **AND** the tool SHALL return the created item's `id`, `name`, `category`, `interval_days`, and `next_due_at`

#### Scenario: Duplicate name rejected

- **WHEN** `ha_maintenance_create` is called with a name that already exists
- **THEN** the tool SHALL return an error message indicating the name is taken

#### Scenario: Complete maintenance item

- **WHEN** `ha_maintenance_complete(name, completed_at=None)` is called
- **THEN** the matching row SHALL be updated with `last_completed_at` set to `completed_at` (default `now()`)
- **AND** `next_due_at` SHALL be recomputed as `last_completed_at + interval_days * interval '1 day'`
- **AND** `updated_at` SHALL be set to `now()`

#### Scenario: List maintenance items

- **WHEN** `ha_maintenance_list(category=None, status=None)` is called
- **THEN** it SHALL return all maintenance items, optionally filtered by category and/or status
- **AND** status filter values SHALL be: `due` (next_due_at <= now), `upcoming` (next_due_at within 7 days), `ok` (next_due_at > 7 days from now or NULL with existing last_completed_at)
- **AND** results SHALL be sorted by `next_due_at` ascending (NULLs first)

#### Scenario: Remove maintenance item

- **WHEN** `ha_maintenance_remove(name)` is called
- **THEN** the matching row SHALL be deleted from `home.maintenance_items`
- **AND** if no matching row exists, the tool SHALL return an error message

### Requirement: Maintenance Memory Integration

Maintenance completion events are stored as memory facts for historical tracking.

#### Scenario: Completion fact stored

- **WHEN** a maintenance item is completed via `ha_maintenance_complete`
- **THEN** a memory fact SHALL be stored with `subject=<item_name>`, `predicate="device_issue"`, `content` describing the completion (e.g., "HVAC filter replaced"), `permanence="standard"`, `importance=5.0`, and `tags` including `"maintenance"` and the item category
