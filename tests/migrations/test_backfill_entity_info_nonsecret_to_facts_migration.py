"""Tests for rel_028 — backfill non-secret public.entity_info into entity_facts.

Covers:
1. Migration file structure and revision chain (unit — no DB required).
2. SQL builder shape: predicate mapping, telegram prefix, ON CONFLICT idempotency,
   technical-config carve-out (unit — no DB).
3. upgrade() guard logic: raises on unexpected non-secret type / parity gap
   (unit — fake bind).
4. Integration (Docker): projection produces correct triples, preserves primary,
   includes the owner, excludes secured + technical-config rows, parity holds,
   and re-running is a no-op.

Issue: bu-oluyt.2
Parent epic: bu-oluyt — retire the contact schema (one graph for identity).
"""

from __future__ import annotations

import importlib.util
import shutil
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "roster"
    / "relationship"
    / "migrations"
    / "028_backfill_entity_info_nonsecret_to_facts.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("rel_028", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Unit tests — no DB required
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMigrationStructure:
    def test_revision_chain(self):
        mod = _load_migration()
        assert mod.revision == "rel_028"
        assert mod.down_revision == "rel_027"
        assert mod.branch_labels is None
        assert mod.depends_on is None

    def test_file_ordered_after_026(self):
        files = sorted(f.name for f in _MIGRATION_PATH.parent.glob("[0-9]*.py"))
        idx_026 = next((i for i, f in enumerate(files) if f.startswith("026_")), None)
        idx_027 = next((i for i, f in enumerate(files) if f.startswith("027_")), None)
        assert idx_026 is not None and idx_027 is not None
        assert idx_027 > idx_026


@pytest.mark.unit
class TestSqlShape:
    def test_technical_config_types_not_projected(self):
        mod = _load_migration()
        for t in mod._TECHNICAL_CONFIG_TYPES:
            assert t not in mod._TYPE_TO_PREDICATE, (
                f"technical-config type {t!r} must not be in the projection map"
            )

    def test_projection_predicate_mapping(self):
        mod = _load_migration()
        m = mod._TYPE_TO_PREDICATE
        assert m["email"] == "has-email"
        assert m["phone"] == "has-phone"
        assert m["whatsapp_phone"] == "has-phone"
        assert m["website"] == "has-website"
        assert m["telegram"] == "has-handle"
        assert m["telegram_chat_id"] == "has-handle"

    def test_projection_insert_is_idempotent(self):
        sql = _load_migration().projection_insert_sql()
        assert "ON CONFLICT (subject, predicate, object) WHERE validity = 'active'" in sql
        assert "DO NOTHING" in sql

    def test_projection_applies_telegram_prefix(self):
        sql = _load_migration().projection_insert_sql()
        assert "'telegram:' ||" in sql
        assert "regexp_replace(ei.value, '^telegram:', '')" in sql

    def test_projection_only_reads_non_secret_rows(self):
        sql = _load_migration().projection_insert_sql()
        assert "ei.secured = false" in sql
        assert "ei.entity_id IS NOT NULL" in sql

    def test_downgrade_is_noop_does_not_touch_db(self):
        mod = _load_migration()
        executed: list[str] = []
        fake_op = MagicMock()
        fake_op.execute.side_effect = lambda sql: executed.append(str(sql))
        fake_op.get_bind.side_effect = AssertionError("downgrade must not touch the DB")
        with patch.object(mod, "op", fake_op):
            mod.downgrade()
        assert executed == []


@pytest.mark.unit
class TestUpgradeGuards:
    """upgrade() raises on unexpected types / parity gaps (fake-bind unit)."""

    def _fake_op(self, *, unexpected, missing):
        """Build a fake op whose get_bind().execute().scalar() returns queued values."""
        scalars = iter([True, unexpected, missing])  # _tables_present, guard1, parity
        bind = MagicMock()
        bind.execute.return_value.scalar.side_effect = lambda: next(scalars)
        fake_op = MagicMock()
        fake_op.get_bind.return_value = bind
        return fake_op

    def test_raises_on_unexpected_type(self):
        mod = _load_migration()
        fake_op = self._fake_op(unexpected=["myspace"], missing=0)
        with patch.object(mod, "op", fake_op):
            with pytest.raises(RuntimeError, match="unmapped type"):
                mod.upgrade()

    def test_raises_on_parity_gap(self):
        mod = _load_migration()
        fake_op = self._fake_op(unexpected=None, missing=3)
        with patch.object(mod, "op", fake_op):
            with pytest.raises(RuntimeError, match="parity FAILED"):
                mod.upgrade()

    def test_clean_run_does_not_raise(self):
        mod = _load_migration()
        fake_op = self._fake_op(unexpected=None, missing=0)
        with patch.object(mod, "op", fake_op):
            mod.upgrade()  # must not raise


# ---------------------------------------------------------------------------
# Integration tests — require Docker + Postgres
# ---------------------------------------------------------------------------


async def _provision_schema(pool) -> None:
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS public.entities (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name        TEXT NOT NULL DEFAULT '',
            entity_type TEXT NOT NULL DEFAULT 'person',
            roles       TEXT[] NOT NULL DEFAULT '{}',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS public.entity_info (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            entity_id   UUID NOT NULL REFERENCES public.entities(id) ON DELETE CASCADE,
            type        VARCHAR NOT NULL,
            value       TEXT NOT NULL,
            label       VARCHAR,
            is_primary  BOOLEAN DEFAULT false,
            secured     BOOLEAN NOT NULL DEFAULT false,
            created_at  TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT uq_entity_info_entity_type UNIQUE (entity_id, type)
        )
    """)
    await pool.execute("CREATE SCHEMA IF NOT EXISTS relationship")
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS relationship.entity_facts (
            id          UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            subject     UUID NOT NULL REFERENCES public.entities(id) ON DELETE CASCADE,
            predicate   TEXT NOT NULL,
            object      TEXT NOT NULL,
            object_kind TEXT NOT NULL CHECK (object_kind IN ('literal', 'entity')),
            src         TEXT NOT NULL,
            conf        FLOAT NOT NULL DEFAULT 1.0 CHECK (conf >= 0.0 AND conf <= 1.0),
            last_seen   TIMESTAMPTZ,
            weight      INT,
            verified    BOOL NOT NULL DEFAULT false,
            "primary"   BOOL,
            validity    TEXT NOT NULL DEFAULT 'active'
                            CHECK (validity IN ('active', 'retracted', 'superseded')),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    await pool.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_ef_spo_active
            ON relationship.entity_facts (subject, predicate, object)
            WHERE validity = 'active'
    """)


async def _new_entity(pool, *, owner: bool = False) -> uuid.UUID:
    eid = uuid.uuid4()
    roles = ["owner"] if owner else []
    await pool.execute(
        "INSERT INTO public.entities (id, name, roles) VALUES ($1, $2, $3)",
        eid,
        "owner" if owner else "person",
        roles,
    )
    return eid


async def _add_info(pool, eid, type_, value, *, secured=False, is_primary=False) -> None:
    await pool.execute(
        "INSERT INTO public.entity_info (entity_id, type, value, secured, is_primary) "
        "VALUES ($1, $2, $3, $4, $5)",
        eid,
        type_,
        value,
        secured,
        is_primary,
    )


pytestmark_integration = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_projection_produces_correct_triples(provisioned_postgres_pool) -> None:
    mod = _load_migration()
    async with provisioned_postgres_pool() as pool:
        await _provision_schema(pool)

        owner = await _new_entity(pool, owner=True)
        person = await _new_entity(pool)

        await _add_info(pool, owner, "telegram_chat_id", "12345", is_primary=True)
        await _add_info(pool, person, "email", "a@b.com", is_primary=True)
        await _add_info(pool, person, "phone", "+15551230000")
        await _add_info(pool, person, "whatsapp_phone", "+19998887777")
        await _add_info(pool, person, "website", "https://example.com")
        await _add_info(pool, person, "telegram", "@handle")
        # secured credential — must NOT be projected
        await _add_info(pool, person, "telegram_bot_token", "secret-token", secured=True)
        # technical config — must NOT be projected
        await _add_info(pool, person, "home_assistant_url", "http://ha.local")

        await pool.execute(mod.projection_insert_sql())

        # Owner Telegram chat id → prefixed has-handle, primary preserved.
        row = await pool.fetchrow(
            'SELECT object, "primary" FROM relationship.entity_facts '
            "WHERE subject = $1 AND predicate = 'has-handle' AND validity = 'active'",
            owner,
        )
        assert row is not None
        assert row["object"] == "telegram:12345"
        assert row["primary"] is True

        # Channel facts for the regular person.
        async def _obj(pred):
            return await pool.fetchval(
                "SELECT object FROM relationship.entity_facts "
                "WHERE subject = $1 AND predicate = $2 AND validity = 'active'",
                person,
                pred,
            )

        assert await _obj("has-email") == "a@b.com"
        assert await _obj("has-website") == "https://example.com"
        # telegram '@handle' → 'telegram:handle' (leading @ stripped, prefix added)
        assert (
            await pool.fetchval(
                "SELECT object FROM relationship.entity_facts "
                "WHERE subject = $1 AND predicate = 'has-handle' AND validity = 'active'",
                person,
            )
            == "telegram:handle"
        )
        # phone + whatsapp_phone both map to has-phone.
        phones = await pool.fetch(
            "SELECT object FROM relationship.entity_facts "
            "WHERE subject = $1 AND predicate = 'has-phone' AND validity = 'active' "
            "ORDER BY object",
            person,
        )
        assert {r["object"] for r in phones} == {"+15551230000", "+19998887777"}

        # secured + technical-config rows produced no triple at all.
        bot = await pool.fetchval(
            "SELECT count(*) FROM relationship.entity_facts WHERE object = 'secret-token'"
        )
        assert bot == 0
        ha = await pool.fetchval(
            "SELECT count(*) FROM relationship.entity_facts WHERE object = 'http://ha.local'"
        )
        assert ha == 0


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_parity_and_idempotency(provisioned_postgres_pool) -> None:
    mod = _load_migration()
    async with provisioned_postgres_pool() as pool:
        await _provision_schema(pool)
        person = await _new_entity(pool)
        await _add_info(pool, person, "email", "p@q.com")
        await _add_info(pool, person, "telegram_chat_id", "987")

        await pool.execute(mod.projection_insert_sql())

        # Parity: zero projectable rows lacking a triple.
        assert await pool.fetchval(mod.parity_missing_sql()) == 0

        count_after_first = await pool.fetchval(
            "SELECT count(*) FROM relationship.entity_facts WHERE validity = 'active'"
        )

        # Re-run: idempotent no-op (ON CONFLICT DO NOTHING).
        await pool.execute(mod.projection_insert_sql())
        count_after_second = await pool.fetchval(
            "SELECT count(*) FROM relationship.entity_facts WHERE validity = 'active'"
        )
        assert count_after_first == count_after_second == 2
        assert await pool.fetchval(mod.parity_missing_sql()) == 0


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_unexpected_type_surfaced_by_guard(provisioned_postgres_pool) -> None:
    mod = _load_migration()
    async with provisioned_postgres_pool() as pool:
        await _provision_schema(pool)
        person = await _new_entity(pool)
        await _add_info(pool, person, "myspace", "tom")  # non-secret, unmapped

        surfaced = await pool.fetchval(mod.unexpected_types_sql())
        assert surfaced is not None and "myspace" in surfaced


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_owner_seed_subsumed_no_duplicate(provisioned_postgres_pool) -> None:
    """A pre-existing owner-bootstrap triple is not duplicated by the backfill."""
    mod = _load_migration()
    async with provisioned_postgres_pool() as pool:
        await _provision_schema(pool)
        owner = await _new_entity(pool, owner=True)
        await _add_info(pool, owner, "telegram_chat_id", "555")
        # Simulate the PR #2465 owner-bootstrap seed already present.
        await pool.execute(
            "INSERT INTO relationship.entity_facts "
            '(subject, predicate, object, object_kind, src, "primary", verified, validity) '
            "VALUES ($1, 'has-handle', 'telegram:555', 'literal', 'owner-bootstrap', true, true, 'active')",
            owner,
        )

        await pool.execute(mod.projection_insert_sql())

        rows = await pool.fetch(
            "SELECT src, verified FROM relationship.entity_facts "
            "WHERE subject = $1 AND predicate = 'has-handle' AND object = 'telegram:555' "
            "AND validity = 'active'",
            owner,
        )
        assert len(rows) == 1  # converged to one active row
        assert rows[0]["src"] == "owner-bootstrap"  # original seed preserved
        assert rows[0]["verified"] is True
