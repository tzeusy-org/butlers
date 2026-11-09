"""core_252 pins ``cost_claim_restore_row`` only while core_239's writers are out of the way.

core_239 replaces the function under ``butlers:core_239:cost_claims``. Its transaction ends at the
autocommit blocks of later revisions, so a chain still replaying core_239 can run beside a chain
at core_252. Updating one ``pg_proc`` row from two transactions fails the loser with "tuple
concurrently updated" unless both take the same advisory key (bu-op5402).
"""

from __future__ import annotations

import shutil
import threading
import time

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import (
    create_migration_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

pytestmark = [
    pytest.mark.db,
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_FUNCTION = "public.cost_claim_restore_row(text, jsonb)"


def test_core_252_waits_for_a_core_239_writer_instead_of_failing(postgres_container) -> None:
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    config = _build_alembic_config(db_url, chains=["core"], target_schema="general")
    command.upgrade(config, "core_251")

    engine = create_engine(
        migration_bootstrap_db_url(postgres_container, db_name), isolation_level="AUTOCOMMIT"
    )
    outcome: dict[str, BaseException | None] = {}

    def upgrade() -> None:
        try:
            command.upgrade(config, "core_252")
            outcome["error"] = None
        except BaseException as exc:  # noqa: BLE001 - the test reports whatever the chain raised
            outcome["error"] = exc

    try:
        with engine.connect() as writer:
            # What a chain replaying core_239 holds: its key and an uncommitted pg_proc update.
            writer.exec_driver_sql("BEGIN")
            writer.exec_driver_sql(
                "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_239:cost_claims', 0))"
            )
            writer.exec_driver_sql(
                f"ALTER FUNCTION {_FUNCTION} SET search_path = pg_catalog, public"
            )
            thread = threading.Thread(target=upgrade)
            thread.start()
            deadline = time.monotonic() + 60
            waiting = 0
            while time.monotonic() < deadline and "error" not in outcome and not waiting:
                waiting = writer.execute(
                    text("SELECT count(*) FROM pg_locks WHERE NOT granted")
                ).scalar_one()
                time.sleep(0.1)
            assert "error" not in outcome, f"core_252 did not wait for the writer: {outcome}"
            assert waiting, "core_252 never queued behind the core_239 writer"
            writer.exec_driver_sql("COMMIT")
            thread.join(timeout=120)
        assert outcome.get("error") is None, outcome
        with engine.connect() as conn:
            config_value = conn.execute(
                text(f"SELECT proconfig FROM pg_proc WHERE oid = '{_FUNCTION}'::regprocedure")
            ).scalar_one()
        assert "search_path=pg_catalog, pg_temp" in config_value
    finally:
        engine.dispose()
