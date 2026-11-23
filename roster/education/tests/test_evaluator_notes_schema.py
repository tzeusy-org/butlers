"""Regression tests for the ``education.quiz_responses.evaluator_notes`` column.

Schema-drift guard (bead bu-ulme0): both the ``mastery_record_response`` INSERT
(roster/education/tools/mastery.py) and the quiz-history SELECT
(roster/education/api/router.py) reference an ``evaluator_notes`` column that the
original 001 migration never created — producing a hard 500 on the core teaching
write path and the quiz-history endpoint alike.

These tests run the real ``core`` + ``education`` Alembic chains against a
testcontainers PostgreSQL database (no hand-written DDL) so they fail
before the 002 forward migration is added and pass once it is.  They exercise
both call sites end-to-end and assert that ``evaluator_notes`` round-trips.
"""

from __future__ import annotations

import shutil
import uuid

import asyncpg
import pytest

_docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not _docker_available, reason="Docker not available"),
]


@pytest.fixture
async def education_pool(db):
    """A private clone migrated with the core and education chains."""
    return await db("core", "education")


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


class TestEvaluatorNotesColumn:
    """The migration must create a nullable TEXT ``evaluator_notes`` column."""

    async def test_column_exists_and_is_nullable_text(self, education_pool):
        row = await education_pool.fetchrow(
            "SELECT data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'education' AND table_name = 'quiz_responses' "
            "  AND column_name = 'evaluator_notes'"
        )
        assert row is not None, "evaluator_notes column is missing from quiz_responses"
        assert row["data_type"] == "text"
        assert row["is_nullable"] == "YES"


class TestMasteryRecordResponse:
    """``mastery_record_response`` INSERT must persist evaluator_notes (no 500)."""

    async def test_record_response_round_trips_evaluator_notes(self, education_pool):
        from butlers.tools.education.mastery import mastery_record_response

        mind_map_id, node_id = await _seed_map_and_node(education_pool)

        response_id = await mastery_record_response(
            pool=education_pool,
            node_id=node_id,
            mind_map_id=mind_map_id,
            question_text="What is a list comprehension?",
            user_answer="A concise way to build a list from an iterable.",
            quality=5,
            response_type="teach",
            evaluator_notes="Clear and correct definition.",
        )

        stored = await education_pool.fetchval(
            "SELECT evaluator_notes FROM education.quiz_responses WHERE id = $1::uuid",
            response_id,
        )
        assert stored == "Clear and correct definition."

    async def test_record_response_allows_null_evaluator_notes(self, education_pool):
        from butlers.tools.education.mastery import mastery_record_response

        mind_map_id, node_id = await _seed_map_and_node(education_pool)

        response_id = await mastery_record_response(
            pool=education_pool,
            node_id=node_id,
            mind_map_id=mind_map_id,
            question_text="Define a tuple.",
            user_answer="An immutable ordered sequence.",
            quality=4,
            response_type="review",
        )

        stored = await education_pool.fetchval(
            "SELECT evaluator_notes FROM education.quiz_responses WHERE id = $1::uuid",
            response_id,
        )
        assert stored is None


class TestQuizHistorySelect:
    """The quiz-history SELECT must execute and return evaluator_notes (no 500)."""

    async def test_quiz_history_select_returns_evaluator_notes(self, education_pool):
        from butlers.tools.education.mastery import mastery_record_response

        mind_map_id, node_id = await _seed_map_and_node(education_pool)
        notes = f"eval-{uuid.uuid4().hex[:8]}"
        await mastery_record_response(
            pool=education_pool,
            node_id=node_id,
            mind_map_id=mind_map_id,
            question_text="Q?",
            user_answer="A.",
            quality=5,
            response_type="teach",
            evaluator_notes=notes,
        )

        # Mirror the exact SELECT used by GET /api/education/quiz-responses
        # (roster/education/api/router.py::list_quiz_responses).
        rows = await education_pool.fetch(
            "SELECT qr.id, qr.node_id, qr.mind_map_id, qr.question_text, qr.user_answer,"
            " qr.quality, qr.response_type, qr.session_id, qr.responded_at,"
            " qr.evaluator_notes, n.label AS node_label"
            " FROM education.quiz_responses qr"
            " LEFT JOIN education.mind_map_nodes n ON n.id = qr.node_id"
            " WHERE qr.node_id = $1::uuid"
            " ORDER BY qr.responded_at DESC"
            " OFFSET $2 LIMIT $3",
            node_id,
            0,
            20,
        )

        assert len(rows) == 1
        assert rows[0]["evaluator_notes"] == notes
        assert rows[0]["node_label"] == "list comprehensions"
