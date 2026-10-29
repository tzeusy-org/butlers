"""D7 ordinary backup, privileged restore and exact catalogue refusal.

Synthetic local testcontainers only. These are mandatory hosted PG owners;
collection or source extraction never counts as executing their bodies.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse
from uuid import UUID, uuid4

import asyncpg
import pytest

from butlers.core.attention_urgency import AttentionOrigin, evaluate_interruption
from butlers.core.scheduler import tick
from butlers.db import register_jsonb_codec
from butlers.testing.migration import migration_bootstrap_db_url
from tests.integration.test_dashboard_stepup_restore import client
from tests.integration.test_owner_creation_attention import (
    attention_env as attention_env,
)
from tests.integration.test_owner_creation_attention import (
    authority as authority,
)
from tests.integration.test_owner_creation_attention import (
    create,
    issue,
)
from tests.integration.test_owner_creation_attention import (
    migrated_db_url as migrated_db_url,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]
ROOT = Path(__file__).resolve().parents[2]


async def test_ordinary_dump_privileged_restore_and_catalogue_loss_refuse(
    attention_env, migrated_db_url, postgres_container, tmp_path
):
    env = attention_env
    _, claim = await issue(env, {"name": "durable"})
    task = await create(env, claim)
    entity = uuid4()
    clause = await env.service.attention_call(
        "clause_create",
        env.authority,
        env.session.data["csrf_token"],
        entity_id=str(entity),
        scope="always",
    )
    # Populate the final-claim witness through the real final tick as well;
    # dump/restore must not silently drop the newly durable fifth relation.
    due = datetime.now(UTC) - timedelta(seconds=1)
    # until_at is part of the immutable definition: genuinely author the final
    # task with that field rather than rewriting the original stamp in fixture.
    raw = {"name": "durable_final_" + uuid4().hex}
    _, final_claim = await issue(env, raw)
    from butlers.core.owner_creation import _pending
    from butlers.core.scheduler import schedule_create

    token = _pending.set(final_claim)
    try:
        with patch("butlers.core.scheduler._next_run", return_value=due):
            final_task = await schedule_create(
                env.domain,
                raw["name"],
                "0 9 * * *",
                "Synthetic final restore",
                until_at=datetime.now(UTC) + timedelta(seconds=30),
                stagger_key="general",
            )
    finally:
        _pending.reset(token)

    async def dispatched(**_kwargs):
        return {"status": "ok"}

    with patch(
        "butlers.core.scheduler._next_run", return_value=datetime.now(UTC) + timedelta(days=1)
    ):
        assert await tick(env.domain, dispatched, stagger_key="general") >= 1
    assert await env.migration.fetchval(
        "SELECT EXISTS(SELECT FROM dashboard_attention.final_claims WHERE task_id=$1 AND NOT revoked)",
        final_task,
    )
    # Populate all five durable relations through the actual fixed writers.
    candidate = await env.migration.fetchval(
        """
        INSERT INTO public.insight_candidates(origin_butler,priority,category,dedup_key,
         expires_at,message,status) VALUES('general',10,'restore',$1,
         clock_timestamp()+interval '1 day','Synthetic candidate','pending') RETURNING id
    """,
        "general:restore:" + uuid4().hex + ":case",
    )
    assert await env.migration.fetchval(
        "SELECT dashboard_attention.remember($1,$2::jsonb)",
        candidate,
        AttentionOrigin(entity_id=entity).record(),
    )
    tables = (
        "dashboard_attention.tickets",
        "dashboard_attention.stamps",
        "dashboard_attention.candidate_causes",
        "public.break_through_clauses",
        "dashboard_attention.final_claims",
    )
    before = {
        table: await env.migration.fetchval(
            f"SELECT jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text) FROM {table} t"
        )
        for table in tables
    }
    assert all(before.values())
    source = urlparse(migrated_db_url)
    backup = await asyncio.to_thread(
        client,
        postgres_container,
        'export POSTGRES_PASSWORD="$SYNTHETIC_PASSWORD"; bash /backup/pg_dump.sh',
        source.password,
        mounts=(
            f"{ROOT / 'deploy/backup/pg_dump.sh'}:/backup/pg_dump.sh:ro",
            f"{tmp_path}:/backups",
        ),
        POSTGRES_HOST="host.docker.internal",
        POSTGRES_PORT=str(postgres_container.get_exposed_port(5432)),
        POSTGRES_USER=source.username,
        POSTGRES_DB=source.path.lstrip("/"),
        BACKUP_DIR="/backups",
    )
    assert backup.returncode == 0, "ordinary managed backup must succeed"
    artifacts = list(tmp_path.glob("butlers_*.sql.gz"))
    assert len(artifacts) == 1
    admin = urlparse(postgres_container.get_connection_url())
    target = "attention_restore_" + uuid4().hex[:12]
    connection = None
    try:
        restored = await asyncio.to_thread(
            client,
            postgres_container,
            f'export POSTGRES_PASSWORD="$SYNTHETIC_PASSWORD"; bash /restore.sh /backup.sql.gz --host host.docker.internal --port {postgres_container.get_exposed_port(5432)} --user {admin.username} --target-db {target} --drop-existing --capture-epoch-file /capture-host/{target}.json',
            admin.password,
            mounts=(
                f"{ROOT / 'scripts/pg_restore.sh'}:/restore.sh:ro",
                f"{artifacts[0]}:/backup.sql.gz:ro",
                f"{tmp_path}:/capture-host",
            ),
        )
        assert restored.returncode == 0, (
            "privileged managed restore must preserve the fixed interface"
        )
        connection = await asyncpg.connect(
            migration_bootstrap_db_url(postgres_container, target).replace(
                "postgresql+psycopg2", "postgresql"
            )
        )
        await register_jsonb_codec(connection)
        after = {
            table: await connection.fetchval(
                f"SELECT jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text) FROM {table} t"
            )
            for table in tables
        }
        assert before == after
        await connection.execute("SET ROLE butler_general_rw")
        try:
            restored_origin = AttentionOrigin(
                namespace="general", task_id=final_task, original_next_run_at=due
            )
            assert (
                await evaluate_interruption(connection, gate="context", origin=restored_origin)
            ).interrupts
        finally:
            await connection.execute("RESET ROLE")
        assert await connection.fetchval(
            "SELECT EXISTS(SELECT FROM general.scheduled_tasks WHERE id=$1)", task
        )
        assert await connection.fetchval(
            "SELECT EXISTS(SELECT FROM public.break_through_clauses WHERE id=$1)",
            UUID(clause["id"]),
        )
        assert claim.nonce not in str(after) and env.session.token not in str(after)
        script = (ROOT / "scripts/pg_restore.sh").read_text()
        match = re.search(
            r"(CREATE FUNCTION pg_temp\.owner_attention_intact.*?"
            r"\\\$attention_catalog\\\$;)\n"
            r"(SELECT pg_temp\.owner_attention_intact\(\$ATTENTION_EXPECTED\))",
            script,
            re.S,
        )
        assert match is not None
        await connection.execute(match.group(1).replace("\\$", "$"))
        predicate = match.group(2).replace("$ATTENTION_EXPECTED", "true")
        legacy_predicate = match.group(2).replace("$ATTENTION_EXPECTED", "false")
        start = script.index("DO \\$attention_stage\\$")
        end = script.index("END \\$attention_stage\\$;", start) + len("END \\$attention_stage\\$;")
        stage = script[start:end].replace("\\$", "$").replace("$ATTENTION_EXPECTED", "true")
        for copy, row, table in (
            ("ATC", "ATR", tables[0]),
            ("ASC", "ASR", tables[1]),
            ("ACC", "ACR", tables[2]),
            ("ABC", "ABR", tables[3]),
            ("AFC", "AFR", tables[4]),
        ):
            stage = stage.replace("$" + copy, "1").replace("$" + row, str(len(before[table])))
        assert await connection.fetchval(predicate) is True
        # Missing rows/configuration, column grants, hostile definer ownership
        # and a missing modern schema refuse the ACTUAL script predicate/stage.
        for mutation in (
            "DELETE FROM dashboard_attention.candidate_causes",
            "DELETE FROM dashboard_attention.final_claims",
            "DROP TABLE dashboard_attention.final_claims",
            "ALTER TABLE dashboard_attention.final_claims RENAME COLUMN definition_digest TO wrong_digest",
            f'GRANT SELECT(nonce_digest) ON dashboard_attention.tickets TO "{source.username}"',
            f'ALTER FUNCTION dashboard_attention.consume(text,text,text,text,text,text,uuid) OWNER TO "{source.username}"',
            "DROP TABLE dashboard_attention.bootstrap_configuration",
            "DROP SCHEMA dashboard_attention CASCADE",
        ):
            control = connection.transaction()
            await control.start()
            try:
                await connection.execute(mutation)
                if mutation.startswith("DELETE"):
                    with pytest.raises(asyncpg.RaiseError, match="population differs"):
                        async with connection.transaction():
                            await connection.execute(stage)
                else:
                    assert await connection.fetchval(predicate) is False
                if mutation.startswith("DROP SCHEMA"):
                    with pytest.raises(asyncpg.RaiseError, match="required owner attention"):
                        async with connection.transaction():
                            await connection.execute(stage)
                    with pytest.raises(asyncpg.RaiseError, match="recorded owner attention"):
                        async with connection.transaction():
                            await connection.execute(stage.replace("IF true THEN", "IF false THEN"))
                    await connection.execute(
                        "UPDATE general.alembic_version SET version_num='core_277' WHERE version_num LIKE 'core_%'"
                    )
                    await connection.execute(stage.replace("IF true THEN", "IF false THEN"))
                    assert await connection.fetchval(legacy_predicate) is True
            finally:
                await control.rollback()
    finally:
        if connection is not None:
            await connection.close()
        control = await asyncpg.connect(
            postgres_container.get_connection_url().replace("postgresql+psycopg2", "postgresql")
        )
        try:
            await control.execute(f'DROP DATABASE IF EXISTS "{target}" WITH (FORCE)')
        finally:
            await control.close()
