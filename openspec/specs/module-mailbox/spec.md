# Mailbox Module

## Purpose

The Mailbox module provides a local message queue for inter-butler and external communication, with durable storage in a PostgreSQL `mailbox` table, status lifecycle management, and five MCP tools for message management.

## Requirements

### Requirement: Mailbox Table Schema

On a fresh hosting-butler schema, `mailbox_001` SHALL create the `mailbox` table with `body TEXT`, `priority INTEGER NOT NULL DEFAULT 0`, and JSONB `metadata`. `mailbox_002` SHALL only add a missing `actioned_at` column to an existing legacy table.

#### Scenario: Fresh table creation via migration

- **WHEN** the mailbox migration `mailbox_001` runs on a fresh hosting-butler schema
- **THEN** the `mailbox` table is created with all required columns
- **AND** the table has `id` (UUID, PK), `sender` (TEXT), `sender_channel` (TEXT), `subject` (TEXT, nullable), `status` (TEXT, default `unread`), `read_at`, `actioned_at`, `archived_at`, `created_at`, and `updated_at` (all TIMESTAMPTZ)
- **AND** indexes are created on `status`, `sender`, and `created_at DESC`

#### Scenario: Legacy actioned-at repair

- **WHEN** `mailbox_002` runs against a legacy mailbox table that lacks `actioned_at`
- **THEN** `actioned_at` is added

#### Scenario: Repair migration rollback

- **WHEN** `mailbox_002` is downgraded
- **THEN** the downgrade is a no-op

#### Scenario: Initial migration rollback

- **WHEN** `mailbox_001` is downgraded
- **THEN** the `mailbox` table is dropped

### Requirement: Message Status Lifecycle

`mailbox_update_status` SHALL accept only `unread`, `read`, `actioned`, and `archived`, without enforcing an ordered transition graph. `mailbox_read` SHALL change an unread message to `read`.

#### Scenario: Auto-read on fetch

- **WHEN** `mailbox_read` is called for an `unread` message
- **THEN** the status is automatically updated to `read`
- **AND** `read_at` is set to the current timestamp when that column exists

#### Scenario: Status update with timestamp tracking

- **WHEN** `mailbox_update_status` sets status to `read`
- **THEN** `read_at` is set via `COALESCE(read_at, now())` when that column exists
- **WHEN** status is set to `actioned`
- **THEN** `actioned_at` is set via `COALESCE(actioned_at, now())`
- **AND** `read_at` is set when that column exists
- **WHEN** status is set to `archived`
- **THEN** `archived_at` is set via `COALESCE(archived_at, now())` when that column exists

#### Scenario: Invalid status rejected

- **WHEN** `mailbox_update_status` is called with a status not in `{"unread", "read", "actioned", "archived"}`
- **THEN** an error dict is returned

### Requirement: Known Channels

The module SHALL recognize `mcp`, `telegram_bot`, `telegram_user_client`, `email`, `api`, `scheduler`, and `system` as known sender-channel labels. The set SHALL not be treated as authorization or sender-provenance verification: `mailbox_post` accepts another value and logs a warning before attempting the insert.

#### Scenario: Unknown channel accepted with warning

- **WHEN** `mailbox_post` is called with a `sender_channel` not in the known set
- **THEN** the value is accepted and insertion is attempted
- **AND** a warning is logged indicating a potential bug

### Requirement: MCP Tool Surface (5 Tools)

When `[modules.mailbox]` is configured and the module starts successfully, the Mailbox module SHALL register exactly five local MCP tools: `mailbox_post`, `mailbox_list`, `mailbox_read`, `mailbox_update_status`, and `mailbox_stats`.

#### Scenario: mailbox_post

- **WHEN** `mailbox_post` is called with `sender`, `sender_channel`, `body`, optional `subject`, `priority` (tool default 2), `metadata`
- **THEN** a new message is inserted into the mailbox table
- **AND** the body is stored as JSONB `{"text": body}` when the column type is `jsonb`
- **AND** the response includes `message_id` (UUID) and `created_at`

#### Scenario: mailbox_list with filters

- **WHEN** `mailbox_list` is called with optional `status`, `sender`, `limit`, `offset`
- **THEN** matching messages are returned ordered by `created_at DESC`
- **AND** pagination is supported via limit/offset

#### Scenario: mailbox_read

- **WHEN** `mailbox_read` is called with a valid `message_id`
- **THEN** the full message row is returned with all fields
- **AND** unread messages are auto-marked as read
- **AND** invalid UUIDs or missing messages return an error dict

#### Scenario: mailbox_update_status

- **WHEN** `mailbox_update_status` is called with a valid message_id and status
- **THEN** the status is updated and the updated row is returned
- **AND** `updated_at` is set when that column exists

#### Scenario: mailbox_stats

- **WHEN** `mailbox_stats` is called
- **THEN** zero-filled counts for the valid statuses are returned as `{"unread": N, "read": N, "actioned": N, "archived": N, "total": N}`
- **AND** `total` equals the sum of those four status counts

### Requirement: Schema-Adaptive Column Handling

The module SHALL inspect the `mailbox` columns in `current_schema()` before posting, listing, and updating messages so it can handle TEXT and JSONB body and metadata variants.

#### Scenario: JSONB body column

- **WHEN** the `body` column is of type `jsonb`
- **THEN** the body dict is passed directly to the asyncpg JSONB codec during INSERT (no explicit `$N::jsonb` cast)
- **AND** metadata is passed as a mapping when its column is also `jsonb`
- **AND** decodable JSON string values in body and metadata are parsed in list, read, and update responses

#### Scenario: TEXT body column

- **WHEN** the `body` column is of type `text`
- **THEN** the body is stored as a plain string
- **AND** metadata is serialized as JSON when its column is not `jsonb`
