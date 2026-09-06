# RFC 0009: Situational Context Bus

**Status:** Accepted
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
| `in_space` | Owner-scoped room/area the user is currently in at home | 1-24 hours | home, general |
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
    IN_SPACE = "in_space"
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
| `in_space` | home, general | Home butler resolves the owner's current room/area from HA presence data |
| `away` | general | General butler handles availability |
| `dnd` | general, switchboard | User-initiated; switchboard enforces |

Write permissions for every **non-DND** signal remain enforced at the application
layer. The normal `set_context()` function validates `(butler_name,
signal_type)` against the permissions table and raises `PermissionError` on
unauthorized writes. The existing broad `public.user_context` runtime grants are
therefore preserved for ordinary authorized context paths; this RFC does not
turn every signal into a database row-level authorization model.

`dnd` is a safety-critical exception. General and Switchboard must use the
canonical DND operation below, and a database-enforced DND RLS/ACL boundary
rejects direct DND DML even from a runtime role with the ordinary table grant.
That exception prevents an unversioned DND transition from bypassing the guard;
it does not grant any butler a peer-schema read or change non-DND permissions.
The RLS exception constrains writes only: all butlers retain the public DND read
path required for RFC 0009 context checks and guarded snapshots.

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
| `in_space` | 12 hours | 24 hours |
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

### Canonical DND Generation Guard

`dnd` is a safety-critical exception to the ordinary row-upsert examples
above. Its logical state is the OR of active General and Switchboard DND rows;
no individual `user_context` row version is a sufficient DND admission fence.
Every canonical DND mutation therefore uses one durable singleton generation
guard in the `public` context-bus boundary.

The guard has a non-negative, monotonic `BIGINT` generation and a mutation
audit keyed by immutable `mutation_id`. A canonical DND request carries its
writer, operation (`set` or `clear`), affected writer row, opaque stable
correlation reference, and complete DND payload. Each audit row and returned
receipt retain `mutation_id`, generation, verified writer, affected row,
operation, opaque correlation, requested/effective expiry, and commit
timestamp. The private audit additionally retains its semantic fingerprint
version/digest for replay comparison; callers receive no fingerprint/digest.
General's explicit-context MCP tools and every future Switchboard DND path are
the only canonical writers. Health, Messenger, connectors, and other domain
butlers may read a DND snapshot but may not mutate DND.

General's relative `hours` convenience input is non-DND-only. A canonical DND
action that needs a custom TTL carries a stable absolute requested expiry in
the routed action and preserves it on retry; a null requested expiry asks the
database to resolve the DND default once. Recomputing a DND absolute expiry
from `hours` on every invocation is not an exact replay and is rejected before
mutation.

The semantic fingerprint is SHA-256 over a versioned canonical document. It
includes the protocol/signal, verified writer, affected row, operation, opaque
correlation, requested and effective expiry normalized to UTC at PostgreSQL
timestamp precision, effective confidence, and canonicalized metadata. The
optional DND value participates without being retained: null and empty are
distinct, while a present value is Unicode-NFC normalized and represented only
by its SHA-256 digest. Metadata is canonicalized and digested before inclusion.
No raw optional value, metadata, user message text, notification content, or
provider payload is placed in the audit or receipt. A clear uses null expiry
and set-only payload fields.

Timestamp normalization renders the stored UTC instant at PostgreSQL microsecond
precision; confidence uses the validated stored `REAL` binary32 representation.
Canonical metadata JSON sorts Unicode-NFC object keys, preserves array order,
distinguishes null from empty values, uses finite canonical numeric forms, and
rejects unrepresentable input. The correlation reference is a normalized opaque
identifier, never a user-text surrogate.

This unkeyed SHA-256 digest is a content-minimizing replay identity, not a
secrecy primitive. No runtime role may directly read the mutation audit; the
canonical operation reads it only to deduplicate and returns a receipt only to
its canonical writer. Snapshot/admission readers receive neither the digest nor
audit rows. The contract does not claim resistance to a reader who can obtain a
digest and test low-entropy candidate payloads, so the digest must never be
exposed to broad context readers or treated as a credential.

The DND mutation transaction MUST:

1. lock the singleton guard with `FOR UPDATE`;
2. deduplicate `mutation_id`, returning an exact prior receipt only when the
   persisted semantic fingerprint matches and rejecting conflicting replays;
3. validate the effective writer and mutate only that writer's DND context row;
4. advance the guard exactly once and persist a minimal mutation receipt; and
5. commit the row change, generation, and receipt atomically.

For a new set, one database timestamp inside the mutation transaction applies
the existing TTL default/max normalization and persists its effective expiry.
For a retry, the candidate fingerprint combines the retry's normalized inputs
with that persisted effective expiry, so a later database clock cannot silently
turn a defaulted or clamped retry into a different semantic mutation. A changed
requested/effective expiry, optional value, confidence, metadata, writer,
operation, or correlation for the same mutation ID fails closed as
`idempotency_conflict` without changing the row, guard, audit, or prior receipt.
If the persisted row lacks its fingerprint version/digest, uses an unsupported
fingerprint version, or has expiry fields incompatible with its operation
(including a set without an effective expiry), the replay identity is unprovable:
the operation returns `replay_identity_unprovable` before DND DML and never
infers a replacement identity, guesses a receipt, or applies a second mutation.

Direct DND DML that bypasses this path is forbidden by the database security
boundary. `ENABLE` plus `FORCE ROW LEVEL SECURITY` preserves existing direct
runtime access to non-DND rows but denies DND and DND-crossing updates. A
pinned `SECURITY INVOKER` gateway is executable only by the General and
Switchboard runtime roles and validates `current_user` before it calls the
private pinned `SECURITY DEFINER` operation. The private definer independently
checks `current_setting('role', true)`, because its `current_user` is the owner;
neither layer trusts a caller-supplied writer, `session_user`, or an absent role.
General and Switchboard may mutate only their own DND row. Every role/writer,
direct/private invocation, ACL, RLS, or guard boundary that cannot be proved
fails closed. A counter that would exceed `BIGINT` maximum fails closed before
changing DND; it never wraps, resets, or reuses a generation.

#### Trusted bootstrap and authority handoff

The DND boundary is installed only by a trusted cluster-superuser bootstrap
interface in `scripts/init-db.sql`. Its fixed no-argument installer and
finalizer use pinned search paths and reject untrusted pre-existing authority
objects; a normal core migration may only catalog-validate a finalized trusted
interface or invoke that fixed installer. It MUST NOT create, adopt, re-own,
repair, or grant authority to a similarly named DND role, table, policy,
gateway, or definer.

A planned `core_197` downgrade may invoke only a separately catalog-proven,
fixed no-argument bootstrap rollback routine as a trusted superuser. That route
first requires an empty mutation audit and singleton generation `0`, then keeps
all `public.user_context` rows while restoring the recorded migration-role
ownership/RLS posture and only the installer handoff. Any durable receipt or
advanced generation fails closed before destructive DDL; it requires a separate
audited recovery migration rather than dropping or reseeding the guard.

Before it transfers the legacy shared table, the installer verifies its complete
known column/key shape, recorded migration-role ownership, and ordinary
disabled-RLS posture; it rejects any pre-existing user policy, trigger, or
rewrite rule. In particular, it cannot accept an arbitrary permissive RLS
policy: policies combine permissively, and such a policy could otherwise reopen
direct DND DML alongside the guarded policy set. Nor can it accept a predecessor
posture that the bounded rollback could not restore. It holds an `ACCESS
EXCLUSIVE` lock through validation and final ownership transfer so the former
shared-table owner cannot race those checks.

The finalizer hands ownership of `public.user_context`, the singleton guard,
the mutation audit, DND policies, and private definer to one dedicated NOLOGIN,
non-superuser, non-BYPASSRLS role. That role has no runtime or migration
membership and is not a caller principal. Final catalog proof includes table
ownership, `ENABLE` and `FORCE RLS`, policy predicates, function owner/security
and pinned search path, and revocation of `PUBLIC`, migration-role, direct, and
cross-role DND authority. Runtime roles receive no direct guard/audit DML or
audit read; only the two canonical writer roles receive the minimal public
gateway and private-function/schema call-chain grants. PostgreSQL requires
those call-chain grants for an invoker gateway, so the private definer repeats
the active-role/writer check and rejects any direct call that cannot prove it.
The durable audit and replay receipt retain no raw DND value, metadata, user
text, notification content, or provider payload.

The real-PostgreSQL migration/role/catalog suite is mandatory evidence for this
boundary: it must prove ownership and forced RLS, approved gateway success,
direct DML denial, cross-writer denial, private-definer denial or active-role
recheck, audit non-disclosure, and ordinary non-DND runtime writes. Static
source tests do not substitute for that execution proof.

The policy is command-specific rather than a blanket `FOR ALL` restriction: it
must not hide DND rows from the shared public read path used by snapshots and
ordinary context checks.

#### DND snapshots and admission

A DND snapshot contains `{generation, dnd_active, observed_at,
revalidate_at}` and is evidence rather than an egress authorization.
`observed_at` and active-state evaluation use database time. For an active
snapshot, `revalidate_at` is the earliest active DND expiry, the first
time-based state transition that can happen without a writer transaction. An
inactive snapshot has no expiry-driven activation: any future set or refresh
must advance generation.

A consumer turning an inactive snapshot into a durable local effect MUST
revalidate it inside the same database transaction that writes that effect. It
takes `FOR SHARE` on the same guard, requires the captured generation and an
inactive current DND state, then commits or rolls back its own record before
releasing the lock. Because writers take the conflicting `FOR UPDATE` lock
first, a DND mutation either advances the generation before admission (which
makes the admission reject) or waits until the local admission boundary is
durable. The lock is never held across MCP or provider I/O.

Health owns its policy/wake decision and Messenger owns final egress admission;
both are future consumers of this contract. Messenger repeats the guarded
check in the transaction that persists its own egress intent. Switchboard only
carries generation/correlation through authenticated MCP packets and never
substitutes a peer-schema SQL check. A DND change after a durable local
admission invalidates later or retry admission; it cannot retract an already
admitted external effect.

#### Precommit cancellation admission

The wake-recovery cancellation consumer is a separate, future protocol layered
on this admission helper. After a complete cohort is durably prepared but
before any Messenger egress intent or send-start marker exists, Health or an
origin Scheduler may provide a local cancellation decision only to the
current-fence Switchboard coordinator. Switchboard carries its immutable
run/fence/cohort/action correlation and captured DND generation through an
authenticated MCP packet; it does not read a peer queue or Messenger state.

Messenger is the final cancellation-admission owner. In the same local
transaction that locks its private prepared-release gate and writes its durable
cancellation receipt, it invokes the guarded DND admission with the captured
generation. It accepts cancellation only if that generation is current, DND is
inactive using database time, and it proves that no egress intent, send-start,
provider receipt, or ambiguous provider-attempt state exists. The receipt is
idempotent and binds the complete cohort; it is never a provider operation.

A changed, active, missing, stale, or otherwise unprovable DND generation
produces Messenger's durable `rejected_blocked_dnd` receipt, not ordinary
scheduler work. Switchboard persists that same-fence receipt and fans the
parent wake-recovery `abort.v1(reason=blocked_dnd)` operation to every current
participant until each returns its parent-defined retained-state receipt. Each
origin performs the parent `aborted_dnd` / `release_retained_dnd` transition
only for its own frozen subset; Switchboard and origins do not re-read DND or
mutate a peer queue to emulate it. A missing fanout receipt remains retained
and replayable, never a partial publication.

An egress-present or ambiguous result likewise never returns a cohort to the
scheduler or authorizes a resend. Only a matching accepted receipt delivered by
the authenticated Switchboard-to-origin `cancel_finalize.v1` operation, followed
by one durable same-fence finalization receipt per participant and authenticated
per-origin `cancel_publish.v1` receipts, may enter the separate Scheduler-return
path. Switchboard retries the same finalization action/request after a timeout
or restart and cannot publish from a missing or conflicting receipt. Any later
effective egress must perform its own Messenger guarded admission. This rule
consumes the canonical generation guard; it does not add a DND writer, alter
mutation/replay semantics, or introduce a peer-schema access exception.

TTL expiry does not itself increment generation. Consumers cannot rely on an
active snapshot at or after `revalidate_at`; they re-read current DND under the
guard using database time. A later DND refresh or reactivation after clear or
expiry is a new mutation with a new ID and advances generation exactly once.
Restart recovery uses the durable guard, mutation audit (including fingerprint
and effective expiry), and canonical context rows only. Missing, stale,
malformed, or otherwise unprovable evidence fails closed for an admission.

### How Butlers Use Context

Context checking is **pull-based**. Butlers query context at decision points, not via push notifications. This keeps the system simple and avoids coupling between butlers.

Typical integration patterns:

1. **Before sending a notification:** Check for `dnd`, `sleeping`, `meeting`, `focused`. If active, defer or suppress.
2. **Before scheduling a prompt:** Check for `traveling`, `sick`. If active, adjust timing or content.
3. **When building a response:** Include relevant context in the LLM prompt preamble (e.g., "The user is currently traveling in Paris").
4. **In scheduler tick handlers:** Check context before executing scheduled prompts. A health check-in can be skipped if the user is in a meeting.

A butler is NOT required to check context. It is an opt-in enhancement. Butlers that do not check context continue to work exactly as they do today.

### Owner Attention Policy Sleep Producer

The deterministic health `sleeping` producer derives its membership and TTL
from the shared `public.approvals_policy` Owner Attention Policy. Its interval
is `[quiet_start_hour, quiet_end_hour)` in the stored IANA timezone, and its
`expires_at` is the exact configured end converted to UTC. Missing, incomplete,
or invalid persisted policy data fails open: the producer does not invent a
timezone or a replacement wake time and clears/avoids its derived signal.

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
- **RFC 0006:** The `public.user_context` table follows the existing public
  schema pattern. All butlers read it via their `search_path`. Non-DND writes
  retain the current application-level authorization and broad runtime grant
  model. DND is the documented narrow exception: forced RLS plus a dedicated
  security-definer mutation operation prevent direct DND DML, while preserving
  schema isolation and the database-security role matrix.
- **RFC 0007:** The dashboard can expose a context timeline view showing active and historical signals.
- **Owner Attention Policy:** The deterministic health sleep producer uses the
  shared exact-end policy anchor; this is a read/TTL projection, not a new
  context clock, cron, or wake/catch-up path.

## Alternatives Considered

**Pub/sub event bus.** Rejected because it adds infrastructure complexity (a message broker or in-process event loop) for a feature that works fine with polling. Butlers already query the database at decision points; adding one more query is negligible. Pub/sub would also create coupling between butlers (subscribers depend on publishers) that contradicts the MCP-only inter-butler communication model (RFC 0002).

**State store (KV) instead of a dedicated table.** Rejected because context signals have structured semantics (TTL, confidence, permissions) that do not map cleanly to a generic KV store. A dedicated table makes these semantics explicit and queryable.

**Push notifications to butlers when context changes.** Rejected for the same reasons as pub/sub. Pull-based checking at decision points is simpler, sufficient, and does not require butlers to maintain listener state or handle missed notifications.

**Per-butler context tables.** Rejected because the entire point is shared awareness. Per-butler tables would recreate the isolation problem this feature solves.
