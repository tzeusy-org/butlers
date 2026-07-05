# Ingestion Policy — Delta

## MODIFIED Requirements

### Requirement: Ingestion rules data model

The system SHALL store all ingestion filtering and routing rules in a single `ingestion_rules` table in the switchboard schema. Each rule has a `scope` that determines its pipeline position: `'global'` rules are evaluated post-ingest/pre-LLM; `'connector:<type>:<identity>'` rules are evaluated at the connector before Switchboard submission.

The table schema:
- `id` UUID PRIMARY KEY (default gen_random_uuid())
- `scope` TEXT NOT NULL -- `'global'` or `'connector:<connector_type>:<endpoint_identity>'`
- `rule_type` TEXT NOT NULL -- unconstrained; known types: `sender_domain`, `sender_address`, `header_condition`, `mime_type`, `substring`, `chat_id`, `channel_id`, `source_channel`, `mic_id`. The last two are evaluated by the engine and seeded via migrations (the OwnTracks/Home Assistant global `skip` rules and the live-listener voice gate) but are not accepted by the REST create/update validator.
- `condition` JSONB NOT NULL -- schema determined by `rule_type`
- `action` TEXT NOT NULL -- `block`, `skip`, `metadata_only`, `low_priority_queue`, `pass_through`, or `route_to:<butler>`
- `priority` INTEGER NOT NULL (>= 0) -- lower = evaluated first
- `enabled` BOOLEAN NOT NULL DEFAULT TRUE
- `name` TEXT -- optional human-readable label
- `description` TEXT -- optional
- `created_by` TEXT NOT NULL DEFAULT `'dashboard'` -- conventional values include `'dashboard'`, `'api'`, `'seed'`, `'migration'`, and `'promotion'` (rules minted via the switchboard rule-promotion suggestion-confirmation flow); the column remains unconstrained TEXT, this is a documented convention, not a DB CHECK
- `promoted_from_suggestion_id` UUID, nullable -- FK to `switchboard.rule_promotion_suggestions(id)`, set only on rules created via promotion-suggestion confirmation; NULL for all other creation paths
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT NOW()
- `updated_at` TIMESTAMPTZ NOT NULL DEFAULT NOW()
- `deleted_at` TIMESTAMPTZ -- soft-delete marker

Constraints:
- `scope = 'global' OR scope LIKE 'connector:%'`
- Connector-scoped rules MUST satisfy `action IN ('block', 'pass_through')` (DB CHECK: `scope = 'global' OR action IN ('block', 'pass_through')`). `pass_through` exists only to support whitelist-equivalent connector rules emitted by the source-filter migration. The REST create/update API is stricter and accepts only `block` for connector scope, so connector `pass_through` rows are migration-seeded, never API-created.
- `priority >= 0`

Indexes:
- `(scope, priority, created_at, id) WHERE enabled = TRUE AND deleted_at IS NULL` -- primary query path per scope
- `(priority, created_at, id) WHERE scope = 'global' AND enabled = TRUE AND deleted_at IS NULL` -- global-only fast path

#### Scenario: Global rule creation
- **WHEN** a rule is created with `scope = 'global'` and `action = 'route_to:finance'`
- **THEN** the rule is persisted and available to the post-ingest evaluator

#### Scenario: Connector-scoped rule creation
- **WHEN** a rule is created with `scope = 'connector:gmail:gmail:user:dev'` and `action = 'block'`
- **THEN** the rule is persisted and available to the Gmail connector's evaluator

#### Scenario: Connector-scoped rule action constraint
- **WHEN** a rule is created with `scope = 'connector:gmail:gmail:user:dev'` and `action = 'route_to:finance'`
- **THEN** the database CHECK constraint rejects the insert

#### Scenario: Soft delete
- **WHEN** a rule is deleted
- **THEN** `deleted_at` is set and `enabled` is set to FALSE; the rule is excluded from all evaluator queries

#### Scenario: Promoted rule carries suggestion provenance
- **WHEN** a rule is created through the rule-promotion suggestion-confirmation flow (see `switchboard-rule-promotion` spec)
- **THEN** the rule's `created_by` field MUST be `'promotion'` and `promoted_from_suggestion_id` MUST reference the originating `rule_promotion_suggestions` row

#### Scenario: Non-promoted rules leave provenance null
- **WHEN** a rule is created via the existing dashboard CRUD API, a migration seed, or any path other than suggestion confirmation
- **THEN** `promoted_from_suggestion_id` MUST be NULL, and existing `created_by` behavior (e.g. `'dashboard'`) is unchanged
