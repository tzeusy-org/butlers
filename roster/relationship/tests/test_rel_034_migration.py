"""rel_034 executes: the DDL and the in-flight approval repair (bu-6jv4m.9).

``roster/relationship/tests/evidence_schema.py`` hand-rolls the same objects for
the writer tests, so those tests prove the *writer* works against a schema that
happens to match the migration. They prove nothing about the migration itself:
its SQL is never parsed by anything else, and its repair block — which rewrites
``pending_actions.tool_args`` for approvals parked by the previous code path —
has no other coverage at all.

This runs ``upgrade()`` for real by substituting an ``op`` that forwards to a
live connection, then asserts the repair moved the parked provenance without
disturbing the owner-facing dossier.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "migrations" / "034_fact_evidence_and_coverage.py"
)

_docker = pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
_session_loop = pytest.mark.asyncio(loop_scope="session")


def _load_migration():
    spec = importlib.util.spec_from_file_location("_migration_rel_034", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.mark.unit
def test_revision_chain() -> None:
    mod = _load_migration()
    assert mod.revision == "rel_034"
    assert mod.down_revision == "rel_033"


@pytest.mark.unit
def test_evidence_text_bound_matches_the_writer() -> None:
    """The CHECK is the backstop for the writer's bound; a drift makes it a lie."""
    from butlers.tools.relationship import fact_evidence

    assert _load_migration()._MAX_TEXT_CHARS == fact_evidence._MAX_TEXT_CHARS


class _CollectingOp:
    """Minimal ``alembic.op`` stand-in that records statements in order."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(str(statement))


def _upgrade_statements() -> list[str]:
    mod = _load_migration()
    fake = _CollectingOp()
    mod.op = fake
    mod.upgrade()
    return fake.statements


def _downgrade_statements() -> list[str]:
    mod = _load_migration()
    fake = _CollectingOp()
    mod.op = fake
    mod.downgrade()
    return fake.statements


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """The pre-rel_034 subset of the relationship schema this migration edits.

    ``pending_actions`` is created schema-qualified on purpose: the approvals
    module creates it unqualified under the butler's ``search_path``, so in
    production it lives in ``relationship``, which is where the repair block
    looks for it.
    """
    async with provisioned_postgres_pool(schema="relationship") as p:
        await p.execute("CREATE SCHEMA IF NOT EXISTS relationship")
        await p.execute("""
            CREATE TABLE IF NOT EXISTS public.entities (
                id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                canonical_name TEXT NOT NULL,
                entity_type    TEXT NOT NULL DEFAULT 'person',
                metadata       JSONB NOT NULL DEFAULT '{}'::jsonb
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS relationship.entity_facts (
                id          UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
                subject     UUID NOT NULL REFERENCES public.entities(id) ON DELETE CASCADE,
                predicate   TEXT NOT NULL,
                object      TEXT NOT NULL,
                object_kind TEXT NOT NULL,
                src         TEXT NOT NULL,
                conf        FLOAT NOT NULL DEFAULT 1.0,
                observed_at TIMESTAMPTZ,
                validity    TEXT NOT NULL DEFAULT 'active',
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS relationship.pending_actions (
                id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tool_name   TEXT NOT NULL,
                tool_args   JSONB NOT NULL,
                status      VARCHAR NOT NULL DEFAULT 'pending',
                why         TEXT,
                evidence    JSONB NOT NULL DEFAULT '[]'::jsonb
            )
        """)
        await p.execute("INSERT INTO public.entities (canonical_name) VALUES ('Owner')")
        yield p


@_docker
@_session_loop
class TestMigrationRunsAgainstPostgres:
    async def test_upgrade_and_downgrade_execute(self, pool):
        async with pool.acquire() as conn:
            for statement in _upgrade_statements():
                await conn.execute(statement)

            fact_id = await conn.fetchval(
                """
                INSERT INTO relationship.entity_facts
                    (subject, predicate, object, object_kind, src, assert_origin)
                SELECT id, 'has-email', 'a@b.test', 'literal', 'test', 'direct'
                FROM public.entities LIMIT 1
                RETURNING id
                """
            )
            await conn.execute(
                """
                INSERT INTO relationship.fact_evidence
                    (fact_id, seq, kind, ref, note, src, origin)
                VALUES ($1, 1, 'url', 'https://example.test/a', 'note', 'test', 'direct')
                """,
                fact_id,
            )
            # The append-only trigger is the whole point of the ledger.
            with pytest.raises(asyncpg.PostgresError):
                await conn.execute(
                    "UPDATE relationship.fact_evidence SET note = 'rewritten' WHERE fact_id = $1",
                    fact_id,
                )

            for statement in _downgrade_statements():
                await conn.execute(statement)
            assert await conn.fetchval("SELECT to_regclass('relationship.fact_evidence')") is None
            assert (
                await conn.fetchval("SELECT to_regclass('relationship.fact_approval_context')")
                is None
            )

    async def test_repair_moves_parked_provenance_out_of_tool_args(self, pool):
        """A pre-rel_034 parked approval becomes dispatchable without losing its source."""
        action_id = uuid.uuid4()
        observed = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
        subject = await pool.fetchval("SELECT id FROM public.entities LIMIT 1")
        stale_args = {
            "subject": str(subject),
            "predicate": "has-email",
            "object": "owner@example.test",
            "object_kind": "literal",
            "src": "gmail",
            "conf": 1.0,
            "verified": False,
            "observed_at": observed.isoformat(),
        }
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO relationship.pending_actions
                    (id, tool_name, tool_args, status, why, evidence)
                VALUES ($1, 'relationship_assert_fact', $2, 'pending', 'because', $3)
                """,
                action_id,
                # Bind the dicts directly: this pool registers a jsonb codec, so
                # pre-serializing would store a jsonb *string* scalar, not an object.
                stale_args,
                [{"type": "text", "ref": "r", "note": "n"}],
            )
            for statement in _upgrade_statements():
                await conn.execute(statement)

            row = await conn.fetchrow(
                "SELECT tool_args, why, evidence FROM relationship.pending_actions WHERE id = $1",
                action_id,
            )
            args = row["tool_args"]
            args = json.loads(args) if isinstance(args, str) else args
            assert "src" not in args
            assert "observed_at" not in args
            # Replay-critical: without this the approved write re-parks forever.
            assert args["approval_action_id"] == str(action_id)
            # The identity quadruple the resolver matches on is untouched.
            assert args["subject"] == str(subject)
            assert args["predicate"] == "has-email"
            assert args["object"] == "owner@example.test"
            # The owner-facing dossier is not collateral damage.
            assert row["why"] == "because"

            ctx = await conn.fetchrow(
                "SELECT src, observed_at FROM relationship.fact_approval_context "
                "WHERE action_id = $1",
                action_id,
            )
            assert ctx["src"] == "gmail"
            assert ctx["observed_at"] == observed
