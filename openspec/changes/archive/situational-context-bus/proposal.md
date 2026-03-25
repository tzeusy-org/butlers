## Why

Butlers operate in isolation with no shared awareness of the user's current situation. The Health butler sends workout reminders during meetings, the General butler schedules focus tasks while the user is traveling, and the Relationship butler prompts social actions while the user is sleeping. A shared situational context bus lets any butler check what the user is currently doing before acting, enabling simple but high-value adaptations: suppressing irrelevant notifications, deferring non-urgent prompts, and enriching LLM sessions with situational awareness.

## What Changes

- New `shared.user_context` table for storing context signals with TTL-based expiry, confidence scores, and per-butler write attribution.
- New Python module (`src/butlers/context_bus.py`) providing `get_active_context()`, `is_user_in_context()`, `set_context()`, and `clear_context()` functions.
- Application-level write permissions: only authorized butlers can write specific signal types (e.g., only Health can set `exercising`, only Travel can set `traveling`).
- Signal vocabulary enum covering core situations: `traveling`, `sleeping`, `meeting`, `focused`, `exercising`, `sick`, `socializing`, `commuting`, `away`, `dnd`.
- Optional context preamble injected into LLM sessions alongside the existing identity preamble.
- Alembic core-chain migration to create the shared table and partial index.
- No pub/sub infrastructure. No message broker. Context checks are plain SQL queries at butler decision points.

## Capabilities

### New Capabilities
- `context-bus`: Shared situational context bus -- table schema, signal vocabulary, TTL semantics, read/write API, permission model, and context preamble for LLM sessions.

### Modified Capabilities
- `core-spawner`: Spawner MAY prepend a context preamble (active situational signals) to the LLM session prompt, alongside the existing identity preamble.

## Impact

- **Database:** New table in `shared` schema, new core-chain Alembic migration. All butlers gain read access via existing `search_path`. No changes to per-butler schemas.
- **Core module:** New `context_bus.py` module with four public functions. No changes to existing core infrastructure (state store, scheduler, sessions).
- **Spawner:** Optional enhancement to prepend context preamble. Backward-compatible -- spawner continues to work without context data.
- **Butler modules:** No mandatory changes. Individual butlers opt in to context checking at their own pace. No butler is required to write or read context signals.
- **Dependencies:** No new external dependencies. Uses existing `asyncpg` pool and `shared` schema access patterns.
