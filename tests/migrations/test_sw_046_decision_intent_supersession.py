"""Historical sw_046 supersession preserves catalog and choice history.

The former owner-decision-desk requirement002 is withdrawn with product source;
this migration and its full storage/rollback assertions remain until later DDL.
"""

from __future__ import annotations

import asyncio
import shutil

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from alembic import command
from butlers.migrations import _build_alembic_config, run_migrations
from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]


def test_sw_046_upgrade_downgrade_preserves_intent_history(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(db_url, chain="core"))
    config = _build_alembic_config(db_url, ["switchboard"], target_schema="switchboard")
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        command.upgrade(config, "switchboard@sw_044")
        # Empty, representable databases round-trip too.
        command.upgrade(config, "switchboard@sw_046")
        command.downgrade(config, "switchboard@sw_044")
        with engine.connect() as conn:
            original = (
                conn.execute(
                    text(
                        "INSERT INTO switchboard.decision_intents "
                        "(bead_id, option, source, actor, status, attempts, claimed_at, finished_at) "
                        "VALUES ('bu-reopen', 'Ship it', 'dashboard', 'owner@dashboard', 'applied', "
                        "1, now() - interval '1 hour', now() - interval '30 minutes') RETURNING *"
                    )
                )
                .mappings()
                .one()
            )
            index_before = conn.execute(
                text(
                    "SELECT indexdef FROM pg_indexes WHERE schemaname = 'switchboard' "
                    "AND indexname = 'uq_decision_intents_live_bead'"
                )
            ).scalar_one()
        command.upgrade(config, "switchboard@sw_046")
        with engine.connect() as conn:
            unchanged = (
                conn.execute(
                    text("SELECT * FROM switchboard.decision_intents WHERE id = :id"),
                    {"id": original["id"]},
                )
                .mappings()
                .one()
            )
            assert dict(unchanged) == dict(original)
            index_after = conn.execute(
                text(
                    "SELECT indexdef FROM pg_indexes WHERE schemaname = 'switchboard' "
                    "AND indexname = 'uq_decision_intents_live_bead'"
                )
            ).scalar_one()
            assert index_before == index_after
            superseded = (
                conn.execute(
                    text(
                        "UPDATE switchboard.decision_intents SET status = 'superseded' "
                        "WHERE id = :id RETURNING *"
                    ),
                    {"id": original["id"]},
                )
                .mappings()
                .one()
            )
            assert {k: v for k, v in superseded.items() if k != "status"} == {
                k: v for k, v in original.items() if k != "status"
            }
            conn.execute(
                text(
                    "INSERT INTO switchboard.decision_intents (bead_id, option, source, actor) "
                    "VALUES ('bu-reopen', 'Hold', 'dashboard', 'owner@dashboard')"
                )
            )
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO switchboard.decision_intents (bead_id, option, source, actor) "
                        "VALUES ('bu-reopen', 'Ship it', 'dashboard', 'owner@dashboard')"
                    )
                )
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO switchboard.decision_intents "
                        "(bead_id, option, source, actor, status) "
                        "VALUES ('bu-bad', 'Hold', 'dashboard', 'owner@dashboard', 'unknown')"
                    )
                )
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO switchboard.decision_intents "
                        "(bead_id, option, source, actor, status) "
                        "VALUES ('bu-bad', 'Hold', 'dashboard', 'owner@dashboard', 'failed')"
                    )
                )
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "UPDATE switchboard.decision_intents SET failure_reason = 'bead_not_open' "
                        "WHERE id = :id"
                    ),
                    {"id": original["id"]},
                )

        with pytest.raises(DBAPIError, match="Cannot downgrade sw_046"):
            command.downgrade(config, "switchboard@sw_044")
        with engine.connect() as conn:
            retained = (
                conn.execute(
                    text("SELECT * FROM switchboard.decision_intents WHERE id = :id"),
                    {"id": original["id"]},
                )
                .mappings()
                .one()
            )
            assert dict(retained) == dict(superseded)
            # Disposable-fixture cleanup makes this database representable;
            # downgrade itself must never remove or rewrite terminal history.
            conn.execute(
                text("DELETE FROM switchboard.decision_intents WHERE id = :id"),
                {"id": original["id"]},
            )
        command.downgrade(config, "switchboard@sw_044")
        with engine.connect() as conn:
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "UPDATE switchboard.decision_intents SET status = 'superseded' "
                        "WHERE bead_id = 'bu-reopen'"
                    )
                )
        command.upgrade(config, "switchboard@sw_046")
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text(
                        "UPDATE switchboard.decision_intents SET status = 'superseded' "
                        "WHERE bead_id = 'bu-reopen' RETURNING status"
                    )
                ).scalar_one()
                == "superseded"
            )
    finally:
        engine.dispose()
