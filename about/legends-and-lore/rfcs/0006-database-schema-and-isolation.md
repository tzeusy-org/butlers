# RFC 0006: Database Schema and Isolation

**Status:** Accepted
**Date:** 2026-03-24

## Summary

Butlers uses a single PostgreSQL database with per-butler schemas and the public schema for cross-butler identity tables. Each butler's database connection is scoped to its own schema plus `public`, preventing cross-butler data access. Schema migrations use Alembic with a multi-chain branching model: core, module, and butler-specific chains are discovered automatically and executed independently at startup. A DB-first credential store provides layered secret resolution with environment variable fallback.

## Motivation

Butler isolation is a foundational architectural constraint. Each butler operates as an independent MCP server (see RFC 0002) with its own state, sessions, and domain-specific tables. Schema-based isolation within a single PostgreSQL instance provides this boundary without the operational overhead of per-butler database servers. The public schema is necessary for identity data that all butlers must read (see RFC 0004). The multi-chain migration model allows modules to own their schema evolution independently, avoiding a single migration bottleneck that would couple all modules together.

## Design

### Schema Topology

```
PostgreSQL Database
  |
  +-- public schema (cross-butler tables)
  |     +-- entities           (identity anchor)
  |     +-- contacts           (named contact -> entity link)
  |     +-- contact_info       (per-channel identifiers)
  |     +-- entity_info        (extended entity data / credentials)
  |     +-- google_accounts    (OAuth account registry)
  |     +-- model_catalog             (LLM model definitions)
  |     +-- token_limits              (per-butler token quotas)
  |     +-- token_usage_ledger        (token consumption tracking)
  |     +-- model_dispatch_attempts   (failover attempt provenance)
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
  +-- lifestyle schema
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

### Cross-Butler Identity Tables (in `public`)

See RFC 0004 for the full identity schema. The public schema is readable by all butler database roles. Writes are controlled by specific modules (primarily the contacts module in the relationship butler).

Additional shared infrastructure tables:

- **`public.model_catalog`** -- LLM model definitions (provider, name, pricing, context window, capabilities).
- **`public.token_limits`** -- Per-butler token quotas (daily/monthly caps).
- **`public.token_usage_ledger`** -- Token consumption tracking for quota enforcement.
- **`public.google_accounts`** -- Google OAuth account registry for multi-account support.
- **`public.model_dispatch_attempts`** -- Per-session failover attempt provenance (quota skips, runtime failures, suppressed failovers, successful fallbacks). Written best-effort from the spawner; queried by `GET /api/dispatch/attempts` and `GET /api/settings/models/{id}/attempts`.

### Staffer Schema Permissions and Cross-Butler Access

Staffers reside in their own schemas (e.g., `switchboard`, `messenger`) under
the same isolation rules as domain butlers: each connection's `search_path` is
`<staffer_schema>, public`. A staffer does not inherit elevated database access
simply by virtue of its type.

Cross-butler access is a declarative permissions model governed by
`[butler.permissions]` in `butler.toml`, not by PostgreSQL grants:

```toml
[butler.permissions]
cross_butler_access = ["*"]   # or a list of specific agent names
```

- `["*"]` grants the staffer authorization to connect to and act on behalf of
  any agent in the ecosystem. This is the expected configuration for the
  Switchboard and Messenger staffers.
- A named list (e.g., `["general", "health"]`) scopes access to those agents
  only.
- Domain butlers omit `[butler.permissions]`, defaulting to no cross-butler
  access. They communicate with other agents exclusively through Switchboard
  routing.

This model is enforced at the database level via PostgreSQL role-based access
control (see "Database Connection Scoping" below). Each butler's connection pool
assumes a designated runtime role (`butler_{schema}_rw`) via `SET ROLE`, which
constrains writes to the butler's own schema plus specifically authorized
`public` tables (see "Public Schema Write Authorization Matrix" below). The
declarative `[butler.permissions]` configuration remains authoritative for
application-layer routing decisions.

### Database Connection Scoping

Each butler's database connection sets `search_path` to `<butler_schema>, public`. The pool's
`setup` callback also executes `SET ROLE "butler_{schema}_rw"` on every connection acquired from
the pool, assuming the runtime role created by the `core_001_foundation` migration. This ensures:

- Unqualified queries default to the butler's own schema.
- Schema prefix is optional for identity table reads (but SHOULD be used explicitly for clarity).
- A butler CANNOT access another butler's schema.
- A butler CANNOT write to `public` tables unless explicitly authorized by the write authorization
  matrix below (enforced by PostgreSQL role privileges, not just application convention).
- If the runtime role does not exist (e.g., in a development environment without CREATEROLE), the
  butler logs a warning and falls back to operating with the shared database user's privileges.
- asyncpg's built-in `RESET ALL` on connection return restores the connecting user's role for pool
  safety.

### Public Schema Write Authorization Matrix

Butler runtime roles have write access to a specific set of `public` tables. The grants are
applied by the `core_065` migration. All other `public` tables are read-only for butler roles.

| Public Table | Granted Operations | Used By |
|---|---|---|
| `entities` | INSERT, UPDATE, DELETE | identity module, bootstrap, memory, contacts |
| `contacts` | INSERT, UPDATE | identity module, contacts module |
| `contact_info` | INSERT, UPDATE, DELETE | identity module, contacts, relationship |
| `entity_info` | INSERT, UPDATE, DELETE | google/steam credentials, entity management |
| `google_accounts` | INSERT, UPDATE | google account registry, calendar, drive |
| `steam_accounts` | INSERT, UPDATE, DELETE | steam account registry |
| `user_context` | INSERT, UPDATE | context bus (RFC 0009) |
| `model_round_robin_counters` | INSERT, UPDATE | model routing round-robin |
| `token_usage_ledger` | INSERT | model routing token tracking |
| `ingestion_events` | INSERT, UPDATE, DELETE | ingestion pipeline, switchboard |
| `healing_attempts` | INSERT, UPDATE | QA/healing module |
| `qa_dismissals` | INSERT, UPDATE, DELETE | QA module |
| `qa_findings` | INSERT, UPDATE | QA module |
| `qa_repo_config` | UPDATE | QA module |
| `qa_patrols` | INSERT, UPDATE | QA module |
| `memory_catalog` | INSERT, UPDATE | memory module |
| `facts` | INSERT, UPDATE | finance anomaly detection |
| `insight_candidates` | INSERT, UPDATE, DELETE | insight broker |
| `insight_cooldowns` | INSERT, DELETE | insight broker |
| `insight_engagement` | INSERT, UPDATE, DELETE | insight engagement tracking |
| `insight_settings` | INSERT, UPDATE | insight delivery settings |
| `model_dispatch_attempts` | SELECT, INSERT | failover provenance (core_104 migration) |

Tables not in this matrix (`model_catalog`, `token_limits`, and any future public tables without
explicit grants) are SELECT-only for butler roles. When a new public table is added and butlers
need write access, a subsequent core migration must add the targeted `GRANT` statements and this
matrix must be updated. The authoritative runtime specification is in
`openspec/specs/database-security/spec.md`.

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

### Credential Store — Three-Tier Authority Model

Credentials follow a three-tier authority model. Each credential has exactly one authoritative storage location.

#### Tier 0 — Bootstrap (Environment Variables)

Infrastructure credentials required before the database is available: `POSTGRES_*`, `SWITCHBOARD_MCP_URL`, `OTEL_EXPORTER_OTLP_ENDPOINT`, OAuth redirect URIs, and connector configuration variables. These are the only credentials that may be read directly from `os.environ`.

#### Tier 1 — System (butler_secrets)

Ecosystem-wide credentials not bound to a specific user identity. The `CredentialStore` class (`src/butlers/credential_store.py`) provides DB-first resolution backed by the `butler_secrets` table. Managed via the **System tab** on the dashboard `/secrets` page.

**Resolution order** for `store.resolve("TELEGRAM_BOT_TOKEN")`:

1. **Local database** -- Query `butler_secrets` in the butler's own schema.
2. **Shared database** -- Query `butler_secrets` in configured fallback pools (the shared `butlers` database).
3. **Environment variable** -- Fall back to `os.environ["TELEGRAM_BOT_TOKEN"]` if `env_fallback=True` (default `False`).

Database-stored credentials always take precedence over environment variables.

**Examples:** `BUTLER_TELEGRAM_TOKEN`, `GOOGLE_OAUTH_CLIENT_ID/SECRET`, `BLOB_S3_*`, LLM API keys (`cli-auth/*`), `DISCORD_BOT_TOKEN`, `owntracks_webhook_token`.

#### Tier 2 — User (entity_info on owner entity)

Identity-bound credentials tied to the owner's personal accounts. Stored in `public.entity_info` on the owner entity (resolved via `'owner' = ANY(e.roles)`). Managed via the **User tab** on the dashboard `/secrets` page.

Accessed at runtime via `resolve_owner_entity_info(pool, info_type)` which joins `entity_info` to `entities`, preferring `is_primary=true` rows. Per-account credentials (Google OAuth refresh tokens, Steam API keys) live on companion entities and are resolved via direct SQL keyed by the companion entity UUID.

**Examples:** `home_assistant_token/url`, `telegram_api_id/hash/session/chat_id`, `email/email_password`, `whatsapp_phone`, `google_oauth_refresh` (companion), `steam_api_key` (companion).

**Key rule:** Connectors needing Tier 2 credentials MUST use `resolve_owner_entity_info()`, never `CredentialStore`.

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
- **RFC 0004:** All identity tables reside in the `public` schema with the access model described there.
- **RFC 0007:** The dashboard reads from all butler schemas (via a privileged connection) to provide cross-butler views.
- **RFC 0010:** Documents a sanctioned exception to schema isolation: a read-only SQL view (`general.v_briefing_contributions`) with migration-based cross-schema grants for daily briefing aggregation. Defines reuse criteria for future exceptions.
- **RFC 0011:** Adds `public.insight_candidates`, `public.insight_cooldowns`, `public.insight_engagement`, and `public.insight_settings` tables to the public schema for the proactive insight delivery pipeline.
- **RFC 0012:** The finance butler uses dedicated typed-column tables (`finance.transactions` and eight supporting tables) instead of SPO facts for high-volume analytical queries, following the per-butler schema isolation model.

## Alternatives Considered

**Per-butler database instances.** Rejected because the operational overhead (connection management, backup coordination, migration orchestration across N databases) outweighs the isolation benefit. Schema-based isolation provides sufficient boundary enforcement with a single connection pool per butler process.

**Shared migration chain.** Rejected because coupling all module migrations into a single linear chain would create merge conflicts between independent module development streams and require coordinating revision IDs across teams. Multi-chain branching allows each module to evolve its schema independently.

**Vault or encrypted secret storage.** Rejected for the user-federated deployment model. The user controls the PostgreSQL instance directly, so DB-level encryption adds complexity without meaningful security improvement. For enterprise or multi-tenant deployments, a Vault integration could be added as an alternative credential store backend without changing the `CredentialStore` interface.
