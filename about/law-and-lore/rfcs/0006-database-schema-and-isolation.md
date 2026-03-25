# RFC 0006: Database Schema and Isolation

**Status:** Accepted
**Date:** 2026-03-24

## Summary

Butlers uses a single PostgreSQL database with per-butler schemas and a shared schema for cross-butler identity tables. Each butler's database connection is scoped to its own schema plus `shared`, preventing cross-butler data access. Schema migrations use Alembic with a multi-chain branching model: core, module, and butler-specific chains are discovered automatically and executed independently at startup. A DB-first credential store provides layered secret resolution with environment variable fallback.

## Motivation

Butler isolation is a foundational architectural constraint. Each butler operates as an independent MCP server (see RFC 0002) with its own state, sessions, and domain-specific tables. Schema-based isolation within a single PostgreSQL instance provides this boundary without the operational overhead of per-butler database servers. A shared schema is necessary for identity data that all butlers must read (see RFC 0004). The multi-chain migration model allows modules to own their schema evolution independently, avoiding a single migration bottleneck that would couple all modules together.

## Design

### Schema Topology

```
PostgreSQL Database
  |
  +-- shared schema
  |     +-- entities           (identity anchor)
  |     +-- contacts           (named contact -> entity link)
  |     +-- contact_info       (per-channel identifiers)
  |     +-- entity_info        (extended entity data / credentials)
  |     +-- google_accounts    (OAuth account registry)
  |     +-- model_catalog      (LLM model definitions)
  |     +-- token_limits       (per-butler token quotas)
  |     +-- token_usage_ledger (token consumption tracking)
  |
  +-- switchboard schema
  |     +-- state              (KV state store)
  |     +-- sessions           (session log)
  |     +-- scheduled_tasks    (cron scheduler)
  |     +-- butler_secrets     (credential store)
  |     +-- route_inbox        (crash-recoverable route queue)
  |     +-- ingestion_events   (ingestion audit log)
  |     +-- routing_log        (routing decision history)
  |     +-- triage_rules       (pre-classification rules)
  |     +-- alembic_version    (migration tracking)
  |     +-- [module-specific tables]
  |
  +-- general schema
  |     +-- state, sessions, scheduled_tasks, butler_secrets
  |     +-- alembic_version
  |     +-- [module-specific tables]
  |
  +-- relationship schema
  |     +-- state, sessions, scheduled_tasks, butler_secrets
  |     +-- alembic_version
  |     +-- [module-specific tables: contacts sync, etc.]
  |
  +-- health schema
  |     +-- state, sessions, scheduled_tasks, butler_secrets
  |     +-- alembic_version
  |     +-- [module-specific tables]
  |
  +-- [additional butler schemas...]
```

### Per-Butler Schema Contents

Every butler schema contains at minimum these core tables (created by core chain migrations):

| Table | Purpose |
|-------|---------|
| `state` | KV state store (key TEXT PK, value JSONB, updated_at TIMESTAMPTZ) |
| `sessions` | Session log (prompt, output, trigger_source, model, request_id, tool_calls, duration, tokens, status) |
| `scheduled_tasks` | Cron scheduler (name, cron expression, prompt, enabled, next_run_at, last_run_at, source) |
| `butler_secrets` | Credential store (secret_key TEXT PK, secret_value, category, is_sensitive, expires_at) |
| `route_inbox` | Crash-recoverable route request queue (see RFC 0003) |
| `alembic_version` | Migration tracking per chain |

Module-specific tables are added by module migration chains (see below). Examples:

- **Memory module:** `episodes`, `facts`, `rules`, `entities`, `predicate_registry`, `consolidation_state`, and others (25+ revisions).
- **Approvals module:** `approval_actions`, `approval_rules`, `approval_events` (3+ revisions).
- **Contacts module:** `contacts_sync` and related tables (2+ revisions).
- **Mailbox module:** `mailbox` (1+ revision).

### Shared Schema Identity Tables

See RFC 0004 for the full identity schema. The shared schema is readable by all butler database roles. Writes are controlled by specific modules (primarily the contacts module in the relationship butler).

Additional shared infrastructure tables:

- **`shared.model_catalog`** -- LLM model definitions (provider, name, pricing, context window, capabilities).
- **`shared.token_limits`** -- Per-butler token quotas (daily/monthly caps).
- **`shared.token_usage_ledger`** -- Token consumption tracking for quota enforcement.
- **`shared.google_accounts`** -- Google OAuth account registry for multi-account support.

### Database Connection Scoping

Each butler's database connection sets `search_path` to `<butler_schema>, shared, public`. This ensures:

- Unqualified queries default to the butler's own schema.
- `shared.` prefix is optional for identity table reads (but SHOULD be used explicitly for clarity).
- A butler CANNOT access another butler's schema.
- A butler CANNOT write to `shared` tables unless explicitly authorized by the module that owns those tables.

### Multi-Chain Alembic Migrations

#### Chain Types

| Chain | Location | Branch Label | Discovery |
|-------|----------|--------------|-----------|
| Core | `alembic/versions/core/` | `"core"` | Hardcoded |
| Module | `src/butlers/modules/<name>/migrations/` | Module name | Auto-discovered by scanning `src/butlers/modules/*/migrations/` |
| Butler-specific | `roster/<name>/migrations/` | Butler name | Auto-discovered by scanning `roster/*/migrations/` |

#### Migration File Structure

```python
"""memory_baseline"""
revision = "mem_001"
down_revision = None              # None for chain root
branch_labels = ("memory",)       # Only on chain root
depends_on = None

def upgrade() -> None:
    op.execute("CREATE TABLE IF NOT EXISTS episodes (...)")

def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS episodes CASCADE")
```

Conventions:

- `branch_labels` is set ONLY on the first revision (chain root).
- `down_revision` chains within the module (e.g., `mem_002` has `down_revision = "mem_001"`).
- Migrations use raw SQL via `op.execute()` rather than Alembic ORM operations.
- Revision IDs follow `<prefix>_<number>` convention.

#### Execution Order at Startup

1. Core migrations (`chain="core"`)
2. Module migrations (for each enabled module with non-None `migration_revisions()`)
3. Butler-specific migrations (if `has_butler_chain(butler_name)` returns True)

Each chain is upgraded to its head independently. Alembic tracks applied revisions per-chain in the `alembic_version` table within the target schema.

#### Schema-Scoped Execution

When running migrations, the target schema is specified:

```python
await run_migrations(db_url, chain="all", schema="general")
```

This sets:

- `version_table_schema` so `alembic_version` tracking lives within the target schema.
- `butlers.target_schema` for migrations that create schema-qualified objects.

#### Version Location Configuration

Alembic's `version_locations` setting is always configured with ALL known chain directories, regardless of which chain is being upgraded. This ensures Alembic can resolve every revision in `alembic_version` even when upgrading a single branch. Without this, cross-chain references would fail resolution.

### Credential Store

The `CredentialStore` class (`src/butlers/credential_store.py`) provides DB-first credential resolution backed by the `butler_secrets` table.

#### Resolution Order

When a module calls `store.resolve("TELEGRAM_BOT_TOKEN")`:

1. **Local database** -- Query `butler_secrets` in the butler's own schema.
2. **Shared database** -- Query `butler_secrets` in configured fallback pools (the shared `butlers` database).
3. **Environment variable** -- Fall back to `os.environ["TELEGRAM_BOT_TOKEN"]` if `env_fallback=True` (default).

Database-stored credentials always take precedence over environment variables. This ensures dashboard-stored secrets are authoritative.

#### butler_secrets Table

```sql
CREATE TABLE butler_secrets (
    secret_key   TEXT PRIMARY KEY,
    secret_value TEXT NOT NULL,
    category     TEXT NOT NULL DEFAULT 'general',
    description  TEXT,
    is_sensitive BOOLEAN NOT NULL DEFAULT true,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ
);
```

- `is_sensitive` controls masking in dashboard UI and logs.
- `category` groups secrets for dashboard display (e.g., `"telegram"`, `"google"`, `"cli-auth"`).
- `expires_at` supports time-bounded secrets.
- `list_secrets()` returns `SecretMetadata` objects only -- raw values are NEVER included in list responses.
- Secret values are NEVER logged, even at DEBUG level.

#### Entity-Based Credentials

Identity-bound credentials (OAuth tokens, Telegram user-client sessions) are stored in `shared.entity_info` rather than `butler_secrets`. The `resolve_owner_entity_info(pool, info_type)` function queries the owner entity's entity_info entries.

#### CLI Auth Token Persistence

CLI runtime tokens (Claude, Codex, etc.) are persisted to `butler_secrets` with category `"cli-auth"`. On startup, `restore_tokens()` reconstructs filesystem token files from DB entries, eliminating the need for persistent volume mounts in containerized deployments.

### Security Model

Butlers is a user-federated platform (each user owns their instance). This shapes credential storage:

- Secrets are stored in plaintext in PostgreSQL -- the user controls the database directly.
- Encryption at rest adds minimal value in this model.
- API-level masking prevents accidental exposure in dashboard responses.
- `is_sensitive=True` secrets are excluded from list responses; a "Reveal" button provides on-demand access.

## Integration

- **RFC 0001:** Database provisioning occurs at phase 6, core migrations at phase 7, module migrations at phase 8, credential store creation at phase 8b.
- **RFC 0002:** Modules declare their migration chains via `migration_revisions()`.
- **RFC 0003:** Switchboard-specific tables (`routing_log`, `triage_rules`, `ingestion_events`) live in the switchboard schema.
- **RFC 0004:** All identity tables reside in the `shared` schema with the access model described there.
- **RFC 0007:** The dashboard reads from all butler schemas (via a privileged connection) to provide cross-butler views.
- **RFC 0010:** Documents a sanctioned exception to schema isolation: a read-only SQL view (`general.v_briefing_contributions`) with migration-based cross-schema grants for daily briefing aggregation. Defines reuse criteria for future exceptions.
- **RFC 0011:** Adds `shared.insight_candidates`, `shared.insight_cooldowns`, `shared.insight_engagement`, and `shared.insight_settings` tables to the shared schema for the proactive insight delivery pipeline.
- **RFC 0012:** The finance butler uses dedicated typed-column tables (`finance.transactions` and eight supporting tables) instead of SPO facts for high-volume analytical queries, following the per-butler schema isolation model.

## Alternatives Considered

**Per-butler database instances.** Rejected because the operational overhead (connection management, backup coordination, migration orchestration across N databases) outweighs the isolation benefit. Schema-based isolation provides sufficient boundary enforcement with a single connection pool per butler process.

**Shared migration chain.** Rejected because coupling all module migrations into a single linear chain would create merge conflicts between independent module development streams and require coordinating revision IDs across teams. Multi-chain branching allows each module to evolve its schema independently.

**Vault or encrypted secret storage.** Rejected for the user-federated deployment model. The user controls the PostgreSQL instance directly, so DB-level encryption adds complexity without meaningful security improvement. For enterprise or multi-tenant deployments, a Vault integration could be added as an alternative credential store backend without changing the `CredentialStore` interface.
