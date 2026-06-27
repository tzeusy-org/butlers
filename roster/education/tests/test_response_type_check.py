"""Regression tests for the ``education.quiz_responses.response_type`` CHECK constraint.

Schema/code-drift guard (bead bu-offxh): ``_VALID_RESPONSE_TYPES`` in
``roster/education/tools/mastery.py`` once included ``'quiz'``, but the
``quiz_responses.response_type`` CHECK constraint
(roster/education/migrations/001_education_tables.py) only permits
``diagnostic``/``teach``/``review``.  A response classified ``'quiz'`` would pass
the Python validation in ``mastery_record_response`` and then fail the DB INSERT.

These tests run the real ``core`` + ``education`` Alembic chains against a
testcontainers PostgreSQL database (no hand-rolled CREATE TABLE) and assert:

1. Every value in ``_VALID_RESPONSE_TYPES`` inserts cleanly via
   ``mastery_record_response`` (no type passes Python validation but fails the
   DB CHECK).
2. The Python allow-set and the DB CHECK allow-set are exactly equal — a parity
   guard that fails if either side drifts again.
"""

from __future__ import annotations

import shutil

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from butlers.tools.education.mastery import _VALID_RESPONSE_TYPES, mastery_record_response

_docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not _docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision a DB with core + education migrations applied once per module."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "education"],
    )


@pytest.fixture(scope="module")
async def pool(migrated_db_url: str):
    """Return a module-scoped asyncpg pool (event loop is session-scoped here)."""
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    try:
        yield p
    finally:
        await p.close()


@pytest.fixture(autouse=True)
async def _clear_tables(pool: asyncpg.Pool):
    """Truncate education quiz tables before each test (pool is module-scoped)."""
    await pool.execute(
        "TRUNCATE TABLE education.quiz_responses, education.mind_map_edges, "
        "education.mind_map_nodes, education.mind_maps CASCADE"
    )


async def _seed_map_and_node(pool: asyncpg.Pool) -> tuple[str, str]:
    """Create one mind map and one node; return (mind_map_id, node_id)."""
    mind_map_id = await pool.fetchval(
        "INSERT INTO education.mind_maps (title) VALUES ($1) RETURNING id",
        "Python",
    )
    node_id = await pool.fetchval(
        "INSERT INTO education.mind_map_nodes (mind_map_id, label) VALUES ($1, $2) RETURNING id",
        mind_map_id,
        "list comprehensions",
    )
    return str(mind_map_id), str(node_id)


async def _db_response_type_check_values(pool: asyncpg.Pool) -> set[str]:
    """Return the set of response_type values permitted by the DB CHECK constraint.

    Parses ``pg_get_constraintdef`` for the CHECK on ``quiz_responses.response_type``
    (e.g. ``CHECK ((response_type = ANY (ARRAY['diagnostic'::text, ...])))``).
    """
    defs = await pool.fetch(
        """
        SELECT pg_get_constraintdef(c.oid) AS def
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = 'education'
          AND t.relname = 'quiz_responses'
          AND c.contype = 'c'
        """
    )
    for row in defs:
        text = row["def"]
        if "response_type" in text:
            # Extract every single-quoted literal in the constraint definition.
            return set(part.split("'", 1)[0] for part in text.split("'")[1::2])
    raise AssertionError("No CHECK constraint on response_type found")


class TestResponseTypeValidValuesInsert:
    """Every Python-valid response_type must satisfy the DB CHECK and insert cleanly."""

    @pytest.mark.parametrize("response_type", sorted(_VALID_RESPONSE_TYPES))
    async def test_valid_response_type_inserts_cleanly(self, pool, response_type):
        mind_map_id, node_id = await _seed_map_and_node(pool)

        response_id = await mastery_record_response(
            pool=pool,
            node_id=node_id,
            mind_map_id=mind_map_id,
            question_text="What is a list comprehension?",
            user_answer="A concise way to build a list from an iterable.",
            quality=4,
            response_type=response_type,
        )

        stored = await pool.fetchval(
            "SELECT response_type FROM education.quiz_responses WHERE id = $1::uuid",
            response_id,
        )
        assert stored == response_type


class TestResponseTypeParity:
    """The Python allow-set and the DB CHECK allow-set must be exactly equal."""

    async def test_python_and_db_allow_sets_match(self, pool):
        db_values = await _db_response_type_check_values(pool)
        # 'quiz' is vestigial — the DB CHECK never permitted it (bu-offxh).
        assert "quiz" not in db_values
        assert db_values == _VALID_RESPONSE_TYPES, (
            "response_type allow-sets drifted: "
            f"Python={_VALID_RESPONSE_TYPES}, DB CHECK={db_values}. "
            "A value valid in Python but not the DB would pass validation then fail INSERT."
        )
