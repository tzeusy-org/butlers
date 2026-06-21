"""Tests for core_115_drop_contact_info (migration bead 10, bu-e2ja9).

Covers:
  (a) Migration module structure — revision/down_revision, callables, accepted
      unmapped-type env parsing.  Pure unit, no DB.
  (b) Parity guard SQL — the in-migration zero-loss check.  Integration
      (requires Docker/Postgres): seeds entities/contacts/entity_facts/contact_info
      and asserts the sweep counts exactly the non-secured, entity-linked, mapped
      rows that lack a matching active triple (and excludes secured rows,
      owner-accepted unmapped types, and telegram ``telegram:``-prefixed objects).

Spec anchor: Brief §6b Amendment 1.1.A.6 + Amendment 1.1.C bead 10.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_115_drop_contact_info.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("_migration_core_115", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# (a) Unit: module structure + env parsing
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMigrationStructure:
    def test_revision_chain(self):
        mod = _load_migration()
        assert mod.revision == "core_115"
        assert mod.down_revision == "core_114"

    def test_accepted_unmapped_default_includes_google_health(self, monkeypatch):
        monkeypatch.delenv("CONTACT_INFO_DROP_ACCEPTED_UNMAPPED_TYPES", raising=False)
        assert "google_health" in _load_migration()._accepted_unmapped_types()

    def test_accepted_unmapped_env_override(self, monkeypatch):
        monkeypatch.setenv("CONTACT_INFO_DROP_ACCEPTED_UNMAPPED_TYPES", "fax, pager ,")
        assert _load_migration()._accepted_unmapped_types() == ["fax", "pager"]

    def test_parity_sql_has_zero_loss_guards(self):
        sql = _load_migration()._PARITY_SWEEP_SQL.text
        # Secured rows are carved out to public.entity_info, never blocking.
        assert "secured = false" in sql
        # Orphan + tombstone guards.
        assert "entity_id IS NOT NULL" in sql
        assert "merged_into" in sql
        # Telegram objects are stored with a 'telegram:' prefix.
        assert "telegram:" in sql
        assert "validity  = 'active'" in sql or "validity = 'active'" in sql

    def test_drops_table_and_snapshots(self):
        src = _MIGRATION_PATH.read_text()
        assert "DROP TABLE IF EXISTS public.contact_info" in src
        assert "contact_info_dropbak_core_115" in src
        assert "CONTACT_INFO_DROP_FORCE" in src


# ---------------------------------------------------------------------------
# (b) Integration: parity guard SQL behaviour against a live DB
# ---------------------------------------------------------------------------

# provisioned_postgres_pool() only creates the DB + extensions; the migration
# chain is not run here. Build the minimal schema the parity sweep references.
_PROVISION_SCHEMA = """
CREATE SCHEMA IF NOT EXISTS relationship;

CREATE TABLE IF NOT EXISTS public.entities (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name TEXT NOT NULL,
    roles          TEXT[] NOT NULL DEFAULT '{}',
    metadata       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.contacts (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name       TEXT NOT NULL,
    entity_id  UUID REFERENCES public.entities(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS relationship.entity_facts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject     UUID NOT NULL,
    predicate   TEXT NOT NULL,
    object      TEXT,
    object_kind TEXT NOT NULL DEFAULT 'literal',
    validity    TEXT NOT NULL DEFAULT 'active',
    src         TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.contact_info (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id  UUID NOT NULL REFERENCES public.contacts(id) ON DELETE CASCADE,
    type        VARCHAR NOT NULL,
    value       TEXT NOT NULL,
    secured     BOOLEAN NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ DEFAULT now()
);
"""


async def _mk_entity_contact(pool, name: str):
    entity_id = await pool.fetchval(
        "INSERT INTO public.entities (canonical_name) VALUES ($1) RETURNING id", name
    )
    contact_id = await pool.fetchval(
        "INSERT INTO public.contacts (name, entity_id) VALUES ($1, $2) RETURNING id",
        name,
        entity_id,
    )
    return entity_id, contact_id


async def _add_ci(pool, contact_id, ci_type: str, value: str, *, secured: bool = False):
    await pool.execute(
        "INSERT INTO public.contact_info (contact_id, type, value, secured) "
        "VALUES ($1, $2, $3, $4)",
        contact_id,
        ci_type,
        value,
        secured,
    )


async def _add_fact(pool, subject, predicate: str, obj: str):
    await pool.execute(
        "INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src) "
        "VALUES ($1, $2, $3, 'literal', 'test')",
        subject,
        predicate,
        obj,
    )


async def _parity_gap(pool, accepted: list[str]) -> dict[str, int]:
    mod = _load_migration()
    sql = mod._PARITY_SWEEP_SQL.text.replace(":accepted_unmapped", "$1::text[]")
    rows = await pool.fetch(sql, accepted)
    return {r["ci_type"]: r["n"] for r in rows}


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_parity_guard_logic(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)

        e1, c1 = await _mk_entity_contact(pool, "drop-parity-alice")

        # 1. Email with NO triple → counted as a gap.
        await _add_ci(pool, c1, "email", "alice@example.com")
        # 2. Secured row → never counted (carved out to public.entity_info).
        await _add_ci(pool, c1, "email", "secret@example.com", secured=True)
        # 3. Owner-accepted unmapped type → not counted when accepted.
        await _add_ci(pool, c1, "google_health", "gh-token-xyz")
        # 4. Telegram with a properly-prefixed active triple → not counted.
        await _add_ci(pool, c1, "telegram_user_id", "123456")
        await _add_fact(pool, e1, "has-handle", "telegram:123456")

        gap = await _parity_gap(pool, ["google_health"])
        assert gap.get("email", 0) == 1, f"uncovered email must be a gap; got {gap}"
        assert "google_health" not in gap, f"accepted unmapped type must be excluded; got {gap}"
        assert "telegram_user_id" not in gap, f"prefixed telegram triple covers row; got {gap}"

        # Backfill the email triple → gap clears entirely.
        await _add_fact(pool, e1, "has-email", "alice@example.com")
        gap_after = await _parity_gap(pool, ["google_health"])
        assert gap_after.get("email", 0) == 0, f"backfilled email must clear gap; got {gap_after}"

        # If google_health is NOT accepted, it surfaces as a gap (conservative).
        gap_strict = await _parity_gap(pool, [])
        assert gap_strict.get("google_health", 0) == 1, f"strict run must flag gh; got {gap_strict}"
