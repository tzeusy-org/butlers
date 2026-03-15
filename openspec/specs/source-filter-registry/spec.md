# Source Filter Registry

## SUPERSEDED

This spec is superseded by the unified ingestion-policy system. The `source_filters` and `connector_source_filters` tables have been migrated to `ingestion_rules` by sw_027. This spec is archived for historical reference only.

## Purpose
Defines the shared registry of named source filter objects and their connector assignments. Filters are created once and reused across any number of connectors. Each filter specifies a filter mode (blacklist or whitelist), a source key type (the envelope field to match against, e.g. `domain`, `chat_id`), and a list of patterns. The registry is stored in the switchboard DB and managed via a REST API.

## Requirements

### Requirement: Source Filter Data Model
The `source_filters` table stores named, reusable filter definitions in the switchboard schema.

#### Scenario: Filter object fields
- **WHEN** a source filter is created
- **THEN** it has: `id` (UUID, generated), `name` (TEXT, unique, non-empty), `description` (TEXT, optional), `filter_mode` (TEXT: `"blacklist"` or `"whitelist"`), `source_key_type` (TEXT: identifies which message field to match, e.g. `"domain"`, `"sender_address"`, `"substring"`, `"chat_id"`, `"channel_id"`), `patterns` (TEXT array, non-empty), `created_at` and `updated_at` (TIMESTAMPTZ)
- **AND** `filter_mode` is constrained at the DB level to `('blacklist', 'whitelist')`
- **AND** `source_key_type` is an unconstrained TEXT column; valid values are enforced by the API layer per connector channel

#### Scenario: Pattern semantics by source_key_type
- **WHEN** `source_key_type` is `"domain"`
- **THEN** each pattern is a domain string (e.g. `"example.com"`); matching uses exact domain comparison OR suffix match (a pattern of `"example.com"` matches `"sub.example.com"`)
- **WHEN** `source_key_type` is `"sender_address"`
- **THEN** each pattern is a normalized full email address (lowercase, no angle brackets); matching is exact
- **WHEN** `source_key_type` is `"substring"`
- **THEN** each pattern is a literal string; matching is case-insensitive substring search against the raw From header value
- **WHEN** `source_key_type` is `"chat_id"`
- **THEN** each pattern is a Telegram chat or user ID as a string (e.g. `"123456789"`); matching is exact string equality against the stringified sender identity
- **WHEN** `source_key_type` is `"channel_id"`
- **THEN** each pattern is a Discord channel ID or guild ID as a string; matching is exact string equality

#### Scenario: Name uniqueness
- **WHEN** a filter with a duplicate `name` is created
- **THEN** the API returns HTTP 409 with a descriptive error message
- **AND** the DB enforces a UNIQUE constraint on `name`

### Requirement: Connector Filter Assignment
The `connector_source_filters` table is the many-to-many join between connectors and named filters.

#### Scenario: Assignment fields
- **WHEN** a filter is assigned to a connector
- **THEN** the assignment record has: `connector_type` (TEXT), `endpoint_identity` (TEXT), `filter_id` (UUID FK → `source_filters.id` ON DELETE CASCADE), `enabled` (BOOL, default true), `priority` (INT, default 0, lower = evaluated first), `attached_at` (TIMESTAMPTZ)
- **AND** the primary key is `(connector_type, endpoint_identity, filter_id)`

#### Scenario: Cascade on filter delete
- **WHEN** a source filter is deleted
- **THEN** all `connector_source_filters` rows referencing that filter are automatically deleted via FK cascade
- **AND** no orphaned connector assignments remain

#### Scenario: Pre-configuration allowed
- **WHEN** a filter is assigned to a connector that does not yet exist in `connector_registry`
- **THEN** the assignment is stored and takes effect when the connector registers via heartbeat
- **AND** the assignment API does not require the connector to be present in `connector_registry`

### Requirement: Source Filter CRUD API
REST endpoints for managing named source filter objects in the switchboard API router.

#### Scenario: List all filters
- **WHEN** `GET /source-filters` is called
- **THEN** it returns `ApiResponse[list[SourceFilter]]` with all named filters ordered by `name ASC`
- **AND** each entry includes `id`, `name`, `description`, `filter_mode`, `source_key_type`, `patterns`, `created_at`, `updated_at`

#### Scenario: Create filter
- **WHEN** `POST /source-filters` is called with `{name, filter_mode, source_key_type, patterns, description?}`
- **THEN** on success it returns HTTP 201 with `ApiResponse[SourceFilter]` containing the created record
- **AND** on duplicate name it returns HTTP 409
- **AND** on invalid `filter_mode` or empty `patterns` it returns HTTP 422

#### Scenario: Get single filter
- **WHEN** `GET /source-filters/{filter_id}` is called
- **THEN** it returns `ApiResponse[SourceFilter]` for the given UUID
- **AND** on unknown `filter_id` it returns HTTP 404

#### Scenario: Update filter (partial)
- **WHEN** `PATCH /source-filters/{filter_id}` is called with any subset of `{name, description, patterns}`
- **THEN** only provided fields are updated; `updated_at` is set to `now()`
- **AND** `filter_mode` and `source_key_type` are immutable after creation (changing them would invalidate existing assignments)
- **AND** on unknown `filter_id` it returns HTTP 404
- **AND** on duplicate `name` it returns HTTP 409

#### Scenario: Delete filter
- **WHEN** `DELETE /source-filters/{filter_id}` is called
- **THEN** the filter and all its connector assignments are deleted atomically
- **AND** it returns HTTP 200 with `ApiResponse[{deleted_id: UUID}]`
- **AND** on unknown `filter_id` it returns HTTP 404

### Requirement: Connector Filter Assignment API
REST endpoints for managing which filters are assigned and enabled on a specific connector.

#### Scenario: List connector filter assignments
- **WHEN** `GET /connectors/{connector_type}/{endpoint_identity}/filters` is called
- **THEN** it returns `ApiResponse[list[ConnectorFilterAssignment]]` containing ALL named filters (not just attached ones)
- **AND** each entry includes `filter_id`, `name`, `filter_mode`, `source_key_type`, `pattern_count`, `enabled` (false for unattached), `priority`, `attached_at` (null for unattached)
- **AND** results are ordered by `priority ASC, name ASC`

#### Scenario: Set connector filter assignments
- **WHEN** `PUT /connectors/{connector_type}/{endpoint_identity}/filters` is called with `list[{filter_id, enabled, priority}]`
- **THEN** existing assignments for this connector are deleted and replaced atomically in a single transaction
- **AND** passing an empty list detaches all filters from the connector
- **AND** on unknown `filter_id` in the payload it returns HTTP 422
- **AND** the response is `ApiResponse[list[ConnectorFilterAssignment]]` reflecting the new state

#### Scenario: Source key type validation
- **WHEN** `GET /connectors/{connector_type}/{endpoint_identity}/filters` is called for a known connector type
- **THEN** filters whose `source_key_type` is not valid for that connector's channel are flagged with `"incompatible": true` in the response (they can still be assigned but will be skipped at enforcement time)
- **AND** valid key types per connector type: `gmail` → `domain`, `sender_address`, `substring`; `telegram-bot` / `telegram-user-client` → `chat_id`; `discord` → `channel_id`
