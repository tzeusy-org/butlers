# Generation-Fenced Codex Auth Rotation Provenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every Codex runtime, prewarm, device-auth, and health operation
prove its exact shared authority generation before launch and before it can
persist a result, while retaining the existing Tier 1 credential row as the
sole raw-value store.

**Architecture:** Add a small, value-free, system-global provenance boundary:
a singleton current-generation pointer, append-only random opaque generations,
and short-lived operations.  Credential-store methods linearize all owner
mutations and conditional child completions at that singleton, while the
adapter gives every child a private stage and treats a staged result only as a
candidate.  Existing local projections remain compatibility outputs, never
recovery authority.

**Tech Stack:** Python 3.12+, asyncpg, PostgreSQL, Alembic, asyncio, pytest,
Ruff, OpenSpec, Docker/CLI-auth sandbox helpers.

## Global Constraints

- The raw `cli-auth/codex` document remains durably only in the existing Tier
  1 `public.butler_secrets` row; no new relation, log, exception, fixture, or
  audit projection may contain it. Preserve the existing owner-only rotate
  `{fingerprint, value}` raw-value-once response as an explicit compatibility
  contract, but add no other response reveal.
- Generation and operation IDs are random database-issued UUIDs. They are not
  credential-derived fingerprints/digests, public identifiers, bearer
  capabilities, PID/session/file identities, or ordering proofs.
- PostgreSQL time may enforce an operation deadline and retention cutoff, but
  may not decide which authority wins; the locked current-generation pointer
  is the only ordering authority.
- A missing, malformed, duplicate, stale, mismatched, unavailable, or
  otherwise unprovable binding fails closed. It never authorizes a local file,
  stage, process cache, or generic legacy write to become authority.
- The dashboard owner save/rotate and revoke paths are serialized authority
  mutations. Runtime, prewarm, device-auth, and health are conditional
  operations. The first valid conditional successor may win; a later owner
  replacement is intentionally current.
- Preserve the existing Codex rotate `{fingerprint, value}` response,
  inventory/detail on-read display fingerprint, other public response
  envelopes, and existing non-Codex `butler_secrets` semantics. Do not expose
  generation/operation/lineage data to browser, MCP, command line, logs,
  telemetry, or audit text; the display fingerprint is not provenance.
- Runtime children use one private stage per operation inside kernel-enforced
  per-invocation isolation: a distinct Bubblewrap mount/PID/user namespace and
  unique leased outer UID/GID for every concurrent child. Stage path separation
  and mode `0600` are defense in depth, not the peer boundary. The Codex CLI's
  `--dangerously-bypass-approvals-and-sandbox` flag may disable only its inner
  policy sandbox; it SHALL remain inside the parent-created kernel boundary.
  Canonical `~/.codex/auth.json` is a database-originated compatibility
  projection under a cross-process lock and atomic `0600` replacement; lock
  failure blocks the associated launch rather than allowing an unlocked launch.
- Keep the established provider execution timeout and bounded authority
  setup/finalizer allowance. An authority deadline is absolute across setup,
  launch, retries, chunks, and cleanup.
- Additive rollout only: no credential parsing/copying in the migration, no
  automatic post-initialization adoption of a raw legacy write, no unsafe
  fallback mode, and no migration/deployment/live-auth action from this plan.
- Terminal operation metadata is retained for at least 90 days. Current,
  referenced, and append-only generation records are never removed by the
  v1 cleanup path.
- Before the first implementation edit, capture the reviewed packet base with
  `IMPLEMENTATION_REVIEW_BASE="$(git rev-parse HEAD)"` and retain it in the
  implementation handoff. Every final diff/static review SHALL inspect
  `${IMPLEMENTATION_REVIEW_BASE}...HEAD`, including committed changes.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `scripts/init-db.sql` | Privileged bootstrap that creates the NOLOGIN provenance owner plus fixed no-argument installer/finalizer before migrations use it. |
| `alembic/versions/core/core_199_codex_auth_generation_provenance.py` | Catalog-verifies and invokes the trusted bootstrap installer after the live `core_198` head; it does not create or own the protected boundary itself. |
| `tests/migrations/test_codex_auth_generation_provenance_migration.py` | Unit checks the migration chain and static schema/DDL invariants. |
| `tests/config/test_init_db_codex_auth_generation_boundary.py` | Proves bootstrap SQL establishes the intended no-login ownership and privilege shape. |
| `tests/config/test_codex_auth_generation_acl_integration.py` | Uses real PostgreSQL effective roles to prove guarded access and direct-DML rejection. |
| `tests/config/test_codex_auth_generation_concurrency_integration.py` | Uses independent real PostgreSQL connections to prove winner/loser serialization and absence of stale health writes. |
| `src/butlers/credential_store.py` | Defines the typed, value-redacted authority leases/results and executes the guarded DB operations. |
| `tests/config/test_credential_store.py` | Exercises atomic binding, conditional completion, owner precedence, expiry, redaction, and unavailable outcomes. |
| `src/butlers/core/runtimes/_codex_auth_sync.py` | Retains strict parsing and safe projection only; removes local digest/cache as successor authority. |
| `src/butlers/core/runtimes/codex.py` | Prepares, rechecks, launches, stages, finalizes, and discards exact-generation Codex operations. |
| `tests/adapters/test_codex_auth_sync.py` | Covers projection, stage containment, no local recovery inference, and lock-failure behavior. |
| `tests/adapters/test_codex_refresh_lock.py` | Covers cross-process projection-lock behavior without an unlocked fallback. |
| `tests/adapters/test_codex_adapter.py` | Covers invoke/prewarm operation fencing, one deadline, race outcomes, and cleanup. |
| `src/butlers/core/spawner.py` | Supplies the selected system-global credential authority to each Codex adapter. |
| `src/butlers/connectors/discretion_dispatcher.py` | Supplies that same boundary to direct-dispatch Codex adapters. |
| `tests/core/test_core_spawner.py` | Proves normal Spawner construction uses the typed authority. |
| `tests/connectors/test_discretion_dispatcher.py` | Proves direct dispatcher construction uses the typed authority. |
| `src/butlers/cli_auth/persistence.py` | Converts Codex device-auth persistence to a durable prepare/complete operation. |
| `src/butlers/cli_auth/session.py` | Retains one redacted lease/stage context from preparation through launch and finalization. |
| `src/butlers/cli_auth/sandbox.py` | Defines the two-phase prepare-then-launch sandbox protocol and stage seed contract. |
| `src/butlers/cli_auth/sandbox_platform.py` | Seeds the exact private HOME, starts no child before successful launch marking, and retains the stage through containment. |
| `src/butlers/cli_auth/health.py` | Converts the parent-only Codex health probe to a lease-bound outcome without local-file authority. |
| `src/butlers/api/routers/cli_auth.py` | Uses the device-auth/probe operation APIs and retains value-free session/API output. |
| `src/butlers/api/routers/secrets_v2.py` | Routes Codex owner save/rotate/revoke through serialized authority methods. |
| `src/butlers/api/app.py`, `src/butlers/api/routers/model_settings.py`, `src/butlers/jobs/model_verify.py` | Preserve explicit shared-authority injection for lifespan, model-setting verification, and model verification callers. |
| `tests/cli/test_cli_auth.py` | Covers device-auth bootstrap, stale completion, containment failure, and value-free failure output. |
| `tests/cli/test_runtime_cli_sandbox.py` | Covers private-stage delivery to the owning child, two-phase launch, and cleanup on stale mark/cancel/timeout. |
| `tests/api/test_secrets_v2_cli_mutations.py` | Covers save/rotate/revoke precedence and no internal provenance response fields. |
| `tests/api/test_secrets_v2_cli_reauthorize.py` | Covers reauthorization against a current or explicitly absent uninitialized authority. |
| `tests/api/test_secrets_v2_codex_authority.py` | Covers shared-authority selection and fence-aware API contract. |
| `frontend/src/api/client.secrets-v2.test.ts` | Locks the existing Codex rotate and display-fingerprint client envelopes without provenance fields. |
| `frontend/src/components/secrets/passport/PageCli.actions.test.tsx` | Locks the owner copy-once rotate UX while excluding generation/operation state. |
| `tests/api/test_app_lifespan_supervision.py`, `tests/api/test_model_settings.py`, `tests/jobs/test_model_verify.py` | Cover shared-authority injection through non-request API/model-verify callers. |
| `src/butlers/jobs/retention.py` | Provides deterministic expiry and terminal-operation pruning without provider work. |
| `src/butlers/scheduled_jobs.py` | Registers the disabled-by-default deterministic provenance cleanup handler. |
| `tests/jobs/test_retention_pruners.py` | Covers disabled, dry-run, exact 90-day, deletion, and pre-migration-safe cleanup. |
| `src/butlers/daemon.py`, `src/butlers/lifecycle.py` | Use a complete shared binding only for startup/restore and make cleanup wiring available. |
| `tests/daemon/test_startup_coverage_gaps.py` | Covers a fresh daemon restoring a complete binding and rejecting orphan local state. |
| `tests/connectors/test_connector_codex_auth_restore.py` | Covers connector restoration with the selected fence-aware shared authority. |
| `docs/data_and_storage/credential-store.md` | Documents opaque authority generations and value-free storage boundary. |
| `docs/identity_and_secrets/cli-runtime-auth.md` | Documents private stages, recovery, precedence, and no-local-authority rule. |
| `docs/concepts/butler-lifecycle.md` | Documents deterministic cleanup and restart posture. |
| `docs/architecture/butler-daemon.md` | Documents shared-authority topology and direct-dispatch behavior. |
| `docs/runtime/spawner.md` | Documents exact-generation prelaunch/finalization boundaries. |
| `tests/contracts/test_codex_auth_generation_completeness.py` | Enumerates Codex authority call sites so a bypass cannot be silently reintroduced. |

## Shared Interfaces

Task 2 introduces the only application-level vocabulary used by later tasks.
It deliberately carries raw documents only as transient `repr=False` values.

```python
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class CodexAuthOperationKind(StrEnum):
    RUNTIME_INVOKE = "runtime_invoke"
    RUNTIME_PREWARM = "runtime_prewarm"
    DEVICE_AUTH = "device_auth"
    HEALTH_PROBE = "health_probe"


class CodexAuthHealthOutcome(StrEnum):
    OK = "ok"
    AUTH_REJECTED = "auth_rejected"
    TIMEOUT = "timeout"
    TRANSPORT_UNAVAILABLE = "transport_unavailable"
    UNKNOWN = "unknown"


class CodexAuthAbandonReason(StrEnum):
    STAGE_PREPARE_FAILED = "stage_prepare_failed"
    PRELAUNCH_CANCELLED = "prelaunch_cancelled"
    LAUNCH_MARK_FAILED = "launch_mark_failed"
    LAUNCH_FAILED = "launch_failed"
    CANCELLED = "cancelled"
    CONTAINMENT_FAILED = "containment_failed"
    INVALID_STAGED_OUTPUT = "invalid_staged_output"
    PERSISTENCE_UNAVAILABLE = "persistence_unavailable"


class CodexAuthCompletionDisposition(StrEnum):
    COMPLETED_UNCHANGED = "completed_unchanged"
    COMPLETED_SUCCESSOR = "completed_successor"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    DISCARDED = "discarded"
    UNAVAILABLE = "unavailable"


class CodexAuthLaunchDisposition(StrEnum):
    LAUNCHED = "launched"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class CodexAuthLaunchLease:
    operation_id: UUID = field(repr=False)
    generation_id: UUID = field(repr=False)
    authority_document: str = field(repr=False)
    deadline_at: datetime


@dataclass(frozen=True)
class CodexAuthBootstrapLease:
    operation_id: UUID = field(repr=False)
    deadline_at: datetime


@dataclass(frozen=True)
class CodexAuthProjectionBinding:
    generation_id: UUID = field(repr=False)
    authority_document: str = field(repr=False)


@dataclass(frozen=True)
class CodexAuthUnavailable:
    reason: CodexAuthCompletionDisposition


@dataclass(frozen=True)
class CodexAuthCompletion:
    disposition: CodexAuthCompletionDisposition
    successor_generation_id: UUID | None = field(default=None, repr=False)


@dataclass(frozen=True)
class CodexAuthLaunchResult:
    disposition: CodexAuthLaunchDisposition
```

`CredentialStore` will own these methods. All four use the explicitly
selected system-global pool and return a safe unavailable result instead of
falling back to a schema-local pool or a local file.

```text
CredentialStore.prepare_codex_auth_operation(
    kind: CodexAuthOperationKind,
    deadline_at: datetime,
) -> CodexAuthLaunchLease | CodexAuthUnavailable

CredentialStore.prepare_codex_auth_device_bootstrap(
    deadline_at: datetime,
) -> CodexAuthBootstrapLease | CodexAuthUnavailable

CredentialStore.read_current_codex_auth_projection_binding(
) -> CodexAuthProjectionBinding | CodexAuthUnavailable

CredentialStore.mark_codex_auth_operation_launched(
    operation_id: UUID,
    expected_generation_id: UUID | None,
) -> CodexAuthLaunchResult

CredentialStore.complete_codex_auth_operation(
    operation_id: UUID,
    expected_generation_id: UUID | None,
    validated_successor_document: str | None,
    health: CodexAuthHealthOutcome | None,
) -> CodexAuthCompletion

CredentialStore.abandon_codex_auth_operation(
    operation_id: UUID,
    expected_generation_id: UUID | None,
    reason: CodexAuthAbandonReason,
) -> CodexAuthCompletion

CredentialStore.replace_codex_auth_as_owner(value: str) -> None

CredentialStore.revoke_codex_auth_as_owner() -> bool
```

The normal prepare operation has no caller-controlled bootstrap switch. It
calls a definer that requires a complete current generation. The separate
device-auth bootstrap method calls a different definer granted only to the
dashboard API's no-`SET ROLE` shared-authority path, not any effective runtime
role; that function derives
`bootstrap_absent` from locked database state. The read-only projection method
creates no operation and authorizes no child.

## Task 1: Establish the Reserved Database Boundary

**Files:**

- Create: `alembic/versions/core/core_199_codex_auth_generation_provenance.py`
- Create: `tests/migrations/test_codex_auth_generation_provenance_migration.py`
- Create: `tests/config/test_init_db_codex_auth_generation_boundary.py`
- Create: `tests/config/test_codex_auth_generation_acl_integration.py`
- Create: `tests/config/test_codex_auth_generation_concurrency_integration.py`
- Modify: `scripts/init-db.sql`
- Test: `tests/migrations/test_codex_auth_generation_provenance_migration.py`
- Test: `tests/config/test_init_db_codex_auth_generation_boundary.py`
- Test: `tests/config/test_codex_auth_generation_acl_integration.py`
- Test: `tests/config/test_codex_auth_generation_concurrency_integration.py`

**Consumes:** The existing `core_198` fixed-installer/finalizer/rollback pattern and migration
head, `public.butler_secrets`, and the shared-login/`SET ROLE` runtime model.

**Produces:** A privileged-bootstrap-owned fixed installer/finalizer and a
`core_199` migration that verifies/invokes it to install guarded database
operations. No application code or normal migration login may own or update
the protected boundary with generic DML.

- [ ] **Step 1: Write migration and ACL tests before changing bootstrap SQL or schema**

```python
def test_core_199_has_additive_chain_and_value_free_provenance() -> None:
    module = _load_migration()
    source = _MIGRATION_PATH.read_text()

    assert module.revision == "core_199"
    assert module.down_revision == "core_198"
    assert "public.codex_auth_authority_state" in source
    assert "public.codex_auth_generations" in source
    assert "public.codex_auth_operations" in source
    assert "codex_auth_generation_id" in source
    assert "secret_value" not in _new_relation_column_definitions(source)
    assert "sha256" not in source.lower()
    assert "digest" not in source.lower()


async def test_runtime_role_cannot_directly_mutate_reserved_codex_row(
    effective_role_connection: asyncpg.Connection,
) -> None:
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await effective_role_connection.execute(
            "UPDATE public.butler_secrets SET updated_at = now() "
            "WHERE secret_key = 'cli-auth/codex'"
        )


async def test_runtime_prepare_role_cannot_invoke_device_bootstrap(
    runtime_prepare_connection: asyncpg.Connection,
) -> None:
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await runtime_prepare_connection.fetchrow(
            "SELECT * FROM public.codex_auth_prepare_device_bootstrap($1)",
            deadline_in_future(),
        )


async def test_normal_migration_requires_exact_privileged_installer(
    upgraded_database_without_new_bootstrap: asyncpg.Connection,
) -> None:
    with pytest.raises(asyncpg.RaiseError, match="bootstrap installer"):
        await run_core_199(upgraded_database_without_new_bootstrap)


async def test_non_codex_secret_write_retains_established_role_path(
    effective_role_pool: asyncpg.Pool,
) -> None:
    store = CredentialStore(effective_role_pool)
    await store.store(
        "contract-non-codex-key",
        "contract-fixture-value",
        category="general",
    )


async def test_present_malformed_raw_row_cannot_bootstrap(
    real_postgres_connection: asyncpg.Connection,
) -> None:
    await insert_present_malformed_codex_row(real_postgres_connection)

    result = await prepare_device_bootstrap_as_dashboard(real_postgres_connection)

    assert result.disposition == "unavailable"
    assert await reserved_row_is_unchanged(real_postgres_connection)


async def test_effective_roles_execute_only_their_guarded_operations(
    effective_role_connections: EffectiveRoleConnections,
) -> None:
    await assert_runtime_can_prepare_only_normal_operation(effective_role_connections)
    await assert_dashboard_can_call_only_documented_owner_operations(effective_role_connections)
    await assert_connector_and_public_have_no_codex_interface(effective_role_connections)
    await assert_non_codex_secret_crud_is_unchanged(effective_role_connections)
```

- [ ] **Step 2: Run the new tests and confirm the missing migration/boundary fails**

Run:

```bash
uv run pytest \
  tests/migrations/test_codex_auth_generation_provenance_migration.py \
  tests/config/test_init_db_codex_auth_generation_boundary.py \
  tests/config/test_codex_auth_generation_acl_integration.py \
  tests/config/test_codex_auth_generation_concurrency_integration.py \
  -q --override-ini='addopts='
```

Expected: failure because `core_199`, the trusted bootstrap installer,
NOLOGIN interface owner, and guarded operations do not yet exist.

- [ ] **Step 3: Add a privileged installer/finalizer and an additive core_199 invoker**

Follow the current `core_198` runtime-attention bootstrap boundary rather than letting Alembic create
objects owned by its ordinary login. Extend `scripts/init-db.sql` with:

- a `codex_auth_provenance_owner` role that is NOLOGIN, NOINHERIT,
  non-superuser, has no memberships, and cannot be assumed by migration or
  runtime logins;
- a bootstrap-superuser-owned `codex_auth_provenance_admin` schema containing
  fixed, no-argument `install_interface()` and `finalize_interface()`
  `SECURITY DEFINER` functions with fixed search paths and no caller-controlled
  object names or DDL;
- exact catalog/ownership checks that reject partial or familiar-looking
  objects before install/finalize; and
- temporary installer execution for the configured migration login, which the
  finalizer revokes together with admin-schema usage, owner membership,
  protected-schema `CREATE`, and direct relation DML.

The installer/finalizer must account for the bootstrap's existing broad
`public` table grants rather than assuming a new relation or trigger is hidden.
It SHALL enable and `FORCE ROW LEVEL SECURITY` on `public.butler_secrets`, drop
all non-system policies on every repair, then recreate exactly two policies:
`codex_auth_non_reserved_secrets` permits the existing granted roles to access
only rows whose `secret_key <> 'cli-auth/codex'`, while
`codex_auth_reserved_owner` permits the membership-free NOLOGIN definer owner
to access the reserved row. The fixed owner owns every provenance relation and
definer function; the existing migration role may remain the
`public.butler_secrets` DDL owner but FORCE RLS gives it no reserved-row data
bypass during ordinary execution.

After every broad-grant bootstrap rerun, the finalizer SHALL revoke table-level
`SELECT` and `UPDATE` from the migration login, all runtime roles,
`connector_writer`, and `PUBLIC`; explicitly
`REVOKE SELECT (codex_auth_generation_id)` and
`REVOKE UPDATE (codex_auth_generation_id)`;
and restore only the precise legacy-column SELECT/INSERT/UPDATE plus row-level
DELETE privileges needed for non-Codex `CredentialStore` CRUD. The reserved
owner alone receives the binding-column access needed by fixed definers. Exact
catalog checks must use `has_column_privilege`, `aclexplode`, `pg_policy`,
`relrowsecurity`, and `relforcerowsecurity` to reject broad table grants, a
readable/updatable `codex_auth_generation_id`, unexpected permissive policies,
or stale owner membership. Effective-role tests must prove that a standard
column read/write for a non-Codex row still succeeds, the reserved row is
invisible outside the definer, and a direct binding-column read raises
`InsufficientPrivilege`.

`core_199` first accepts an already-finalized interface only after proving its
exact owner, function signatures, search paths, ACLs, and absence of migration-
owner bypass. Otherwise it proves the exact trusted bootstrap installer and
invokes it. If `scripts/init-db.sql` has not been rerun on an upgraded database,
the migration fails closed with an actionable bootstrap-ordering error and
creates nothing. The normal migration never creates the protected schema,
relations, functions, or owner role itself.

The fixed installer creates the following value-free data shape, using
`gen_random_uuid()` only at the database boundary:

```sql
CREATE TABLE public.codex_auth_authority_state (
    authority_key TEXT PRIMARY KEY CHECK (authority_key = 'cli-auth/codex'),
    current_generation_id UUID NULL,
    has_ever_initialized BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE public.codex_auth_generations (
    generation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    predecessor_generation_id UUID NULL
        REFERENCES public.codex_auth_generations(generation_id),
    source_kind TEXT NOT NULL CHECK (
        source_kind IN ('legacy_adoption', 'owner_replace', 'device_auth', 'runtime_rotation')
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    retired_at TIMESTAMPTZ NULL,
    retired_reason TEXT NULL CHECK (
        retired_reason IN ('superseded', 'revoked')
    )
);

CREATE TABLE public.codex_auth_operations (
    operation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    generation_id UUID NULL REFERENCES public.codex_auth_generations(generation_id),
    bootstrap_absent BOOLEAN NOT NULL DEFAULT FALSE,
    kind TEXT NOT NULL CHECK (
        kind IN ('runtime_invoke', 'runtime_prewarm', 'device_auth', 'health_probe')
    ),
    state TEXT NOT NULL CHECK (
        state IN ('prepared', 'launched', 'completed_unchanged',
                  'completed_successor', 'discarded', 'superseded', 'expired')
    ),
    deadline_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    launched_at TIMESTAMPTZ NULL,
    terminal_at TIMESTAMPTZ NULL,
    successor_generation_id UUID NULL REFERENCES public.codex_auth_generations(generation_id),
    terminal_reason TEXT NULL CHECK (
        terminal_reason IN ('completed', 'owner_replaced', 'revoked', 'expired',
                            'stage_prepare_failed', 'prelaunch_cancelled',
                            'launch_mark_failed', 'launch_failed', 'cancelled',
                            'containment_failed', 'invalid_staged_output',
                            'persistence_unavailable', 'authority_unavailable')
    ),
    CHECK ((state IN ('prepared', 'launched')) = (terminal_at IS NULL)),
    CHECK (
        successor_generation_id IS NULL
        OR state = 'completed_successor'
    ),
    CHECK (
        (generation_id IS NOT NULL AND bootstrap_absent = FALSE)
        OR (generation_id IS NULL AND bootstrap_absent = TRUE AND kind = 'device_auth')
    )
);
```

Add the nullable `codex_auth_generation_id UUID` foreign key to
`public.butler_secrets`. The guarded mutation functions must lock the singleton
state row with `FOR UPDATE`, validate the complete raw-row/generation/current
pointer invariant, write all three participating records atomically, and
reset inherited health state in the same value-changing transaction. The
functions must be the only privileged route for:

```sql
public.codex_auth_prepare_operation(kind TEXT, deadline_at TIMESTAMPTZ)
public.codex_auth_prepare_device_bootstrap(deadline_at TIMESTAMPTZ)
public.codex_auth_mark_operation_launched(operation_id UUID, expected_generation_id UUID)
public.codex_auth_complete_operation(
    operation_id UUID,
    expected_generation_id UUID,
    successor_document TEXT,
    health_outcome TEXT
)
public.codex_auth_abandon_operation(
    operation_id UUID,
    expected_generation_id UUID,
    reason TEXT
)
public.codex_auth_replace_as_owner(document TEXT)
public.codex_auth_revoke_as_owner()
public.codex_auth_cleanup_operations(retention_days INTEGER)
```

Normal `prepare` must perform one permitted `legacy_adoption` only when there is a
valid existing raw row and no prior state/generation. A malformed row, a
mismatched state, or a post-initialization generic write returns safe
unavailable. It accepts no bootstrap boolean or equivalent caller selector.
The distinct device-auth bootstrap function is granted only to the dashboard
API's no-`SET ROLE` shared-authority path; a runtime/prewarm prepare role receives
`InsufficientPrivilege` when invoking it. The bootstrap definer itself checks
that the locked singleton is absent, `has_ever_initialized = FALSE`, there is
no generation row, and there is no reserved raw row at all; only then does it record
`bootstrap_absent = TRUE` and open an empty private stage. Any owner replacement or revoke sets
`has_ever_initialized = TRUE` before releasing the same lock, permanently
closing this bootstrap route. A bootstrap operation must provide one strictly
validated successor; it cannot complete unchanged or attach health to an
absent authority. `complete` permits one exact `launched` operation to create
a successor; it first locks the operation and singleton, compares a normal
operation's generation with the current pointer or rechecks the recorded
never-initialized absent state, checks `clock_timestamp() < deadline_at`, and
terminalizes a loser rather than writing a successor or health result.

Any present malformed raw row, including a null/mismatched unbound raw row,
is an inconsistent authority rather than absence. It cannot be adopted or
bootstrap a device flow and remains untouched until direct owner replacement.
The real-database negative test
`test_present_malformed_raw_row_cannot_bootstrap` must insert such a row, call
the dashboard bootstrap definer under its effective role, receive only the safe
unavailable result, and prove the row and provenance catalog are unchanged.

The guarded `codex_auth_abandon_operation` is the sole explicit abandonment
path for a nonterminal operation. It locks the operation and singleton, checks
the recorded expected generation or exact bootstrap-absence state, accepts
only the closed `CodexAuthAbandonReason` vocabulary, writes no successor or
health, and transitions `prepared` or a fully-contained `launched` operation to
`discarded`. A prepared-stage failure uses `stage_prepare_failed`; cancellation
before a child uses `prelaunch_cancelled`; a failed launch recheck uses
`launch_mark_failed` unless the marking transaction already terminalized the
operation as superseded/expired; process creation failure after a successful
mark uses `launch_failed`. Cancellation/containment/parser/persistence failures
after launch call the same guard only after the whole child domain is proven
dead. Duplicate abandonment returns the existing terminal disposition and
does not alter `terminal_at`, health, generations, or the raw row. If database
unavailability prevents abandonment, the caller removes no still-live stage,
starts no child, and expiry remains the deterministic terminalizer.

The `core_199` migration is intentionally irreversible. Its `downgrade()`
raises before issuing DDL; it must not remove RLS, ACLs, bindings, lineage, or
the reserved-row guard and must leave the Alembic revision applied. The real
PostgreSQL test
`test_core_199_downgrade_fails_without_catalog_or_data_change` snapshots the
catalog and value-free rows, invokes downgrade, and proves the transaction
failed with the snapshot unchanged. Routine application rollback therefore
means a fence-aware fail-closed launch posture. Any later removal of this
boundary requires a future independently reviewed migration with explicit
exclusive-maintenance preconditions; this packet contains no hidden privileged
downgrade.

The concurrency suite SHALL execute these named cases against the installed
functions with independent real PostgreSQL connections and production
effective roles:

```python
async def test_two_conditional_successors_have_exactly_one_winner(...): ...
async def test_owner_replacement_racing_completion_has_no_stale_health_write(...): ...
async def test_revoke_racing_device_auth_has_no_stale_health_write(...): ...
async def test_duplicate_completion_commits_exactly_once(...): ...
async def test_effective_roles_execute_only_their_guarded_operations(...): ...
```

Each race uses a transaction barrier so both contenders reach the guarded
operation before either is released. The post-race query must prove exactly one
winning current generation (or the intentional owner/revoked state), one
terminal outcome per operation, no stale health write on the winner, no extra
raw-row mutation, and no orphan pointer. Mocks may supplement but SHALL NOT substitute
for these real PostgreSQL connections and effective-role calls.

The compatibility guard must reject a direct insert/update/delete of the
reserved key unless the fixed definer operation establishes an internal,
non-caller-controlled transaction context. It must leave non-Codex keys on
their existing code path. Revoke direct state/generation/operation access from
runtime roles and `PUBLIC`; grant only named internal dashboard and Codex
runtime caller roles the minimum function execution rights. Do not grant
browser-facing roles any read surface for provenance tables. The finalizer
must leave the migration login unable to assume the provenance owner, create in
the protected/admin schemas, write protected relations, or execute the
installer/finalizer.

- [ ] **Step 4: Run unit and effective-role database tests and confirm the boundary passes**

Run:

```bash
uv run pytest \
  tests/migrations/test_codex_auth_generation_provenance_migration.py \
  tests/config/test_init_db_codex_auth_generation_boundary.py \
  tests/config/test_codex_auth_generation_acl_integration.py \
  tests/config/test_codex_auth_generation_concurrency_integration.py \
  tests/config/test_migration_chain_head.py \
  -q --override-ini='addopts='
```

Expected: PASS. The upgraded-database test must prove migration-before-
bootstrap fails closed and bootstrap-then-migration succeeds. Effective-role
tests must confirm direct reserved-row DML, direct provenance table writes,
owner membership, protected-schema `CREATE`, installer/finalizer execution,
runtime bootstrap invocation, and `PUBLIC` function invocation fail, while a
normal guarded runtime call, the dashboard-only bootstrap definer, and an
established non-Codex write succeed.

The ACL assertions run through the actual shared connecting login followed by
the production `SET ROLE` paths. They must prove table-level and binding-column
privileges, reserved-row visibility, fixed function execution, and successful
non-Codex CRUD; testing as the migration owner alone is insufficient.

- [ ] **Step 5: Commit the schema boundary**

```bash
git add \
  scripts/init-db.sql \
  alembic/versions/core/core_199_codex_auth_generation_provenance.py \
  tests/migrations/test_codex_auth_generation_provenance_migration.py \
  tests/config/test_init_db_codex_auth_generation_boundary.py \
  tests/config/test_codex_auth_generation_acl_integration.py \
  tests/config/test_codex_auth_generation_concurrency_integration.py
git commit -m "feat: add Codex auth generation boundary"
```

## Task 2: Build the Typed Credential-Store Authority API

**Files:**

- Modify: `src/butlers/credential_store.py`
- Modify: `tests/config/test_credential_store.py`
- Test: `tests/config/test_credential_store.py`

**Consumes:** Task 1's guarded database operations and the system-global pool
selection already used by `load_codex_cli_auth`.

**Produces:** Typed lease, unavailable, and completion values for every
runtime/dashboard caller; no caller uses raw-value CAS as an authority proof.

- [ ] **Step 1: Write failing atomic authority API tests**

```python
async def test_prepare_returns_one_redacted_lease_for_complete_current_binding() -> None:
    store, conn = make_authority_store_with_complete_binding()

    result = await store.prepare_codex_auth_operation(
        kind=CodexAuthOperationKind.RUNTIME_INVOKE,
        deadline_at=deadline_in_future(),
    )

    assert isinstance(result, CodexAuthLaunchLease)
    assert "authority_document" not in repr(result)
    assert conn.fetchrow.await_count == 1


async def test_prepare_allows_only_never_initialized_device_bootstrap() -> None:
    store = make_authority_store_with_absent_never_initialized_state()

    result = await store.prepare_codex_auth_device_bootstrap(
        deadline_at=deadline_in_future(),
    )

    assert isinstance(result, CodexAuthBootstrapLease)
    assert "generation_id" not in repr(result)


async def test_normal_prepare_has_no_bootstrap_selector() -> None:
    store = make_authority_store_with_absent_never_initialized_state()

    result = await store.prepare_codex_auth_operation(
        kind=CodexAuthOperationKind.DEVICE_AUTH,
        deadline_at=deadline_in_future(),
    )

    assert isinstance(result, CodexAuthUnavailable)
    assert no_bootstrap_operation_created()


async def test_completion_loses_to_owner_replace_without_health_attachment() -> None:
    store, lease = make_launched_runtime_lease()

    await store.replace_codex_auth_as_owner(validated_document_fixture())
    result = await store.complete_codex_auth_operation(
        operation_id=lease.operation_id,
        expected_generation_id=lease.generation_id,
        validated_successor_document=validated_document_fixture(),
        health=CodexAuthHealthOutcome.OK,
    )

    assert result.disposition is CodexAuthCompletionDisposition.SUPERSEDED
    assert_no_successor_or_health_write_after_owner_replace()


async def test_expired_or_duplicate_completion_is_non_committing() -> None:
    store, lease = make_launched_runtime_lease()
    expire_operation_in_database_time(lease.operation_id)

    first = await store.complete_codex_auth_operation(
        operation_id=lease.operation_id,
        expected_generation_id=lease.generation_id,
        validated_successor_document=None,
        health=CodexAuthHealthOutcome.TIMEOUT,
    )
    second = await store.complete_codex_auth_operation(
        operation_id=lease.operation_id,
        expected_generation_id=lease.generation_id,
        validated_successor_document=None,
        health=CodexAuthHealthOutcome.TIMEOUT,
    )

    assert first.disposition is CodexAuthCompletionDisposition.EXPIRED
    assert second.disposition is CodexAuthCompletionDisposition.EXPIRED


async def test_prepared_stage_failure_is_abandoned_without_health_or_successor() -> None:
    store, lease = make_prepared_runtime_lease()

    result = await store.abandon_codex_auth_operation(
        operation_id=lease.operation_id,
        expected_generation_id=lease.generation_id,
        reason=CodexAuthAbandonReason.STAGE_PREPARE_FAILED,
    )

    assert result.disposition is CodexAuthCompletionDisposition.DISCARDED
    assert_no_successor_or_health_write()


async def test_duplicate_abandonment_is_idempotent() -> None:
    store, lease = make_prepared_runtime_lease()

    first = await abandon_for_prelaunch_cancel(store, lease)
    second = await abandon_for_prelaunch_cancel(store, lease)

    assert first.disposition is CodexAuthCompletionDisposition.DISCARDED
    assert second.disposition is CodexAuthCompletionDisposition.DISCARDED
    assert_terminal_record_was_written_once()
```

- [ ] **Step 2: Run the focused tests and confirm the typed methods are absent**

Run:

```bash
uv run pytest tests/config/test_credential_store.py -q --override-ini='addopts='
```

Expected: failure because the lease/result classes and generation-fenced
methods do not exist.

- [ ] **Step 3: Add typed methods and replace Codex raw-value CAS authority identity**

Add the shared interfaces in this plan to `credential_store.py`, preserving
`repr=False` on every transient raw document. Implement each method as a
bounded call against `require_system_global_pool()` and a guarded SQL function;
never select an arbitrary fallback pool, use an in-process cache as proof, or
return database UUIDs to an API caller.

The normal `prepare_codex_auth_operation` calls only the normal definer and has
no bootstrap parameter. `prepare_codex_auth_device_bootstrap` calls only the
separately granted dashboard device-auth definer. Add
`read_current_codex_auth_projection_binding` as a read-only complete-binding
query for startup compatibility projection; it creates no operation and cannot
authorize launch or finalization.

`abandon_codex_auth_operation` calls only
`public.codex_auth_abandon_operation`. It accepts the closed
`CodexAuthAbandonReason`, never an exception/provider string, and is used for
prepared-stage failure, cancellation, failed mark, launch failure, and
post-containment discard. It is not an alias for
`complete_codex_auth_operation`: abandonment can terminalize `prepared` work,
whereas completion accepts only one exact launched operation and may be the
sole successor/health writer.

Use one helper to convert database outcomes to safe Python types:

```python
def _codex_completion_from_row(row: asyncpg.Record | None) -> CodexAuthCompletion:
    if row is None:
        return CodexAuthCompletion(CodexAuthCompletionDisposition.UNAVAILABLE)
    return CodexAuthCompletion(
        disposition=CodexAuthCompletionDisposition(row["disposition"]),
        successor_generation_id=row["successor_generation_id"],
    )
```

`load_codex_cli_auth` becomes an internal compatibility read only for callers
that have already acquired a complete current lease. The generic
`store_codex_cli_auth`, `store_codex_cli_auth_if_unchanged`,
`record_codex_cli_auth_test_result_if_unchanged`, and deletion paths are either
removed from Codex call sites or become private compatibility wrappers that
delegate to the new guarded methods. No new code compares raw documents to
identify authority. Keep non-Codex `store`, `store_shared`, and conditional
store semantics unchanged.

- [ ] **Step 4: Add transaction tests for all expected state transitions**

Extend the focused test file with exact database-mock or test-transaction
assertions for:

```python
EXPECTED_DISPOSITIONS = {
    "current_unchanged": CodexAuthCompletionDisposition.COMPLETED_UNCHANGED,
    "validated_successor": CodexAuthCompletionDisposition.COMPLETED_SUCCESSOR,
    "owner_replace_race": CodexAuthCompletionDisposition.SUPERSEDED,
    "revoke_race": CodexAuthCompletionDisposition.SUPERSEDED,
    "duplicate_completion": CodexAuthCompletionDisposition.SUPERSEDED,
    "expired": CodexAuthCompletionDisposition.EXPIRED,
    "malformed_or_missing_binding": CodexAuthCompletionDisposition.UNAVAILABLE,
    "prepared_stage_abandonment": CodexAuthCompletionDisposition.DISCARDED,
    "duplicate_abandonment": CodexAuthCompletionDisposition.DISCARDED,
}
```

Assert an owner replacement clears health with its new generation, a health-only
completion changes neither raw value nor generation pointer, only the distinct
dashboard bootstrap method works for an absent never-initialized authority,
normal prepare never selects bootstrap, and no `repr`,
logger argument, or safe result exposes the transient document, operation ID,
or generation ID. Add a paired test that an absent state after any generated
or revoked authority returns `UNAVAILABLE` even when the device-auth caller
uses the bootstrap entry point, an initialized inconsistent binding also
rejects bootstrap and requires direct owner replacement, and a paired test that
an unfinished bootstrap with no strictly validated successor calls guarded
abandonment with `invalid_staged_output` after containment, without attaching
health or creating a generation.

- [ ] **Step 5: Run the authority API tests and commit**

Run:

```bash
uv run pytest tests/config/test_credential_store.py -q --override-ini='addopts='
git add src/butlers/credential_store.py tests/config/test_credential_store.py
git commit -m "feat: fence Codex credential operations by generation"
```

Expected: PASS, including legacy-adoption-once, conditional successor,
owner/revoke precedence, health-only, expiry, duplicate, and redaction cases.

## Task 3: Make Local Authentication a Projection and Private Stages the Child Input

**Files:**

- Modify: `src/butlers/core/runtimes/_codex_auth_sync.py`
- Modify: `tests/adapters/test_codex_auth_sync.py`
- Modify: `tests/adapters/test_codex_refresh_lock.py`
- Test: `tests/adapters/test_codex_auth_sync.py`
- Test: `tests/adapters/test_codex_refresh_lock.py`

**Consumes:** `CodexAuthLaunchLease`, `CodexAuthBootstrapLease`, and safe
completion dispositions from Task 2.

**Produces:** Strict staged-document parsing/projection helpers that cannot
promote a local file, and a projection lock that blocks rather than authorizes
an unlocked child.

- [ ] **Step 1: Write failing stage/projection tests**

```python
async def test_two_leases_receive_disjoint_private_stages(tmp_path: Path) -> None:
    first = await write_private_codex_auth_stage(tmp_path, lease=make_lease())
    second = await write_private_codex_auth_stage(tmp_path, lease=make_lease())

    assert first.path != second.path
    assert first.path.stat().st_mode & 0o777 == 0o600
    assert second.path.stat().st_mode & 0o777 == 0o600


async def test_orphaned_local_file_is_not_a_successor_candidate(tmp_path: Path) -> None:
    local_path = make_changed_local_auth_file(tmp_path)

    result = read_validated_private_stage(
        stage=None,
        token_path=local_path,
        expected_operation_id=make_operation_id(),
    )

    assert result is None


async def test_projection_lock_timeout_refuses_launch_instead_of_running_unlocked() -> None:
    result = await project_current_authority_under_lock(
        lease=make_lease(),
        lock=never_acquired_lock(),
    )

    assert result is False
    assert no_subprocess_was_started()
```

- [ ] **Step 2: Run the stage/projection tests and confirm existing cache/lock behavior fails them**

Run:

```bash
uv run pytest \
  tests/adapters/test_codex_auth_sync.py \
  tests/adapters/test_codex_refresh_lock.py \
  -q --override-ini='addopts='
```

Expected: failure because local cache/digest inference and the existing
unlocked lock-contended posture do not meet the private-stage contract.

- [ ] **Step 3: Replace local predecessor inference with narrow projection and stage helpers**

Remove `_AUTH_SYNC_CACHE`, `_AUTH_AUTHORITY_CACHE`, `_AuthFileSnapshot` use as
authority provenance, `_has_rotated`, and any full-file digest comparison used
to promote a successor. Retain a strict parser that returns only a transient
validated document or `None`, an atomic `0600` writer, containment checks, and
safe generic diagnostics.

Introduce an operation-private stage type whose path is not returned to a
browser, command line, telemetry record, or log:

```text
@dataclass(frozen=True)
class CodexAuthPrivateStage:
    operation_id: UUID = field(repr=False)
    path: Path = field(repr=False)

write_private_codex_auth_stage(
    root: Path,
    *,
    lease: CodexAuthLaunchLease | CodexAuthBootstrapLease,
) -> CodexAuthPrivateStage | None

read_validated_private_stage(
    stage: CodexAuthPrivateStage | None,
    *,
    expected_operation_id: UUID,
) -> str | None

project_current_authority_under_lock(
    token_path: Path,
    *,
    lease: CodexAuthLaunchLease,
    timeout_s: float,
) -> bool
```

The stage helper validates that the stage belongs to the exact operation, is
inside the operation-root directory, is not a symlink, is mode `0600`, and is
strictly parseable only after the entire child process group has terminated.
These checks protect parent attribution but do not isolate two children running
under the same host identity; Task 4 supplies the mandatory kernel boundary.
For `CodexAuthLaunchLease` it seeds the stage from the transient document; for
`CodexAuthBootstrapLease` it creates an empty stage and accepts a candidate
only at the one guarded device-auth completion. On any failure, remove the
stage best-effort and return no candidate. The projection helper accepts only a
complete normal lease document, holds the cross-process lock, atomically writes
the compatibility file, and returns `False` on contention or I/O failure. It
must not attempt to read a local canonical file to repair or identify
authority.

- [ ] **Step 4: Add crash/restart and safe-diagnostic regression cases**

Add tests that a changed canonical local file, a changed prior stage, a missing
stage, a symlinked stage, a malformed stage, a path outside the operation root,
and a lock timeout each return no candidate and do not call completion. Assert
the captured logs contain no stage content, operation UUID, generation UUID,
or filesystem path from the rejected stage.

- [ ] **Step 5: Run the focused stage tests and commit**

Run:

```bash
uv run pytest \
  tests/adapters/test_codex_auth_sync.py \
  tests/adapters/test_codex_refresh_lock.py \
  -q --override-ini='addopts='
git add \
  src/butlers/core/runtimes/_codex_auth_sync.py \
  tests/adapters/test_codex_auth_sync.py \
  tests/adapters/test_codex_refresh_lock.py
git commit -m "refactor: isolate Codex auth stages from local authority"
```

Expected: PASS. No test should prove a successor from canonical local state,
file metadata, a digest, or a process-local cache.

## Task 4: Fence Codex Invocations and Prewarms at the Child Boundary

**Files:**

- Modify: `src/butlers/core/runtimes/codex.py`
- Modify: `src/butlers/cli_auth/sandbox.py`
- Modify: `src/butlers/cli_auth/sandbox_platform.py`
- Modify: `tests/adapters/test_codex_adapter.py`
- Modify: `tests/adapters/test_codex_auth_sync.py`
- Modify: `tests/cli/test_runtime_cli_sandbox.py`
- Test: `tests/adapters/test_codex_adapter.py`
- Test: `tests/cli/test_runtime_cli_sandbox.py`

**Consumes:** Task 2 lease/completion methods and Task 3 private-stage helpers.

**Produces:** Every Codex `invoke` and prewarm creates a durable operation,
rechecks it immediately before `create_subprocess_exec`, and finalizes only
through that operation.

- [ ] **Step 1: Write failing adapter race and deadline tests**

```python
async def test_adapter_marks_exact_lease_launched_before_creating_child() -> None:
    adapter, authority = make_adapter_with_authority()
    authority.prepare_codex_auth_operation.return_value = make_lease()
    authority.mark_codex_auth_operation_launched.return_value = CodexAuthLaunchResult(
        CodexAuthLaunchDisposition.LAUNCHED
    )

    await adapter.invoke(make_request())

    assert authority.mark_codex_auth_operation_launched.await_count == 1
    assert authority.mark_call_precedes_subprocess_start()


async def test_replacement_between_prepare_and_launch_creates_no_child() -> None:
    adapter, authority = make_adapter_with_authority()
    authority.prepare_codex_auth_operation.return_value = make_lease()
    authority.mark_codex_auth_operation_launched.return_value = CodexAuthLaunchResult(
        CodexAuthLaunchDisposition.SUPERSEDED
    )

    result = await adapter.invoke(make_request())

    assert result.is_authority_unavailable
    assert no_subprocess_was_started()


async def test_stale_stage_completion_withholds_successor_and_health() -> None:
    adapter, authority = make_adapter_with_authority()
    authority.complete_codex_auth_operation.return_value = superseded()

    await adapter.invoke(make_request())

    assert authority.complete_codex_auth_operation.await_count == 1
    assert no_health_is_attached_to_replacement()


async def test_authority_deadline_is_not_extended_by_retry_or_chunk() -> None:
    adapter, authority = make_adapter_with_authority()
    deadline = fixed_deadline()
    authority.prepare_codex_auth_operation.return_value = make_lease(deadline_at=deadline)

    await adapter.invoke(make_request_with_multiple_chunks())

    assert all_observed_operation_deadlines() == [deadline]


async def test_codex_peer_cannot_read_another_operation_stage(
    real_bubblewrap_sandbox: RuntimeCLIAuthSandbox,
) -> None:
    first, second = await real_bubblewrap_sandbox.launch_two_blocked_codex_peers()
    assert await first.try_read_host_path(second.stage_host_path) in {"EACCES", "ENOENT"}


async def test_codex_peer_cannot_write_another_operation_stage(
    real_bubblewrap_sandbox: RuntimeCLIAuthSandbox,
) -> None:
    first, second = await real_bubblewrap_sandbox.launch_two_blocked_codex_peers()
    assert await second.try_write_host_path(first.stage_host_path) in {"EACCES", "ENOENT"}
    assert await first.read_own_stage_sentinel() == "unchanged"
```

- [ ] **Step 2: Run adapter tests and confirm they fail before the generation fence exists**

Run:

```bash
uv run pytest tests/adapters/test_codex_adapter.py -q --override-ini='addopts='
```

Expected: failure because the adapter currently reconciles a shared canonical
file and finalizes by raw-value CAS instead of an exact operation, and its
ordinary private HOME does not provide behavior-executing peer isolation.

- [ ] **Step 3: Refactor invocation and prewarm through the one exact operation**

Replace the preflight/finalization pair in `CodexAdapter` with a single
operation lifecycle. `invoke` and `run_codex_pre_warm` must calculate one
existing absolute authority deadline, prepare the appropriate kind, write a
private stage, optionally project the canonical file under its required lock,
conditionally mark launched, and only then create a child.

Generalize the existing root-owned Bubblewrap dashboard launcher into a shared
`RuntimeCLIAuthSandbox` without weakening its PID1/pidfd containment receipts.
Every runtime invoke, internal retry, prewarm, and device-auth child leases one
unique reserved outer UID/GID, enters distinct user/mount/PID/IPC/UTS namespaces,
and sees only its own stage bind at its HOME plus the reviewed read-only runtime
inputs. The parent stage root is not mounted, and a peer's host stage path is
both absent from the mount namespace and inaccessible to the peer's leased
outer identity. Identity reuse occurs only after namespace PID1 death and stage
cleanup. This is the kernel-enforced per-invocation isolation boundary; distinct
paths, mode `0600`, `no_new_privs`, and process groups alone are insufficient.
The existing `--dangerously-bypass-approvals-and-sandbox` Codex argument remains
inside this outer Bubblewrap boundary and cannot select an unsandboxed process
creation path.

Use this sequence without expanding a retry's deadline:

```python
lease_or_unavailable = await authority.prepare_codex_auth_operation(
    kind=CodexAuthOperationKind.RUNTIME_INVOKE,
    deadline_at=authority_deadline,
)
if not isinstance(lease_or_unavailable, CodexAuthLaunchLease):
    return authority_unavailable_result()

stage = await write_private_codex_auth_stage(stage_root, lease=lease_or_unavailable)
if stage is None:
    await authority.abandon_codex_auth_operation(
        operation_id=lease_or_unavailable.operation_id,
        expected_generation_id=lease_or_unavailable.generation_id,
        reason=CodexAuthAbandonReason.STAGE_PREPARE_FAILED,
    )
    return authority_unavailable_result()

launch = await authority.mark_codex_auth_operation_launched(
    operation_id=lease_or_unavailable.operation_id,
    expected_generation_id=lease_or_unavailable.generation_id,
)
if launch.disposition is not CodexAuthLaunchDisposition.LAUNCHED:
    await authority.abandon_codex_auth_operation(
        operation_id=lease_or_unavailable.operation_id,
        expected_generation_id=lease_or_unavailable.generation_id,
        reason=CodexAuthAbandonReason.LAUNCH_MARK_FAILED,
    )
    discard_private_stage(stage)
    return authority_unavailable_result()

try:
    child = await runtime_cli_auth_sandbox.launch_with_private_stage(stage)
except Exception:
    await authority.abandon_codex_auth_operation(
        operation_id=lease_or_unavailable.operation_id,
        expected_generation_id=lease_or_unavailable.generation_id,
        reason=CodexAuthAbandonReason.LAUNCH_FAILED,
    )
    raise
```

After full child/process-group termination, read only that private stage,
strictly validate it, classify a safe health outcome, and call
`complete_codex_auth_operation` exactly once. A completion disposition other
than `COMPLETED_SUCCESSOR` or `COMPLETED_UNCHANGED` withholds all authority
side effects. Preserve ordinary runtime result/failover behavior: a potential
side-effect child is not replayed to overcome a provenance failure.

- [ ] **Step 4: Cover prewarm, cancellation, malformed output, and two-daemon interleavings**

Add tests for `RUNTIME_PREWARM` using its own lease/stage; prewarm loses an
owner replacement; two adapters receive distinct stage roots; a cancelled or
timed-out child terminalizes safely after child-group cleanup; malformed stage
does not call a successor write; and a failing completion deletes the private
stage. Include a test that lock contention returns the safe no-launch result
and never takes the previous "proceed unlocked" path.

The peer-isolation tests must be behavior-executing integration tests against
the real Bubblewrap launcher, not argv snapshots. Launch two blocked peers at
once, have each attempt open/read and open/write the other's absolute host
stage path, assert `EACCES` or `ENOENT`, and prove both own-stage sentinels are
unchanged. Also execute a child carrying
`--dangerously-bypass-approvals-and-sandbox` and prove the same denial. Static
argument tests may supplement these cases but SHALL NOT substitute for them.

- [ ] **Step 5: Run adapter tests and commit**

Run:

```bash
uv run pytest \
  tests/adapters/test_codex_adapter.py \
  tests/adapters/test_codex_auth_sync.py \
  tests/adapters/test_codex_refresh_lock.py \
  tests/cli/test_runtime_cli_sandbox.py \
  -q --override-ini='addopts='
git add \
  src/butlers/core/runtimes/codex.py \
  src/butlers/cli_auth/sandbox.py \
  src/butlers/cli_auth/sandbox_platform.py \
  tests/adapters/test_codex_adapter.py \
  tests/adapters/test_codex_auth_sync.py \
  tests/cli/test_runtime_cli_sandbox.py
git commit -m "feat: fence Codex subprocess authority at launch"
```

Expected: PASS. The test output must prove both direct invocation and prewarm
refuse unavailable/stale authority before starting a child.

## Task 5: Route Dashboard Owner, Device-Auth, and Probe Paths Through the Same Boundary

**Files:**

- Modify: `src/butlers/cli_auth/persistence.py`
- Modify: `src/butlers/cli_auth/session.py`
- Modify: `src/butlers/cli_auth/sandbox.py`
- Modify: `src/butlers/cli_auth/sandbox_platform.py`
- Modify: `src/butlers/cli_auth/health.py`
- Modify: `src/butlers/api/routers/cli_auth.py`
- Modify: `src/butlers/api/routers/secrets_v2.py`
- Modify: `tests/cli/test_cli_auth.py`
- Modify: `tests/cli/test_runtime_cli_sandbox.py`
- Modify: `tests/api/test_secrets_v2_cli_mutations.py`
- Modify: `tests/api/test_secrets_v2_cli_reauthorize.py`
- Modify: `tests/api/test_secrets_v2_codex_authority.py`
- Modify: `frontend/src/api/client.secrets-v2.test.ts`
- Modify: `frontend/src/components/secrets/passport/PageCli.actions.test.tsx`
- Test: `tests/cli/test_cli_auth.py`
- Test: `tests/cli/test_runtime_cli_sandbox.py`
- Test: `tests/api/test_secrets_v2_cli_mutations.py`
- Test: `tests/api/test_secrets_v2_cli_reauthorize.py`
- Test: `tests/api/test_secrets_v2_codex_authority.py`
- Test: `frontend/src/api/client.secrets-v2.test.ts`
- Test: `frontend/src/components/secrets/passport/PageCli.actions.test.tsx`

**Consumes:** Task 2's owner mutation and operation interfaces and Task 3's
strict contained-stage helpers.

**Produces:** The dashboard's existing Codex rotate raw-value-once and
inventory/detail display-fingerprint surfaces retain their exact shapes, while
all state mutation has the same precedence and internal-state redaction as
runtime code. One lease/stage context reaches the actual platform child and
survives through containment/finalization.

- [ ] **Step 1: Write failing dashboard/device-auth redaction and precedence tests**

```python
async def test_owner_rotate_supersedes_waiting_device_auth_without_internal_response_fields() -> None:
    client, authority, device_session = make_dashboard_with_waiting_device_auth()

    response = client.post("/api/secrets/cli/cli-auth/codex/rotate", json=valid_rotate_payload())
    completion = await device_session.complete_with_validated_stage()

    assert response.status_code == 200
    assert completion.is_safe_failure
    assert set(response.json()["data"]) == {"fingerprint", "value"}
    assert_provenance_fields_absent(response.json())
    authority.replace_codex_auth_as_owner.assert_awaited_once()


async def test_revoke_prevents_device_auth_resurrection() -> None:
    client, authority, device_session = make_dashboard_with_waiting_device_auth()

    client.delete("/api/secrets/cli/cli-auth/codex")
    completion = await device_session.complete_with_validated_stage()

    assert completion.is_safe_failure
    assert authority.complete_codex_auth_operation.await_count == 1
    assert_no_new_shared_credential_write()


async def test_probe_loses_replacement_without_attaching_stale_health() -> None:
    probe = make_launched_probe_operation()
    replace_current_authority_as_owner()

    response = await finalize_dashboard_probe(probe, CodexAuthHealthOutcome.OK)

    assert response.is_value_free
    assert_no_health_history_or_audit_attached_to_replacement()


async def test_stale_device_auth_mark_starts_no_platform_child() -> None:
    session, authority, sandbox = make_prepared_codex_device_session()
    authority.mark_codex_auth_operation_launched.return_value = superseded_launch()

    await session.start()

    sandbox.prepared_handle.launch.assert_not_awaited()
    assert sandbox.prepared_handle.stage_removed


def test_codex_inventory_and_detail_expose_display_fingerprint_only() -> None:
    inventory, detail = read_codex_inventory_and_detail()

    assert inventory["fingerprint"] and detail["fingerprint"]
    assert "value" not in inventory and "value" not in detail
    assert_provenance_fields_absent(inventory)
    assert_provenance_fields_absent(detail)
```

- [ ] **Step 2: Run dashboard and device-auth tests and confirm current raw-baseline flow fails**

Run:

```bash
uv run pytest \
  tests/cli/test_cli_auth.py \
  tests/cli/test_runtime_cli_sandbox.py \
  tests/api/test_secrets_v2_cli_mutations.py \
  tests/api/test_secrets_v2_cli_reauthorize.py \
  tests/api/test_secrets_v2_codex_authority.py \
  -q --override-ini='addopts='
cd frontend && npm run test -- \
  src/api/client.secrets-v2.test.ts \
  src/components/secrets/passport/PageCli.actions.test.tsx
```

Expected: failure because device auth captures a raw expected value and owner
paths write/delete the Codex row through generic methods.

- [ ] **Step 3: Convert direct owner save, rotate, and revoke to serialized authority mutations**

In `secrets_v2.py`, validate the owner-supplied document with the existing
strict parser, then invoke only `replace_codex_auth_as_owner`. The `DELETE`
path invokes only `revoke_codex_auth_as_owner`. Deliberately preserve the
owner-only Codex rotate response as exactly `{fingerprint, value}`, with the raw
value returned once, and preserve inventory/detail `fingerprint` computed on
read without raw values. The fingerprint is a display aid, never persisted or
passed into provenance. Do not include a generation, operation, lineage, new
credential-derived identifier, provider error, or exception detail in any
response/audit/log text. Add backend, TypeScript client, and PageCli assertions
for all three surfaces rather than silently changing the envelope.

In `persistence.py` and `cli_auth.py`, replace
`capture_device_auth_authority_baseline` and raw expected-value completion with:

```python
lease_or_unavailable = await codex_authority.prepare_codex_auth_operation(
    kind=CodexAuthOperationKind.DEVICE_AUTH,
    deadline_at=session_deadline_at,
)
if isinstance(lease_or_unavailable, CodexAuthUnavailable):
    lease_or_unavailable = await codex_authority.prepare_codex_auth_device_bootstrap(
        deadline_at=session_deadline_at,
    )
if isinstance(lease_or_unavailable, CodexAuthUnavailable):
    raise HTTPException(status_code=503, detail="System-global Codex credential authority unavailable.")

prepared_sandbox = await sandbox.prepare_device_auth(
    provider,
    authority_seed=stage_seed_from(lease_or_unavailable),
)
launch = await codex_authority.mark_codex_auth_operation_launched(
    operation_id=lease_or_unavailable.operation_id,
    expected_generation_id=expected_generation(lease_or_unavailable),
)
if launch.disposition is CodexAuthLaunchDisposition.LAUNCHED:
    handle = await prepared_sandbox.launch()
else:
    await prepared_sandbox.discard()
    await codex_authority.abandon_codex_auth_operation(
        operation_id=lease_or_unavailable.operation_id,
        expected_generation_id=expected_generation(lease_or_unavailable),
        reason=CodexAuthAbandonReason.LAUNCH_MARK_FAILED,
    )
    raise HTTPException(status_code=503, detail="System-global Codex credential authority unavailable.")
```

First call normal `prepare_codex_auth_operation(kind=DEVICE_AUTH, ...)`. Only
the dashboard device-auth service may then try the distinct bootstrap method
after normal preparation returns unavailable; the bootstrap definer rechecks
the exact never-initialized absence and returns unavailable for malformed,
initialized-inconsistent, revoked, or otherwise ineligible state.
`CLIAuthSession.start()` must no longer accept a preparer returning a
boolean and then call a one-shot `launch_device_auth(provider)`. Persistence
returns a redacted preparation context; the session retains it, passes its seed
to `DashboardCLIAuthSandbox.prepare_device_auth`, marks the same durable
operation immediately before `PreparedDeviceAuthSandbox.launch()`, and retains
the context until finalization.

The platform launcher seeds only the prepared private stage. A normal lease
stages the transient authority document; a bootstrap lease starts empty. A
failed/stale mark starts no child. Once the child is fully terminated, the same
prepared handle proves containment and yields the candidate to
`complete_codex_auth_operation` with the same lease. Cancellation, timeout,
launch error, containment error, or persistence error terminalizes the
operation and removes the stage. A missing/ambiguous candidate creates no raw
write and returns the existing generic session failure message.

Every non-success seam invokes the explicit abandonment method with the closed
reason matching its stage: `stage_prepare_failed`, `prelaunch_cancelled`,
`launch_mark_failed`, `launch_failed`, `cancelled`, `containment_failed`,
`invalid_staged_output`, or `persistence_unavailable`. Prepared-stage failure,
cancellation before launch, failed marking, process-launch failure, and
duplicate abandonment each have a named test. For a launched child, containment
must be proven before abandonment or stage removal; if it cannot be proven, the
stage and leased identity remain quarantined and database expiry is the only
terminalizer.

For a Codex health probe, prepare/mark a `HEALTH_PROBE` operation. Its safe
outcome may be returned in the existing health shape, but stale completion
cannot attach a health/history/audit state to the replacement. The probe never
receives or emits internal UUIDs.

In `cli_auth/health.py`, replace `_prepare_codex_probe_authority`'s canonical
file reconciliation and `codex_auth_file_matches_authority` check with the
normal-lease path. It must prepare a `HEALTH_PROBE`, reject a bootstrap lease
for this kind, mark the lease launched immediately before the parent-only
backend request, parse only `lease.authority_document` transiently, classify
the result with `CodexAuthHealthOutcome`, and complete that exact operation.
It must never read the local canonical auth file as a probe source or attach a
late result after a replacement. Keep the current parent-only/no-`login status`
probe rule and generic value-free detail.

- [ ] **Step 4: Add explicit bootstrap and redaction cases**

Add tests for exactly one uninitialized authority device-auth bootstrap, while
an absent authority that previously had a generation or an initialized
inconsistent binding returns the same safe unavailable result and requires
direct owner replacement. Prove a normal runtime prepare role cannot invoke
bootstrap. Assert malformed/ambiguous/escaping stage cases leave no
credential row or provenance value in session output, captured logs, or audit
notes. Assert concurrent dashboard owner rotate, runtime successor, and device
success resolve through the shared pointer without a clock, dashboard session,
device code, PID, or file identity tie-break.

At the session/sandbox/platform seam, prove normal and bootstrap seeds reach
only their owning child; a failed mark launches no process; completion after
containment uses the identical lease; and cancellation, timeout, failed launch,
or failed finalization removes the stage and terminalizes the operation.
Add named cases for prepared-stage failure, prelaunch cancellation, failed
marking, process-launch failure, and duplicate abandonment, asserting each
writes no successor or health and never starts an unauthorized child.

Add a parent-only health test that races a completed `HEALTH_PROBE` against an
owner replacement and asserts the probe response remains generic while its
completion cannot attach health/history/audit to the replacement. Assert it
does not invoke the canonical-file reconcile/match helpers or spawn a status
child.

- [ ] **Step 5: Run dashboard/device auth tests and commit**

Run:

```bash
uv run pytest \
  tests/cli/test_cli_auth.py \
  tests/cli/test_runtime_cli_sandbox.py \
  tests/api/test_secrets_v2_cli_mutations.py \
  tests/api/test_secrets_v2_cli_reauthorize.py \
  tests/api/test_secrets_v2_codex_authority.py \
  -q --override-ini='addopts='
cd frontend && npm run test -- \
  src/api/client.secrets-v2.test.ts \
  src/components/secrets/passport/PageCli.actions.test.tsx
git add \
  src/butlers/cli_auth/persistence.py \
  src/butlers/cli_auth/session.py \
  src/butlers/cli_auth/sandbox.py \
  src/butlers/cli_auth/sandbox_platform.py \
  src/butlers/cli_auth/health.py \
  src/butlers/api/routers/cli_auth.py \
  src/butlers/api/routers/secrets_v2.py \
  tests/cli/test_cli_auth.py \
  tests/cli/test_runtime_cli_sandbox.py \
  tests/api/test_secrets_v2_cli_mutations.py \
  tests/api/test_secrets_v2_cli_reauthorize.py \
  tests/api/test_secrets_v2_codex_authority.py \
  frontend/src/api/client.secrets-v2.test.ts \
  frontend/src/components/secrets/passport/PageCli.actions.test.tsx
git commit -m "feat: serialize dashboard Codex authority changes"
```

Expected: PASS. No browser/API route reads or returns provenance records.

## Task 6: Wire Startup, Direct Dispatch, and Deterministic Cleanup

**Files:**

- Modify: `src/butlers/core/spawner.py`
- Modify: `src/butlers/connectors/discretion_dispatcher.py`
- Modify: `src/butlers/daemon.py`
- Modify: `src/butlers/lifecycle.py`
- Modify: `src/butlers/api/app.py`
- Modify: `src/butlers/api/routers/model_settings.py`
- Modify: `src/butlers/jobs/model_verify.py`
- Modify: `src/butlers/jobs/retention.py`
- Modify: `src/butlers/scheduled_jobs.py`
- Modify: `tests/core/test_core_spawner.py`
- Modify: `tests/connectors/test_discretion_dispatcher.py`
- Modify: `tests/daemon/test_startup_coverage_gaps.py`
- Modify: `tests/connectors/test_connector_codex_auth_restore.py`
- Modify: `tests/api/test_app_lifespan_supervision.py`
- Modify: `tests/api/test_model_settings.py`
- Modify: `tests/jobs/test_model_verify.py`
- Modify: `tests/jobs/test_retention_pruners.py`
- Test: `tests/core/test_core_spawner.py`
- Test: `tests/connectors/test_discretion_dispatcher.py`
- Test: `tests/daemon/test_startup_coverage_gaps.py`
- Test: `tests/connectors/test_connector_codex_auth_restore.py`
- Test: `tests/api/test_app_lifespan_supervision.py`
- Test: `tests/api/test_model_settings.py`
- Test: `tests/jobs/test_model_verify.py`
- Test: `tests/jobs/test_retention_pruners.py`

**Consumes:** Task 2 authority API and Task 3's projection rule.

**Produces:** A complete startup/direct-dispatch topology and deterministic
maintenance route that use the current shared binding only.

- [ ] **Step 1: Write failing recovery, constructor, and cleanup tests**

```python
async def test_fresh_daemon_projects_only_complete_shared_binding() -> None:
    daemon, authority = make_fresh_daemon_with_changed_local_file()
    authority.read_current_codex_auth_projection_binding.return_value = make_projection_binding()

    await daemon.restore_codex_auth_if_configured()

    assert projection_received(binding_document_from(authority))
    assert local_file_was_not_promoted()
    authority.prepare_codex_auth_operation.assert_not_awaited()
    assert no_nonterminal_operation_created()


async def test_direct_dispatcher_passes_explicit_authority_to_codex_adapter() -> None:
    dispatcher, authority = make_direct_dispatcher()

    adapter = await dispatcher.build_runtime_adapter_for_codex()

    assert adapter.credential_store is authority


async def test_provenance_cleanup_is_safe_before_migration() -> None:
    pool = make_pool_missing_codex_provenance_relations()

    result = await prune_codex_auth_operations(pool, enabled=True, dry_run=False)

    assert result == {"enabled": True, "available": False, "expired": 0, "deleted": 0}
    assert no_credential_or_projection_write()
```

- [ ] **Step 2: Run these focused tests and confirm current startup/cleanup wiring fails them**

Run:

```bash
uv run pytest \
  tests/core/test_core_spawner.py \
  tests/connectors/test_discretion_dispatcher.py \
  tests/daemon/test_startup_coverage_gaps.py \
  tests/jobs/test_retention_pruners.py \
  -q --override-ini='addopts='
```

Expected: failure because startup and cleanup do not yet use the complete
generation binding or deterministic provenance job.

- [ ] **Step 3: Pass the explicit boundary to every Codex constructor and startup restore**

Keep current pool-selection logic, but ensure `Spawner`, direct dispatcher,
lifecycle, daemon startup, connector restoration, model verification, and API
lifespan construction pass the selected `CredentialStore` that implements the
new typed methods. Do not make a local pool authoritative when the shared pool
is absent.

Specifically, retain `api/app.py`'s explicit shared authority ownership rather
than constructing a fresh schema-local store in a route/lifespan callback;
make `model_settings.py` and `jobs/model_verify.py` accept the same injected
authority for any Codex verification; and make connector restoration accept it
as a required dependency instead of using a discovered local path. Add one
constructor test per path that fails when a Codex adapter/probe receives `None`
or a non-system-global store, while non-Codex model verification retains its
current behavior.

Startup compatibility projection must follow this read-only condition:

```python
binding_or_unavailable = await authority.read_current_codex_auth_projection_binding()
if isinstance(binding_or_unavailable, CodexAuthProjectionBinding):
    await project_current_authority_under_lock(
        canonical_auth_path,
        binding=binding_or_unavailable,
        timeout_s=startup_projection_timeout_s,
    )
else:
    record_safe_codex_authority_unavailable()
```

This query validates a complete current binding but creates no durable
operation, grants no launch authority, and cannot complete a successor or
health outcome. Startup projection must never launch a child or
inspect/overwrite the database from the changed local file. If the binding is
unavailable or projection fails, leave Codex launches unavailable and preserve
existing honest health reporting. A later child still requires a fresh normal
operation, so a concurrent replacement cannot turn a startup projection into
launch authority.

- [ ] **Step 4: Add deterministic cleanup with a bounded, non-provider job**

Add `prune_codex_auth_operations` to `jobs/retention.py`, following the
existing `prune_secret_probe_log` disabled-by-default/dry-run contract. The
only production mutation is the guarded cleanup function, which first marks
expired `prepared`/`launched` operations terminal using PostgreSQL time, then
deletes only terminal operation rows whose `terminal_at` is at least 90 days
old. It never deletes the credential row, current state row, current
generation, or an operation-referenced generation.

```python
async def prune_codex_auth_operations(
    pool: asyncpg.Pool,
    *,
    enabled: bool = False,
    dry_run: bool = True,
    retention_days: int = 90,
    batch_limit: int = 500,
) -> dict[str, int | bool]:
    if retention_days < 90:
        raise ValueError("retention_days must be at least 90")
    if not enabled:
        return {"enabled": False, "available": True, "expired": 0, "deleted": 0}
    return await _run_guarded_codex_auth_cleanup(
        pool,
        dry_run=dry_run,
        retention_days=retention_days,
        batch_limit=batch_limit,
    )
```

Register `codex_auth_operation_prune` in `_RETENTION_PRUNER_JOB_HANDLERS` with
the same disabled-by-default configuration shape. Catch an undefined-table or
unavailable guarded-function posture as `{available: False}` only; do not
change a credential or projection to repair it. Do not run an LLM session,
provider command, or dashboard mutation from this job.

- [ ] **Step 5: Add multidaemon, crash, and retention proof cases**

Extend tests to prove two daemons share the same selected authority but use
distinct operation stages, repeated startup projection leaves no prepared or
launched operation, a fresh process refuses a changed local file, an
orphaned launched operation cannot be completed after restart, exact 90 days
is accepted, 89 days raises, dry run does not delete, and cleanup cannot remove
the current/linked generation or raw row. Test a pre-migration/core-only setup
returns safe unavailable and leaves ordinary startup working.

- [ ] **Step 6: Run the focused topology and maintenance tests and commit**

Run:

```bash
uv run pytest \
  tests/core/test_core_spawner.py \
  tests/connectors/test_discretion_dispatcher.py \
  tests/daemon/test_startup_coverage_gaps.py \
  tests/connectors/test_connector_codex_auth_restore.py \
  tests/api/test_app_lifespan_supervision.py \
  tests/api/test_model_settings.py \
  tests/jobs/test_model_verify.py \
  tests/jobs/test_retention_pruners.py \
  -q --override-ini='addopts='
git add \
  src/butlers/core/spawner.py \
  src/butlers/connectors/discretion_dispatcher.py \
  src/butlers/daemon.py \
  src/butlers/lifecycle.py \
  src/butlers/api/app.py \
  src/butlers/api/routers/model_settings.py \
  src/butlers/jobs/model_verify.py \
  src/butlers/jobs/retention.py \
  src/butlers/scheduled_jobs.py \
  tests/core/test_core_spawner.py \
  tests/connectors/test_discretion_dispatcher.py \
  tests/daemon/test_startup_coverage_gaps.py \
  tests/connectors/test_connector_codex_auth_restore.py \
  tests/api/test_app_lifespan_supervision.py \
  tests/api/test_model_settings.py \
  tests/jobs/test_model_verify.py \
  tests/jobs/test_retention_pruners.py
git commit -m "feat: recover and prune Codex auth provenance safely"
```

Expected: PASS. The deterministic job has no provider interaction and no
authority-recovery fallback.

## Task 7: Add Source-Completeness Protection and Contract Documentation

**Files:**

- Create: `tests/contracts/test_codex_auth_generation_completeness.py`
- Modify: `docs/data_and_storage/credential-store.md`
- Modify: `docs/identity_and_secrets/cli-runtime-auth.md`
- Modify: `docs/concepts/butler-lifecycle.md`
- Modify: `docs/architecture/butler-daemon.md`
- Modify: `docs/runtime/spawner.md`
- Modify: `openspec/specs/core-credentials/spec.md`
- Modify: `openspec/specs/core-spawner/spec.md`
- Modify: `openspec/specs/core-daemon/spec.md`
- Modify: `openspec/specs/dashboard-api/spec.md`
- Modify: `openspec/specs/database-security/spec.md`
- Test: `tests/contracts/test_codex_auth_generation_completeness.py`

**Consumes:** Every previous implementation task and the requirements under
`openspec/changes/generation-fenced-codex-auth-rotation-provenance/specs/`.

**Produces:** A source-level bypass detector and user/developer documentation
that states the durable boundary without creating a public provenance surface.

- [ ] **Step 1: Write the failing source-completeness test**

```python
def test_every_codex_authority_path_uses_generation_fenced_boundary() -> None:
    forbidden_callers = {
        "src/butlers/core/runtimes/codex.py": {
            "store_codex_cli_auth_if_unchanged",
            "record_codex_cli_auth_test_result_if_unchanged",
        },
        "src/butlers/cli_auth/persistence.py": {
            "capture_device_auth_authority_baseline",
        },
        "src/butlers/api/routers/secrets_v2.py": {
            "store_codex_cli_auth",
            "delete_codex_cli_auth",
        },
    }
    required_callers = {
        "src/butlers/core/runtimes/codex.py": "prepare_codex_auth_operation",
        "src/butlers/cli_auth/persistence.py": "complete_codex_auth_operation",
        "src/butlers/api/routers/secrets_v2.py": "replace_codex_auth_as_owner",
        "src/butlers/api/routers/cli_auth.py": "prepare_codex_auth_operation",
        "src/butlers/core/spawner.py": "CredentialStore",
        "src/butlers/connectors/discretion_dispatcher.py": "CredentialStore",
    }

    assert_no_forbidden_call_sites(forbidden_callers)
    assert_required_call_sites(required_callers)
```

The test must additionally enumerate runtime invoke, prewarm, device auth,
health probe, startup, direct dispatcher, connector restoration, dashboard
save/revoke, model verification, and lifespan construction. It should reject a
new raw-value CAS call from any of those Codex paths while allowing generic
non-Codex credential behavior.

- [ ] **Step 2: Run the source-completeness test and confirm it catches pre-conversion paths**

Run:

```bash
uv run pytest tests/contracts/test_codex_auth_generation_completeness.py -q --override-ini='addopts='
```

Expected: failure until every listed Codex path has been converted.

- [ ] **Step 3: Update docs and reconcile final capability specs**

Document these exact contracts without showing a raw document, UUID value, or
credential-derived identifier:

1. `credential-store.md`: the existing Tier 1 row remains raw-value owner;
   generations/operations are internal, opaque, value-free, and unavailable
   bindings fail closed.
2. `cli-runtime-auth.md`: every child uses a private stage; projection is
   database-originated; local files, caches, PID, mtime, and stage contents are
   never recovery authority; dashboard replacement/revoke wins stale work.
3. `butler-lifecycle.md` and `butler-daemon.md`: startup restores only a
   complete shared binding; cleanup is deterministic, disabled by default,
   90-day minimum, and has a pre-migration-safe no-op posture.
4. `spawner.md`: direct and normal dispatch pass the same authority boundary;
   authority deadline, execution timeout, and side-effect retry posture remain
   distinct.
5. OpenSpec composition: first confirm
   `harden-runtime-auth-and-breaker-attention` has landed and its still-active
   `core-credentials` replacement is synced/archived or otherwise establish its
   exact canonical wording. Rebase before implementing this packet. Then carry
   forward this change's `core-credentials`, `core-daemon`, `dashboard-api`, and
   `database-security` additions plus its full `MODIFIED` replacement of
   canonical `core-spawner` requirement `Pre-Launch and Prewarm Codex Auth
   Synchronization`. Do not retain the old local-fallback scenarios or create a
   parallel additive private-stage requirement. Run strict validation and add
   stable requirement IDs to every behavior-executing test docstring or test
   name where the repository's current spec style supports them.

- [ ] **Step 4: Run source/doc/spec checks and commit**

Run:

```bash
openspec validate core-credentials --strict
openspec validate core-spawner --strict
openspec validate core-daemon --strict
openspec validate dashboard-api --strict
openspec validate database-security --strict
uv run pytest tests/contracts/test_codex_auth_generation_completeness.py -q --override-ini='addopts='
git add \
  tests/contracts/test_codex_auth_generation_completeness.py \
  docs/data_and_storage/credential-store.md \
  docs/identity_and_secrets/cli-runtime-auth.md \
  docs/concepts/butler-lifecycle.md \
  docs/architecture/butler-daemon.md \
  docs/runtime/spawner.md \
  openspec/specs/core-credentials/spec.md \
  openspec/specs/core-spawner/spec.md \
  openspec/specs/core-daemon/spec.md \
  openspec/specs/dashboard-api/spec.md \
  openspec/specs/database-security/spec.md
git commit -m "docs: specify Codex auth generation fencing"
```

Expected: PASS. The source test has a controlled, reviewed list of authority
call sites rather than a superficial token search.

## Task 8: Perform End-to-End Verification and Additive Rollout Review

**Files:**

- Modify: files from Tasks 1 through 7 only when a verification failure proves
  a direct defect.
- Test: the focused files from Tasks 1 through 7.

**Consumes:** The complete implementation and five reconciled capability
specifications.

**Produces:** Evidence that the migration, role boundary, operational state
machine, private-stage flow, dashboard contract, restart posture, and cleanup
match the approved OpenSpec before any deployment authorization.

- [ ] **Step 1: Run the focused cross-layer regression suite**

Run:

```bash
uv run pytest \
  tests/migrations/test_codex_auth_generation_provenance_migration.py \
  tests/config/test_init_db_codex_auth_generation_boundary.py \
  tests/config/test_codex_auth_generation_acl_integration.py \
  tests/config/test_codex_auth_generation_concurrency_integration.py \
  tests/config/test_credential_store.py \
  tests/adapters/test_codex_auth_sync.py \
  tests/adapters/test_codex_refresh_lock.py \
  tests/adapters/test_codex_adapter.py \
  tests/core/test_core_spawner.py \
  tests/connectors/test_discretion_dispatcher.py \
  tests/cli/test_cli_auth.py \
  tests/cli/test_runtime_cli_sandbox.py \
  tests/api/test_secrets_v2_cli_mutations.py \
  tests/api/test_secrets_v2_cli_reauthorize.py \
  tests/api/test_secrets_v2_codex_authority.py \
  tests/api/test_app_lifespan_supervision.py \
  tests/api/test_model_settings.py \
  tests/jobs/test_model_verify.py \
  tests/daemon/test_startup_coverage_gaps.py \
  tests/connectors/test_connector_codex_auth_restore.py \
  tests/jobs/test_retention_pruners.py \
  tests/contracts/test_codex_auth_generation_completeness.py \
  -q --override-ini='addopts='
cd frontend && npm run test -- \
  src/api/client.secrets-v2.test.ts \
  src/components/secrets/passport/PageCli.actions.test.tsx
```

Expected: PASS. If a failure appears, preserve its exact evidence, identify
whether it is schema, role, operation state, stage, caller wiring, or test
harness behavior, and make the smallest defect-specific correction before
rerunning the affected test and this suite.

- [ ] **Step 2: Run static, formatting, and strict OpenSpec gates**

Run:

```bash
uv run ruff check \
  src/butlers/credential_store.py \
  src/butlers/core/runtimes/_codex_auth_sync.py \
  src/butlers/core/runtimes/codex.py \
  src/butlers/core/spawner.py \
  src/butlers/connectors/discretion_dispatcher.py \
  src/butlers/cli_auth/persistence.py \
  src/butlers/cli_auth/session.py \
  src/butlers/cli_auth/sandbox.py \
  src/butlers/cli_auth/sandbox_platform.py \
  src/butlers/cli_auth/health.py \
  src/butlers/api/routers/cli_auth.py \
  src/butlers/api/routers/secrets_v2.py \
  src/butlers/api/app.py \
  src/butlers/api/routers/model_settings.py \
  src/butlers/jobs/model_verify.py \
  src/butlers/daemon.py \
  src/butlers/lifecycle.py \
  src/butlers/jobs/retention.py \
  src/butlers/scheduled_jobs.py \
  tests/migrations/test_codex_auth_generation_provenance_migration.py \
  tests/config/test_init_db_codex_auth_generation_boundary.py \
  tests/config/test_codex_auth_generation_acl_integration.py \
  tests/config/test_codex_auth_generation_concurrency_integration.py \
  tests/contracts/test_codex_auth_generation_completeness.py
uv run ruff format --check \
  src/butlers/credential_store.py \
  src/butlers/core/runtimes/_codex_auth_sync.py \
  src/butlers/core/runtimes/codex.py \
  src/butlers/core/spawner.py \
  src/butlers/connectors/discretion_dispatcher.py \
  src/butlers/cli_auth/persistence.py \
  src/butlers/cli_auth/session.py \
  src/butlers/cli_auth/sandbox.py \
  src/butlers/cli_auth/sandbox_platform.py \
  src/butlers/cli_auth/health.py \
  src/butlers/api/routers/cli_auth.py \
  src/butlers/api/routers/secrets_v2.py \
  src/butlers/api/app.py \
  src/butlers/api/routers/model_settings.py \
  src/butlers/jobs/model_verify.py \
  src/butlers/daemon.py \
  src/butlers/lifecycle.py \
  src/butlers/jobs/retention.py \
  src/butlers/scheduled_jobs.py \
  tests/migrations/test_codex_auth_generation_provenance_migration.py \
  tests/config/test_init_db_codex_auth_generation_boundary.py \
  tests/config/test_codex_auth_generation_acl_integration.py \
  tests/config/test_codex_auth_generation_concurrency_integration.py \
  tests/contracts/test_codex_auth_generation_completeness.py
openspec validate core-credentials --strict
openspec validate core-spawner --strict
openspec validate core-daemon --strict
openspec validate dashboard-api --strict
openspec validate database-security --strict
```

Expected: every command exits zero.

- [ ] **Step 3: Run the repository quality gate and review rollout safety**

Run:

```bash
make test-qg
test -n "${IMPLEMENTATION_REVIEW_BASE:-}"
test "$(git merge-base "${IMPLEMENTATION_REVIEW_BASE}" HEAD)" = "${IMPLEMENTATION_REVIEW_BASE}"
git diff --check "${IMPLEMENTATION_REVIEW_BASE}...HEAD"
git diff --stat "${IMPLEMENTATION_REVIEW_BASE}...HEAD"
git diff "${IMPLEMENTATION_REVIEW_BASE}...HEAD" -- scripts/init-db.sql \
  alembic/versions/core/core_199_codex_auth_generation_provenance.py \
  src/butlers/credential_store.py \
  src/butlers/core/runtimes/_codex_auth_sync.py \
  src/butlers/core/runtimes/codex.py \
  src/butlers/core/spawner.py \
  src/butlers/connectors/discretion_dispatcher.py \
  src/butlers/cli_auth/persistence.py \
  src/butlers/cli_auth/health.py \
  src/butlers/cli_auth/session.py \
  src/butlers/cli_auth/sandbox.py \
  src/butlers/cli_auth/sandbox_platform.py \
  src/butlers/api/routers/cli_auth.py \
  src/butlers/api/routers/secrets_v2.py \
  src/butlers/api/app.py \
  src/butlers/api/routers/model_settings.py \
  src/butlers/jobs/model_verify.py \
  src/butlers/jobs/retention.py \
  src/butlers/scheduled_jobs.py
```

Expected: the quality gate exits zero and manual diff review confirms:

- the captured reviewed base through `HEAD` was inspected, including all
  committed implementation rather than only the current worktree delta;

- the migration does not parse, copy, hash, or log legacy raw auth;
- an uninitialized valid existing authority can be adopted only once via the
  guarded path;
- an initialized lineage cannot silently adopt a later direct raw write;
- fence-aware code fails closed during rollback rather than falling back to
  local authority;
- terminal operations are retained at least 90 days, append-only generations
  remain value-free, and no cleanup removes authority;
- production activation remains separately authorized after code review,
  migration review, staged rollout, and operational owner approval.

- [ ] **Step 4: Commit any verification-only corrections and prepare review evidence**

```bash
git status --short
git add \
  scripts/init-db.sql \
  alembic/versions/core/core_199_codex_auth_generation_provenance.py \
  src/butlers/credential_store.py \
  src/butlers/core/runtimes/_codex_auth_sync.py \
  src/butlers/core/runtimes/codex.py \
  src/butlers/core/spawner.py \
  src/butlers/connectors/discretion_dispatcher.py \
  src/butlers/cli_auth/persistence.py \
  src/butlers/cli_auth/session.py \
  src/butlers/cli_auth/sandbox.py \
  src/butlers/cli_auth/sandbox_platform.py \
  src/butlers/cli_auth/health.py \
  src/butlers/api/routers/cli_auth.py \
  src/butlers/api/routers/secrets_v2.py \
  src/butlers/daemon.py \
  src/butlers/lifecycle.py \
  src/butlers/api/app.py \
  src/butlers/api/routers/model_settings.py \
  src/butlers/jobs/model_verify.py \
  src/butlers/jobs/retention.py \
  src/butlers/scheduled_jobs.py \
  tests/migrations/test_codex_auth_generation_provenance_migration.py \
  tests/config/test_init_db_codex_auth_generation_boundary.py \
  tests/config/test_codex_auth_generation_acl_integration.py \
  tests/config/test_codex_auth_generation_concurrency_integration.py \
  tests/config/test_credential_store.py \
  tests/adapters/test_codex_auth_sync.py \
  tests/adapters/test_codex_refresh_lock.py \
  tests/adapters/test_codex_adapter.py \
  tests/core/test_core_spawner.py \
  tests/connectors/test_discretion_dispatcher.py \
  tests/cli/test_cli_auth.py \
  tests/cli/test_runtime_cli_sandbox.py \
  tests/api/test_secrets_v2_cli_mutations.py \
  tests/api/test_secrets_v2_cli_reauthorize.py \
  tests/api/test_secrets_v2_codex_authority.py \
  tests/api/test_app_lifespan_supervision.py \
  tests/api/test_model_settings.py \
  tests/jobs/test_model_verify.py \
  tests/daemon/test_startup_coverage_gaps.py \
  tests/connectors/test_connector_codex_auth_restore.py \
  tests/jobs/test_retention_pruners.py \
  tests/contracts/test_codex_auth_generation_completeness.py \
  frontend/src/api/client.secrets-v2.test.ts \
  frontend/src/components/secrets/passport/PageCli.actions.test.tsx
git commit -m "fix: correct Codex auth fence verification defect"
```

Expected: run the staging/commit lines only when a verification defect exists;
omit them when the worktree has no defect-specific changes. The final handoff
lists the exact migration revision, focused test command/result, quality-gate
result, schema/role evidence, and explicitly states that no live credential,
auth replay, migration execution, restart, deployment, or provider operation
was performed during implementation review.

## Requirement-to-Task Coverage

| Requirement | Implementation tasks |
| --- | --- |
| Opaque System-Global Codex Authority Generation | 1, 2, 6, 7, 8 |
| Generation-Fenced Codex Operation Completion | 1, 2, 4, 5, 8 |
| Codex Owner Replacement and Revocation Precedence | 1, 2, 4, 5, 8 |
| Expiry, Recovery, and Value-Free Codex Provenance | 1, 2, 3, 6, 8 |
| Fence-Aware Codex Authority Recovery at Startup | 3, 6, 8 |
| Deterministic Provenance Cleanup Wiring | 1, 6, 8 |
| Pre-Launch and Prewarm Codex Auth Synchronization | 2, 3, 4, 8 |
| Codex Adapter Finalization Uses the Launch Operation | 2, 3, 4, 8 |
| Codex Projection Lock Is Not a Fallback Authorization | 3, 4, 8 |
| Dashboard Codex Mutations Use Shared Generation Precedence | 2, 5, 7, 8 |
| Dashboard Device Authentication Has a Durable Prelaunch Fence | 2, 3, 5, 8 |
| Reserved Codex Authority Provenance Boundary | 1, 2, 7, 8 |
| Provenance Expiry and Retention Security | 1, 2, 6, 8 |

## Execution Handoff

This plan is implementation-ready but deliberately non-operational. Execute it
only in a separately authorized implementation assignment. A worker must use
the stated TDD red/green sequence for every task, keep migration/role tests
real rather than mocked, and obtain deployment/credential-operation authority
separately from code-change approval.
