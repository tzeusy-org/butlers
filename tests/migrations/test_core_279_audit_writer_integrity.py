"""REQ-dashboard-audit-log-001: genuine role/transaction/bootstrap boundaries.

All database bodies require migrated PostgreSQL; collection is not execution.
"""

from __future__ import annotations

import asyncio
import shutil
from urllib.parse import urlparse

import asyncpg
import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.config import RuntimeSeedConfig
from butlers.core import audit
from butlers.core.runtime_config import RuntimeConfigAccessor
from butlers.db import register_jsonb_codec
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import (
    _bootstrap_migration_prerequisites,
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)
from tests.audit_helpers import clear_audit_log_for_test

pytestmark = [
    pytest.mark.integration,
    pytest.mark.db,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_RUNTIME_ROLES = (
    "butler_chronicler_rw",
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_lifestyle_rw",
    "butler_messenger_rw",
    "butler_qa_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
    "connector_writer",
)


@pytest.fixture(scope="module", params=["migrated", "bootstrap_replayed"])
def audit_database(postgres_container, request):
    name = migration_db_name()
    url = create_migrated_test_db(
        postgres_container, name, chains=["core"], schemas={"core": "general"}
    )
    admin_url = migration_bootstrap_db_url(postgres_container, name)
    if request.param == "bootstrap_replayed":
        _bootstrap_migration_prerequisites(admin_url, urlparse(url).username)
    return url, admin_url


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["concurrent_success", "required_audit_denied"])
async def test_runtime_reconciliation_commits_once_or_rolls_back(audit_database, outcome):
    url, _ = audit_database
    owner = await asyncpg.create_pool(url, min_size=1, max_size=2, init=register_jsonb_codec)

    async def runtime_role(connection):
        await connection.execute('SET ROLE "butler_general_rw"')

    runtime = await asyncpg.create_pool(
        url, min_size=1, max_size=3, init=register_jsonb_codec, setup=runtime_role
    )
    try:
        seed = RuntimeSeedConfig(core_groups=("infra", "delegation"))
        await owner.execute("""
            INSERT INTO general.runtime_config(butler_name,core_groups)
            VALUES ('general',ARRAY['infra']::text[])
            ON CONFLICT(butler_name) DO UPDATE
            SET core_groups=EXCLUDED.core_groups, core_groups_narrowing_reason=NULL
        """)
        before = await owner.fetchval("""
            SELECT count(*) FROM public.audit_log
            WHERE action='core_groups_reconciled' AND target='general'
        """)
        if outcome == "required_audit_denied":
            # Negative-only fixture revocation; the production chain owns the
            # positive privilege. Restore it in finally for the other cases.
            await owner.execute("REVOKE INSERT ON public.audit_log FROM butler_general_rw")
            with pytest.raises(audit.AuditTableNotAvailableError):
                await RuntimeConfigAccessor(runtime, "general").seed_if_empty(seed, "general")
        else:
            results = await asyncio.gather(
                *(
                    RuntimeConfigAccessor(runtime, "general").seed_if_empty(seed, "general")
                    for _ in range(2)
                )
            )
            assert all(result.core_groups == ("infra", "delegation") for result in results)
        async with owner.acquire() as observer:
            assert await observer.fetchval(
                "SELECT core_groups FROM general.runtime_config WHERE butler_name='general'"
            ) == (["infra"] if outcome == "required_audit_denied" else ["infra", "delegation"])
            assert await observer.fetchval("""
                SELECT count(*) FROM public.audit_log
                WHERE action='core_groups_reconciled' AND target='general'
            """) == before + (outcome == "concurrent_success")
        # Explicit fixture-only table-owner reset must leave all guards enabled.
        await clear_audit_log_for_test(owner)
        assert (
            await owner.fetchval("""
            SELECT count(*) FROM pg_trigger WHERE tgrelid='public.audit_log'::regclass
            AND tgname IN('audit_log_writer_identity','audit_log_no_row_mutation',
                           'audit_log_no_truncate') AND tgenabled='A'
        """)
            == 3
        )
    finally:
        await owner.execute("GRANT INSERT ON public.audit_log TO butler_general_rw")
        await asyncio.wait_for(runtime.close(), timeout=10)
        await asyncio.wait_for(owner.close(), timeout=10)


def test_upgrade_and_downgrade_preserve_historical_evidence(postgres_container):
    name = migration_db_name()
    url = create_migrated_test_db(
        postgres_container, name, chains=["core"], revisions={"core": "core_278"}
    )
    engine = create_engine(url)
    config = _build_alembic_config(url, chains=["core"])
    try:
        with engine.begin() as connection:
            old_id = connection.execute(
                text("""
                INSERT INTO public.audit_log(actor,action) VALUES ('old-actor','old-history')
                RETURNING id
            """)
            ).scalar_one()
        with engine.begin() as connection:
            connection.execute(text('SET LOCAL ROLE "butler_general_rw"'))
            # Actual omission of production core279 permits mutation; no fake ACL.
            assert (
                connection.execute(
                    text("UPDATE public.audit_log SET note='legacy-unprotected' WHERE id=:id"),
                    {"id": old_id},
                ).rowcount
                == 1
            )
        command.upgrade(config, "core_279")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT writer_role FROM public.audit_log WHERE id=:id"), {"id": old_id}
                ).scalar_one()
                is None
            )
        from sqlalchemy.exc import DBAPIError

        with pytest.raises(DBAPIError) as refused, engine.begin() as connection:
            connection.execute(text('SET LOCAL ROLE "butler_general_rw"'))
            connection.execute(
                text("UPDATE public.audit_log SET note='forged' WHERE id=:id"), {"id": old_id}
            )
        assert refused.value.orig.pgcode == "42501"
        with engine.begin() as connection:
            new_id, writer = connection.execute(
                text("""
                INSERT INTO public.audit_log(actor,action) VALUES ('new-actor','new-history')
                RETURNING id,writer_role
            """)
            ).one()
        command.downgrade(config, "core_278")
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT actor,writer_role FROM public.audit_log WHERE id=:id"), {"id": new_id}
            ).one() == ("new-actor", writer)
            assert (
                connection.execute(
                    text("SELECT actor FROM public.audit_log WHERE id=:id"), {"id": old_id}
                ).scalar_one()
                == "old-actor"
            )
        command.upgrade(config, "core_279")
    finally:
        engine.dispose()


def test_existing_constrained_projection_stamps_its_real_insert_role(audit_database):
    _, admin_url = audit_database
    engine = create_engine(admin_url)
    try:
        with engine.begin() as connection:
            before = connection.execute(
                text("SELECT COALESCE(max(id),0) FROM public.audit_log")
            ).scalar_one()
            connection.execute(text('SET LOCAL ROLE "restore_drill_executor_owner"'))
            connection.execute(
                text("SELECT restore_drill_executor_admin.write_audit_projection('pass')")
            )
        with engine.connect() as observer:
            row = observer.execute(
                text("""
                SELECT actor,action,writer_role FROM public.audit_log
                WHERE id>:before ORDER BY id
            """),
                {"before": before},
            ).one()
            assert tuple(row) == (
                "restore_drill",
                "restore_drill_result",
                "restore_drill_executor_audit_writer",
            )
            # This public projection still does not manufacture an authoritative
            # private restore result: that separate executor interface is intact.
            assert (
                observer.execute(
                    text("SELECT count(*) FROM restore_drill_executor.restore_drill_results")
                ).scalar_one()
                == 0
            )
    finally:
        engine.dispose()
