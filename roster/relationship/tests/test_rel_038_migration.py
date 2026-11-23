"""rel_038: ``relationship.address_geocodes`` (bu-r3fmja.3).

Openspec change ``chronicler-situational-inference``.

The table is the fixed contract Chronicler's anchor resolver reads: one geocode per
``has-address`` fact, keyed to the SHA-256 of the address text, with coordinates
present exactly when resolved. Only ``butler_chronicler_rw`` is granted read access
beyond Relationship itself.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import asyncpg
import pytest

from butlers.testing.migration import load_migration

_MIGRATION_PATH = Path(__file__).resolve().parents[1] / "migrations" / "038_address_geocodes.py"

_docker = pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
_session_loop = pytest.mark.asyncio(loop_scope="session")


class _CollectingOp:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(str(statement))


def _statements(direction: str) -> list[str]:
    mod = load_migration(_MIGRATION_PATH)
    fake = _CollectingOp()
    mod.__dict__["op"] = fake  # stand in for alembic.op
    getattr(mod, direction)()
    return fake.statements


@pytest.mark.unit
def test_revision_grants_and_downgrade() -> None:
    mod = load_migration(_MIGRATION_PATH)
    assert (mod.revision, mod.down_revision) == ("rel_038", "rel_037")
    assert mod.branch_labels is None and mod.depends_on is None

    # Chronicler gets SELECT only, and only when its role exists.
    sql = "\n".join(_statements("upgrade"))
    assert "rolname = 'butler_chronicler_rw'" in sql
    assert re.findall(r'GRANT [^;]*? TO "butler_chronicler_rw"', sql) == [
        'GRANT SELECT ON TABLE relationship.address_geocodes TO "butler_chronicler_rw"'
    ]
    assert "ALL TABLES" not in sql

    assert any(
        "DROP TABLE IF EXISTS relationship.address_geocodes" in s for s in _statements("downgrade")
    )


@pytest.fixture
async def rel(db):
    p = await db(
        "core",
        "memory",
        "relationship",
        schemas={"relationship": "relationship"},
        schema="relationship",
    )
    subject = await p.fetchval(
        "INSERT INTO public.entities (canonical_name) VALUES ('Owner') RETURNING id"
    )
    fact_id = await p.fetchval(
        """
        INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src)
        VALUES ($1, 'has-address', '1 Example Avenue, Singapore 123456', 'literal', 'test')
        RETURNING id
        """,
        subject,
    )
    return p, fact_id


_INSERT = """
    INSERT INTO relationship.address_geocodes
        (fact_id, address_sha256, status, lat, lon, provider, confidence)
    VALUES ($1, 'abc', $2, $3, $4, $5, $6)
"""


@pytest.mark.integration
@_docker
@_session_loop
class TestAddressGeocodesTable:
    async def test_resolved_rows_need_coordinates_and_others_must_not_carry_them(self, rel):
        rel_pool, fact_id = rel
        bad_rows = [
            ("resolved", None, None, "onemap", 0.9),
            ("resolved", 1.3, None, "onemap", 0.9),
            ("not_found", 1.3, 103.8, "nominatim", None),
            ("failed", None, 103.8, "onemap", None),
            ("resolved", 1.3, 103.8, "google", 0.9),
            ("pending", None, None, None, None),
        ]
        for row in bad_rows:
            with pytest.raises(asyncpg.CheckViolationError):
                await rel_pool.execute(_INSERT, fact_id, *row)

        await rel_pool.execute(_INSERT, fact_id, "not_found", None, None, None, None)
        stored = await rel_pool.fetchrow(
            "SELECT attempts, geocoded_at, next_attempt_at FROM relationship.address_geocodes"
        )
        assert stored["attempts"] == 0
        assert stored["geocoded_at"] is not None
        assert stored["next_attempt_at"] is None

    async def test_coordinates_are_stored_at_five_decimals(self, rel):
        rel_pool, fact_id = rel
        await rel_pool.execute(
            _INSERT, fact_id, "resolved", 1.300004999, -179.123456789, "onemap", 0.9
        )
        row = await rel_pool.fetchrow(
            "SELECT lat::text, lon::text FROM relationship.address_geocodes"
        )
        assert (row["lat"], row["lon"]) == ("1.30000", "-179.12346")

    async def test_row_follows_its_fact_on_delete(self, rel):
        rel_pool, fact_id = rel
        await rel_pool.execute(_INSERT, fact_id, "failed", None, None, "onemap", None)
        await rel_pool.execute("DELETE FROM relationship.entity_facts WHERE id = $1", fact_id)
        assert await rel_pool.fetchval("SELECT count(*) FROM relationship.address_geocodes") == 0

    async def test_only_relationship_and_chronicler_can_read(self, rel):
        rel_pool, fact_id = rel
        roles = [
            r["rolname"]
            for r in await rel_pool.fetch(
                "SELECT rolname FROM pg_roles WHERE rolname LIKE 'butler\\_%\\_rw'"
            )
        ]
        assert "butler_chronicler_rw" in roles and "butler_relationship_rw" in roles

        def can(role: str, privilege: str):
            return rel_pool.fetchval(
                "SELECT has_table_privilege($1, 'relationship.address_geocodes', $2)",
                role,
                privilege,
            )

        assert await can("butler_chronicler_rw", "SELECT")
        for privilege in ("INSERT", "UPDATE", "DELETE"):
            assert not await can("butler_chronicler_rw", privilege)
            assert await can("butler_relationship_rw", privilege)
        assert await can("butler_relationship_rw", "SELECT")
        for role in roles:
            if role not in {"butler_chronicler_rw", "butler_relationship_rw"}:
                assert not await can(role, "SELECT"), role

    async def test_downgrade_and_reupgrade(self, rel):
        rel_pool, fact_id = rel
        async with rel_pool.acquire() as conn:
            for statement in _statements("downgrade"):
                await conn.execute(statement)
            assert (
                await conn.fetchval("SELECT to_regclass('relationship.address_geocodes')") is None
            )
            for statement in _statements("upgrade"):
                await conn.execute(statement)
            assert await conn.fetchval("SELECT to_regclass('relationship.address_geocodes')")
