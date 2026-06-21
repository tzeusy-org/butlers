"""Regression tests for core_093 complexity_tier rename migration.

Covers:
- Migration revision chain metadata is correct.
- Upgrade path remap: extra_high→reasoning, high→reasoning, medium→workhorse,
  trivial→cheap, discretion→specialty, self_healing→specialty.
- Downgrade path inverse (best-effort, lossy): reasoning→high, workhorse→medium,
  cheap→trivial, specialty→discretion, local→trivial, legacy→trivial.
- CHECK constraint swap: old vocabulary → new canonical six
  (reasoning|workhorse|cheap|specialty|local|legacy).
- round_robin_counters SUM-merge collision when multiple old tiers map to the
  same new tier (e.g. 'high' counter + 'extra_high' counter → 'reasoning').
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

pytestmark = pytest.mark.integration

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_093_complexity_tier_rename.py"
)

_OLD_TIERS = ("trivial", "medium", "high", "extra_high", "discretion", "self_healing")
_NEW_TIERS = ("reasoning", "workhorse", "cheap", "specialty", "local", "legacy")

_OLD_CHECK = "('trivial', 'medium', 'high', 'extra_high', 'discretion', 'self_healing')"
_NEW_CHECK = "('reasoning', 'workhorse', 'cheap', 'specialty', 'local', 'legacy')"


# ---------------------------------------------------------------------------
# Module loader
# ---------------------------------------------------------------------------


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_093", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Helpers to materialise the three affected tables in a test DB
# (no FK wiring — only what the migration itself touches)
# ---------------------------------------------------------------------------


async def _create_prerequisite_tables(pool: asyncpg.Pool) -> None:
    """Create minimal versions of the three tables with old-vocabulary CHECK constraints."""
    await pool.execute(f"""
        CREATE TABLE public.model_catalog (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            alias           TEXT NOT NULL UNIQUE,
            runtime_type    TEXT NOT NULL DEFAULT 'codex',
            model_id        TEXT NOT NULL DEFAULT 'dummy',
            extra_args      JSONB NOT NULL DEFAULT '[]'::jsonb,
            complexity_tier TEXT NOT NULL DEFAULT 'medium',
            enabled         BOOLEAN NOT NULL DEFAULT true,
            priority        INTEGER NOT NULL DEFAULT 0,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_model_catalog_complexity_tier
                CHECK (complexity_tier IN {_OLD_CHECK})
        )
    """)

    await pool.execute(f"""
        CREATE TABLE public.butler_model_overrides (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            butler_name      TEXT NOT NULL,
            catalog_entry_id UUID NOT NULL REFERENCES public.model_catalog(id) ON DELETE CASCADE,
            enabled          BOOLEAN NOT NULL DEFAULT true,
            priority         INTEGER,
            complexity_tier  TEXT,
            source           TEXT,
            CONSTRAINT uq_butler_model_overrides_butler_entry
                UNIQUE (butler_name, catalog_entry_id),
            CONSTRAINT chk_butler_model_overrides_complexity_tier
                CHECK (complexity_tier IS NULL OR complexity_tier IN {_OLD_CHECK})
        )
    """)

    await pool.execute(f"""
        CREATE TABLE public.model_round_robin_counters (
            butler_name      TEXT NOT NULL,
            complexity_tier  TEXT NOT NULL,
            counter          BIGINT NOT NULL DEFAULT 0,
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (butler_name, complexity_tier),
            CONSTRAINT chk_rr_complexity_tier
                CHECK (complexity_tier IN {_OLD_CHECK})
        )
    """)


async def _run_upgrade(pool: asyncpg.Pool) -> None:
    """Replay upgrade() SQL statements against the live pool."""
    mod = _load_migration()
    sqls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = lambda sql: sqls.append(sql)
    with patch.object(mod, "op", mock_op):
        mod.upgrade()
    for sql in sqls:
        await pool.execute(sql)


async def _run_downgrade(pool: asyncpg.Pool) -> None:
    """Replay downgrade() SQL statements against the live pool."""
    mod = _load_migration()
    sqls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = lambda sql: sqls.append(sql)
    with patch.object(mod, "op", mock_op):
        mod.downgrade()
    for sql in sqls:
        await pool.execute(sql)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def migration_pool(provisioned_postgres_pool):
    """Fresh DB with pre-migration schema already in place."""
    async with provisioned_postgres_pool() as pool:
        await _create_prerequisite_tables(pool)
        yield pool


# ---------------------------------------------------------------------------
# Static / unit-style checks (no DB needed)
# ---------------------------------------------------------------------------


def test_migration_revision_chain() -> None:
    mod = _load_migration()
    assert mod.revision == "core_093"
    assert mod.down_revision == "core_092"


# ---------------------------------------------------------------------------
# Upgrade: data remap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_upgrade_remaps_model_catalog_tiers(migration_pool: asyncpg.Pool) -> None:
    """Each old tier in model_catalog is remapped to the correct new canonical value."""
    pool = migration_pool

    # Temporarily drop check to seed old-vocabulary rows.
    await pool.execute(
        "ALTER TABLE public.model_catalog DROP CONSTRAINT IF EXISTS chk_model_catalog_complexity_tier"
    )

    old_to_new = {
        "extra_high": "reasoning",
        "high": "reasoning",
        "medium": "workhorse",
        "trivial": "cheap",
        "discretion": "specialty",
        "self_healing": "specialty",
    }
    for old_tier, expected_new in old_to_new.items():
        await pool.execute(
            "INSERT INTO public.model_catalog (alias, runtime_type, model_id, complexity_tier)"
            " VALUES ($1, 'codex', 'test-model', $2)",
            old_tier,
            old_tier,  # alias == old tier name; migration will rename it
        )

    await _run_upgrade(pool)

    rows = await pool.fetch(
        "SELECT alias, complexity_tier FROM public.model_catalog ORDER BY alias"
    )
    result = {r["alias"]: r["complexity_tier"] for r in rows}
    for old_alias, expected_new in old_to_new.items():
        assert result[old_alias] == expected_new, (
            f"model_catalog alias={old_alias!r}: expected {expected_new!r}, got {result[old_alias]!r}"
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_upgrade_remaps_butler_model_overrides_tiers(migration_pool: asyncpg.Pool) -> None:
    """Each old tier in butler_model_overrides is remapped to the correct new canonical value."""
    pool = migration_pool

    # Seed a model_catalog row (with old check already dropped by upgrade scaffold).
    await pool.execute(
        "ALTER TABLE public.model_catalog DROP CONSTRAINT IF EXISTS chk_model_catalog_complexity_tier"
    )
    await pool.execute(
        "ALTER TABLE public.butler_model_overrides"
        " DROP CONSTRAINT IF EXISTS chk_butler_model_overrides_complexity_tier"
    )

    catalog_id = await pool.fetchval(
        "INSERT INTO public.model_catalog (alias, runtime_type, model_id, complexity_tier)"
        " VALUES ('base-model', 'codex', 'test-model', 'medium') RETURNING id"
    )

    old_to_new = {
        "extra_high": "reasoning",
        "high": "reasoning",
        "medium": "workhorse",
        "trivial": "cheap",
        "discretion": "specialty",
        "self_healing": "specialty",
    }
    for old_tier_name, expected_new in old_to_new.items():
        await pool.execute(
            "INSERT INTO public.butler_model_overrides"
            " (butler_name, catalog_entry_id, complexity_tier)"
            " VALUES ($1, $2, $3)",
            old_tier_name,
            catalog_id,
            old_tier_name,  # seed OLD vocabulary so the migration's UPDATE is exercised
        )

    await _run_upgrade(pool)

    rows = await pool.fetch(
        "SELECT butler_name, complexity_tier FROM public.butler_model_overrides"
    )
    result = {r["butler_name"]: r["complexity_tier"] for r in rows}
    for butler, expected_new in old_to_new.items():
        assert result[butler] == expected_new, (
            f"butler_model_overrides butler={butler!r}: expected {expected_new!r},"
            f" got {result[butler]!r}"
        )


# ---------------------------------------------------------------------------
# Upgrade: CHECK constraint swap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_upgrade_check_constraint_rejects_old_vocabulary(
    migration_pool: asyncpg.Pool,
) -> None:
    """After upgrade, inserting an old-vocabulary tier into model_catalog raises."""
    pool = migration_pool
    await _run_upgrade(pool)

    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            "INSERT INTO public.model_catalog (alias, runtime_type, model_id, complexity_tier)"
            " VALUES ('bad-row', 'codex', 'test-model', 'high')"
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_upgrade_check_constraint_accepts_new_vocabulary(
    migration_pool: asyncpg.Pool,
) -> None:
    """After upgrade, all new canonical tiers are accepted by model_catalog."""
    pool = migration_pool
    await _run_upgrade(pool)

    for tier in _NEW_TIERS:
        row_id = await pool.fetchval(
            "INSERT INTO public.model_catalog (alias, runtime_type, model_id, complexity_tier)"
            " VALUES ($1, 'codex', 'test-model', $2) RETURNING id",
            f"new-model-{tier}",
            tier,
        )
        assert row_id is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_upgrade_check_constraint_rr_counters(migration_pool: asyncpg.Pool) -> None:
    """After upgrade, old-vocabulary tiers are rejected in round_robin_counters."""
    pool = migration_pool
    await _run_upgrade(pool)

    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            "INSERT INTO public.model_round_robin_counters (butler_name, complexity_tier, counter)"
            " VALUES ('butler-a', 'extra_high', 1)"
        )


# ---------------------------------------------------------------------------
# Upgrade: round_robin_counters SUM-merge collision
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_upgrade_round_robin_counters_sum_merge(migration_pool: asyncpg.Pool) -> None:
    """Counters for high and extra_high both map to reasoning and are summed.

    Likewise discretion and self_healing merge into specialty via SUM.
    Old-vocabulary rows must be absent after upgrade.
    """
    pool = migration_pool

    # Drop the old check to insert rows freely.
    await pool.execute(
        "ALTER TABLE public.model_round_robin_counters"
        " DROP CONSTRAINT IF EXISTS chk_rr_complexity_tier"
    )

    # Seed overlapping tiers for butler-a.
    rows_to_seed = [
        ("butler-a", "high", 10),
        ("butler-a", "extra_high", 7),
        ("butler-a", "medium", 5),
        ("butler-a", "discretion", 3),
        ("butler-a", "self_healing", 2),
        ("butler-a", "trivial", 1),
    ]
    for butler, tier, count in rows_to_seed:
        await pool.execute(
            "INSERT INTO public.model_round_robin_counters (butler_name, complexity_tier, counter)"
            " VALUES ($1, $2, $3)",
            butler,
            tier,
            count,
        )

    # Re-add the old check before upgrade so the migration's DROP can find it.
    await pool.execute(f"""
        ALTER TABLE public.model_round_robin_counters
        ADD CONSTRAINT chk_rr_complexity_tier
            CHECK (complexity_tier IN {_OLD_CHECK})
    """)

    await _run_upgrade(pool)

    rows = await pool.fetch(
        "SELECT complexity_tier, counter FROM public.model_round_robin_counters"
        " WHERE butler_name = 'butler-a' ORDER BY complexity_tier"
    )
    result = {r["complexity_tier"]: r["counter"] for r in rows}

    # high(10) + extra_high(7) = 17
    assert result.get("reasoning") == 17, f"reasoning counter wrong: {result}"
    # medium(5) → workhorse
    assert result.get("workhorse") == 5, f"workhorse counter wrong: {result}"
    # trivial(1) → cheap
    assert result.get("cheap") == 1, f"cheap counter wrong: {result}"
    # discretion(3) + self_healing(2) = 5
    assert result.get("specialty") == 5, f"specialty counter wrong: {result}"

    # Old-vocabulary rows must be absent.
    for old_tier in _OLD_TIERS:
        assert old_tier not in result, f"Old tier {old_tier!r} still present after upgrade"


# ---------------------------------------------------------------------------
# Downgrade: data remap (best-effort / lossy)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_downgrade_remaps_model_catalog_tiers(migration_pool: asyncpg.Pool) -> None:
    """Downgrade maps new canonical tiers back to old vocabulary (lossy choices documented).

    Inverse mapping used by the migration:
      reasoning → high   (lossy: could have been extra_high)
      workhorse → medium
      cheap     → trivial
      specialty → discretion  (lossy: could have been self_healing)
      local     → trivial  (lossy: no old equivalent)
      legacy    → trivial  (lossy: no old equivalent)
    """
    pool = migration_pool

    # Run upgrade first so CHECK is on new vocabulary.
    await _run_upgrade(pool)

    new_to_expected_old = {
        "reasoning": "high",
        "workhorse": "medium",
        "cheap": "trivial",
        "specialty": "discretion",
        "local": "trivial",
        "legacy": "trivial",
    }
    for alias, new_tier in new_to_expected_old.items():
        # After upgrade, complexity_tier must use the NEW vocabulary (alias = new tier name).
        await pool.execute(
            "INSERT INTO public.model_catalog (alias, runtime_type, model_id, complexity_tier)"
            " VALUES ($1, 'codex', 'test-model', $2)",
            alias,
            alias,  # alias IS the new tier name; new_to_expected_old[alias] is the expected old
        )

    await _run_downgrade(pool)

    rows = await pool.fetch(
        "SELECT alias, complexity_tier FROM public.model_catalog ORDER BY alias"
    )
    result = {r["alias"]: r["complexity_tier"] for r in rows}
    for alias, expected_old in new_to_expected_old.items():
        assert result[alias] == expected_old, (
            f"downgrade model_catalog alias={alias!r}: expected {expected_old!r},"
            f" got {result[alias]!r}"
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_downgrade_check_constraint_rejects_new_vocabulary(
    migration_pool: asyncpg.Pool,
) -> None:
    """After downgrade, inserting a new-vocabulary tier into model_catalog raises."""
    pool = migration_pool
    await _run_upgrade(pool)
    await _run_downgrade(pool)

    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            "INSERT INTO public.model_catalog (alias, runtime_type, model_id, complexity_tier)"
            " VALUES ('bad-row', 'codex', 'test-model', 'reasoning')"
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_downgrade_check_constraint_accepts_old_vocabulary(
    migration_pool: asyncpg.Pool,
) -> None:
    """After downgrade, all old-vocabulary tiers are accepted by model_catalog."""
    pool = migration_pool
    await _run_upgrade(pool)
    await _run_downgrade(pool)

    for tier in _OLD_TIERS:
        row_id = await pool.fetchval(
            "INSERT INTO public.model_catalog (alias, runtime_type, model_id, complexity_tier)"
            " VALUES ($1, 'codex', 'test-model', $2) RETURNING id",
            f"old-model-{tier}",
            tier,
        )
        assert row_id is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_downgrade_round_robin_counters_restored(migration_pool: asyncpg.Pool) -> None:
    """After upgrade then downgrade, round_robin_counters use old-vocabulary tiers."""
    pool = migration_pool

    # Drop old check, seed rows.
    await pool.execute(
        "ALTER TABLE public.model_round_robin_counters"
        " DROP CONSTRAINT IF EXISTS chk_rr_complexity_tier"
    )
    await pool.execute(
        "INSERT INTO public.model_round_robin_counters (butler_name, complexity_tier, counter)"
        " VALUES ('butler-b', 'medium', 4)"
    )
    await pool.execute(f"""
        ALTER TABLE public.model_round_robin_counters
        ADD CONSTRAINT chk_rr_complexity_tier
            CHECK (complexity_tier IN {_OLD_CHECK})
    """)

    await _run_upgrade(pool)
    await _run_downgrade(pool)

    rows = await pool.fetch(
        "SELECT complexity_tier, counter FROM public.model_round_robin_counters"
        " WHERE butler_name = 'butler-b'"
    )
    result = {r["complexity_tier"]: r["counter"] for r in rows}

    # medium(4) → workhorse(4) on upgrade → medium(4) on downgrade
    assert result.get("medium") == 4, f"Expected medium=4 after round-trip; got {result}"

    # No new-vocabulary tiers should remain.
    for new_tier in _NEW_TIERS:
        assert new_tier not in result, f"New tier {new_tier!r} found after downgrade"


# ---------------------------------------------------------------------------
# last_verified_* columns: added by upgrade, removed by downgrade
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_upgrade_adds_last_verified_columns(migration_pool: asyncpg.Pool) -> None:
    """Upgrade adds last_verified_at, last_verified_latency_ms, last_verified_ok."""
    pool = migration_pool
    await _run_upgrade(pool)

    cols = await pool.fetch(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema = 'public' AND table_name = 'model_catalog'"
    )
    col_names = {r["column_name"] for r in cols}
    for col in ("last_verified_at", "last_verified_latency_ms", "last_verified_ok"):
        assert col in col_names, f"Expected column {col!r} not found after upgrade"


@pytest.mark.asyncio(loop_scope="session")
async def test_downgrade_removes_last_verified_columns(migration_pool: asyncpg.Pool) -> None:
    """Downgrade drops the three last_verified_* columns."""
    pool = migration_pool
    await _run_upgrade(pool)
    await _run_downgrade(pool)

    cols = await pool.fetch(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema = 'public' AND table_name = 'model_catalog'"
    )
    col_names = {r["column_name"] for r in cols}
    for col in ("last_verified_at", "last_verified_latency_ms", "last_verified_ok"):
        assert col not in col_names, f"Column {col!r} should be gone after downgrade"
