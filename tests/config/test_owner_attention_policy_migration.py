"""Contract tests for core_177 Owner Attention Policy consolidation.

The migration runs in the shared public schema but is replayed by the core
chain in varied database shapes. These tests keep its guarded precedence and
downgrade compatibility visible without requiring a live deployment database.
"""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = (
    _REPO_ROOT / "alembic" / "versions" / "core" / "core_177_consolidate_owner_attention_policy.py"
)
_DOCKER_AVAILABLE = shutil.which("docker") is not None


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_177_owner_attention", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sqls(function_name: str) -> list[str]:
    migration = _load_migration()
    op = MagicMock()
    captured: list[str] = []
    op.execute.side_effect = captured.append
    with patch.object(migration, "op", op):
        getattr(migration, function_name)()
    return captured


def test_core_177_chains_from_current_core_head() -> None:
    migration = _load_migration()
    assert migration.revision == "core_177"
    assert migration.down_revision == "core_176"
    assert migration.branch_labels is None
    assert callable(migration.upgrade)
    assert callable(migration.downgrade)


def test_upgrade_guards_optional_legacy_schema_and_preserves_canonical_precedence() -> None:
    sql = "\n".join(_sqls("upgrade"))
    assert "to_regclass('public.approvals_policy')" in sql
    assert "to_regclass('public.insight_settings')" in sql
    assert "canonical_start IS NULL OR canonical_end IS NULL" in sql
    assert "legacy_start IS NOT NULL AND legacy_end IS NOT NULL" in sql
    assert "legacy_start BETWEEN 0 AND 23" in sql
    assert "legacy_end BETWEEN 0 AND 23" in sql
    assert "NULLIF(btrim(legacy_timezone), '')" in sql
    assert "DROP COLUMN IF EXISTS quiet_start" in sql
    assert "DROP COLUMN IF EXISTS quiet_end" in sql
    assert "DROP COLUMN IF EXISTS quiet_timezone" in sql


def test_downgrade_restores_legacy_shape_from_canonical_policy() -> None:
    sql = "\n".join(_sqls("downgrade"))
    assert "ADD COLUMN IF NOT EXISTS quiet_start INTEGER" in sql
    assert "ADD COLUMN IF NOT EXISTS quiet_end INTEGER" in sql
    assert "ADD COLUMN IF NOT EXISTS quiet_timezone TEXT" in sql
    assert "quiet_start = EXCLUDED.quiet_start" in sql
    assert "quiet_end = EXCLUDED.quiet_end" in sql
    assert "quiet_timezone = EXCLUDED.quiet_timezone" in sql


def _prepare_core_176(postgres_container) -> str:
    """Create an actual core database immediately before core_177."""
    from alembic import command
    from butlers.migrations import _build_alembic_config
    from butlers.testing.migration import create_migration_db, migration_db_name

    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, chains=["core"])
    command.upgrade(config, "core@core_176")
    return db_url


def _apply_core_177(db_url: str) -> None:
    from alembic import command
    from butlers.migrations import _build_alembic_config

    command.upgrade(_build_alembic_config(db_url, chains=["core"]), "core@core_177")


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
@pytest.mark.parametrize(
    ("canonical", "legacy", "expected"),
    [
        (
            (None, 7, "Asia/Singapore"),
            (22, 7, "Mars/Olympus"),
            (22, 7, "Mars/Olympus"),
        ),
        (
            (23, 8, "Asia/Singapore"),
            (22, 7, "America/New_York"),
            (23, 8, "Asia/Singapore"),
        ),
        (
            (23, 8, "Mars/Olympus"),
            (22, 7, "America/New_York"),
            (23, 8, "Mars/Olympus"),
        ),
    ],
    ids=[
        "complete-legacy-backfills-partial-canonical",
        "complete-canonical-wins-conflict",
        "complete-canonical-invalid-zone-still-wins",
    ],
)
def test_upgrade_consolidates_real_pre_177_schema(
    postgres_container,
    canonical: tuple[int | None, int | None, str],
    legacy: tuple[int, int, str],
    expected,
) -> None:
    """Run the migration against real pre-177 tables, including an invalid IANA string."""
    from sqlalchemy import create_engine, text

    db_url = _prepare_core_176(postgres_container)
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    "UPDATE public.approvals_policy "
                    "SET quiet_start_hour=:start, quiet_end_hour=:end, timezone=:timezone "
                    "WHERE id=1"
                ),
                {"start": canonical[0], "end": canonical[1], "timezone": canonical[2]},
            )
            conn.execute(
                text(
                    "UPDATE public.insight_settings "
                    "SET quiet_start=:start, quiet_end=:end, quiet_timezone=:timezone "
                    "WHERE id=1"
                ),
                {"start": legacy[0], "end": legacy[1], "timezone": legacy[2]},
            )
    finally:
        engine.dispose()

    _apply_core_177(db_url)
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT quiet_start_hour, quiet_end_hour, timezone "
                    "FROM public.approvals_policy WHERE id=1"
                )
            ).one()
            assert tuple(row) == expected
            legacy_columns = (
                conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name='insight_settings' "
                        "AND column_name IN ('quiet_start', 'quiet_end', 'quiet_timezone')"
                    )
                )
                .scalars()
                .all()
            )
            assert legacy_columns == []
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
def test_upgrade_fails_open_for_partial_canonical_without_legacy_schema(postgres_container) -> None:
    """Core-only installs do not need insight tables to preserve a safe policy state."""
    from sqlalchemy import create_engine, text

    db_url = _prepare_core_176(postgres_container)
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text("DROP TABLE public.insight_settings"))
            conn.execute(
                text(
                    "UPDATE public.approvals_policy "
                    "SET quiet_start_hour=22, quiet_end_hour=NULL, timezone='Asia/Singapore' "
                    "WHERE id=1"
                )
            )
    finally:
        engine.dispose()

    _apply_core_177(db_url)
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT quiet_start_hour, quiet_end_hour, timezone "
                    "FROM public.approvals_policy WHERE id=1"
                )
            ).one()
            assert tuple(row) == (None, None, "Asia/Singapore")
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
def test_core_chain_replay_skips_retired_legacy_insight_seed(postgres_container) -> None:
    """Schema-scoped core replays remain safe after core_177 removes legacy fields."""
    from butlers.migrations import run_migrations

    db_url = _prepare_core_176(postgres_container)
    _apply_core_177(db_url)

    asyncio.run(run_migrations(db_url, chain="core", schema="general"))


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
def test_downgrade_restores_legacy_values_from_canonical_policy(postgres_container) -> None:
    """An older broker regains its three fields populated from the canonical row."""
    from sqlalchemy import create_engine, text

    from alembic import command
    from butlers.migrations import _build_alembic_config

    db_url = _prepare_core_176(postgres_container)
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    "UPDATE public.approvals_policy "
                    "SET quiet_start_hour=23, quiet_end_hour=8, timezone='Asia/Singapore' "
                    "WHERE id=1"
                )
            )
    finally:
        engine.dispose()

    _apply_core_177(db_url)
    config = _build_alembic_config(db_url, chains=["core"])
    command.downgrade(config, "core@core_176")

    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT quiet_start, quiet_end, quiet_timezone "
                    "FROM public.insight_settings WHERE id=1"
                )
            ).one()
            assert tuple(row) == (23, 8, "Asia/Singapore")
    finally:
        engine.dispose()
