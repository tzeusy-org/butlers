"""Actual ordinary backup and privileged lossless D13 restore; no live data."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

import asyncpg
import pytest

from butlers.api.owner_auth.service import _digest
from butlers.core.dashboard_stepup import dashboard_stepup_proof
from butlers.db import register_jsonb_codec
from butlers.modules.approvals import operations
from butlers.testing.migration import migration_bootstrap_db_url
from tests.integration.test_dashboard_stepup import authority as authority
from tests.integration.test_dashboard_stepup import migrated_db_url as migrated_db_url
from tests.integration.test_dashboard_stepup import proof_for

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]
ROOT = Path(__file__).resolve().parents[2]


def client(container, shell, password, mounts=(), **environment):
    client_name = "stepup_client_" + uuid4().hex
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        client_name,
        "--add-host=host.docker.internal:host-gateway",
    ]
    for mount in mounts:
        command += ["-v", mount]
    for name in ("SYNTHETIC_PASSWORD", *environment):
        command += ["-e", name]
    command += ["postgres:17", "bash", "-c", shell]
    try:
        return subprocess.run(
            command,
            env={**os.environ, "SYNTHETIC_PASSWORD": password, **environment},
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired:
        # Killing only the Docker CLI can leave its owned container running.
        # Cleanup cannot turn the original timeout into a successful owner.
        try:
            subprocess.run(
                ["docker", "rm", "--force", client_name],
                capture_output=True,
                timeout=10,
                check=False,
            )
        finally:
            raise


async def test_ordinary_dump_privileged_restore_preserves_receipts_owners_and_acl(
    authority, migrated_db_url, postgres_container, tmp_path
):
    pool, _, _, _, _, _ = authority
    parameters = dict(
        tool_name="synthetic_send",
        arg_constraints={},
        description="Durable restore",
        expires_at=None,
        max_uses=1,
    )
    proof = await proof_for(authority, "create", parameters)
    token = dashboard_stepup_proof.set(proof)
    try:
        created = await operations.create_approval_rule(pool, **parameters)
    finally:
        dashboard_stepup_proof.reset(token)
    assert "error" not in created
    before = await pool.fetch(
        "SELECT to_jsonb(b) AS value FROM dashboard_stepup.bindings b ORDER BY id"
    )
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
    assert backup.returncode == 0, "ordinary managed backup must produce a complete artifact"
    artifacts = list(tmp_path.glob("butlers_*.sql.gz"))
    assert len(artifacts) == 1
    admin = urlparse(postgres_container.get_connection_url())
    target = "stepup_restore_" + uuid4().hex[:12]
    connection = None
    try:
        restored = await asyncio.to_thread(
            client,
            postgres_container,
            f'export POSTGRES_PASSWORD="$SYNTHETIC_PASSWORD"; bash /restore.sh /backup.sql.gz --host host.docker.internal --port {postgres_container.get_exposed_port(5432)} --user {admin.username} --target-db {target} --drop-existing',
            admin.password,
            mounts=(
                f"{ROOT / 'scripts/pg_restore.sh'}:/restore.sh:ro",
                f"{artifacts[0]}:/backup.sql.gz:ro",
            ),
        )
        assert restored.returncode == 0, (
            "privileged managed restore must preserve every definer owner"
        )
        target_url = migration_bootstrap_db_url(postgres_container, target)
        connection = await asyncpg.connect(target_url.replace("postgresql+psycopg2", "postgresql"))
        await register_jsonb_codec(connection)
        after = await connection.fetch(
            "SELECT to_jsonb(b) AS value FROM dashboard_stepup.bindings b ORDER BY id"
        )
        assert [row["value"] for row in before] == [row["value"] for row in after]
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM approval_rules WHERE id=$1", UUID(created["id"])
            )
            == 1
        )
        assert proof.nonce not in str(after)
        assert await connection.fetchval(
            "SELECT nonce_digest FROM dashboard_stepup.bindings WHERE nonce_digest=$1",
            _digest(proof.nonce),
        ) == _digest(proof.nonce)
        # The dump deliberately excludes unrelated DND control-plane tables.
        # Replaying all of init-db here would correctly refuse that partial DND
        # catalog before reaching D13. Replay the exact owned bootstrap tail,
        # including its fixed installer/finalizer and hostile-owner checks.
        bootstrap = (ROOT / "scripts/init-db.sql").read_text()
        marker = "-- D13 fixed step-up interface."
        assert bootstrap.count(marker) == 1
        stepup_bootstrap = bootstrap[bootstrap.index(marker) :]
        await connection.execute(
            "SELECT set_config('butlers.connecting_user', $1, false)", source.username
        )
        await connection.execute(stepup_bootstrap)
        assert [row["value"] for row in before] == [
            row["value"]
            for row in await connection.fetch(
                "SELECT to_jsonb(b) AS value FROM dashboard_stepup.bindings b ORDER BY id"
            )
        ]
        # Read the ACTUAL fixed private-schema predicate executed by the restore
        # script. A catalog mutation must invalidate it, not merely a source grep.
        script = (ROOT / "scripts/pg_restore.sh").read_text()
        match = re.search(
            r'(\nSELECT CASE WHEN to_regnamespace\(\'dashboard_stepup\'.*?END)"\)', script, re.S
        )
        assert match is not None
        predicate = match.group(1).replace("$STEPUP_EXPECTED", "true")
        stage_start = script.index("DO \\$stepup_stage\\$")
        stage_end = script.index("END \\$stepup_stage\\$;", stage_start)
        stage_sql = script[stage_start : stage_end + len("END \\$stepup_stage\\$;")]
        stage_sql = (
            stage_sql.replace("\\$", "$")
            .replace("$STEPUP_EXPECTED", "true")
            .replace("$STEPUP_COPY_COUNT", "1")
            .replace("$STEPUP_ROW_COUNT", str(len(before)))
        )
        # Missing durable receipt data must refuse even with intact owners/DDL.
        receipt_control = connection.transaction()
        await receipt_control.start()
        try:
            await connection.execute("DELETE FROM dashboard_stepup.bindings")
            with pytest.raises(asyncpg.RaiseError, match="receipt population differs"):
                async with connection.transaction():
                    await connection.execute(stage_sql)
        finally:
            await receipt_control.rollback()
        assert await connection.fetchval(predicate) is True
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM pg_auth_members WHERE roleid='dashboard_stepup_owner'::regrole OR member='dashboard_stepup_owner'::regrole"
            )
            == 0
        )
        acl = await connection.fetch(
            "SELECT a.privilege_type,r.rolname FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a JOIN pg_roles r ON r.oid=a.grantee WHERE c.oid='dashboard_stepup.bindings'::regclass AND r.rolname<>'dashboard_stepup_owner'"
        )
        assert [(row["privilege_type"], row["rolname"]) for row in acl] == [
            ("SELECT", source.username)
        ]
        await connection.execute(
            f'ALTER FUNCTION dashboard_stepup.consume(text,text,text,text,text,text,text,jsonb) OWNER TO "{source.username}"'
        )
        assert await connection.fetchval(predicate) is False
        # Even whole-schema loss must fail for modern dumps. The destructive
        # controls are rolled back; the durable restored history is unchanged.
        control = connection.transaction()
        await control.start()
        try:
            await connection.execute("DROP SCHEMA dashboard_stepup CASCADE")
            assert await connection.fetchval(predicate) is False
            with pytest.raises(asyncpg.RaiseError, match="required dashboard step-up"):
                async with connection.transaction():
                    await connection.execute(stage_sql)
            # Missing dump DDL does not override actual core_277 history.
            with pytest.raises(asyncpg.RaiseError, match="recorded dashboard step-up"):
                async with connection.transaction():
                    await connection.execute(stage_sql.replace("IF true THEN", "IF false THEN"))
            # A genuine pre-feature, schema-free database retains its old door.
            await connection.execute(
                "UPDATE public.alembic_version SET version_num='core_275' WHERE version_num LIKE 'core_%'"
            )
            await connection.execute(stage_sql.replace("IF true THEN", "IF false THEN"))
            assert await connection.fetchval(predicate.replace("NOT true", "NOT false")) is True
        finally:
            await control.rollback()
    finally:
        if connection is not None:
            await connection.close()
        control_url = postgres_container.get_connection_url().replace(
            "postgresql+psycopg2", "postgresql"
        )
        control = await asyncpg.connect(control_url)
        try:
            await control.execute(f'DROP DATABASE IF EXISTS "{target}" WITH (FORCE)')
        finally:
            await control.close()
