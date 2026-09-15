# Core Tables (Every Butler Schema Gets These)

Load when you need the exact DDL of the five core tables, their columns, or
their primary access pattern.

Every butler schema contains five core tables created by
`core_001_target_state_baseline.py`. They are created once in the migration but
land in whichever schema `search_path` points to at migration time, so each
butler gets its own copy (no cross-butler contamination).

| Table | Purpose | Primary access pattern |
|---|---|---|
| `state` | Key-value JSONB store | Point lookups by key, prefix scans |
| `sessions` | Runtime invocation history & trace metadata | Recent-first, lookup by request_id |
| `scheduled_tasks` | Cron-driven recurring prompts + job dispatch | Query enabled + due tasks |
| `route_inbox` | Accept-then-process inbox for route requests | Filter by lifecycle_state |
| `butler_secrets` | Encrypted secrets store (tokens, API keys) | Lookup by secret_key, filter by category |

## 1. `state` — Key-Value Store

General-purpose persistent storage for structured data. Used by core components
and modules for configuration state, counters, flags, cached results,
module-specific KV data.

```sql
CREATE TABLE state (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    version INTEGER NOT NULL DEFAULT 1
);

-- Prefix scans for namespaced keys (e.g., "module:email:%")
CREATE INDEX idx_state_key_prefix ON state (key text_pattern_ops);
```

Namespace keys with colons: `module:email:last_check`, `scheduler:last_tick`,
`config:override:timezone`. `version` tracks mutation count for optimistic
concurrency.

## 2. `sessions` — Runtime Invocation History

Every LLM CLI invocation spawned by this butler is recorded here, with trace
metadata, token usage, and cost tracking.

```sql
CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt TEXT NOT NULL,
    trigger_source TEXT NOT NULL,          -- 'schedule:<task-name>', 'tick', 'external', 'trigger'
    model TEXT,
    success BOOLEAN,
    error TEXT,
    result TEXT,
    tool_calls JSONB NOT NULL DEFAULT '[]'::jsonb,
    duration_ms INTEGER,
    trace_id TEXT,
    request_id TEXT,
    cost JSONB,
    input_tokens INTEGER,
    output_tokens INTEGER,
    parent_session_id UUID,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX idx_sessions_request_id ON sessions (request_id);
```

## 3. `scheduled_tasks` — Cron-Driven Scheduler

Stores TOML-defined (bootstrap) and runtime-created scheduled tasks. Two
dispatch modes: `prompt` (spawns an LLM session) and `job` (calls a Python
function directly).

```sql
CREATE TABLE scheduled_tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    cron TEXT NOT NULL,
    prompt TEXT,                                -- Required for prompt mode, NULL for job mode
    dispatch_mode TEXT NOT NULL DEFAULT 'prompt',
    job_name TEXT,                              -- Required for job mode
    job_args JSONB,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    start_at TIMESTAMPTZ,                      -- Window start (optional)
    end_at TIMESTAMPTZ,                        -- Window end (optional)
    until_at TIMESTAMPTZ,                      -- Expiry date (optional)
    display_title TEXT,
    calendar_event_id UUID,                    -- FK to calendar_events for linked events
    source TEXT NOT NULL DEFAULT 'db',         -- 'toml' or 'db'
    enabled BOOLEAN NOT NULL DEFAULT true,
    next_run_at TIMESTAMPTZ,
    last_run_at TIMESTAMPTZ,
    last_result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT scheduled_tasks_dispatch_mode_check
        CHECK (dispatch_mode IN ('prompt', 'job')),
    CONSTRAINT scheduled_tasks_dispatch_payload_check
        CHECK (
            (dispatch_mode = 'prompt' AND prompt IS NOT NULL AND job_name IS NULL)
            OR (dispatch_mode = 'job' AND job_name IS NOT NULL)
        ),
    CONSTRAINT scheduled_tasks_window_bounds_check
        CHECK (start_at IS NULL OR end_at IS NULL OR end_at > start_at),
    CONSTRAINT scheduled_tasks_until_bounds_check
        CHECK (until_at IS NULL OR start_at IS NULL OR until_at >= start_at)
);

CREATE UNIQUE INDEX ix_scheduled_tasks_calendar_event_id
    ON scheduled_tasks (calendar_event_id)
    WHERE calendar_event_id IS NOT NULL;
```

## 4. `route_inbox` — Accept-Then-Process Inbox

Incoming route requests are accepted immediately (returning an ID) then
processed asynchronously.

```sql
CREATE TABLE route_inbox (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    route_envelope JSONB NOT NULL,
    lifecycle_state TEXT NOT NULL DEFAULT 'accepted',
    processed_at TIMESTAMPTZ,
    session_id UUID,
    error TEXT
);

CREATE INDEX idx_route_inbox_lifecycle_state
    ON route_inbox (lifecycle_state, received_at);
```

## 5. `butler_secrets` — Secrets Store

Generic secrets store for tokens, API keys, and sensitive configuration. Stored
per-butler in the butler's own schema.

```sql
CREATE TABLE butler_secrets (
    secret_key TEXT PRIMARY KEY,
    secret_value TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    description TEXT,
    is_sensitive BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ
);

CREATE INDEX ix_butler_secrets_category ON butler_secrets (category);
```
