"""core_255: premise-bound proactive speech objects (bu-q7vx1q.5).

Real PostgreSQL: the new columns/vocabulary arrive without disturbing existing
rows, the enqueue definer is idempotent per (candidate, resolution), a second
schema's populated core run preserves every outcome and its provenance, and
bounded downgrades retain their documented folds without narrowing the shared
ledger CHECK.
"""

from __future__ import annotations

import asyncio
import re
import shutil

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from butlers.migrations import _build_alembic_config, run_migrations
from butlers.testing.migration import assert_at_chain_head, create_migration_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]


def _run(db_url: str, statement: str):
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            return conn.execute(text(statement)).scalar()
    finally:
        engine.dispose()


def _attention_ledger_snapshot(db_url: str) -> tuple[list[dict], list[tuple], list[tuple]]:
    """Read persisted provenance and catalog shape through a new connection."""
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            rows = (
                conn.execute(
                    text(
                        "SELECT to_jsonb(ledger) FROM public.attention_ledger AS ledger ORDER BY id"
                    )
                )
                .scalars()
                .all()
            )
            columns = conn.execute(
                text(
                    "SELECT column_name, data_type, is_nullable, column_default "
                    "FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'attention_ledger' "
                    "ORDER BY ordinal_position"
                )
            ).all()
            checks = conn.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid), convalidated "
                    "FROM pg_constraint WHERE conrelid = 'public.attention_ledger'::regclass "
                    "AND contype = 'c' ORDER BY conname"
                )
            ).all()
            return rows, [tuple(c) for c in columns], [tuple(c) for c in checks]
    finally:
        engine.dispose()


def _seed_attention_ledger(conn, outcomes: tuple[str, ...], provenance: str) -> None:
    """Plant the same full outcome/provenance witness for replay and rollback."""
    conn.execute(
        text(
            "INSERT INTO public.attention_ledger "
            "(origin_butler, source, outcome, reason, dedup_key, notification_ref, metadata) "
            "VALUES ('general', 'insight', :outcome, :reason, :dedup, :notification, "
            "jsonb_build_object('seed', CAST(:outcome AS text), 'provenance', CAST(:provenance AS text)))"
        ),
        [
            {
                "outcome": outcome,
                "reason": f"synthetic-reason:{outcome}",
                "dedup": f"synthetic-dedup:{outcome}",
                "notification": f"synthetic-notification:{outcome}",
                "provenance": provenance,
            }
            for outcome in outcomes
        ],
    )


def test_current_attention_outcomes_survive_populated_schema_replay(postgres_container) -> None:
    """A new core schema must preserve an already-current shared ledger."""
    outcomes = (
        "delivered",
        "coalesced",
        "deferred",
        "suppressed",
        "failed",
        "expired",
        "withdrawn",
        "amended",
    )
    db_url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(db_url, chain="core", schema="general"))
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            assert_at_chain_head(conn, schema="general")
            assert conn.execute(
                text(
                    "SELECT NOT rolsuper AND NOT rolcreaterole AND NOT rolcreatedb "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            ).scalar_one()
            _seed_attention_ledger(conn, outcomes, "synthetic-replay-sentinel")
            candidate = conn.execute(
                text(
                    "INSERT INTO public.insight_candidates "
                    "(origin_butler, priority, category, dedup_key, expires_at, message, status) "
                    "VALUES ('general', 50, 'synthetic', 'synthetic-replay-candidate', "
                    "now() + interval '1 day', 'Synthetic replay sentinel', 'withdrawn') RETURNING id"
                )
            ).scalar_one()
        before = _attention_ledger_snapshot(db_url)
        assert len(before[0]) == len(outcomes)
        assert {row["outcome"] for row in before[0]} == set(outcomes)

        # Baseline falsification: before the repair this genuine production
        # traversal reaches core_168's narrower CHECK with all eight rows present.
        # Do not catch that failure, stamp past it, or hand-build a newer table.
        asyncio.run(run_migrations(db_url, chain="core", schema="health"))
        for schema in ("general", "health", "health"):
            asyncio.run(run_migrations(db_url, chain="core", schema=schema))
            with engine.connect() as conn:
                assert_at_chain_head(conn, schema=schema)
            assert _attention_ledger_snapshot(db_url) == before
        with engine.begin() as conn:
            assert (
                conn.execute(
                    text("SELECT status FROM public.insight_candidates WHERE id = :id"),
                    {"id": candidate},
                ).scalar_one()
                == "withdrawn"
            )
            with pytest.raises(IntegrityError):
                with conn.begin_nested():
                    conn.execute(
                        text(
                            "INSERT INTO public.attention_ledger (origin_butler, source, outcome) "
                            "VALUES ('general', 'insight', 'unadopted-outcome')"
                        )
                    )
        assert _attention_ledger_snapshot(db_url) == before
    finally:
        engine.dispose()

    # A genuinely empty database is a positive control, not the reproduction.
    fresh_url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(fresh_url, chain="core", schema="health"))
    fresh = _attention_ledger_snapshot(fresh_url)
    assert fresh[0] == []
    assert fresh[1:] == before[1:]
    outcome_check = next(check for check in fresh[2] if check[0] == "chk_attention_ledger_outcome")
    assert outcome_check[2] is True
    assert set(re.findall(r"'([^']+)'", outcome_check[1])) == set(outcomes)


@pytest.mark.parametrize(
    ("revision", "predecessor", "folds"),
    [
        ("core_168", "core_167", {"failed": "deferred"}),
        ("core_241", "core_240", {"expired": "suppressed"}),
        ("core_255", "core_254", {"withdrawn": "suppressed", "amended": "delivered"}),
    ],
)
def test_bounded_attention_downgrades_preserve_cumulative_check_and_provenance(
    postgres_container, revision, predecessor, folds
) -> None:
    """Only the owned revision's documented outcome fold may change a row."""
    outcomes = (
        "delivered",
        "coalesced",
        "deferred",
        "suppressed",
        "failed",
        "expired",
        "withdrawn",
        "amended",
    )
    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, ["core"])
    # Bound the actual upgrade: this never rolls back unrelated later boundaries.
    command.upgrade(config, revision)
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            _seed_attention_ledger(conn, outcomes, "synthetic-downgrade-sentinel")
        before = _attention_ledger_snapshot(db_url)
        assert len(before[0]) == len(outcomes)
        assert {row["outcome"] for row in before[0]} == set(outcomes)
        expected = (
            [dict(row, outcome=folds.get(row["outcome"], row["outcome"])) for row in before[0]],
            before[1],
            before[2],
        )
        command.downgrade(config, predecessor)
        assert _attention_ledger_snapshot(db_url) == expected
        check = next(c for c in expected[2] if c[0] == "chk_attention_ledger_outcome")
        assert check[2] is True
        assert set(re.findall(r"'([^']+)'", check[1])) == set(outcomes)
        # Keeping newer values must not turn the constraint into a fail-open one.
        with engine.begin() as conn:
            with pytest.raises(IntegrityError):
                with conn.begin_nested():
                    conn.execute(
                        text(
                            "INSERT INTO public.attention_ledger (origin_butler, source, outcome) "
                            "VALUES ('general', 'insight', 'unadopted-outcome')"
                        )
                    )
        command.upgrade(config, revision)
        assert _attention_ledger_snapshot(db_url) == expected
    finally:
        engine.dispose()


def test_core_255_adds_premise_objects_idempotently_and_downgrades_cleanly(
    postgres_container,
) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, ["core"])
    command.upgrade(config, "core_254")
    candidate = _run(
        db_url,
        "INSERT INTO public.insight_candidates (origin_butler, priority, category, dedup_key,"
        " expires_at, message) VALUES ('finance', 80, 'bill-due', 'finance:bill-due:b:d',"
        " now() + interval '1 day', 'Bill due') RETURNING id::text",
    )

    command.upgrade(config, "core_255")
    unbound = (
        f"SELECT premise IS NULL AND delivery_ref IS NULL FROM public.insight_candidates"
        f" WHERE id = '{candidate}'"
    )
    assert _run(db_url, unbound) is True
    # The core chain replays per butler schema against the shared public objects.
    asyncio.run(run_migrations(db_url, chain="core", schema="general"))

    _run(
        db_url,
        "UPDATE public.insight_candidates SET status = 'delivered',"
        " delivered_at = now() - interval '1 hour',"
        ' premise = \'{"kind": "owner_condition", "source": "s",'
        f' "fingerprint": "f"}}\'::jsonb WHERE id = \'{candidate}\' RETURNING 1',
    )
    resolved = "'2099-01-01T00:00:00Z'::timestamptz"
    enqueue = f"SELECT public.enqueue_premise_amendments('s', 'f', {resolved}, NULL)"
    assert _run(db_url, enqueue) == 1
    assert _run(db_url, enqueue) == 0  # the same resolution never queues twice
    assert _run(db_url, "SELECT count(*) FROM public.insight_amendments") == 1

    _run(
        db_url,
        "INSERT INTO public.attention_ledger (origin_butler, source, outcome)"
        " VALUES ('finance', 'insight', 'withdrawn') RETURNING 1",
    )
    _run(
        db_url,
        f"UPDATE public.insight_candidates SET status = 'withdrawn' WHERE id = '{candidate}'"
        " RETURNING 1",
    )

    command.downgrade(config, "core_254")
    # The amendment ledger is durable evidence and survives a downgrade.
    assert _run(db_url, "SELECT to_regclass('public.insight_amendments') IS NOT NULL") is True
    assert (
        _run(
            db_url,
            "SELECT to_regprocedure('public.enqueue_premise_amendments(text, text, timestamptz, text)') IS NULL",
        )
        is True
    )
    status = f"SELECT status FROM public.insight_candidates WHERE id = '{candidate}'"
    assert _run(db_url, status) == "filtered"
    assert _run(db_url, "SELECT outcome FROM public.attention_ledger") == "suppressed"

    command.upgrade(config, "core_255")
    assert _run(db_url, "SELECT count(*) FROM public.insight_amendments") == 1
    pins = (
        "SELECT bool_and(proconfig = ARRAY['search_path=pg_catalog, pg_temp']) FROM pg_proc"
        " WHERE proname IN ('enqueue_premise_amendments', 'resolve_finance_bill_status')"
    )
    assert _run(db_url, pins) is True
