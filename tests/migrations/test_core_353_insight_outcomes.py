"""core_353 insight outcome ledger on migrated PostgreSQL (bu-s11n0s.15 S1).

The two shared tables are installed once across the per-schema core chains, fenced to
Switchboard by row level security (ENABLE, not FORCE: the owning executor keeps its bypass for
migrations and backups), and dropped only by the last schema to leave core_353.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = [pytest.mark.integration, pytest.mark.db]

_TABLES = ("insight_outcomes", "insight_class_efficacy")
_PRESENT = text(
    "SELECT to_regclass('public.insight_outcomes') IS NOT NULL "
    "AND to_regclass('public.insight_class_efficacy') IS NOT NULL"
)


def test_tables_are_fenced_to_switchboard_and_round_trip(postgres_container) -> None:
    from alembic import command
    from butlers.migrations import _build_alembic_config

    url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
        revisions={"core": "core_353"},
    )
    config = _build_alembic_config(url, chains=["core"])
    engine = create_engine(url)
    try:
        with engine.connect() as c:
            assert c.execute(_PRESENT).scalar() is True
            for table in _TABLES:
                row = c.execute(
                    text(
                        "SELECT cls.relrowsecurity, cls.relforcerowsecurity, "
                        "pg_get_userbyid(cls.relowner) = current_user AS owned, "
                        "has_table_privilege('butler_switchboard_rw', cls.oid, 'UPDATE') AS sb, "
                        "has_table_privilege('butler_finance_rw', cls.oid, 'SELECT') AS other, "
                        "has_table_privilege('butler_switchboard_rw', cls.oid, 'DELETE') AS sb_del, "
                        "(SELECT count(*) FROM pg_policy p WHERE p.polrelid = cls.oid) AS policies "
                        "FROM pg_class cls WHERE cls.oid = to_regclass(:t)"
                    ),
                    {"t": f"public.{table}"},
                ).one()
                assert row.relrowsecurity is True
                assert row.relforcerowsecurity is False
                assert row.owned is True  # the executor that runs migrations and backups
                assert (row.sb, row.other, row.sb_del, row.policies) == (True, False, False, 1)
        command.downgrade(config, "core_352")
        with engine.connect() as c:
            assert c.execute(_PRESENT).scalar() is False
        command.downgrade(config, "core_349")  # already gone: tolerated
        command.upgrade(config, "core_353")
        with engine.connect() as c:
            assert c.execute(_PRESENT).scalar() is True
    finally:
        engine.dispose()


def test_a_settled_outcome_needs_its_resolution_fields(postgres_container) -> None:
    url = create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])
    engine = create_engine(url)
    insert = text(
        "INSERT INTO public.insight_outcomes (candidate_id, origin_butler, category, "
        "dedup_family, priority, premise, exposure, proposed_at, exposed_at, window_end, "
        "outcome, resolved_at, resolved_precision, resolution_reason, settled_at) VALUES "
        "(gen_random_uuid(), 'finance', 'bill-due', 'finance:bill-due:a', 70, "
        "'{\"kind\": \"probe\"}', 'held_budget', now(), NULL, now() + interval '14 days', "
        ":outcome, :resolved_at, :precision, :reason, :settled_at)"
    )
    try:
        for bad in (
            {
                "outcome": "resolved",
                "resolved_at": None,
                "precision": "exact",
                "reason": "satisfied",
                "settled_at": "2026-11-19",
            },
            {
                "outcome": None,
                "resolved_at": None,
                "precision": None,
                "reason": None,
                "settled_at": "2026-11-19",
            },
            {
                "outcome": "persisted",
                "resolved_at": "2026-11-19",
                "precision": "exact",
                "reason": "x",
                "settled_at": "2026-11-19",
            },
        ):
            with pytest.raises(Exception, match="insight_outcomes_"):
                with engine.begin() as c:
                    c.execute(insert, bad)
        with engine.begin() as c:
            c.execute(
                insert,
                {
                    "outcome": "resolved",
                    "resolved_at": "2026-11-19",
                    "precision": "exact",
                    "reason": "satisfied",
                    "settled_at": "2026-11-19",
                },
            )
    finally:
        engine.dispose()
