"""Regression tests for core_159 (model_catalog_defaults.toml bootstrap reseed).

bu-vq97l: core_004's ``_load_seed_entries()`` filters
``model_catalog_defaults.toml`` entries against the LEGACY tier vocabulary
(``trivial``/``medium``/``high``/``extra_high``/``discretion``/
``self_healing``), but the toml's entries all carry the CANONICAL
post-core_093 vocabulary (``cheap``/``workhorse``/``reasoning``/
``specialty``/``local``/``legacy``). None of them ever matched, so a
genuinely fresh install seeds ZERO rows from the toml — the ``workhorse``,
``reasoning``, and ``local`` tiers end up with no catalog candidates at all
(only core_157's two ``api-haiku-*`` rows exist, both ``cheap``/``specialty``).

These tests prove:
  1. The static revision chain wiring (core_159 revises core_157).
  2. Against a REAL fresh Alembic bootstrap of the full core chain (the exact
     regression the bug survived without — a prior "empirical" check only
     ever inspected a migrated test DB manually, never asserted in CI), every
     canonical tier that has toml entries actually gets catalog rows, and
     specific toml aliases resolve with the correct tier/runtime/model_id.
  3. Re-running the migration's insert is idempotent (``ON CONFLICT DO
     NOTHING`` — no duplicate-alias errors, no row-count growth).
  4. ``downgrade()`` removes exactly the toml-derived aliases it inserted.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import (
    create_migrated_test_db,
    create_migration_db,
    migration_db_name,
)

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_159_model_catalog_defaults_reseed.py"
)

_DEFAULTS_TOML_PATH = Path(__file__).resolve().parents[2] / "model_catalog_defaults.toml"


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_159", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_revision_chain() -> None:
    mod = _load_migration()
    assert mod.revision == "core_159"
    assert mod.down_revision == "core_157"


def test_canonical_tiers_match_core_093_rename_target() -> None:
    """core_159's tier filter must accept exactly the vocabulary core_093 introduced."""
    mod = _load_migration()
    assert set(mod._CANONICAL_TIERS) == {
        "reasoning",
        "workhorse",
        "cheap",
        "specialty",
        "local",
        "legacy",
    }


def test_defaults_toml_entries_all_use_canonical_vocab() -> None:
    """Guard against re-drifting the toml back to legacy tier names."""
    import tomllib

    mod = _load_migration()
    with open(_DEFAULTS_TOML_PATH, "rb") as f:
        data = tomllib.load(f)
    for entry in data["models"]:
        assert entry["complexity_tier"] in mod._CANONICAL_TIERS, (
            f"{entry['alias']!r} uses complexity_tier={entry['complexity_tier']!r}, "
            "which core_159's seed filter won't match — either the toml drifted back "
            "to legacy vocab, or a new canonical tier was added without updating "
            "_CANONICAL_TIERS in core_159."
        )


# ---------------------------------------------------------------------------
# Fresh-bootstrap regression (real Postgres, full core chain)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fresh_core_db_url(postgres_container) -> str:
    """A genuinely fresh database with the FULL core migration chain applied.

    This is the reproduction environment for the bug: a brand new install
    running every core migration from core_001 through head, exactly as
    ``butlers.migrations.run_migrations`` would on first daemon boot.
    """
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
    )


async def _fetch_catalog_rows(db_url: str) -> list[asyncpg.Record]:
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
    try:
        return await pool.fetch(
            "SELECT alias, runtime_type, model_id, complexity_tier, priority, enabled"
            " FROM public.model_catalog"
        )
    finally:
        await pool.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_fresh_bootstrap_seeds_every_toml_tier(fresh_core_db_url: str) -> None:
    """The bug: on a fresh install, workhorse/reasoning/local had ZERO rows.

    After core_159, every tier represented in model_catalog_defaults.toml has
    at least one catalog row post-bootstrap.
    """
    import tomllib

    with open(_DEFAULTS_TOML_PATH, "rb") as f:
        toml_data = tomllib.load(f)
    tiers_in_toml = {m["complexity_tier"] for m in toml_data["models"]}

    rows = await _fetch_catalog_rows(fresh_core_db_url)
    tiers_seeded = {row["complexity_tier"] for row in rows}

    missing = tiers_in_toml - tiers_seeded
    assert not missing, (
        f"Tiers {missing} have toml entries but zero model_catalog rows after a "
        "fresh core-chain bootstrap — the toml seed is still dead for them."
    )
    # Sanity: workhorse/reasoning/local specifically must not regress to empty
    # (these were the tiers silently starved by the original bug).
    assert "workhorse" in tiers_seeded
    assert "reasoning" in tiers_seeded
    assert "local" in tiers_seeded


@pytest.mark.asyncio(loop_scope="session")
async def test_fresh_bootstrap_specific_toml_alias_resolves(fresh_core_db_url: str) -> None:
    """A representative, non-core_157 toml alias lands with its declared shape."""
    rows = await _fetch_catalog_rows(fresh_core_db_url)
    by_alias = {row["alias"]: row for row in rows}

    assert "gpt-5.4-mini" in by_alias, (
        "gpt-5.4-mini (cheap, enabled, from model_catalog_defaults.toml) is missing "
        "from a fresh install's model_catalog — toml bootstrap seeding regressed."
    )
    row = by_alias["gpt-5.4-mini"]
    assert row["runtime_type"] == "codex"
    assert row["model_id"] == "gpt-5.4-mini"
    assert row["complexity_tier"] == "cheap"
    assert row["enabled"] is True

    # core_157's data-migration rows must still be present and untouched.
    assert "api-haiku-cheap" in by_alias
    assert by_alias["api-haiku-cheap"]["priority"] == 30


@pytest.mark.asyncio(loop_scope="session")
async def test_reseed_upgrade_is_idempotent(fresh_core_db_url: str) -> None:
    """Re-running core_159's upgrade() again must not error or duplicate rows."""
    mod = _load_migration()
    pool = await asyncpg.create_pool(fresh_core_db_url, min_size=1, max_size=2)
    try:
        before = await pool.fetchval("SELECT COUNT(*) FROM public.model_catalog")

        # Re-run the same seed INSERT the migration's upgrade() issues, using
        # asyncpg's native $-style params directly (sqlalchemy's ``:name``
        # bind style used inside the real migration doesn't translate 1:1
        # without a live Alembic op.get_bind(), so this reproduces the same
        # SQL/ON CONFLICT semantics against the pool instead of replaying the
        # module's upgrade() function verbatim).
        seed_entries = mod._load_seed_entries()
        for entry in seed_entries:
            await pool.execute(
                """
                INSERT INTO public.model_catalog
                    (alias, runtime_type, model_id, extra_args,
                     complexity_tier, priority, enabled)
                VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
                ON CONFLICT (alias) DO NOTHING
                """,
                entry["alias"],
                entry["runtime_type"],
                entry["model_id"],
                json.dumps(entry.get("extra_args", [])),
                entry["complexity_tier"],
                entry.get("priority", 0),
                entry.get("enabled", True),
            )

        after = await pool.fetchval("SELECT COUNT(*) FROM public.model_catalog")
        assert after == before, "Re-running the toml reseed must not add duplicate rows"
    finally:
        await pool.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_downgrade_removes_toml_aliases(fresh_core_db_url: str) -> None:
    """downgrade() deletes exactly the toml-derived aliases; other rows survive."""
    mod = _load_migration()
    pool = await asyncpg.create_pool(fresh_core_db_url, min_size=1, max_size=2)
    try:
        seed_entries = mod._load_seed_entries()
        aliases = [entry["alias"] for entry in seed_entries]

        # Insert a sentinel non-toml row to prove downgrade doesn't touch it.
        await pool.execute(
            """
            INSERT INTO public.model_catalog
                (alias, runtime_type, model_id, complexity_tier, priority, enabled)
            VALUES ('sentinel-manual-entry', 'codex', 'sentinel-model', 'workhorse', 1, true)
            ON CONFLICT (alias) DO NOTHING
            """
        )

        await pool.execute(
            "DELETE FROM public.model_catalog WHERE alias = ANY($1::text[])",
            aliases,
        )

        remaining_toml_aliases = await pool.fetchval(
            "SELECT COUNT(*) FROM public.model_catalog WHERE alias = ANY($1::text[])",
            aliases,
        )
        assert remaining_toml_aliases == 0

        sentinel_count = await pool.fetchval(
            "SELECT COUNT(*) FROM public.model_catalog WHERE alias = 'sentinel-manual-entry'"
        )
        assert sentinel_count == 1
    finally:
        await pool.close()


@pytest.mark.integration
def test_downgrade_and_reupgrade_real_alembic_roundtrip(postgres_container) -> None:
    """Drive the actual ``upgrade()``/``downgrade()`` functions through real Alembic.

    The tests above replay the migration's INSERT/DELETE logic by hand via a raw
    asyncpg pool (see the comment on ``test_reseed_upgrade_is_idempotent``) because
    ``op.get_bind()`` only resolves inside a live Alembic migration context. That
    leaves the module's own ``upgrade()``/``downgrade()`` code paths — including
    the ``sa.text(...)``/``:name``-style bind params they actually use — completely
    unexercised. This test closes that gap using the same
    ``alembic.command.upgrade``/``downgrade`` pattern already established by
    ``tests/migrations/test_calendar_search_trgm_migration.py``, against its own
    dedicated database so it cannot disturb the module-scoped fixture the async
    tests above share.
    """
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    core = _build_alembic_config(db_url, chains=["core"])

    command.upgrade(core, "core@head")

    mod = _load_migration()
    aliases = [entry["alias"] for entry in mod._load_seed_entries()]
    assert aliases, "toml must have canonical-vocab entries for this test to mean anything"

    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            seeded = conn.execute(
                text("SELECT COUNT(*) FROM public.model_catalog WHERE alias = ANY(:aliases)"),
                {"aliases": aliases},
            ).scalar()
        assert seeded == len(aliases), "upgrade() should have seeded every toml alias"

        # Real downgrade(): must delete exactly the toml-derived aliases via op.get_bind().
        command.downgrade(core, "core_157")
        with engine.connect() as conn:
            after_downgrade = conn.execute(
                text("SELECT COUNT(*) FROM public.model_catalog WHERE alias = ANY(:aliases)"),
                {"aliases": aliases},
            ).scalar()
        assert after_downgrade == 0, "downgrade() must remove every toml-derived alias"

        # Real upgrade() again: proves the ON CONFLICT DO NOTHING INSERT path
        # works standalone through op.get_bind(), not just via hand-reproduced SQL.
        # Target "core@head" (not a hardcoded revision id) so this test survives
        # this migration being renumbered again by a parallel lane.
        command.upgrade(core, "core@head")
        with engine.connect() as conn:
            after_reupgrade = conn.execute(
                text("SELECT COUNT(*) FROM public.model_catalog WHERE alias = ANY(:aliases)"),
                {"aliases": aliases},
            ).scalar()
        assert after_reupgrade == len(aliases), "re-running upgrade() must reseed every alias"
    finally:
        engine.dispose()
