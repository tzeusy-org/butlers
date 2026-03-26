# RFC 0009: Situational Context Bus

**Status:** Draft
**Date:** 2026-03-25

## Summary

A shared awareness layer enabling butlers to read and write the user's current situational context (traveling, sleeping, sick, in a meeting, focused, etc.) via a `public.user_context` table. Context signals have TTLs and expire automatically. Any butler can read the context table; only specific butlers are authorized to write specific signal types. Context checks are lightweight SQL queries performed before action, not a push/subscription model.

## Motivation

Butlers currently operate in isolation. The Health butler does not know the user is traveling. Finance does not know the user is sick. Travel does not know the user is in a focused work block. This leads to poorly timed notifications, redundant questions, and missed opportunities for contextual adaptation.

Examples of context-blind behavior today:

- Health butler sends a workout reminder while the user is in a meeting.
- General butler schedules a deep-focus task while the user is traveling.
- Relationship butler sends a social prompt while the user is sleeping.
- Finance butler asks about a purchase while the user is exercising.

A shared context bus lets each butler check the user's current situation before acting, enabling simple but high-value adaptations: suppressing irrelevant notifications, adjusting tone, deferring non-urgent prompts, or enriching responses with situational awareness.

## Design

### public.user_context Table

```sql
CREATE TABLE public.user_context (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_type     TEXT NOT NULL,         -- enum value from signal vocabulary
    value           TEXT,                  -- optional qualifier (e.g., "Paris" for traveling, "dentist" for appointment)
    set_by_butler   TEXT NOT NULL,         -- butler name that wrote this signal
    set_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,  -- signal is dead after this time
    confidence      REAL NOT NULL DEFAULT 1.0 CHECK (confidence >= 0.0 AND confidence <= 1.0),
    metadata        JSONB,                -- extensible (source event, trigger details, etc.)
    superseded_at   TIMESTAMPTZ,          -- non-null means this signal was explicitly cleared
    UNIQUE (signal_type, set_by_butler)   -- one active signal per type per butler
);

CREATE INDEX idx_user_context_active
    ON public.user_context (signal_type)
    WHERE superseded_at IS NULL AND expires_at > now();
```

Column semantics:

| Column | Purpose |
|--------|---------|
| `signal_type` | What situation the user is in (see vocabulary below) |
| `value` | Optional free-text qualifier giving specifics |
| `set_by_butler` | Which butler asserted this signal (audit trail) |
| `set_at` | When the signal was set |
| `expires_at` | When the signal automatically becomes stale |
| `confidence` | How certain the butler is (1.0 = explicit user statement, lower = inferred) |
| `metadata` | Extensible JSONB for source details (e.g., calendar event ID, flight booking reference) |
| `superseded_at` | Set when a butler explicitly clears a signal before its TTL expires |

The `UNIQUE (signal_type, set_by_butler)` constraint ensures each butler maintains at most one active signal per type. Updating a signal uses `INSERT ... ON CONFLICT DO UPDATE`.

### Signal Vocabulary

A fixed vocabulary of context types. New types require a migration to extend the check constraint.

| Signal Type | Description | Typical TTL | Example Writers |
|-------------|-------------|-------------|-----------------|
| `traveling` | User is on a trip or in transit | 1-14 days | travel, general |
| `sleeping` | User is asleep or in a sleep window | 6-10 hours | health, general |
| `meeting` | User is in a meeting or call | 15 min - 3 hours | general (calendar) |
| `focused` | User is in a deep work / focus block | 1-4 hours | general (calendar) |
| `exercising` | User is working out | 30 min - 2 hours | health |
| `sick` | User is unwell | 1-7 days | health, general |
| `socializing` | User is at a social event | 1-6 hours | relationship, general |
| `commuting` | User is commuting | 15 min - 2 hours | travel, general |
| `at_home` | User is at their home location | 1-24 hours | travel, home, general |
| `away` | User is away / unreachable | 1 hour - 30 days | general |
| `dnd` | Do not disturb (explicit user request) | 1-12 hours | general, switchboard |

The vocabulary is enforced at the application level, not by a database CHECK constraint, to allow easy extension without migrations. The canonical list lives in a Python enum:

```python
class ContextSignal(str, Enum):
    TRAVELING = "traveling"
    SLEEPING = "sleeping"
    MEETING = "meeting"
    FOCUSED = "focused"
    EXERCISING = "exercising"
    SICK = "sick"
    SOCIALIZING = "socializing"
    COMMUTING = "commuting"
    AT_HOME = "at_home"
    AWAY = "away"
    DND = "dnd"
```

### Read/Write Permissions

**Read:** All butlers can read the full `public.user_context` table. Context is shared awareness by definition.

**Write:** Only specific butlers may write specific signal types. This prevents conflicting assertions (e.g., the Finance butler should not be asserting the user is exercising).

| Signal Type | Authorized Writers | Rationale |
|-------------|-------------------|-----------|
| `traveling` | travel, general | Travel butler detects trips; general relays explicit user statements |
| `sleeping` | health, general | Health butler infers from schedule; general relays user statements |
| `meeting` | general | Calendar module detects meeting blocks |
| `focused` | general | Calendar module detects focus blocks |
| `exercising` | health | Health butler tracks workouts |
| `sick` | health, general | Health butler tracks illness; general relays user statements |
| `socializing` | relationship, general | Relationship butler detects social events |
| `commuting` | travel, general | Travel butler detects commute patterns |
| `at_home` | travel, home, general | Travel butler detects Home geofence entry; home butler detects home network/device presence; general relays user statements |
| `away` | general | General butler handles availability |
| `dnd` | general, switchboard | User-initiated; switchboard enforces |

Write permissions are enforced at the application layer. The `set_context()` function validates `(butler_name, signal_type)` against the permissions table and raises `PermissionError` on unauthorized writes.

### TTL Semantics

Signals expire automatically. A signal is **active** when:

```sql
superseded_at IS NULL AND expires_at > now()
```

Expired signals are not deleted; they remain for audit and pattern analysis. A periodic cleanup task (run by any butler's scheduler) can archive signals older than 30 days.

When a butler sets a context signal, it MUST provide an `expires_at` timestamp. There is no indefinite context. Default TTLs per signal type serve as guardrails:

| Signal Type | Default TTL | Max TTL |
|-------------|-------------|---------|
| `traveling` | 24 hours | 30 days |
| `sleeping` | 8 hours | 12 hours |
| `meeting` | 1 hour | 4 hours |
| `focused` | 2 hours | 8 hours |
| `exercising` | 1 hour | 3 hours |
| `sick` | 24 hours | 14 days |
| `socializing` | 3 hours | 12 hours |
| `commuting` | 45 minutes | 3 hours |
| `at_home` | 12 hours | 24 hours |
| `away` | 12 hours | 30 days |
| `dnd` | 2 hours | 24 hours |

If a butler omits a TTL, the default is applied. If a butler requests a TTL exceeding the max, it is clamped to the max.

### Context Query API

Butlers check context via two lightweight functions, both performing simple SQL queries against the public schema.

#### get_active_context()

Returns all currently active context signals:

```python
async def get_active_context(pool: asyncpg.Pool) -> list[ContextEntry]:
    """Return all active (non-expired, non-superseded) context signals."""
    rows = await pool.fetch("""
        SELECT signal_type, value, set_by_butler, set_at, expires_at,
               confidence, metadata
        FROM public.user_context
        WHERE superseded_at IS NULL AND expires_at > now()
        ORDER BY confidence DESC, set_at DESC
    """)
    return [ContextEntry(**row) for row in rows]
```

#### is_user_in_context()

Checks whether a specific context signal is active:

```python
async def is_user_in_context(
    pool: asyncpg.Pool,
    signal_type: str,
    min_confidence: float = 0.5,
) -> bool:
    """Check if the user is currently in a specific context."""
    row = await pool.fetchval("""
        SELECT EXISTS(
            SELECT 1 FROM public.user_context
            WHERE signal_type = $1
              AND superseded_at IS NULL
              AND expires_at > now()
              AND confidence >= $2
        )
    """, signal_type, min_confidence)
    return row
```

#### set_context()

Sets or updates a context signal (with permission check):

```python
async def set_context(
    pool: asyncpg.Pool,
    butler_name: str,
    signal_type: str,
    expires_at: datetime,
    value: str | None = None,
    confidence: float = 1.0,
    metadata: dict | None = None,
) -> None:
    """Set a context signal. Raises PermissionError if butler is not authorized."""
    _check_write_permission(butler_name, signal_type)
    expires_at = _clamp_ttl(signal_type, expires_at)
    await pool.execute("""
        INSERT INTO public.user_context
            (signal_type, value, set_by_butler, set_at, expires_at, confidence, metadata)
        VALUES ($1, $2, $3, now(), $4, $5, $6)
        ON CONFLICT (signal_type, set_by_butler) DO UPDATE SET
            value = EXCLUDED.value,
            set_at = now(),
            expires_at = EXCLUDED.expires_at,
            confidence = EXCLUDED.confidence,
            metadata = EXCLUDED.metadata,
            superseded_at = NULL
    """, signal_type, value, butler_name, expires_at, confidence,
         json.dumps(metadata) if metadata else None)
```

#### clear_context()

Explicitly clears a signal before its TTL:

```python
async def clear_context(
    pool: asyncpg.Pool,
    butler_name: str,
    signal_type: str,
) -> None:
    """Explicitly clear a context signal set by this butler."""
    await pool.execute("""
        UPDATE public.user_context
        SET superseded_at = now()
        WHERE signal_type = $1
          AND set_by_butler = $2
          AND superseded_at IS NULL
    """, signal_type, butler_name)
```

### How Butlers Use Context

Context checking is **pull-based**. Butlers query context at decision points, not via push notifications. This keeps the system simple and avoids coupling between butlers.

Typical integration patterns:

1. **Before sending a notification:** Check for `dnd`, `sleeping`, `meeting`, `focused`. If active, defer or suppress.
2. **Before scheduling a prompt:** Check for `traveling`, `sick`. If active, adjust timing or content.
3. **When building a response:** Include relevant context in the LLM prompt preamble (e.g., "The user is currently traveling in Paris").
4. **In scheduler tick handlers:** Check context before executing scheduled prompts. A health check-in can be skipped if the user is in a meeting.

A butler is NOT required to check context. It is an opt-in enhancement. Butlers that do not check context continue to work exactly as they do today.

### Context Preamble for LLM Sessions

When a butler spawns an LLM session, it MAY prepend a context summary to the prompt:

```
[User Context: traveling (Paris, high confidence), meeting (standup, expires in 15min)]
```

This gives the LLM session awareness of the user's situation without requiring tool calls. The spawner can optionally call `get_active_context()` and format the result as a preamble, similar to the identity preamble (RFC 0004).

### Conflict Resolution

Multiple butlers may assert the same signal type (e.g., both health and general set `sleeping`). The `UNIQUE (signal_type, set_by_butler)` constraint allows this: each butler maintains its own assertion. When reading, the query returns all matching signals. The `confidence` field provides a natural tiebreaker: explicit user statements (`confidence = 1.0`) outrank inferred signals (`confidence < 1.0`).

If butlers disagree (health says sleeping, general says not sleeping via clearing), the higher-confidence signal wins in `is_user_in_context()` because it filters by `min_confidence`. In `get_active_context()`, both signals are returned and the caller decides.

### Migration

The `public.user_context` table is created by a shared-schema migration in `alembic/versions/core/`:

```python
"""add user_context table to public schema"""
revision = "core_XXX"
down_revision = "<previous_core_revision>"

def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.user_context (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            signal_type     TEXT NOT NULL,
            value           TEXT,
            set_by_butler   TEXT NOT NULL,
            set_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at      TIMESTAMPTZ NOT NULL,
            confidence      REAL NOT NULL DEFAULT 1.0
                            CHECK (confidence >= 0.0 AND confidence <= 1.0),
            metadata        JSONB,
            superseded_at   TIMESTAMPTZ,
            UNIQUE (signal_type, set_by_butler)
        );

        CREATE INDEX IF NOT EXISTS idx_user_context_active
            ON public.user_context (signal_type)
            WHERE superseded_at IS NULL AND expires_at > now();
    """)

def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.user_context CASCADE")
```

## Integration

- **RFC 0001:** Context query functions are initialized at daemon startup (phase 8b, alongside credential store). No background tasks are required for the pull-based model.
- **RFC 0002:** Butlers MAY expose MCP tools for setting and querying context. A `check_context` tool gives LLM sessions direct access to situational awareness.
- **RFC 0004:** Context preamble complements the identity preamble. Both are prepended to routed messages when available.
- **RFC 0006:** The `public.user_context` table follows the existing public schema pattern. All butlers read it via their `search_path`. Write access is enforced at the application level, not the database level, consistent with the current public schema access model.
- **RFC 0007:** The dashboard can expose a context timeline view showing active and historical signals.

## Alternatives Considered

**Pub/sub event bus.** Rejected because it adds infrastructure complexity (a message broker or in-process event loop) for a feature that works fine with polling. Butlers already query the database at decision points; adding one more query is negligible. Pub/sub would also create coupling between butlers (subscribers depend on publishers) that contradicts the MCP-only inter-butler communication model (RFC 0002).

**State store (KV) instead of a dedicated table.** Rejected because context signals have structured semantics (TTL, confidence, permissions) that do not map cleanly to a generic KV store. A dedicated table makes these semantics explicit and queryable.

**Push notifications to butlers when context changes.** Rejected for the same reasons as pub/sub. Pull-based checking at decision points is simpler, sufficient, and does not require butlers to maintain listener state or handle missed notifications.

**Per-butler context tables.** Rejected because the entire point is shared awareness. Per-butler tables would recreate the isolation problem this feature solves.
