"""Real schema/bootstrap/ordinary-role and existing backup-path outcomes."""

import asyncio
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from butlers.core.goal_admission import Evidence, SourceRef
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import (
    _bootstrap_migration_prerequisites,
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)
from tests.switchboard.goal_ledger_fixtures import (  # noqa: F401
    TABLES,
    admit,
    effect,
    pools,
)

pytest_plugins = ("tests.switchboard.goal_ledger_fixtures",)

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
# Roles scripts/init-db.sql creates. Roles are cluster-level and outlive per-test databases, so
# the isolation test names exactly these instead of a LIKE pattern that also matches strays
# (a leaked `connector_probe` failed it by shard order, bu-28av8x.12).
BOOTSTRAP_RUNTIME_ROLES = frozenset(
    {
        *(
            f"butler_{butler}_rw"
            for butler in (
                "chronicler",
                "concierge",
                "education",
                "finance",
                "general",
                "health",
                "home",
                "lifestyle",
                "messenger",
                "qa",
                "relationship",
                "switchboard",
                "travel",
            )
        ),
        "connector_writer",
    }
)
# Created only when the calendar module is enabled; checked if present.
OPTIONAL_BOOTSTRAP_ROLES = frozenset({"butler_calendar_rw"})


def bootstrap_again(container, url):
    parsed = urlparse(url)
    _bootstrap_migration_prerequisites(
        migration_bootstrap_db_url(container, parsed.path[1:]), parsed.username
    )


async def populate(url):
    # The ordinary full-backup contract includes live catalog definitions too;
    # it must not succeed merely because their FORCE-RLS tables were empty.
    from tests.integration.test_catalog_publication import publish_all
    from tests.switchboard.goal_admission_fixtures import limits
    from tests.switchboard.goal_ledger_fixtures import configure

    async with pools(url) as (owner, runtime):
        await owner.execute("UPDATE public.capability_catalog_control SET enabled=true")
        await publish_all(url)
        await configure(owner, limits())
        result = await admit(runtime)
        # A joined reply populates goal_inbox, so every ledger table has a row.
        delivery, entity = f"populate-{uuid4()}", uuid4()
        expiry = await owner.fetchval("SELECT now() + interval '1 hour'")
        await effect(
            owner,
            result.goal_id,
            delivery=delivery,
            entity=entity,
            channel="telegram",
            accepts=True,
            expiry=expiry,
        )
        reply = Evidence(
            "message",
            "owner",
            owner=True,
            reply_delivery=delivery,
            owner_entity=entity,
            owner_channel="telegram",
        )
        assert (await admit(runtime, reply)).result == "joined"
        return result


def snapshot(url):
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            rows = {
                t: conn.execute(
                    text(f"SELECT to_jsonb(t) FROM switchboard.{t} t ORDER BY to_jsonb(t)::text")
                )
                .scalars()
                .all()
                for t in TABLES
            }
            for suffix in ("installations", "control", "members", "history", "current"):
                qualified = "public.capability_catalog_" + suffix
                rows[qualified] = (
                    conn.execute(
                        text(f"SELECT to_jsonb(t) FROM {qualified} t ORDER BY to_jsonb(t)::text")
                    )
                    .scalars()
                    .all()
                )
            posture = conn.execute(
                text("""
                SELECT c.relname,pg_get_userbyid(c.relowner),c.relrowsecurity,c.relforcerowsecurity,
                    p.polname,p.polcmd,pg_get_expr(p.polqual,p.polrelid),pg_get_expr(p.polwithcheck,p.polrelid)
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                JOIN pg_policy p ON p.polrelid=c.oid
                WHERE n.nspname='switchboard' AND c.relname=ANY(:tables) ORDER BY 1,5
            """),
                {"tables": list(TABLES)},
            ).all()
            constraints = conn.execute(
                text("""
                SELECT c.relname,k.conname,pg_get_constraintdef(k.oid)
                FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='switchboard' AND c.relname=ANY(:tables) ORDER BY 1,2
            """),
                {"tables": list(TABLES)},
            ).all()
            return rows, posture, constraints
    finally:
        engine.dispose()


def test_goal_ledger_bootstrap_runtime_isolation(ledger_url, postgres_container):
    asyncio.run(populate(ledger_url))
    bootstrap_again(postgres_container, ledger_url)
    engine = create_engine(ledger_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            posture = snapshot(ledger_url)[1]
            assert len(posture) == 4 * len(TABLES) and all(row[2] and not row[3] for row in posture)
            expected_counts = {
                t: conn.execute(text(f"SELECT count(*) FROM switchboard.{t}")).scalar_one()
                for t in TABLES
            }
            roles = conn.execute(
                text(
                    "SELECT rolname,rolsuper,rolbypassrls FROM pg_roles WHERE rolname = ANY(:names)"
                ),
                {"names": sorted(BOOTSTRAP_RUNTIME_ROLES | OPTIONAL_BOOTSTRAP_ROLES)},
            ).all()
            found = {row[0] for row in roles}
            assert BOOTSTRAP_RUNTIME_ROLES <= found, sorted(BOOTSTRAP_RUNTIME_ROLES - found)
            original_rows = snapshot(ledger_url)[0]
            # Resolve names as the owner: denied runtimes cannot traverse the
            # schema, but the OID overload still checks their actual table ACL.
            table_oids = dict(
                conn.execute(
                    text(
                        "SELECT c.relname,c.oid FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid=c.relnamespace "
                        "WHERE n.nspname='switchboard' AND c.relname=ANY(:tables)"
                    ),
                    {"tables": list(TABLES)},
                ).all()
            )
            assert set(table_oids) == set(TABLES)
            for role, superuser, bypass in roles:
                assert not superuser and not bypass
                assert not conn.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='switchboard' AND c.relname=ANY(:tables) AND pg_has_role(:role,c.relowner,'MEMBER'))"
                    ),
                    {"tables": list(TABLES), "role": role},
                ).scalar_one()
                if role != "butler_switchboard_rw":
                    assert not conn.execute(
                        text("SELECT pg_has_role(:role,'butler_switchboard_rw','MEMBER')"),
                        {"role": role},
                    ).scalar_one()
                conn.exec_driver_sql(f'SET ROLE "{role}"')
                try:
                    for table in TABLES:
                        assert not conn.execute(
                            text(
                                "SELECT has_table_privilege(current_user,CAST(:table_oid AS oid),'TRUNCATE')"
                            ),
                            {"table_oid": table_oids[table]},
                        ).scalar_one()
                        try:
                            count = conn.execute(
                                text(f"SELECT count(*) FROM switchboard.{table}")
                            ).scalar_one()
                            assert count == (
                                expected_counts[table] if role == "butler_switchboard_rw" else 0
                            )
                        except DBAPIError:
                            assert role != "butler_switchboard_rw"
                    if role != "butler_switchboard_rw":
                        for table in TABLES:
                            row = original_rows[table][0].copy()
                            identity = (
                                "goal_id"
                                if table == "budgets"
                                else "attempt_id"
                                if table == "admission_attempt_bindings"
                                else "id"
                            )
                            row[identity] = str(uuid4())
                            columns = ",".join(c for c in row if c != "parent_depth")
                            with pytest.raises(DBAPIError) as denied:
                                conn.exec_driver_sql(
                                    f"INSERT INTO switchboard.{table} ({columns}) SELECT {columns} "
                                    f"FROM jsonb_populate_record(NULL::switchboard.{table}, %s::jsonb)",
                                    (json.dumps(row),),
                                )
                            assert denied.value.orig.pgcode == "42501"
                        # Relationship/connector bootstrap grants cannot update RLS rows.
                        try:
                            assert (
                                conn.execute(
                                    text("UPDATE switchboard.goals SET status='stopped'")
                                ).rowcount
                                == 0
                            )
                        except DBAPIError:
                            pass
                finally:
                    conn.exec_driver_sql("RESET ROLE")
    finally:
        engine.dispose()


def test_goal_ledger_existing_full_backup_restores_losslessly(
    ledger_url, postgres_container, tmp_path
):
    asyncio.run(populate(ledger_url))
    bootstrap_again(postgres_container, ledger_url)
    before = snapshot(ledger_url)
    parsed = urlparse(ledger_url)
    password_env = {**os.environ, "PGPASSWORD_FOR_TEST": parsed.password or ""}
    base = [
        "docker",
        "run",
        "--rm",
        "--add-host=host.docker.internal:host-gateway",
        "-e",
        "PGPASSWORD_FOR_TEST",
    ]
    backup = subprocess.run(
        [
            *base,
            "-v",
            f"{ROOT / 'deploy/backup/pg_dump.sh'}:/backup.sh:ro",
            "-v",
            f"{tmp_path}:/backups",
            "-e",
            "POSTGRES_HOST=host.docker.internal",
            "-e",
            f"POSTGRES_PORT={postgres_container.get_exposed_port(5432)}",
            "-e",
            f"POSTGRES_USER={parsed.username}",
            "-e",
            f"POSTGRES_DB={parsed.path[1:]}",
            "-e",
            "BACKUP_DIR=/backups",
            "postgres:17",
            "sh",
            "-c",
            'POSTGRES_PASSWORD="$PGPASSWORD_FOR_TEST" sh /backup.sh',
        ],
        env=password_env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert backup.returncode == 0, backup.stderr
    files = list(tmp_path.glob("*.sql.gz"))
    assert len(files) == 1
    target = migration_db_name()
    admin = create_engine(postgres_container.get_connection_url(), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            # Same existing owner; no new membership/BYPASS/backup exclusion.
            conn.exec_driver_sql(f'CREATE DATABASE "{target}" OWNER "{parsed.username}"')
        target_url = parsed._replace(path="/" + target).geturl()
        bootstrap_again(postgres_container, target_url)
        # Offline restore uses existing disposable CONTROL to assume fenced owners;
        # dump, ledger ownership, snapshots and runtime pools stay ordinary.
        restore_identity = urlparse(migration_bootstrap_db_url(postgres_container, target))
        restore_env = {**os.environ, "PGPASSWORD_FOR_TEST": restore_identity.password or ""}
        restore = subprocess.run(
            [
                *base,
                "-v",
                f"{ROOT / 'scripts/pg_restore.sh'}:/restore.sh:ro",
                "-v",
                f"{files[0]}:/backup.sql.gz:ro",
                "postgres:17",
                "bash",
                "-c",
                'export POSTGRES_PASSWORD="$PGPASSWORD_FOR_TEST"; bash /restore.sh /backup.sql.gz '
                f"--host host.docker.internal --port {postgres_container.get_exposed_port(5432)} "
                f"--user {restore_identity.username} --target-db {target}",
            ],
            env=restore_env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert restore.returncode == 0, restore.stderr
        assert snapshot(target_url) == before

        # Retain runtime enforcement, not merely policy catalog parity.
        async def restored_read():
            async with pools(target_url) as (owner, runtime):
                assert await runtime.fetchval("SELECT count(*) FROM switchboard.goals") == len(
                    before[0]["goals"]
                )
                with pytest.raises(
                    asyncpg.RaiseError, match="Goal authority and source association are immutable"
                ):
                    await runtime.execute("UPDATE switchboard.goals SET ceiling='[]'")

        asyncio.run(restored_read())
    finally:
        with admin.connect() as conn:
            conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{target}" WITH (FORCE)')
        admin.dispose()


def test_goal_ledger_downgrade_refuses_durable_history(ledger_url, postgres_container):
    asyncio.run(populate(ledger_url))
    before = snapshot(ledger_url)
    config = _build_alembic_config(ledger_url, ["switchboard"], target_schema="switchboard")
    with pytest.raises(DBAPIError, match="Cannot discard"):
        command.downgrade(
            config, "switchboard@sw_046"
        )  # pinned-revision: owns sw_047 rollback boundary
    assert snapshot(ledger_url) == before

    # Bound this lifecycle to the owning revision: future forward-only
    # migrations must not silently become part of sw_051's rollback proof.
    reason_url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        ["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
        revisions={"switchboard": "sw_051"},  # pinned-revision: owns reason lifecycle
    )
    asyncio.run(populate(reason_url))
    before = snapshot(reason_url)
    config = _build_alembic_config(reason_url, ["switchboard"], target_schema="switchboard")

    # This forward revision owns only the reason vocabulary. Existing durable
    # rows may downgrade to its predecessor and re-upgrade without other DDL.
    command.downgrade(config, "switchboard@sw_050")  # pinned-revision: sw_051 predecessor
    prior = snapshot(reason_url)
    assert prior[:2] == before[:2]

    def reasons(state):
        return next(row[2] for row in state[2] if row[1] == "admission_decisions_reason_check")

    def other_constraints(state):
        return [row for row in state[2] if row[1] != "admission_decisions_reason_check"]

    old_reasons = {
        "source_evidence_unavailable",
        "limits_unavailable",
        "cause_invalid",
        "creator_unavailable",
        "causal_chain_limit",
        "reply_joined",
        "quota_exhausted",
        "new_goal",
    }
    assert set(re.findall(r"'([^']*)'", reasons(prior))) == old_reasons
    assert other_constraints(prior) == other_constraints(before)
    engine = create_engine(reason_url)
    insert = text(
        "INSERT INTO switchboard.admission_decisions "
        "(id,attempt_id,source_key,source_ref,source_kind,result,reason) "
        "VALUES (:id,:attempt,:source,:source,'message','refused',:reason)"
    )

    def insert_reason(reason):
        with engine.begin() as conn:
            conn.execute(
                insert,
                {"id": uuid4(), "attempt": uuid4(), "source": str(uuid4()), "reason": reason},
            )

    try:
        for reason in sorted(old_reasons):
            insert_reason(reason)
        for invalid in ("recorded_without_goal", "unknown_reason"):
            with pytest.raises(DBAPIError) as refused:
                insert_reason(invalid)
            assert refused.value.orig.diag.constraint_name == "admission_decisions_reason_check"
        prior = snapshot(reason_url)
        command.upgrade(config, "switchboard@sw_051")
        upgraded = snapshot(reason_url)
        assert upgraded[:2] == prior[:2]
        assert other_constraints(upgraded) == other_constraints(prior)
        assert set(re.findall(r"'([^']*)'", reasons(upgraded))) == old_reasons | {
            "recorded_without_goal"
        }
        # Repeat the actual installer under an owning transaction as well as
        # Alembic's already-current path: constraint replacement is idempotent.
        import importlib.util

        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        spec = importlib.util.spec_from_file_location(
            "record_only_reason",
            ROOT / "roster/switchboard/migrations/051_record_only_admission_reason.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
            module.upgrade()
        assert snapshot(reason_url) == upgraded
        with pytest.raises(DBAPIError) as refused:
            insert_reason("unknown_reason")
        assert refused.value.orig.diag.constraint_name == "admission_decisions_reason_check"

        async def record_without_goal():
            async with pools(reason_url) as (owner, runtime):
                goals = await owner.fetchval("SELECT count(*) FROM switchboard.goals")
                budgets = await owner.fetchval("SELECT count(*) FROM switchboard.budgets")
                source = SourceRef("feed", str(uuid4()))
                facts = Evidence("feed", "migration-feed", record_only=True)
                recorded = await admit(runtime, facts, source)
                assert recorded.result == "refused" and recorded.reason == "recorded_without_goal"
                assert await admit(runtime, facts, source) == recorded
                assert await owner.fetchval("SELECT count(*) FROM switchboard.goals") == goals
                assert await owner.fetchval("SELECT count(*) FROM switchboard.budgets") == budgets

        asyncio.run(record_without_goal())
        durable = snapshot(reason_url)
        with pytest.raises(DBAPIError, match="Cannot discard durable record-only"):
            command.downgrade(
                config, "switchboard@sw_050"
            )  # pinned-revision: sw_051 rollback fence
        assert snapshot(reason_url) == durable
        command.upgrade(config, "switchboard@sw_051")
        assert snapshot(reason_url) == durable
    finally:
        engine.dispose()
