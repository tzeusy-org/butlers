"""Real-Postgres integration coverage for bu-ondtw.2's tool_exposure_policy vertical.

Neither the mocked-accessor unit tests (``tests/core/test_runtime_config.py``)
nor the mocked-pool API tests (``tests/api/test_runtime_config.py``) can prove
two governing claims from the bead design:

- The ``ck_runtime_config_tool_exposure_policy`` CHECK constraint rejects an
  invalid value at the database itself, independent of any Pydantic/dataclass
  validation layer (REQ-runtime-config-table-001, REQ-runtime-config-api-002).
- "The first session planned after a committed PATCH sees the new policy
  without a daemon restart" is a claim about *two independent processes* (the
  dashboard API and the butler daemon), each holding its own in-memory
  accessor state. Two ``RuntimeConfigAccessor`` instances sharing one real
  database stand in for those two processes: only a real DB round-trip can
  show that the daemon-side accessor's authoritative per-attempt read
  reflects a PATCH committed elsewhere, while its separately TTL-cached
  ``get()`` does not (REQ-core-tool-discovery-003).
"""

from __future__ import annotations

import asyncio
import shutil
import time
from concurrent.futures import ThreadPoolExecutor

import asyncpg
import pytest

from butlers.config import RuntimeSeedConfig
from butlers.core.runtime_config import RuntimeConfigAccessor

pytestmark = pytest.mark.integration

_DOCKER_AVAILABLE = shutil.which("docker") is not None
_skip_without_docker = pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")


def _prepare_core_222(postgres_container) -> str:
    """Create a real core database at core_222, immediately before core_224.

    core_223 is claimed by a different, still-open PR (#4043) that has not
    landed on main, so this migration chains directly after core_222.
    """
    from alembic import command
    from butlers.migrations import _build_alembic_config
    from butlers.testing.migration import create_migration_db, migration_db_name

    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, chains=["core"])
    command.upgrade(config, "core@core_222")
    return db_url


def _apply_core_224(db_url: str) -> None:
    from alembic import command
    from butlers.migrations import _build_alembic_config

    command.upgrade(_build_alembic_config(db_url, chains=["core"]), "core@core_224")


@_skip_without_docker
def test_migration_defaults_existing_row_to_eager_filtered_under_db_constraint(
    postgres_container,
) -> None:
    """Existing rows land on the conservative default and the vocabulary is DB-closed."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import IntegrityError

    db_url = _prepare_core_222(postgres_container)
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text("INSERT INTO public.runtime_config (butler_name) VALUES ('finance')"))
    finally:
        engine.dispose()

    _apply_core_224(db_url)

    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            value = conn.execute(
                text(
                    "SELECT tool_exposure_policy FROM public.runtime_config "
                    "WHERE butler_name = 'finance'"
                )
            ).scalar_one()
            assert value == "eager_filtered"

            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "UPDATE public.runtime_config SET tool_exposure_policy = 'caller-invented'"
                    )
                )
    finally:
        engine.dispose()


@_skip_without_docker
def test_downgrade_removes_column_without_dropping_existing_rows(postgres_container) -> None:
    """Rollback is eager-first: the column goes, the row (and its identity) does not."""
    from sqlalchemy import create_engine, text

    from alembic import command
    from butlers.migrations import _build_alembic_config

    db_url = _prepare_core_222(postgres_container)
    _apply_core_224(db_url)
    command.downgrade(_build_alembic_config(db_url, chains=["core"]), "core@core_222")

    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            columns = (
                conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name='runtime_config' "
                        "AND column_name='tool_exposure_policy'"
                    )
                )
                .scalars()
                .all()
            )
            assert columns == []
    finally:
        engine.dispose()


@_skip_without_docker
@pytest.mark.asyncio(loop_scope="session")
async def test_concurrent_seed_produces_one_row_with_the_seeded_policy(postgres_container) -> None:
    """Concurrent daemon-start seeding is race-safe and never loses the seed value."""
    from butlers.testing.migration import create_migrated_test_pool

    pool = await create_migrated_test_pool(postgres_container, chains=["core"])
    try:
        seed = RuntimeSeedConfig(tool_exposure_policy="auto")
        accessor_a = RuntimeConfigAccessor(pool, "public")
        accessor_b = RuntimeConfigAccessor(pool, "public")

        results = await asyncio.gather(
            accessor_a.seed_if_empty(seed, "concurrent-butler"),
            accessor_b.seed_if_empty(seed, "concurrent-butler"),
        )

        assert all(r.tool_exposure_policy == "auto" for r in results)
        row_count = await pool.fetchval(
            "SELECT count(*) FROM public.runtime_config WHERE butler_name = 'concurrent-butler'"
        )
        assert row_count == 1
    finally:
        await pool.close()


@_skip_without_docker
@pytest.mark.asyncio(loop_scope="session")
async def test_concurrent_git_reconciliation_writes_one_audit_row(postgres_container) -> None:
    """Two restarts converge on Git authority with one durable audit receipt."""
    from butlers.testing.migration import create_migrated_test_pool

    pool = await create_migrated_test_pool(postgres_container, chains=["core"])
    try:
        await pool.execute(
            "INSERT INTO public.runtime_config (butler_name, core_groups) VALUES ($1, $2)",
            "reconcile-butler",
            ["infra"],
        )
        seed = RuntimeSeedConfig(core_groups=("infra", "delegation", "fleet_cases"))
        accessor_a = RuntimeConfigAccessor(pool, "public")
        accessor_b = RuntimeConfigAccessor(pool, "public")

        results = await asyncio.gather(
            accessor_a.seed_if_empty(seed, "reconcile-butler"),
            accessor_b.seed_if_empty(seed, "reconcile-butler"),
        )

        assert all(
            result.core_groups == ("infra", "delegation", "fleet_cases") for result in results
        )
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM public.audit_log "
                "WHERE action = 'core_groups_reconciled' AND target = 'reconcile-butler'"
            )
            == 1
        )
    finally:
        await pool.close()


@_skip_without_docker
@pytest.mark.asyncio(loop_scope="session")
async def test_concurrent_operator_narrowing_wins_over_stale_startup_reconciliation(
    postgres_container,
) -> None:
    """Startup rechecks the locked row before replacing a stale narrowing."""
    from butlers.testing.migration import create_migrated_test_pool

    pool = await create_migrated_test_pool(postgres_container, chains=["core"])
    initial_read_done = asyncio.Event()
    allow_reconciliation = asyncio.Event()

    class PausingPool:
        def __init__(self) -> None:
            self.initial_read_seen = False

        async def execute(self, query, *args):
            return await pool.execute(query, *args)

        async def fetchrow(self, query, *args):
            row = await pool.fetchrow(query, *args)
            if query.lstrip().startswith("SELECT *") and not self.initial_read_seen:
                self.initial_read_seen = True
                initial_read_done.set()
                await allow_reconciliation.wait()
            return row

    try:
        await pool.execute(
            "INSERT INTO public.runtime_config (butler_name, core_groups) VALUES ($1, $2)",
            "operator-race-butler",
            ["infra"],
        )
        seed = RuntimeSeedConfig(core_groups=("infra", "delegation", "graph"))
        accessor = RuntimeConfigAccessor(PausingPool(), "public")
        startup = asyncio.create_task(accessor.seed_if_empty(seed, "operator-race-butler"))

        await asyncio.wait_for(initial_read_done.wait(), timeout=5)
        await pool.execute(
            "UPDATE public.runtime_config "
            "SET core_groups = $1, core_groups_narrowing_reason = $2 "
            "WHERE butler_name = $3",
            ["infra", "delegation"],
            "Operator disabled graph pending review",
            "operator-race-butler",
        )
        allow_reconciliation.set()

        result = await asyncio.wait_for(startup, timeout=5)
        assert result.core_groups == ("infra", "delegation")
        assert result.core_groups_narrowing_reason == "Operator disabled graph pending review"
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM public.audit_log "
                "WHERE action = 'core_groups_reconciled' AND target = 'operator-race-butler'"
            )
            == 0
        )
    finally:
        allow_reconciliation.set()
        await pool.close()


@_skip_without_docker
@pytest.mark.asyncio(loop_scope="session")
async def test_committed_patch_reaches_a_separate_process_accessor_without_restart(
    postgres_container,
) -> None:
    """Cross-process correctness for the hot policy.

    ``daemon_accessor`` stands in for a long-lived daemon process; the raw
    ``pool.execute`` UPDATE stands in for the dashboard API process, which
    writes with direct SQL (see ``routers/runtime_config.py``) rather than
    through any shared accessor or cache-invalidation channel.
    """
    from butlers.testing.migration import create_migrated_test_pool

    pool = await create_migrated_test_pool(postgres_container, chains=["core"])
    try:
        seed = RuntimeSeedConfig(tool_exposure_policy="eager_filtered")
        daemon_accessor = RuntimeConfigAccessor(pool, "public", ttl_s=300.0)
        await daemon_accessor.seed_if_empty(seed, "cross-process-butler")

        # Warm the daemon's long-lived TTL cache, as at startup.
        warm = await daemon_accessor.get()
        assert warm.tool_exposure_policy == "eager_filtered"

        # A separate process commits a PATCH directly against the DB row.
        await pool.execute(
            "UPDATE public.runtime_config SET tool_exposure_policy = 'auto' "
            "WHERE butler_name = 'cross-process-butler'"
        )

        # The daemon's TTL-cached get() alone is unaware of the commit.
        stale = await daemon_accessor.get()
        assert stale.tool_exposure_policy == "eager_filtered"

        # The per-attempt authoritative read sees it immediately: no restart,
        # no explicit invalidate_cache() call from the writer.
        live = await daemon_accessor.get_tool_exposure_policy()
        assert live == "auto"
    finally:
        await pool.close()


@_skip_without_docker
@pytest.mark.asyncio(loop_scope="session")
async def test_rejected_patch_value_never_becomes_the_committed_policy(postgres_container) -> None:
    """A failed/invalid PATCH must not be observable as if it had committed."""
    from butlers.testing.migration import create_migrated_test_pool

    pool = await create_migrated_test_pool(postgres_container, chains=["core"])
    try:
        seed = RuntimeSeedConfig(tool_exposure_policy="eager_filtered")
        accessor = RuntimeConfigAccessor(pool, "public")
        await accessor.seed_if_empty(seed, "rejected-patch-butler")

        with pytest.raises(asyncpg.PostgresError):
            await pool.execute(
                "UPDATE public.runtime_config SET tool_exposure_policy = 'caller-invented' "
                "WHERE butler_name = 'rejected-patch-butler'"
            )

        live = await accessor.get_tool_exposure_policy()
        assert live == "eager_filtered"
    finally:
        await pool.close()


@_skip_without_docker
@pytest.mark.parametrize("failed_predicate", ["acl", "durable_evidence"])
def test_core_232_deep_downgrade_preflight_preserves_head_and_round_trips(
    postgres_container,
    failed_predicate: str,
) -> None:
    """Every core_198 refusal fails before core_231 can commit."""
    from sqlalchemy import create_engine, text

    from alembic import command
    from butlers.migrations import _build_alembic_config, get_chain_head, run_migrations
    from butlers.testing.migration import (
        create_migration_db,
        migration_bootstrap_db_url,
        migration_db_name,
    )

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(run_migrations(db_url, chain="core"))
    bootstrap_url = migration_bootstrap_db_url(postgres_container, db_name)
    bootstrap_config = _build_alembic_config(bootstrap_url, chains=["core"])

    admin_engine = create_engine(bootstrap_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            # Project the state core_199's protected downgrade deliberately
            # requires an operator to establish before crossing core_198, then
            # introduce a separate catalog/ACL failure.
            conn.execute(
                text(
                    "DROP TRIGGER IF EXISTS runtime_attention_plant_legacy_debounce_marker_trigger "
                    "ON public.model_dispatch_attempts"
                )
            )
            conn.execute(
                text(
                    "DROP FUNCTION IF EXISTS "
                    "public.runtime_attention_plant_legacy_debounce_marker()"
                )
            )
            conn.execute(text("DROP TABLE IF EXISTS public.runtime_attention_producer_control"))
            if failed_predicate == "acl":
                conn.execute(
                    text("GRANT INSERT ON public.model_catalog TO runtime_attention_outbox_owner")
                )
            else:
                conn.execute(
                    text(
                        "INSERT INTO public.runtime_attention_outbox ("
                        "source, fleet_halt_month, lifecycle_state, source_snapshot, payload"
                        ") VALUES ("
                        "'fleet_halt', date '2026-08-01', 'pending', "
                        "jsonb_build_object('month', '2026-08', 'denied_count', 1, "
                        "'first_denied_at', NULL), "
                        "jsonb_build_object('classification', 'monthly_spend_ceiling', "
                        "'door', '/spend?outcome=quota_skip')"
                        ")"
                    )
                )

        with pytest.raises(RuntimeError, match="protected core_198 rollback preflight failed"):
            command.downgrade(bootstrap_config, "core_197")

        with create_engine(db_url).connect() as conn:
            assert conn.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == get_chain_head("core")
            assert (
                conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'runtime_config' "
                        "AND column_name = 'core_groups_narrowing_reason'"
                    )
                ).scalar_one()
                == "core_groups_narrowing_reason"
            )

        with admin_engine.connect() as conn:
            if failed_predicate == "acl":
                conn.execute(
                    text(
                        "REVOKE INSERT ON public.model_catalog FROM runtime_attention_outbox_owner"
                    )
                )
            else:
                conn.execute(text("TRUNCATE public.runtime_attention_outbox"))

        # A bounded rollback never crosses core_198 and remains reversible.
        command.downgrade(bootstrap_config, "core@-1")
        command.upgrade(bootstrap_config, "core@head")
        with create_engine(db_url).connect() as conn:
            assert conn.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == get_chain_head("core")
    finally:
        admin_engine.dispose()


@_skip_without_docker
def test_core_232_deep_downgrade_rechecks_evidence_after_lock_wait(
    postgres_container,
) -> None:
    """Evidence committed while preflight waits preserves the current head."""
    from sqlalchemy import create_engine, text

    from alembic import command
    from butlers.migrations import _build_alembic_config, run_migrations
    from butlers.testing.migration import (
        assert_at_chain_head,
        create_migration_db,
        migration_bootstrap_db_url,
        migration_db_name,
    )

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(run_migrations(db_url, chain="core"))
    bootstrap_url = migration_bootstrap_db_url(postgres_container, db_name)
    bootstrap_config = _build_alembic_config(bootstrap_url, chains=["core"])
    engine = create_engine(bootstrap_url)
    admin_engine = create_engine(bootstrap_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            conn.execute(
                text(
                    "DROP TRIGGER IF EXISTS runtime_attention_plant_legacy_debounce_marker_trigger "
                    "ON public.model_dispatch_attempts"
                )
            )
            conn.execute(
                text(
                    "DROP FUNCTION IF EXISTS "
                    "public.runtime_attention_plant_legacy_debounce_marker()"
                )
            )
            conn.execute(text("DROP TABLE IF EXISTS public.runtime_attention_producer_control"))

        writer = engine.connect()
        writer.execute(
            text(
                "INSERT INTO public.runtime_attention_outbox ("
                "source, fleet_halt_month, lifecycle_state, source_snapshot, payload"
                ") VALUES ("
                "'fleet_halt', date '2026-08-01', 'pending', "
                "jsonb_build_object('month', '2026-08', 'denied_count', 1, "
                "'first_denied_at', NULL), "
                "jsonb_build_object('classification', 'monthly_spend_ceiling', "
                "'door', '/spend?outcome=quota_skip')"
                ")"
            )
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            downgrade = pool.submit(command.downgrade, bootstrap_config, "core_197")
            try:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    with admin_engine.connect() as observer:
                        waiting = observer.execute(
                            text(
                                "SELECT EXISTS ("
                                "SELECT 1 FROM pg_locks WHERE NOT granted "
                                "AND locktype = 'relation' AND mode = 'AccessExclusiveLock' "
                                "AND relation = 'public.runtime_attention_outbox'::regclass"
                                ")"
                            )
                        ).scalar_one()
                    if waiting:
                        break
                    time.sleep(0.05)
                else:
                    raise AssertionError("core_232 preflight never waited for the evidence lock")

                writer.commit()
                with pytest.raises(
                    RuntimeError, match="protected core_198 rollback preflight failed"
                ):
                    downgrade.result(timeout=60)
            finally:
                writer.close()

        with engine.connect() as conn:
            assert_at_chain_head(conn)
            assert conn.execute(
                text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema = current_schema() "
                    "AND table_name = 'runtime_config' "
                    "AND column_name = 'core_groups_narrowing_reason'"
                    ")"
                )
            ).scalar_one()
            assert (
                conn.execute(
                    text("SELECT count(*) FROM public.runtime_attention_outbox")
                ).scalar_one()
                == 1
            )
    finally:
        engine.dispose()
        admin_engine.dispose()
