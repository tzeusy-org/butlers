# Mailbox Module

## Purpose

The Mailbox module provides a local message queue for inter-butler and external communication, with durable storage in a PostgreSQL `mailbox` table, status lifecycle management, and five MCP tools for message management.

## ADDED Requirements

### Requirement: Mailbox Table Schema

The `mailbox` table stores messages with fields: `id` (UUID, PK), `sender` (TEXT), `sender_channel` (TEXT), `subject` (TEXT, nullable), `body` (TEXT), `priority` (INTEGER, default 0), `status` (TEXT, default 'unread'), `metadata` (JSONB, default '{}'), `read_at` (TIMESTAMPTZ), `archived_at` (TIMESTAMPTZ), `created_at` (TIMESTAMPTZ), `updated_at` (TIMESTAMPTZ).

#### Scenario: Table creation via migration

- **WHEN** the mailbox migration `mailbox_001` runs
- **THEN** the `mailbox` table is created with all required columns
- **AND** indexes are created on `status`, `sender`, and `created_at DESC`

#### Scenario: Migration rollback

- **WHEN** the downgrade runs
- **THEN** the `mailbox` table is dropped

### Requirement: Message Status Lifecycle

Messages transition through statuses: `unread`, `read`, `actioned`, `archived`.

#### Scenario: Auto-read on fetch

- **WHEN** `mailbox_read` is called for an `unread` message
- **THEN** the status is automatically updated to `read`
- **AND** `read_at` is set to the current timestamp

#### Scenario: Status update with timestamp tracking

- **WHEN** `mailbox_update_status` sets status to `read`
- **THEN** `read_at` is set via `COALESCE(read_at, now())`
- **WHEN** status is set to `actioned`
- **THEN** both `read_at` and `actioned_at` are set (if not already)
- **WHEN** status is set to `archived`
- **THEN** `archived_at` is set

#### Scenario: Invalid status rejected

- **WHEN** `mailbox_update_status` is called with a status not in `{"unread", "read", "actioned", "archived"}`
- **THEN** an error dict is returned

### Requirement: Known Channels

The module recognizes a set of known sender channels: `mcp`, `telegram`, `email`, `api`, `scheduler`, `system`.

#### Scenario: Unknown channel accepted with warning

- **WHEN** `mailbox_post` is called with a `sender_channel` not in the known set
- **THEN** the message is accepted and inserted
- **AND** a warning is logged indicating a potential bug

### Requirement: MCP Tool Surface (5 Tools)

The module registers 5 MCP tools for mailbox management.

#### Scenario: mailbox_post

- **WHEN** `mailbox_post` is called with `sender`, `sender_channel`, `body`, optional `subject`, `priority`, `metadata`
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
- **THEN** the status is updated and the updated row is returned with `updated_at` set

#### Scenario: mailbox_stats

- **WHEN** `mailbox_stats` is called
- **THEN** message counts grouped by status are returned as `{"unread": N, "read": N, "actioned": N, "archived": N, "total": N}`

### Requirement: Schema-Adaptive Column Handling

The module introspects the actual database schema to handle column type variations (e.g., `body` as TEXT vs JSONB).

#### Scenario: JSONB body column

- **WHEN** the `body` column is of type `jsonb`
- **THEN** the body is cast with `$N::jsonb` during INSERT
- **AND** JSON string values in body and metadata are parsed on read

#### Scenario: TEXT body column

- **WHEN** the `body` column is of type `text`
- **THEN** the body is stored as a plain string
