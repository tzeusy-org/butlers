"""core_255: premise-bound proactive speech objects (bu-q7vx1q.5).

Real PostgreSQL: the new columns/vocabulary arrive without disturbing existing
rows, the enqueue definer is idempotent per (candidate, resolution), a second
schema's core run is a no-op, and downgrade folds the new values back before
narrowing the constraints.
"""

from __future__ import annotations

import asyncio
import shutil

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.migrations import _build_alembic_config, run_migrations
from butlers.testing.migration import create_migration_db, migration_db_name

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
