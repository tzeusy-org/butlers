"""rel_035 executes: the effective-time expand stage (bu-h3b7t.1).

The writer tests run against the complete real head chains. They prove the writer;
this file proves the migration as a schema transition on real PostgreSQL:

* it is additive, nullable and backfill-free, and re-running it is a no-op;
* it RETAINS ``uq_ef_spo_active``, so the deployed writer's exact inferred
  ``ON CONFLICT`` statement still prepares and writes, and a second active
  occurrence stays impossible during the compatibility stage;
* a pre-cutover downgrade keeps every fact, rel_034 evidence, coverage receipt
  and approval-context row, and the stage can be re-applied;
* after a (simulated, never shipped) cutover the old statement fails outright --
  which is why proving the old writer absent is a hard cutover prerequisite --
  while the transition writer's targetless insert keeps working, and the
  downgrade refuses;
* the named CHECK constraints accept every unknown / unbounded / partial /
  coarse bound state and reject every second encoding of one.
"""

from __future__ import annotations

import hashlib
import importlib.util
import shutil
import uuid
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import asyncpg
import pytest

from butlers.testing.migrated_templates import MigrationStage
from butlers.testing.migration import migrated_pool
from butlers.tools.relationship import fact_temporal
from butlers.tools.relationship.fact_evidence import EvidencePacket
from roster.relationship.tests.evidence_schema import rel_035, simulate_temporal_cutover

_docker = pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
_session_loop = pytest.mark.asyncio(loop_scope="session")

#: The deployed (pre-transition) writer's insert, verbatim. Its inferred
#: conflict target names the legacy index -- the reason that index must
#: survive the expand stage.
_OLD_WRITER_INSERT = """
    INSERT INTO relationship.entity_facts (
        id, subject, predicate, object, object_kind,
        src, conf, last_seen, observed_at, weight, verified, "primary",
        validity, created_at, updated_at,
        assert_origin, assert_session_id, assert_action_id
    )
    VALUES (
        gen_random_uuid(), $1, $2, $3, $4,
        $5, $6, $7, $8, $9, $10, $11,
        'active', now(), now(),
        $12, $13, $14
    )
    ON CONFLICT (subject, predicate, object) WHERE validity = 'active'
    DO NOTHING
    RETURNING id
"""


class _CollectingOp:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(str(statement))


def _statements(direction: str, mod=None) -> list[str]:
    mod = mod or rel_035()
    fake = _CollectingOp()
    original, mod.op = mod.op, fake
    try:
        getattr(mod, direction)()
    finally:
        mod.op = original
    return fake.statements


def _rel_034():
    path = rel_035().__file__.replace(
        "035_entity_fact_effective_time_expand", "034_fact_evidence_and_coverage"
    )
    spec = importlib.util.spec_from_file_location("_migration_rel_034_for_035", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.unit
def test_revision_chain_and_writer_constants_agree() -> None:
    """The writer's sentinel, index name and vocabulary are the migration's."""
    mod = rel_035()
    assert (mod.revision, mod.down_revision) == ("rel_035", "rel_034")
    assert mod.down_revision == _rel_034().revision
    assert uuid.UUID(mod.DEFAULT_OCCURRENCE_SENTINEL) == fact_temporal.DEFAULT_OCCURRENCE_SENTINEL
    assert f"relationship.{mod.LEGACY_SPO_INDEX}" == fact_temporal.LEGACY_SPO_INDEX
    assert set(mod.CONCRETE_PRECISIONS) == fact_temporal.CONCRETE_PRECISIONS
    assert mod.UNBOUNDED_PRECISION == fact_temporal.UNBOUNDED
    assert set(mod.TEMPORAL_REQUEST_MODES) == {m.value for m in fact_temporal.RequestMode}


@pytest.fixture
async def pool(postgres_container):
    """Real rel034 prefix; every test still executes the rel035 subject itself."""
    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("relationship", schema="relationship", revision="rel_034"),
        ),
        pool_schema="relationship",
    ) as p:
        yield p


async def _old_write(conn, subject, obj):
    return await conn.fetchval(
        _OLD_WRITER_INSERT,
        subject,
        "has-email",
        obj,
        "literal",
        "old-writer",
        1.0,
        None,
        datetime(2026, 1, 1, tzinfo=UTC),
        None,
        False,
        None,
        "direct",
        None,
        None,
    )


@lru_cache(maxsize=1)
def _protected_transition_insert():
    """Execute the exact protected461 transition SQL, outside current-source coverage.

    rel035 is a historical pre-authority transition. Its fixture deliberately
    lacks rel037 columns. This function's full body is copied from protected
    main461e03b, whole module SHAe79360614b4f43279afe070d3a0c501e669c9fd0d04027da00d67b52b4d12e6b.
    It proves that transition insert on the real old schema, not current
    report admission. Temporal/evidence data inputs retain their actual types.
    """
    path = Path(__file__).parent / "fixtures/protected_461_transition_insert.py.txt"
    source = path.read_bytes()
    if (
        hashlib.sha256(source).hexdigest()
        != "7f8c3d5fe907a780f8826843c6c6d15d9322f59135886295a2a635c67c8bc820"
    ):
        raise RuntimeError("protected transition writer fixture changed")
    namespace = {
        "PACKET_COLUMNS": fact_temporal.PACKET_COLUMNS,
        "UNKNOWN": fact_temporal.UNKNOWN,
    }
    exec(
        compile(
            "from __future__ import annotations\n" + source.decode(),
            "protected-fixture://461e03b/relationship-transition-insert",
            "exec",
        ),
        namespace,
    )
    return namespace["_insert_active_fact"]


async def _new_write(conn, subject, obj, temporal=fact_temporal.UNKNOWN):
    return await _protected_transition_insert()(
        conn,
        subject=subject,
        predicate="has-email",
        object=obj,
        object_kind="literal",
        src="transition-writer",
        conf=1.0,
        last_seen=None,
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        weight=None,
        verified=False,
        primary=None,
        packet=EvidencePacket(items=(), src="transition-writer", origin="direct"),
        temporal=temporal,
    )


async def _preserved_counts(conn) -> tuple[int, int, int, int]:
    return (
        await conn.fetchval("SELECT count(*) FROM relationship.entity_facts"),
        await conn.fetchval("SELECT count(*) FROM relationship.fact_evidence"),
        await conn.fetchval("SELECT count(*) FROM relationship.fact_coverage"),
        await conn.fetchval("SELECT count(*) FROM relationship.fact_approval_context"),
    )


async def _column_exists(conn, table: str, column: str) -> bool:
    return await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'relationship' AND table_name = $1 AND column_name = $2
        )
        """,
        table,
        column,
    )


@_docker
@_session_loop
async def test_expand_keeps_the_old_writer_and_reverses_without_losing_facts(pool) -> None:
    upgrade, downgrade = _statements("upgrade"), _statements("downgrade")
    async with pool.acquire() as conn:
        subject = await conn.fetchval("INSERT INTO public.entities DEFAULT VALUES RETURNING id")
        legacy_id = await _old_write(conn, subject, "legacy@example.test")
        await conn.execute(
            """
            INSERT INTO relationship.fact_evidence (fact_id, seq, kind, ref, note, src, origin)
            VALUES ($1, 1, 'url', 'https://example.test/a', '', 'old-writer', 'direct')
            """,
            legacy_id,
        )
        await conn.execute(
            """
            INSERT INTO relationship.fact_coverage (subject, predicate, src, outcome, observed_at)
            VALUES ($1, 'has-email', 'old-writer', 'present', now())
            """,
            subject,
        )
        await conn.execute(
            "INSERT INTO relationship.fact_approval_context (action_id, src) VALUES ($1, 'x')",
            uuid.uuid4(),
        )
        seeded = await _preserved_counts(conn)
        legacy_indexdef = await conn.fetchval(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_ef_spo_active'"
        )

        # A failed upgrade leaves nothing behind (transactional DDL).
        with pytest.raises(RuntimeError):
            async with conn.transaction():
                for statement in upgrade:
                    await conn.execute(statement)
                raise RuntimeError("simulated failure after the last statement")
        assert not await _column_exists(conn, "entity_facts", "effective_from")

        # Upgrade, twice: the second run is a no-op.
        for _ in range(2):
            for statement in upgrade:
                await conn.execute(statement)

        # No backfill: the legacy row is the default occurrence, unknown bounds.
        packet = await conn.fetchrow(
            f"SELECT {fact_temporal.PACKET_COLUMNS} FROM relationship.entity_facts WHERE id = $1",
            legacy_id,
        )
        assert fact_temporal.TemporalPacket.from_row(packet) == fact_temporal.UNKNOWN
        assert (
            await conn.fetchval(
                "SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_ef_spo_active'"
            )
            == legacy_indexdef
        )
        assert await conn.fetchval(
            "SELECT to_regclass('relationship.uq_ef_spo_occurrence_active') IS NOT NULL"
        )
        assert await _column_exists(conn, "fact_approval_context", "temporal_request_mode")

        # The deployed writer still prepares, writes, and dedups on its target.
        assert await _old_write(conn, subject, "old@example.test") is not None
        assert await _old_write(conn, subject, "old@example.test") is None
        # The transition writer's targetless insert works beside it.
        assert await _new_write(conn, subject, "new@example.test") is not None
        # The legacy index still forbids a second active occurrence.
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                """
                INSERT INTO relationship.entity_facts
                    (subject, predicate, object, object_kind, src, effective_period_id)
                VALUES ($1, 'has-email', 'legacy@example.test', 'literal', 't', $2)
                """,
                subject,
                uuid.uuid4(),
            )

        # Pre-cutover downgrade keeps every fact and rel_034 row; re-upgrade works.
        written = await _preserved_counts(conn)
        assert written == (seeded[0] + 2, *seeded[1:])
        for statement in downgrade:
            await conn.execute(statement)
        assert await _preserved_counts(conn) == written
        assert not await _column_exists(conn, "entity_facts", "effective_period_id")
        assert not await _column_exists(conn, "fact_approval_context", "temporal_base_fact_id")
        for statement in upgrade:
            await conn.execute(statement)

        # After a cutover the old statement cannot even plan, while the
        # transition writer admits a repeated occurrence, and rollback refuses.
        await simulate_temporal_cutover(conn)
        with pytest.raises(asyncpg.InvalidColumnReferenceError):
            await _old_write(conn, subject, "late@example.test")
        repeated = fact_temporal.TemporalPacket(period_id=uuid.uuid4())
        assert await _new_write(conn, subject, "legacy@example.test", repeated) is not None
        assert await _new_write(conn, subject, "legacy@example.test", repeated) is None
        with pytest.raises(asyncpg.RaiseError, match="downgrade refused"):
            async with conn.transaction():
                for statement in downgrade:
                    await conn.execute(statement)


_MONTH_START = datetime(2024, 3, 1, tzinfo=UTC)
_MID_MONTH = datetime(2024, 3, 15, tzinfo=UTC)
_LATER = datetime(2024, 6, 1, tzinfo=UTC)

#: (case, packet in PACKET_COLUMNS order, accepted by the CHECK constraints)
_BOUND_STATES = [
    ("unknown", (None, None, None, None, None), True),
    ("all-time", (None, None, "unbounded", None, "unbounded"), True),
    ("partial-lower", (None, _MONTH_START, "month", None, None), True),
    ("mixed-precision", (None, _MID_MONTH, "instant", _LATER, "month"), True),
    (
        "period-with-upper",
        (uuid.uuid4(), None, None, datetime(2025, 1, 1, tzinfo=UTC), "year"),
        True,
    ),
    ("misaligned-year", (None, None, None, _LATER, "year"), False),
    ("misaligned-month", (None, _MID_MONTH, "month", None, None), False),
    ("value-no-precision", (None, _MONTH_START, None, None, None), False),
    ("precision-no-value", (None, None, "day", None, None), False),
    ("unknown-token", (None, None, "unknown", None, None), False),
    ("unbounded-with-value", (None, _MONTH_START, "unbounded", None, None), False),
    ("empty-interval", (None, _MONTH_START, "instant", _MONTH_START, "instant"), False),
    ("reversed", (None, _LATER, "month", _MONTH_START, "month"), False),
    ("zero-period", (uuid.UUID(int=0), None, None, None, None), False),
]


@_docker
@_session_loop
async def test_checks_admit_each_bound_state_once(pool) -> None:
    """One schema, every case: each bound state has exactly one accepted encoding."""
    async with pool.acquire() as conn:
        for statement in _statements("upgrade"):
            await conn.execute(statement)
        subject = await conn.fetchval("INSERT INTO public.entities DEFAULT VALUES RETURNING id")
        outcomes = {}
        for case, packet, _ in _BOUND_STATES:
            try:
                async with conn.transaction():
                    await conn.execute(
                        f"""
                        INSERT INTO relationship.entity_facts
                            (subject, predicate, object, object_kind, src,
                             {fact_temporal.PACKET_COLUMNS})
                        VALUES ($1, 'has-email', $2, 'literal', 't', $3, $4, $5, $6, $7)
                        """,
                        subject,
                        case,
                        *packet,
                    )
                outcomes[case] = True
            except asyncpg.CheckViolationError:
                outcomes[case] = False
        assert outcomes == {case: accepted for case, _, accepted in _BOUND_STATES}
