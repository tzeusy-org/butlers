"""REQ-endpoint-custody-holds-002: real installed identity and acquired writers.

These controls use migrated disposable PostgreSQL, real SET ROLE identities and
production startup. They do not claim native canonical writer/MCP/topology or
complete command/source behavior, which require additional positioned controls.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlparse

import asyncpg
import pytest
from fastmcp import Client, FastMCP

from alembic import command
from butlers.config import load_config
from butlers.core.custody_admission import CustodyProfile
from butlers.core.custody_bindings import owning_binding_publisher
from butlers.core.custody_bootstrap import CustodyRuntime
from butlers.core.custody_control import CustodyControlTransport, host_profile, prepare_host_source
from butlers.core.custody_installed import verify_installed_functions
from butlers.core.custody_lifecycle import daemon_profile
from butlers.core.custody_source import CustodyError, digest, utc_timestamp
from butlers.db import Database, register_jsonb_codec
from butlers.migrations import _build_alembic_config, get_chain_revision_ids
from butlers.testing.migration import (
    _bootstrap_migration_prerequisites,
    create_migration_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def custody_database_urls(postgres_container):
    name = migration_db_name()
    ordinary = create_migration_db(postgres_container, name)
    for schema, chains in (
        ("relationship", ("core", "memory", "relationship")),
        ("switchboard", ("core", "switchboard")),
    ):
        for chain in chains:
            command.upgrade(
                _build_alembic_config(ordinary, chains=[chain], target_schema=schema),
                f"{chain}@head",
            )
    bootstrap = migration_bootstrap_db_url(postgres_container, name)
    # This is the genuine post-migration broad bootstrap replay. The private
    # feature ACL/RLS finalizer must remain effective after its public regrants.
    _bootstrap_migration_prerequisites(bootstrap, urlparse(ordinary).username)
    return ordinary, urlparse(bootstrap)._replace(scheme="postgresql").geturl()


def _database(url: str, actor: str) -> Database:
    parsed = urlparse(url)
    return Database(
        db_name=unquote(parsed.path[1:]),
        schema=actor,
        role=f"butler_{actor}_rw",
        host=parsed.hostname,
        port=parsed.port,
        user=unquote(parsed.username),
        password=unquote(parsed.password),
        ssl="disable",
        strict_role_enforcement=True,
        min_pool_size=1,
        max_pool_size=2,
    )


def _profile(actor: str) -> CustodyProfile:
    families = ("domain_evidence",)
    operations = ("write",)
    return CustodyProfile(
        actor,
        f"butler_{actor}_rw",
        families,
        operations,
        (actor,),
        digest({"actor": actor, "purpose": "owning-writer-control"}),
    )


def _observe_first_anchor_renew(monkeypatch, admin, database):
    """Observe the original first call without changing its closed outcome.

    No error text, query arguments, identities or connection strings are emitted.
    Catalog reads use only the existing disposable test bootstrap connection;
    they confer no production permission and never retry or replace renewal.
    """
    original = asyncpg.Connection.fetchval
    observed = False

    async def fetchval(connection, statement, *values, **kwargs):
        nonlocal observed
        if statement != "SELECT public.custody_anchor_renew()" or observed:
            return await original(connection, statement, *values, **kwargs)
        observed = True
        facts = {"snapshot_available": False}
        try:
            own = await original(
                connection,
                "SELECT jsonb_build_object('physical_backend',pg_backend_pid()=$1,"
                "'effective_role',current_user=$2,'database',current_database()=$3)",
                connection.get_server_pid(),
                database.role,
                database.db_name,
            )
            enrolled = await original(
                admin,
                """
                SELECT jsonb_build_object(
                    'enrolled',count(p.process_id)=1,
                    'backend_birth',bool_and(p.anchor_backend_start=a.backend_start),
                    'database_binding',bool_and(p.database_oid=a.datid),
                    'login_binding',bool_and(p.login_oid=a.usesysid),
                    'role_binding',bool_and(p.role_oid=r.oid),
                    'control_epoch',bool_and(p.control_epoch=c.control_epoch),
                    'restore_epoch',bool_and(p.restore_epoch=c.restore_epoch),
                    'lease_current',bool_and(p.lease_expires_at>clock_timestamp()),
                    'not_revoked',bool_and(p.revoked_at IS NULL),
                    'control_ready',bool_and(c.admission_state='ready'))
                FROM custody_admission.processes p
                JOIN pg_stat_activity a ON a.pid=p.anchor_pid
                JOIN custody_admission.control c ON c.singleton
                JOIN pg_roles r ON r.rolname=$2
                WHERE p.anchor_pid=$1
                """,
                connection.get_server_pid(),
                database.role,
            )
            keys = (
                "physical_backend",
                "effective_role",
                "database",
                "enrolled",
                "backend_birth",
                "database_binding",
                "login_binding",
                "role_binding",
                "control_epoch",
                "restore_epoch",
                "lease_current",
                "not_revoked",
                "control_ready",
            )
            combined = own | enrolled
            facts = {key: combined[key] if type(combined[key]) is bool else None for key in keys}
            facts["snapshot_available"] = True
        except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, KeyError, TypeError):
            # A missing diagnostic snapshot remains unknown. The original
            # renewal still runs once and its actual error remains authoritative.
            pass
        try:
            return await original(connection, statement, *values, **kwargs)
        except asyncpg.PostgresError as exc:
            allowed = {
                "InsufficientPrivilegeError",
                "UndefinedColumnError",
                "UndefinedTableError",
                "UndefinedFunctionError",
                "AmbiguousColumnError",
                "DatatypeMismatchError",
                "InvalidTextRepresentationError",
                "SerializationError",
                "InternalServerError",
                "ObjectNotInPrerequisiteStateError",
                "CheckViolationError",
            }
            state = exc.sqlstate
            report = facts | {
                "sqlstate": state
                if isinstance(state, str) and re.fullmatch(r"[A-Z0-9]{5}", state)
                else "unknown",
                "error_class": type(exc).__name__
                if type(exc).__name__ in allowed
                else "PostgresError",
                "result_category": "original_anchor_renew_failed",
            }
            print("CUSTODY_FIRST_ANCHOR_RENEW " + json.dumps(report, sort_keys=True))
            raise

    monkeypatch.setattr(asyncpg.Connection, "fetchval", fetchval)


async def test_installed_schema_real_roles_and_same_acquired_writer(
    custody_database_urls, tmp_path, monkeypatch
):
    ordinary_url, bootstrap_url = custody_database_urls
    async with AsyncExitStack() as stack:
        admin = await asyncpg.connect(bootstrap_url)
        stack.push_async_callback(admin.close)
        await register_jsonb_codec(admin)
        await admin.execute("SELECT custody_admission.install_interface()")
        # Existing production bootstrap preserves normal runtime LOGIN roles.
        # LOGIN is not enrollment, source authority or a bound writer. This
        # positive reaches the exact prover that previously rejected all real
        # normal migrated fixtures; the real denials below remain mandatory.
        assert await admin.fetchval(
            "SELECT rolcanlogin FROM pg_roles WHERE rolname='butler_relationship_rw'"
        )
        proof = await admin.fetchval("SELECT custody_admission.prove_interface()")
        verify_installed_functions(proof)
        await admin.execute("SELECT custody_admission.install_interface()")
        verify_installed_functions(
            await admin.fetchval("SELECT custody_admission.prove_interface()")
        )

        # Every mutation is reached in real PostgreSQL, restores by actual
        # rollback, and has the intact-schema positive after restoration.
        for mutation in (
            "ALTER TABLE custody_admission.processes ALTER COLUMN lease_expires_at DROP NOT NULL",
            "ALTER TABLE custody_admission.connections ALTER COLUMN acquisition_generation SET DEFAULT 9",
            "ALTER TABLE custody_admission.targets DROP CONSTRAINT targets_generation_check",
            "ALTER TABLE custody_admission.connections DROP CONSTRAINT connections_verified_call_id_fkey",
            "DROP INDEX public.custody_one_active_target",
            "ALTER TABLE public.custody_holds DISABLE ROW LEVEL SECURITY",
        ):
            transaction = admin.transaction()
            await transaction.start()
            try:
                await admin.execute(mutation)
                with pytest.raises(
                    asyncpg.InsufficientPrivilegeError, match="schema drift|installed authority"
                ):
                    async with admin.transaction():
                        await admin.fetchval("SELECT custody_admission.prove_interface()")
                # The installer must refuse repair/adoption of the damaged
                # structure rather than overwrite its recorded identity.
                with pytest.raises(asyncpg.InsufficientPrivilegeError, match="schema drift"):
                    async with admin.transaction():
                        await admin.execute("SELECT custody_admission.install_interface()")
            finally:
                await transaction.rollback()
            verify_installed_functions(
                await admin.fetchval("SELECT custody_admission.prove_interface()")
            )

        # Empty shared-schema rollback is idempotent and disables admission.
        # Neither arbitrary drift nor a revoked/recorded state can be adopted.
        identity = await admin.fetchval(
            "SELECT schema_identity FROM custody_admission.bootstrap_configuration WHERE singleton"
        )
        for mutation in (
            "ALTER TABLE custody_admission.targets ADD COLUMN unexpected integer",
            "ALTER TABLE custody_admission.targets DROP CONSTRAINT targets_generation_check",
            "ALTER POLICY custody_bootstrap_engine ON public.custody_holds USING (true)",
            "ALTER TABLE custody_admission.targets OWNER TO butler_relationship_rw",
            "INSERT INTO custody_admission.provider_inventory(owner_entity_id,provider,version,declared_contributors) "
            "VALUES (gen_random_uuid(),'fixture',1,'[]'::jsonb)",
        ):
            transaction = admin.transaction()
            await transaction.start()
            try:
                await admin.execute("SELECT custody_admission.rollback_interface()")
                await admin.execute(
                    "ALTER TABLE custody_admission.case_scopes DROP CONSTRAINT case_scopes_case_id_fkey"
                )
                await admin.execute(mutation)
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    async with admin.transaction():
                        await admin.execute("SELECT custody_admission.install_interface()")
            finally:
                await transaction.rollback()
            assert await admin.fetchval("SELECT custody_admission.schema_identity()") == identity

        # A missing FK while admission is ready is not an authorized rollback.
        transaction = admin.transaction()
        await transaction.start()
        try:
            await admin.execute(
                "ALTER TABLE custody_admission.case_scopes DROP CONSTRAINT case_scopes_case_id_fkey"
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with admin.transaction():
                    await admin.execute("SELECT custody_admission.install_interface()")
        finally:
            await transaction.rollback()

        # Neutralize only the fixed missing-FK repair in its real SQL body.
        # Reaching this installer refusal positions the control at the repair,
        # rather than failing on a missing helper or before migration setup.
        source_sql = (Path(__file__).resolve().parents[2] / "scripts/init-db.sql").read_text()
        start = source_sql.index("CREATE OR REPLACE FUNCTION custody_admission.install_interface()")
        end = source_sql.index("$custody_install$;", start) + len("$custody_install$;")
        installer_sql = source_sql[start:end]
        repair_start = installer_sql.index(
            "    ELSIF prior_identity IS DISTINCT FROM custody_admission.schema_identity() THEN"
        )
        repair_end = installer_sql.index("    EXECUTE $custody_tables$", repair_start)
        neutralized = (
            installer_sql[:repair_start]
            + "    ELSIF prior_identity IS DISTINCT FROM custody_admission.schema_identity() THEN\n"
            + "        RAISE EXCEPTION 'custody installed schema drift' USING ERRCODE='42501';\n"
            + "    END IF;\n"
            + installer_sql[repair_end:]
        )
        assert neutralized != installer_sql
        transaction = admin.transaction()
        await transaction.start()
        try:
            await admin.execute("SELECT custody_admission.rollback_interface()")
            await admin.execute(
                "ALTER TABLE custody_admission.case_scopes DROP CONSTRAINT case_scopes_case_id_fkey"
            )
            await admin.execute(neutralized)
            with pytest.raises(asyncpg.InsufficientPrivilegeError, match="installed schema drift"):
                async with admin.transaction():
                    await admin.execute("SELECT custody_admission.install_interface()")
        finally:
            await transaction.rollback()
        verify_installed_functions(
            await admin.fetchval("SELECT custody_admission.prove_interface()")
        )

        # Capture genuine installed foreign version rows before touching core.
        # The separate committed readback below must retain those exact rows.
        core_ids = get_chain_revision_ids("core")
        foreign_versions = {}
        for schema in ("relationship", "switchboard"):
            rows = await admin.fetch(f"SELECT version_num FROM {schema}.alembic_version")
            foreign_versions[schema] = {row["version_num"] for row in rows} - core_ids
            assert foreign_versions[schema]  # A core-only fixture would be vacuous.

        # The existing actual deep lifecycle, not a hand-copied fleet schema,
        # drops the public fleet table and its incoming private FK through217.
        for schema in ("relationship", "switchboard"):
            command.downgrade(
                _build_alembic_config(ordinary_url, chains=["core"], target_schema=schema),
                "core_215",
            )
        for schema, original_versions in foreign_versions.items():
            rows = await admin.fetch(f"SELECT version_num FROM {schema}.alembic_version")
            assert {row["version_num"] for row in rows} - core_ids == original_versions
        assert (
            await admin.fetchval(
                "SELECT admission_state FROM custody_admission.control WHERE singleton"
            )
            == "unavailable"
        )
        assert not await admin.fetchval(
            "SELECT EXISTS(SELECT FROM pg_constraint WHERE conrelid="
            "'custody_admission.case_scopes'::regclass AND conname='case_scopes_case_id_fkey')"
        )
        for schema in ("relationship", "switchboard"):
            command.upgrade(
                _build_alembic_config(ordinary_url, chains=["core"], target_schema=schema),
                "core@head",
            )
        # A separate acquisition reads committed installer/control/schema state.
        readback = await asyncpg.connect(bootstrap_url)
        try:
            await register_jsonb_codec(readback)
            for schema, original_versions in foreign_versions.items():
                rows = await readback.fetch(f"SELECT version_num FROM {schema}.alembic_version")
                assert {row["version_num"] for row in rows} - core_ids == original_versions
            assert await readback.fetchval("SELECT custody_admission.schema_identity()") == identity
            assert (
                await readback.fetchval(
                    "SELECT schema_identity FROM custody_admission.bootstrap_configuration WHERE singleton"
                )
                == identity
            )
            assert (
                await readback.fetchval(
                    "SELECT admission_state FROM custody_admission.control WHERE singleton"
                )
                == "ready"
            )
            verify_installed_functions(
                await readback.fetchval("SELECT custody_admission.prove_interface()")
            )
        finally:
            await readback.close()

        # Explicit revoked state never reopens through installer or rollback.
        transaction = admin.transaction()
        await transaction.start()
        try:
            await admin.execute(
                "UPDATE custody_admission.control SET admission_state='revoked' WHERE singleton"
            )
            for operation in ("install_interface", "rollback_interface"):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    async with admin.transaction():
                        await admin.execute(f"SELECT custody_admission.{operation}()")
            assert (
                await admin.fetchval(
                    "SELECT admission_state FROM custody_admission.control WHERE singleton"
                )
                == "revoked"
            )
        finally:
            await transaction.rollback()

        database = _database(ordinary_url, "relationship")
        await database.connect()
        stack.push_async_callback(database.close)
        runtime = CustodyRuntime(database, _profile("relationship"))
        with monkeypatch.context() as first_renew_observer:
            _observe_first_anchor_renew(first_renew_observer, admin, database)
            admission = await runtime.start()
        stack.push_async_callback(runtime.stop)
        # An actually enrolled allocation is evidence: rollback cannot erase or
        # reopen it. The failed savepoint leaves the committed enrollment intact.
        enrolled_before = await admin.fetchval("SELECT count(*) FROM custody_admission.processes")
        assert enrolled_before > 0
        with pytest.raises(asyncpg.InsufficientPrivilegeError, match="populated rollback"):
            async with admin.transaction():
                await admin.execute("SELECT custody_admission.rollback_interface()")
        assert (
            await admin.fetchval("SELECT count(*) FROM custody_admission.processes")
            == enrolled_before
        )
        assert (
            await admin.fetchval(
                "SELECT admission_state FROM custody_admission.control WHERE singleton"
            )
            == "ready"
        )
        assert runtime.channel_bindings is not None
        assert owning_binding_publisher(database.pool) is runtime.channel_bindings
        assert runtime.accepted_ingress is None
        assert await database.pool.fetchval("SELECT current_user") == "butler_relationship_rw"
        assert not await database.pool.fetchval(
            "SELECT rolsuper OR rolcreaterole OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await database.pool.fetchval("SELECT count(*) FROM custody_admission.processes")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await database.pool.execute("UPDATE public.custody_holds SET reason='lost' WHERE false")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await database.pool.fetchval("SELECT count(*) FROM public.custody_holds")

        # Pool presence/default role does not constitute two-sided writer bind.
        async with database.pool.acquire() as connection:
            begin = await connection.fetchval("SELECT public.custody_connection_begin()")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await connection.fetchval(
                    "SELECT public.custody_connection_finish($1)", str(begin["writer_nonce"])
                )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await connection.fetchval(
                    "SELECT public.custody_connection_unbind($1)",
                    begin["acquisition_generation"] + 1,
                )
            cleanup = await connection.fetchval(
                "SELECT public.custody_connection_unbind($1)", begin["acquisition_generation"]
            )
            assert cleanup["unbound"] is True
            # A real guarded acquisition can establish and finish its own new
            # generation after this failed inert proposal.
            async with admission.bound_writer(connection) as writer:
                assert writer._connection is connection
                assert admission.owns_writer(writer)
                with pytest.raises(CustodyError, match="conflict"):
                    async with admission.bound_writer(connection):
                        pytest.fail("nested acquisition cannot establish first-lock order")
                generation = writer.generation
                private = await admin.fetchrow(
                    "SELECT state,finished_at FROM custody_admission.connections "
                    "WHERE backend_pid=$1 AND acquisition_generation=$2",
                    connection.get_server_pid(),
                    generation,
                )
                # Uncommitted finish is not yet visible to an independent
                # acquisition. The public inert begin/bind committed earlier.
                assert private["state"] == "bound" and private["finished_at"] is None
            assert not admission.owns_writer(writer)
            committed = await admin.fetchrow(
                "SELECT state,finished_at FROM custody_admission.connections "
                "WHERE backend_pid=$1 AND acquisition_generation=$2",
                connection.get_server_pid(),
                generation,
            )
            assert committed["state"] == "released" and committed["finished_at"] is not None

        # The same acquired writer's domain change must roll back on failure;
        # observe absence using another acquisition, plus a committed positive.
        key = "custody-control-" + uuid.uuid4().hex
        with pytest.raises(CustodyError, match="refused"):
            async with admission.writer() as writer:
                await writer._connection.execute(
                    "INSERT INTO relationship.state(key,value) VALUES($1,$2::jsonb)",
                    key,
                    {"marker": "rollback"},
                )
                raise CustodyError("refused")
        assert (
            await database.pool.fetchval("SELECT value FROM relationship.state WHERE key=$1", key)
            is None
        )
        async with admission.writer() as writer:
            await writer._connection.execute(
                "INSERT INTO relationship.state(key,value) VALUES($1,$2::jsonb)",
                key,
                {"marker": "committed"},
            )
        assert await database.pool.fetchval(
            "SELECT value FROM relationship.state WHERE key=$1", key
        ) == {"marker": "committed"}
        await database.pool.execute("DELETE FROM relationship.state WHERE key=$1", key)
        assert (
            await database.pool.fetchval("SELECT value FROM relationship.state WHERE key=$1", key)
            is None
        )

        # Explicit legacy no-schema profile still proves its existing owning
        # role, acquired writer and source registration in real PostgreSQL.
        # This checks profile/pool composition, not whole daemon/CLI topology.
        legacy_path = tmp_path / "legacy-relationship"
        legacy_path.mkdir()
        (legacy_path / "butler.toml").write_text(
            '[butler]\nname="relationship"\nport=19102\n[butler.db]\nname="custody-legacy-config"\n'
        )
        legacy_config = load_config(legacy_path)
        assert legacy_config.db_schema is None
        legacy_db = _database(ordinary_url, "relationship")
        legacy_db.set_schema(None)
        await legacy_db.connect()
        legacy_runtime = CustodyRuntime(
            legacy_db, daemon_profile(SimpleNamespace(config=legacy_config, db=legacy_db))
        )
        try:
            legacy_admission = await legacy_runtime.start()
            assert await legacy_db.pool.fetchval("SELECT current_user") == "butler_relationship_rw"
            assert not legacy_db.role_enforcement_disabled
            async with legacy_admission.writer() as legacy_writer:
                legacy_observation = await legacy_writer.register_source(
                    "domain_evidence",
                    {
                        "locator": "evidence:" + str(uuid.uuid4()),
                        "revision": 1,
                        "content_digest": digest({"kind": "real-legacy-pool"}),
                        "origin_digest": digest({"kind": "legacy-owning-observation"}),
                        "owner_entity_id": None,
                        "issuer_target": None,
                        "target_set": [],
                        "target_set_version": 1,
                        "expires_at": utc_timestamp(datetime.now(UTC) + timedelta(minutes=1)),
                    },
                )
                assert legacy_observation["source_ref"]
        finally:
            await legacy_runtime.stop()
            assert owning_binding_publisher(legacy_db.pool) is None
            await legacy_db.close()

        # Genuine trusted-host command producer -> actual registered FastMCP
        # middleware/tool -> same restricted receiver writer -> durable SQL.
        # This is not an accepted-ingress/native-writer/topology proof.
        owner = await admin.fetchval(
            "INSERT INTO public.entities(canonical_name,entity_type,roles) "
            "VALUES('custody-control-owner','person',ARRAY['owner']) RETURNING id"
        )
        switchboard_db = _database(ordinary_url, "switchboard")
        await switchboard_db.connect()
        stack.push_async_callback(switchboard_db.close)
        default_config_path = tmp_path / "default-switchboard"
        default_config_path.mkdir()
        (default_config_path / "butler.toml").write_text(
            '[butler]\nname="switchboard"\nport=19101\n'
        )
        default_config = load_config(default_config_path)
        assert default_config.db_name == "butlers" and default_config.db_schema == "switchboard"
        receiver_runtime = CustodyRuntime(
            switchboard_db,
            daemon_profile(SimpleNamespace(config=default_config, db=switchboard_db)),
        )
        receiver = await receiver_runtime.start()
        stack.push_async_callback(receiver_runtime.stop)
        host_runtime = CustodyRuntime(
            switchboard_db, host_profile(digest({"kind": "host-control"}))
        )
        host = await host_runtime.start()
        stack.push_async_callback(host_runtime.stop)
        assert receiver.profile.actor == "switchboard"
        assert host.profile.actor == "host-switchboard"
        assert receiver.profile.role == host.profile.role == "butler_switchboard_rw"
        assert host_runtime.accepted_ingress is None
        target = {
            "target_id": str(uuid.uuid4()),
            "target_kind": "endpoint",
            "binding_digest": digest({"kind": "test-owned-endpoint", "owner": str(owner)}),
            "binding_version": 1,
            "generation": 0,
        }
        # Real source-owning bootstrap fixture allocated through the registered
        # public source interface. No direct trusted source object/row is planted.
        async with receiver.writer() as writer:
            await writer.register_source(
                "domain_evidence",
                {
                    "locator": "evidence:" + str(uuid.uuid4()),
                    "revision": 1,
                    "content_digest": digest(target),
                    "origin_digest": digest({"fixture": "owning-target"}),
                    "owner_entity_id": str(owner),
                    "issuer_target": None,
                    "target_set": [target],
                    "target_set_version": 1,
                    "expires_at": utc_timestamp(datetime.now(UTC) + timedelta(minutes=5)),
                },
            )
        command_source = await prepare_host_source(
            host_runtime,
            "hold",
            {"target_set": [target], "target_set_version": 1, "reason": "lost"},
        )
        mcp = FastMCP("custody-real-owning-control")
        service = receiver_runtime.attach_mcp(mcp)
        # Real definitions use the same constructor-retained service as daemon
        # dispatch. The network/deployment paths are separately required.
        from butlers.core_tools._base import ToolContext
        from butlers.core_tools._custody import register_custody_tools

        register_custody_tools(
            ToolContext(
                daemon=SimpleNamespace(_custody_mcp_service=service),
                pool=switchboard_db.pool,
                spawner=None,
                butler_name="switchboard",
                butler_type=None,
                is_switchboard=True,
                is_messenger=False,
                route_metrics=None,
            ),
            mcp,
            lambda function: function,
        )
        transport = CustodyControlTransport(host, Client(mcp))
        # Genuine prepared source, live issuer/receiver, armed exact call and
        # bound owning writer still cannot bypass the registered online guard.
        unguarded_call = await host.mint(
            command_source.source_ref,
            {
                "mint_request_id": str(uuid.uuid4()),
                "operation": "hold",
                "method": "custody.commit",
                "arguments": {"command_id": str(command_source.command_id)},
                "command_id": str(command_source.command_id),
                "target_set": [target],
                "target_set_version": 1,
            },
            "switchboard",
            source_digest=command_source.source_digest,
        )
        unguarded_id = uuid.UUID(unguarded_call["call_ref"])
        unguarded_challenge = await receiver.challenge(
            unguarded_id, unguarded_call["operation_digest"]
        )
        await host.respond(unguarded_id, unguarded_challenge["challenge_ref"])
        with pytest.raises(CustodyError, match="refused"):
            async with receiver.writer() as unverified_writer:
                await unverified_writer.commit_command(command_source.command_id, unguarded_id)
        assert await admin.fetchval("SELECT count(*) FROM public.custody_holds") == 0
        assert await admin.fetchval("SELECT count(*) FROM custody_admission.receipts") == 0
        result = await transport.execute(command_source)
        assert result["status"] == "committed" and result["containment"] == "held"
        assert result["command_id"] == str(command_source.command_id)
        receipt = await admin.fetchval(
            "SELECT result FROM custody_admission.receipts WHERE command_id=$1",
            command_source.command_id,
        )
        assert result == receipt
        assert (
            await admin.fetchval(
                "SELECT count(*) FROM public.custody_holds WHERE source_command_id=$1 AND released_at IS NULL",
                command_source.command_id,
            )
            == 1
        )
        assert (
            await admin.fetchval(
                "SELECT count(*) FROM public.fleet_case_evidence WHERE ref=$1 AND kind='custody_recovery'",
                str(command_source.command_id),
            )
            == 1
        )
        # ACL refusal alone does not prove RLS. The actual committed hold is
        # a planted positive, and temporarily disabling the policy makes that
        # SAME role/read find it. All fixture-only mutations restore exactly.
        await admin.execute("GRANT SELECT ON public.custody_holds TO butler_relationship_rw")
        try:
            assert await admin.fetchval("SELECT count(*) FROM public.custody_holds") == 1
            assert await database.pool.fetchval("SELECT count(*) FROM public.custody_holds") == 0
            await admin.execute("ALTER TABLE public.custody_holds DISABLE ROW LEVEL SECURITY")
            assert await database.pool.fetchval("SELECT count(*) FROM public.custody_holds") == 1
        finally:
            await admin.execute("ALTER TABLE public.custody_holds ENABLE ROW LEVEL SECURITY")
            await admin.execute("ALTER TABLE public.custody_holds FORCE ROW LEVEL SECURITY")
            await admin.execute("SELECT custody_admission.finalize_interface()")
        verify_installed_functions(
            await admin.fetchval("SELECT custody_admission.prove_interface()")
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await database.pool.fetchval("SELECT count(*) FROM public.custody_holds")

        original_selection = await admin.fetchval(
            "SELECT selection FROM custody_admission.commands WHERE command_id=$1",
            command_source.command_id,
        )

        # Expire the original authorization only; its receipt/ID/generations
        # survive. Direct old-source read must actually refuse before a new
        # current host ticket successfully reads the SAME original result.
        await admin.execute(
            "UPDATE custody_admission.commands SET expires_at=clock_timestamp()-interval '1 second' "
            "WHERE command_id=$1",
            command_source.command_id,
        )
        with pytest.raises(CustodyError, match="refused"):
            await transport.execute(command_source, read_only=True)
        read_selection = {
            "target_set": [],
            "target_set_version": 1,
            "result_command_id": str(command_source.command_id),
        }
        read_source = await prepare_host_source(host_runtime, "eligibility", read_selection)
        assert read_source.command_id != command_source.command_id
        assert read_source.result_command_id == command_source.command_id
        with pytest.raises(CustodyError, match="refused"):
            await transport.execute(read_source)
        assert await transport.execute(read_source, read_only=True) == receipt
        assert (
            await admin.fetchval(
                "SELECT selection FROM custody_admission.commands WHERE command_id=$1",
                command_source.command_id,
            )
            == original_selection
        )
        assert await admin.fetchval("SELECT count(*) FROM custody_admission.receipts") == 1
        assert await admin.fetchval("SELECT count(*) FROM public.custody_holds") == 1
        for wrong_id in (uuid.uuid4(), read_source.command_id):
            with pytest.raises(CustodyError, match="refused"):
                await host.mint(
                    read_source.source_ref,
                    {
                        "mint_request_id": str(uuid.uuid4()),
                        "operation": "eligibility",
                        "method": "custody.result",
                        "arguments": {"command_id": str(wrong_id)},
                        "command_id": str(wrong_id),
                        "target_set": [],
                        "target_set_version": 1,
                    },
                    "switchboard",
                    source_digest=read_source.source_digest,
                )

        # A second genuinely enrolled receiver is a causal ambiguity negative,
        # not a mocked destination. Host source remains a distinct incarnation.
        overlap_db = _database(ordinary_url, "switchboard")
        await overlap_db.connect()
        stack.push_async_callback(overlap_db.close)
        overlap = CustodyRuntime(overlap_db, _profile("switchboard"))
        await overlap.start()
        try:
            with pytest.raises(CustodyError, match="refused"):
                await transport.execute(read_source, read_only=True)
        finally:
            await overlap.stop()
        assert await transport.execute(read_source, read_only=True) == receipt
        # Revoke the fresh read authorization after prepare; existing private
        # source/locator and live daemon cannot bypass this currentness check.
        await admin.execute(
            "UPDATE custody_admission.commands SET expires_at=clock_timestamp()-interval '1 second' "
            "WHERE command_id=$1",
            read_source.command_id,
        )
        with pytest.raises(CustodyError, match="refused"):
            await transport.execute(read_source, read_only=True)
        fresh_read = await prepare_host_source(host_runtime, "eligibility", read_selection)
        assert await transport.execute(fresh_read, read_only=True) == receipt

        # Component contract: the actual native fact writer + SAME acquired
        # mutation adapter publishes current canonical mapping; real ingest_v1
        # persists the accepted source. The target inventory above is still a
        # bounded fixture, not native provider-generation/topology evidence.
        from butlers.tools.relationship.relationship_assert_fact import (
            relationship_assert_fact,
            retract_contact_info_fact,
        )
        from butlers.tools.switchboard.ingestion.ingest import ingest_v1

        sender = "custody-surviving@example.test"
        bindings = runtime.channel_bindings
        # Actual native entrypoint owns the publisher hook; no manually
        # injected mutation object wraps this positive. The trusted test host
        # seeds its owner's channel via the existing owner-bootstrap branch;
        # src is not claimed as model authentication or custody privilege.
        fact = await relationship_assert_fact(
            database.pool, owner, "has-email", sender, src="owner-bootstrap"
        )
        # Separate acquisition reads the actual committed canonical native row.
        assert (
            await database.pool.fetchval(
                "SELECT object FROM relationship.entity_facts WHERE id=$1 AND validity='active'",
                fact.fact_id,
            )
            == sender
        )
        # Real registered owning resolver, actual owning pool + canonical SQL,
        # and actual constructor publisher. This in-process MCP component is
        # not an independent run/up/Compose/Kubernetes deployment proof.
        from butlers.modules._roster_relationship import (
            RelationshipModule,
            RelationshipModuleConfig,
        )

        relation_module = RelationshipModule()
        relation_mcp = FastMCP("custody-owning-relationship")
        await relation_module.register_tools(
            relation_mcp,
            RelationshipModuleConfig(groups=["contacts"]),
            db=database,
            butler_name="relationship",
        )
        async with Client(relation_mcp) as relation_client:
            result = await relation_client.call_tool(
                "identity_resolve_channels", {"channel_type": "email", "channel_values": [sender]}
            )
        assert not result.is_error
        resolved = result.structured_content
        assert resolved[sender]["entity_id"] == str(owner)
        assert set(resolved[sender]) == {"name", "roles", "entity_id", "is_unidentified"}

        async def accepted_lock(prefix="LOCK"):
            return await ingest_v1(
                switchboard_db.pool,
                {
                    "schema_version": "ingest.v1",
                    "source": {
                        "channel": "email",
                        "provider": "gmail",
                        "endpoint_identity": "receiving-mailbox@example.test",
                    },
                    "event": {
                        "external_event_id": str(uuid.uuid4()),
                        "observed_at": datetime.now(UTC).isoformat(),
                    },
                    "sender": {"identity": sender},
                    "payload": {
                        "raw": {"body": "explicit lost endpoint"},
                        "normalized_text": prefix + " " + target["target_id"],
                    },
                    "control": {"ingestion_tier": "full", "policy_tier": "default"},
                },
                enable_thread_affinity=False,
            )

        accepted = await accepted_lock()
        ingress = receiver_runtime.accepted_ingress
        first_source = await ingress.capture_lock(accepted.request_id)
        replay = await ingress.capture_lock(accepted.request_id)
        assert replay == first_source  # Frozen expiry/digest/generations, SAME source/command.
        actual_source = await admin.fetchrow(
            "SELECT projection,expires_at FROM custody_admission.sources WHERE source_ref=$1",
            first_source.source_ref,
        )
        assert actual_source["projection"]["owner_entity_id"] == str(owner)
        assert actual_source["projection"]["selected_target_ids"] == [target["target_id"]]
        assert actual_source["projection"]["issuer_target"] != target["target_id"]
        assert actual_source["projection"]["target_set"][0]["generation"] == 1
        assert first_source.source_digest == digest(actual_source["projection"])
        accepted_at = await switchboard_db.pool.fetchval(
            "SELECT received_at FROM switchboard.message_inbox WHERE id=$1", accepted.request_id
        )
        assert actual_source["expires_at"] == accepted_at + timedelta(minutes=5)

        # Real native INSERT + separate committed metadata readback. The
        # ordinary role cannot touch or install the private birth boundary.
        birth = await admin.fetchrow(
            "SELECT * FROM custody_admission.accepted_births WHERE record_id=$1",
            accepted.request_id,
        )
        assert birth["received_at"] == accepted_at
        assert birth["content_digest"] == actual_source["projection"]["content_digest"]
        assert birth["first_process"] == uuid.UUID(receiver_runtime._receipt["process_id"])
        assert birth["retired_at"] is None
        assert (
            await admin.fetchval(
                "SELECT state FROM custody_admission.accepted_work WHERE record_id=$1",
                accepted.request_id,
            )
            == "pending"
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await switchboard_db.pool.fetchval(
                "SELECT custody_admission.accepted_work('claim',$1::jsonb)",
                {"process_id": receiver_runtime._receipt["process_id"]},
            )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await switchboard_db.pool.fetchval(
                "SELECT count(*) FROM custody_admission.accepted_work"
            )

        async def work_call(action, claim=None, **fields):
            payload = {"process_id": receiver_runtime._receipt["process_id"]}
            if claim is not None:
                payload.update(record_id=claim["record_id"], claim_ref=claim["claim_ref"])
            return await admin.fetchval(
                "SELECT custody_admission.accepted_work($1,$2::jsonb)", action, payload | fields
            )

        first_claim = await work_call("claim")
        assert first_claim["record_id"] == str(accepted.request_id)
        assert first_claim["source_ref"] is None
        assert await work_call(
            "prepared", first_claim, source_ref=str(first_source.source_ref)
        ) == {
            "status": "prepared",
            "command_id": str(first_source.command_id),
        }
        # Simulate an actual expired no-I/O claim, using the disposable host
        # only to advance the clock predicate. Recovery retains source/ID and
        # expiry; it cannot borrow a stale claim or remint target generations.
        await admin.execute(
            "UPDATE custody_admission.accepted_work SET lease_until=clock_timestamp()-interval '1 second' "
            "WHERE record_id=$1",
            accepted.request_id,
        )
        recovered_claim = await work_call("claim")
        assert recovered_claim["record_id"] == first_claim["record_id"]
        assert recovered_claim["source_ref"] == str(first_source.source_ref)
        assert recovered_claim["claim_ref"] != first_claim["claim_ref"]
        with pytest.raises(asyncpg.SerializationError):
            await work_call("attempt", first_claim)
        assert await work_call(
            "prepared", recovered_claim, source_ref=str(first_source.source_ref)
        ) == {"status": "prepared", "command_id": str(first_source.command_id)}
        assert await work_call("attempt", recovered_claim) == {
            "status": "attempted",
            "command_id": str(first_source.command_id),
        }
        # Separate committed readback proves the dispatch marker exists BEFORE
        # real registered owning MCP. This is not a provider-start witness.
        assert (
            await admin.fetchval(
                "SELECT count(*) FROM public.fleet_case_evidence WHERE ref=$1",
                str(first_source.command_id),
            )
            == 0
        )
        attempt_row = await admin.fetchrow(
            "SELECT state,attempted_at,source_ref FROM custody_admission.accepted_work WHERE record_id=$1",
            accepted.request_id,
        )
        assert attempt_row["state"] == "attempted" and attempt_row["attempted_at"] is not None
        assert attempt_row["source_ref"] == first_source.source_ref
        dispatch_receipt = await CustodyControlTransport(receiver, Client(mcp)).execute(
            first_source
        )
        assert dispatch_receipt["status"] == "committed"
        assert (
            await admin.fetchval(
                "SELECT count(*) FROM public.fleet_case_evidence WHERE ref=$1",
                str(first_source.command_id),
            )
            == 1
        )
        assert await work_call("finish", recovered_claim) == {"status": "committed"}
        assert (
            await admin.fetchval(
                "SELECT result FROM custody_admission.receipts WHERE command_id=$1",
                first_source.command_id,
            )
            == dispatch_receipt
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await switchboard_db.pool.execute(
                "ALTER TABLE switchboard.message_inbox DISABLE TRIGGER custody_accepted_birth"
            )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await switchboard_db.pool.execute("SELECT custody_admission.install_accepted_birth()")
        duplicate = await accepted_lock()
        assert duplicate.duplicate and duplicate.request_id == accepted.request_id
        assert (
            await admin.fetchval(
                "SELECT count(*) FROM custody_admission.accepted_births WHERE record_id=$1",
                accepted.request_id,
            )
            == 1
        )
        # Lifecycle processing changes no source body/birth and replays exactly.
        await switchboard_db.pool.execute(
            "UPDATE switchboard.message_inbox SET processing_metadata=$2::jsonb WHERE id=$1",
            accepted.request_id,
            {"synthetic_lifecycle": "processed"},
        )
        assert await ingress.capture_lock(accepted.request_id) == first_source
        assert (
            await admin.fetchval(
                "SELECT retired_at FROM custody_admission.accepted_births WHERE record_id=$1",
                accepted.request_id,
            )
            is None
        )

        # Neutralize the actual INSERT stamp with genuine bound/native ingress.
        # Recent canonical rows without a birth still refuse. Restore the exact
        # installed trigger, prove it, and plant a NEW native positive; the old
        # no-birth row is never retroactively adopted by a locator/replay.
        await admin.execute(
            "ALTER TABLE switchboard.message_inbox DISABLE TRIGGER custody_accepted_birth"
        )
        try:
            with pytest.raises(asyncpg.InsufficientPrivilegeError, match="birth trigger"):
                await admin.fetchval("SELECT custody_admission.prove_interface()")
            unstamped = await accepted_lock("lOCK")
            assert not unstamped.duplicate
            assert (
                await switchboard_db.pool.fetchval(
                    "SELECT count(*) FROM switchboard.message_inbox WHERE id=$1",
                    unstamped.request_id,
                )
                == 1
            )
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM custody_admission.accepted_births WHERE record_id=$1",
                    unstamped.request_id,
                )
                == 0
            )
            with pytest.raises(CustodyError, match="unavailable"):
                await ingress.capture_lock(unstamped.request_id)
        finally:
            await admin.execute(
                "ALTER TABLE switchboard.message_inbox ENABLE TRIGGER custody_accepted_birth"
            )
        verify_installed_functions(
            await admin.fetchval("SELECT custody_admission.prove_interface()")
        )
        with pytest.raises(CustodyError, match="unavailable"):
            await ingress.capture_lock(unstamped.request_id)
        stamped = await accepted_lock("LoCK")
        assert not stamped.duplicate
        stamped_source = await ingress.capture_lock(stamped.request_id)
        assert stamped_source.source_ref != first_source.source_ref
        assert (
            await admin.fetchval(
                "SELECT count(*) FROM custody_admission.accepted_births WHERE record_id=$1 "
                "AND retired_at IS NULL",
                stamped.request_id,
            )
            == 1
        )
        unknown_claim = await work_call("claim")
        assert unknown_claim["record_id"] == str(stamped.request_id)
        await work_call("prepared", unknown_claim, source_ref=str(stamped_source.source_ref))
        await work_call("attempt", unknown_claim)
        # Crash after committed attempt, with no owning receipt, cannot be
        # classified as never sent. Recovery records UNKNOWN and excludes it
        # from redispatch. Its frozen source remains exactly the same.
        await admin.execute(
            "UPDATE custody_admission.accepted_work SET lease_until=clock_timestamp()-interval '1 second' "
            "WHERE record_id=$1",
            stamped.request_id,
        )
        assert await work_call("claim") == {"status": "reconciled"}
        assert (
            await admin.fetchval(
                "SELECT state FROM custody_admission.accepted_work WHERE record_id=$1",
                stamped.request_id,
            )
            == "unknown"
        )
        assert await work_call("claim") == {"status": "idle"}
        assert (
            await admin.fetchval(
                "SELECT count(*) FROM custody_admission.receipts WHERE command_id=$1",
                stamped_source.command_id,
            )
            == 0
        )
        assert await ingress.capture_lock(stamped.request_id) == stamped_source

        # Fail the REAL second canonical INSERT, after the trigger was reached.
        # This test-only disposable SQL poison gives no production privileges.
        inbox_before = await admin.fetchval("SELECT count(*) FROM switchboard.message_inbox")
        births_before = await admin.fetchval(
            "SELECT count(*) FROM custody_admission.accepted_births"
        )
        work_before = await admin.fetchval("SELECT count(*) FROM custody_admission.accepted_work")
        await admin.execute(
            "CREATE FUNCTION public.custody_test_event_poison() RETURNS trigger "
            "LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$ "
            "BEGIN RAISE EXCEPTION 'synthetic event rollback'; END; $$; "
            "CREATE TRIGGER custody_test_event_poison BEFORE INSERT ON public.ingestion_events "
            "FOR EACH ROW EXECUTE FUNCTION public.custody_test_event_poison()"
        )
        try:
            with pytest.raises(RuntimeError, match="synthetic event rollback"):
                await accepted_lock("lOcK")
        finally:
            await admin.execute(
                "DROP TRIGGER custody_test_event_poison ON public.ingestion_events; "
                "DROP FUNCTION public.custody_test_event_poison()"
            )
        assert (
            await admin.fetchval("SELECT count(*) FROM switchboard.message_inbox") == inbox_before
        )
        assert (
            await admin.fetchval("SELECT count(*) FROM custody_admission.accepted_births")
            == births_before
        )
        assert (
            await admin.fetchval("SELECT count(*) FROM custody_admission.accepted_work")
            == work_before
        )
        after_rollback = await accepted_lock("lOcK")
        assert not after_rollback.duplicate
        assert await ingress.capture_lock(after_rollback.request_id)

        # Actual partitioned physical-row ambiguity: no caller timestamp may
        # select one of two copies of a canonical locator. Rollback restores
        # the same planted positive, including its frozen source identity.
        duplicate_at = accepted_at + timedelta(seconds=1)
        await switchboard_db.pool.execute(
            "SELECT switchboard.switchboard_message_inbox_ensure_partition($1)", duplicate_at
        )
        async with admin.transaction():
            await admin.execute(
                "INSERT INTO switchboard.message_inbox "
                "(id,received_at,request_context,raw_payload,normalized_text) "
                "SELECT id,$2,request_context,raw_payload,normalized_text "
                "FROM switchboard.message_inbox WHERE id=$1 AND received_at=$3",
                accepted.request_id,
                duplicate_at,
                accepted_at,
            )
        try:
            with pytest.raises(CustodyError, match="unavailable"):
                await ingress.capture_lock(accepted.request_id)
        finally:
            await admin.execute(
                "DELETE FROM switchboard.message_inbox WHERE id=$1 AND received_at=$2",
                accepted.request_id,
                duplicate_at,
            )
        assert await ingress.capture_lock(accepted.request_id) == first_source

        async with receiver.writer() as writer:
            original = await ingress.read(writer, accepted.request_id)
            base = {
                "locator": "inbox:" + str(accepted.request_id),
                "revision": 1,
                "content_digest": original.row_digest,
                "origin_digest": original.origin_digest,
                "owner_entity_id": None,
                "issuer_target": None,
                "target_set": [],
                "target_set_version": 1,
                "expires_at": utc_timestamp(accepted_at + timedelta(minutes=5)),
                "intent": "LOCK",
                "selected_target_ids": [target["target_id"]],
            }
            for change in (
                {"origin_digest": "e" * 64},
                {"content_digest": "f" * 64},
                {"revision": 2},
                {"owner_entity_id": str(owner)},
                {"selected_target_ids": [str(uuid.uuid4())]},
            ):
                with pytest.raises(CustodyError, match="refused"):
                    async with writer._connection.transaction():
                        await writer.register_source("accepted_ingress", base | change)
            assert (await writer.register_source("accepted_ingress", base))["source_ref"] == str(
                first_source.source_ref
            )

        competitor = await admin.fetchval(
            "INSERT INTO public.entities(canonical_name,entity_type,roles) "
            "VALUES('custody-competing-contact','person',ARRAY[]::text[]) RETURNING id"
        )
        # A failed native mutation and publisher roll back together. The fresh
        # owning resolver positive still proves the original association.
        with pytest.raises(RuntimeError, match="rollback-owning-binding"):
            async with database.pool.acquire() as connection:
                async with bindings.mutation(connection, [competitor]) as writer:
                    await relationship_assert_fact(
                        database.pool,
                        competitor,
                        "has-email",
                        sender,
                        src="test",
                        conn=writer._connection,
                    )
                    raise RuntimeError("rollback-owning-binding")
        assert (
            await database.pool.fetchval(
                "SELECT count(*) FROM relationship.entity_facts "
                "WHERE subject=$1 AND predicate='has-email' AND validity='active'",
                competitor,
            )
            == 0
        )
        assert (await bindings.observe_channels("email", [sender]))[sender]["entity_id"] == str(
            owner
        )
        assert await ingress.capture_lock(accepted.request_id) == first_source

        # A real competing canonical write removes current owner authority at
        # its SAME business COMMIT. Old capture cannot be repaired by today's
        # pointer; no literal source field can substitute for that currentness.
        await relationship_assert_fact(database.pool, competitor, "has-email", sender, src="test")
        with pytest.raises(CustodyError, match="refused"):
            await ingress.capture_lock(accepted.request_id)
        with pytest.raises(CustodyError, match="refused"):
            await CustodyControlTransport(receiver, Client(mcp)).execute(first_source)
        assert (
            await retract_contact_info_fact(database.pool, competitor, "email", sender) is not None
        )
        assert (await bindings.observe_channels("email", [sender]))[sender]["entity_id"] == str(
            owner
        )
        # Restoration is a NEW binding generation. It never relinks the old
        # accepted source even though owner and sender strings now match again.
        with pytest.raises(CustodyError, match="refused"):
            await ingress.capture_lock(accepted.request_id)
        # Different actual accepted text avoids the producer's genuine hourly
        # content-hash dedup. A new external event ID alone is insufficient.
        fresh_accepted = await accepted_lock("lock")
        assert not fresh_accepted.duplicate and fresh_accepted.request_id != accepted.request_id
        fresh_lock = await ingress.capture_lock(fresh_accepted.request_id)
        assert fresh_lock.source_ref != first_source.source_ref
        assert await CustodyControlTransport(receiver, Client(mcp)).execute(fresh_lock)
        assert (
            await admin.fetchval(
                "SELECT count(*) FROM custody_admission.receipts WHERE command_id=$1",
                fresh_lock.command_id,
            )
            == 1
        )

        aged = await accepted_lock("LoCk")
        assert not aged.duplicate
        async with receiver.writer() as writer:
            aged_report = await ingress.read(writer, aged.request_id)
            # Bound genuine current report, earlier (authority-removing) source
            # expiry: this reaches the actual deadline predicate, not a missing
            # provenance fixture. No existing source or receipt is planted.
            with pytest.raises(CustodyError, match="refused"):
                async with writer._connection.transaction():
                    await writer.register_source(
                        "accepted_ingress",
                        {
                            "locator": "inbox:" + str(aged.request_id),
                            "revision": 1,
                            "content_digest": aged_report.row_digest,
                            "origin_digest": aged_report.origin_digest,
                            "owner_entity_id": None,
                            "issuer_target": None,
                            "target_set": [],
                            "target_set_version": 1,
                            "expires_at": utc_timestamp(datetime.now(UTC) - timedelta(seconds=1)),
                            "intent": "LOCK",
                            "selected_target_ids": [target["target_id"]],
                        },
                    )
        assert (
            await admin.fetchval(
                "SELECT count(*) FROM custody_admission.sources "
                "WHERE source_family='accepted_ingress' AND source_locator=$1",
                "inbox:" + str(aged.request_id),
            )
            == 0
        )
        fresh_age_positive = await ingress.capture_lock(aged.request_id)
        assert fresh_age_positive.source_ref != fresh_lock.source_ref
        original_at = aged_report.received_at
        past = original_at - timedelta(minutes=6)
        await switchboard_db.pool.execute(
            "SELECT switchboard.switchboard_message_inbox_ensure_partition($1)", past
        )
        # An actual lifetime mutation retires rather than refills source birth.
        # Changing it back never restores the old report's authority. This is
        # disposable native-storage mutation, not a restored-history admission.
        await admin.execute(
            "UPDATE switchboard.message_inbox SET received_at=$2 WHERE id=$1 AND received_at=$3",
            aged.request_id,
            past,
            original_at,
        )
        with pytest.raises(CustodyError, match="unavailable"):
            await ingress.capture_lock(aged.request_id)
        await admin.execute(
            "UPDATE switchboard.message_inbox SET received_at=$2 WHERE id=$1 AND received_at=$3",
            aged.request_id,
            original_at,
            past,
        )
        with pytest.raises(CustodyError, match="unavailable"):
            await ingress.capture_lock(aged.request_id)
        with pytest.raises(CustodyError, match="refused"):
            await CustodyControlTransport(receiver, Client(mcp)).execute(fresh_age_positive)
        assert await admin.fetchval(
            "SELECT retired_at IS NOT NULL FROM custody_admission.accepted_births WHERE record_id=$1",
            aged.request_id,
        )
        new_age_positive = await accepted_lock("LOcK")
        assert not new_age_positive.duplicate
        assert await ingress.capture_lock(new_age_positive.request_id)
        # Delete/recreate the SAME physical identifiers/content through actual
        # SQL, with no claimed cryptographic timestamp/incarnation property.
        # Retirement survives even this exact unchanged replay; only a genuinely
        # new native report receives a new birth.
        deleted_report = await admin.fetchrow(
            "DELETE FROM switchboard.message_inbox WHERE id=$1 "
            "RETURNING id,received_at,request_context,raw_payload,normalized_text",
            new_age_positive.request_id,
        )
        assert deleted_report is not None
        await admin.execute(
            "INSERT INTO switchboard.message_inbox "
            "(id,received_at,request_context,raw_payload,normalized_text) VALUES($1,$2,$3,$4,$5)",
            deleted_report["id"],
            deleted_report["received_at"],
            deleted_report["request_context"],
            deleted_report["raw_payload"],
            deleted_report["normalized_text"],
        )
        with pytest.raises(CustodyError, match="unavailable"):
            await ingress.capture_lock(new_age_positive.request_id)
        final_birth_positive = await accepted_lock("lOCk")
        assert not final_birth_positive.duplicate
        assert await ingress.capture_lock(final_birth_positive.request_id)

        # Actual separate API pool + constructor-owned canonical writer. It
        # uses the existing ordinary login and existing Relationship role; no
        # test-only role/grant is needed for this positive. The empty scope is
        # not a second receiving incarnation or an ingress command source.
        from butlers.api.db import DatabaseManager
        from butlers.api.routers.memory import UpdateEntityRequest, update_entity

        parsed_api = urlparse(ordinary_url)
        api_manager = DatabaseManager(
            host=parsed_api.hostname,
            port=parsed_api.port,
            user=unquote(parsed_api.username),
            password=unquote(parsed_api.password),
            ssl="disable",
            min_pool_size=1,
            max_pool_size=2,
        )
        await api_manager.add_butler(
            "relationship", db_name=unquote(parsed_api.path[1:]), db_schema="relationship"
        )
        await api_manager.set_credential_shared_pool(
            unquote(parsed_api.path[1:]), db_schema="public"
        )
        stack.push_async_callback(api_manager.close)
        await api_manager.start_identity_writer(unquote(parsed_api.path[1:]))
        api_writer_runtime = api_manager._identity_writer_runtime
        api_writer = api_writer_runtime.admission
        assert api_writer.profile.operations == () and api_writer.profile.audiences == ()
        assert api_writer.profile.source_kinds == ("domain_evidence",)
        shared_writer_runtime = api_manager._shared_identity_writer_runtime
        shared_writer = shared_writer_runtime.admission
        assert shared_writer._pool is api_manager.credential_shared_pool()
        assert shared_writer.profile.operations == () and shared_writer.profile.audiences == ()
        assert shared_writer.profile.role == api_writer.profile.role == "butler_relationship_rw"
        assert shared_writer._receipt["process_id"] != api_writer._receipt["process_id"]
        with pytest.raises(CustodyError, match="refused"):
            api_writer_runtime.attach_mcp(FastMCP("not-an-api-writer-receiver"))
        async with api_writer.writer() as writer:
            with pytest.raises(CustodyError, match="refused"):
                async with writer._connection.transaction():
                    await writer.register_source(
                        "domain_evidence",
                        {
                            "locator": "evidence:" + str(uuid.uuid4()),
                            "revision": 1,
                            "intent": "provider_callback",
                            "target_set": [],
                        },
                    )
        # Freeze a genuine original accepted report, then change the owner's
        # canonical roles through the real native API function. Its role and
        # origin pointer are committed atomically and visible separately.
        api_original_report = await accepted_lock("LOCK  ")
        api_original_source = await ingress.capture_lock(api_original_report.request_id)
        owner_roles = await admin.fetchval("SELECT roles FROM public.entities WHERE id=$1", owner)
        removed = await update_entity(str(owner), UpdateEntityRequest(roles=[]), db=api_manager)
        assert removed.data.roles == []
        assert await admin.fetchval("SELECT roles FROM public.entities WHERE id=$1", owner) == []
        assert (
            await admin.fetchval(
                "SELECT owner_entity_id FROM custody_admission.origin_bindings WHERE origin_digest=$1",
                await admin.fetchval(
                    "SELECT projection->>'origin_digest' FROM custody_admission.sources WHERE source_ref=$1",
                    api_original_source.source_ref,
                ),
            )
            is None
        )
        with pytest.raises(CustodyError, match="refused"):
            await ingress.capture_lock(api_original_report.request_id)
        restored = await update_entity(
            str(owner), UpdateEntityRequest(roles=owner_roles), db=api_manager
        )
        assert restored.data.roles == owner_roles
        # Old immutable association is never repaired by regranting the role.
        with pytest.raises(CustodyError, match="refused"):
            await ingress.capture_lock(api_original_report.request_id)
        assert await ingress.capture_lock((await accepted_lock("LOCK   ")).request_id)
        async with api_manager.pool("relationship").acquire() as reset_connection:
            assert await reset_connection.fetchval("SELECT current_user") == unquote(
                parsed_api.username
            )
        # Position rollback at the actual same-transaction publication call,
        # AFTER the native API UPDATE reached PostgreSQL. This is a causal
        # engineering control, not an injected trusted source or SQL verdict.
        api_publisher = api_writer_runtime.channel_bindings
        original_publish = api_publisher.publish_current
        reached_update = []

        async def failed_api_publication(writer, channel, value):
            assert (
                await writer._connection.fetchval(
                    "SELECT roles FROM public.entities WHERE id=$1", owner
                )
                == []
            )
            reached_update.append(True)
            raise CustodyError("unavailable")

        with monkeypatch.context() as publication_control:
            publication_control.setattr(api_publisher, "publish_current", failed_api_publication)
            with pytest.raises(CustodyError, match="unavailable"):
                await update_entity(str(owner), UpdateEntityRequest(roles=[]), db=api_manager)
        assert reached_update and api_publisher.publish_current == original_publish
        assert (
            await admin.fetchval("SELECT roles FROM public.entities WHERE id=$1", owner)
            == owner_roles
        )
        assert await ingress.capture_lock((await accepted_lock("LOCK    ")).request_id)

        # Native API contact lifecycle control pins the CURRENT projection
        # independently of live-owner-role checks: the owner row remains live.
        # Verify is a non-authority-changing confirmation and preserves a
        # genuinely captured report; retraction atomically removes its mapping.
        import hashlib

        from butlers.api.router_discovery import _load_router_module

        identity_api = _load_router_module(
            Path(__file__).resolve().parents[2] / "roster" / "relationship" / "api" / "router.py",
            "custody_identity_api_control",
        )
        api_channel_report = await accepted_lock("LOCK     ")
        api_channel_source = await ingress.capture_lock(api_channel_report.request_id)
        value_hash = hashlib.sha256(sender.encode()).hexdigest()[:16]
        verified = await identity_api.verify_entity_contact(
            owner, "has-email", value_hash, db=api_manager
        )
        assert verified.verified
        assert await ingress.capture_lock(api_channel_report.request_id) == api_channel_source
        deleted = await identity_api.delete_entity_contact(
            owner, "has-email", value_hash, db=api_manager
        )
        assert deleted.deleted
        assert (
            await admin.fetchval("SELECT roles FROM public.entities WHERE id=$1", owner)
            == owner_roles
        )
        assert (
            await admin.fetchval(
                "SELECT count(*) FROM relationship.entity_facts WHERE subject=$1 "
                "AND predicate='has-email' AND object=$2 AND validity='active'",
                owner,
                sender,
            )
            == 0
        )
        with pytest.raises(CustodyError, match="refused"):
            await ingress.capture_lock(api_channel_report.request_id)
        # Genuine restored native writer plus NEW accepted source, never a
        # patched SQL verdict, pointer or old-report association refill.
        await relationship_assert_fact(
            database.pool, owner, "has-email", sender, src="owner-bootstrap"
        )
        with pytest.raises(CustodyError, match="refused"):
            await ingress.capture_lock(api_channel_report.request_id)
        assert await ingress.capture_lock((await accepted_lock("LOCK      ")).request_id)

        # Genuine all-version companion cascade through the actual shared API
        # pool. No token, role grant or foreign source is used. A publisher
        # failure AFTER DELETE reaches real SQL and rolls the cascade back.
        from butlers import google_account_registry, steam_account_registry

        shared_pool = api_manager.credential_shared_pool()
        shared_publisher = owning_binding_publisher(shared_pool)
        for index, registry in enumerate((google_account_registry, steam_account_registry)):
            before_report = await accepted_lock("LOCK" + " " * (7 + index * 2))
            before_source = await ingress.capture_lock(before_report.request_id)
            if registry is google_account_registry:
                # Planted existing active companion reaches the native role
                # UPSERT, not merely new empty-entity creation. Fixture roles
                # are disposable setup, never caller authentication. Genuine
                # owning resolver SQL publishes the pre-change positive.
                companion = await admin.fetchval(
                    "INSERT INTO public.entities(canonical_name,entity_type,roles) "
                    "VALUES('google-account:cascade-control@example.test','other',ARRAY['owner']) "
                    "RETURNING id"
                )
                upsert_sender = "companion-role-control@example.test"
                await relationship_assert_fact(
                    database.pool, companion, "has-email", upsert_sender, src="owner-bootstrap"
                )
                async with Client(relation_mcp) as relation_client:
                    observed = await relation_client.call_tool(
                        "identity_resolve_channels",
                        {"channel_type": "email", "channel_values": [upsert_sender]},
                    )
                assert not observed.is_error
                assert observed.structured_content[upsert_sender]["entity_id"] == str(companion)
                from butlers.core.custody_bindings import channel_origin_digest

                upsert_origin = channel_origin_digest("email", upsert_sender)
                previous_generation = await admin.fetchval(
                    "SELECT binding_generation FROM custody_admission.origin_bindings WHERE origin_digest=$1",
                    upsert_origin,
                )
                account = await registry.create_google_account(
                    shared_pool,
                    email="cascade-control@example.test",
                    display_name="cascade control",
                )
                assert account.entity_id == companion
                assert await admin.fetchval(
                    "SELECT roles FROM public.entities WHERE id=$1", companion
                ) == ["google_account"]
                changed_origin = await admin.fetchrow(
                    "SELECT owner_entity_id,binding_generation FROM custody_admission.origin_bindings "
                    "WHERE origin_digest=$1",
                    upsert_origin,
                )
                assert changed_origin["owner_entity_id"] is None
                assert changed_origin["binding_generation"] == previous_generation + 1
            else:
                account = await registry.create_steam_account(
                    shared_pool, steam_id=76561198000000199, display_name="cascade control"
                )
            historical_value = f"historical-{index}@example.test"
            historical_fact = await relationship_assert_fact(
                database.pool, account.entity_id, "has-email", historical_value, src="test"
            )
            await retract_contact_info_fact(
                database.pool, account.entity_id, "email", historical_value
            )
            competing_fact = await relationship_assert_fact(
                database.pool, account.entity_id, "has-email", sender, src="test"
            )
            with pytest.raises(CustodyError, match="refused"):
                await ingress.capture_lock(before_report.request_id)
            original_shared_publish = shared_publisher.publish_current

            async def cascade_poison(writer, channel, value):
                assert shared_writer.owns_writer(writer)
                assert (
                    await writer._connection.fetchval(
                        "SELECT count(*) FROM public.entities WHERE id=$1", account.entity_id
                    )
                    == 0
                )
                assert (
                    await writer._connection.fetchval(
                        "SELECT count(*) FROM relationship.entity_facts WHERE id=ANY($1::uuid[])",
                        [historical_fact.fact_id, competing_fact.fact_id],
                    )
                    == 0
                )
                raise CustodyError("unavailable")

            monkeypatch.setattr(shared_publisher, "publish_current", cascade_poison)
            try:
                with pytest.raises(CustodyError, match="unavailable"):
                    await registry.disconnect_account(shared_pool, account.id, hard_delete=True)
            finally:
                monkeypatch.setattr(shared_publisher, "publish_current", original_shared_publish)
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM public.entities WHERE id=$1", account.entity_id
                )
                == 1
            )
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM relationship.entity_facts WHERE id=ANY($1::uuid[])",
                    [historical_fact.fact_id, competing_fact.fact_id],
                )
                == 2
            )
            await registry.disconnect_account(shared_pool, account.id, hard_delete=True)
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM public.entities WHERE id=$1", account.entity_id
                )
                == 0
            )
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM relationship.entity_facts WHERE id=ANY($1::uuid[])",
                    [historical_fact.fact_id, competing_fact.fact_id],
                )
                == 0
            )
            # Reinstating the canonical owner mapping is a new generation. It
            # cannot relink an older report, despite the same UUID/sender.
            with pytest.raises(CustodyError, match="refused"):
                await ingress.capture_lock(before_report.request_id)
            new_report = await accepted_lock("LOCK" + " " * (8 + index * 2))
            new_source = await ingress.capture_lock(new_report.request_id)
            assert new_source.source_ref != before_source.source_ref
            assert (await bindings.observe_channels("email", [sender]))[sender]["entity_id"] == str(
                owner
            )
            async with shared_pool.acquire() as connection:
                assert await connection.fetchval("SELECT current_user") == unquote(
                    parsed_api.username
                )

        await api_writer_runtime.stop()
        assert not api_manager.pool("relationship")._closed
        assert owning_binding_publisher(api_manager.pool("relationship")) is api_publisher
        with pytest.raises(CustodyError, match="unavailable"):
            await update_entity(str(owner), UpdateEntityRequest(roles=[]), db=api_manager)
        assert (
            await admin.fetchval("SELECT roles FROM public.entities WHERE id=$1", owner)
            == owner_roles
        )
        await api_manager.close()
        assert owning_binding_publisher(api_writer._pool) is None

        # A real separate restricted LOGIN can SET ROLE into the existing own
        # runtime role; RESET ROLE still cannot reach trusted host enrollment.
        # Provision/cleanup belongs solely to the disposable bootstrap identity.
        role = "custody_limited_" + uuid.uuid4().hex
        password = uuid.uuid4().hex
        await admin.execute(
            f'CREATE ROLE "{role}" LOGIN NOINHERIT NOSUPERUSER NOCREATEROLE NOBYPASSRLS'
        )
        restricted = None
        try:
            # Execute without emitting the generated secret-bearing statement
            # or storing it in a repository artifact. Cleanup owns setup too.
            statement = await admin.fetchval(
                "SELECT format('ALTER ROLE %I PASSWORD %L', $1::text, $2::text)", role, password
            )
            await admin.execute(statement)
            await admin.execute(f'GRANT butler_relationship_rw TO "{role}"')
            parsed = urlparse(ordinary_url)
            restricted = await asyncpg.connect(
                host=parsed.hostname,
                port=parsed.port,
                database=parsed.path[1:],
                user=role,
                password=password,
                ssl="disable",
            )
            assert not await restricted.fetchval(
                "SELECT rolsuper OR rolcreaterole OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
            )
            await restricted.execute("SET ROLE butler_relationship_rw")
            assert await restricted.fetchval("SELECT current_user") == "butler_relationship_rw"
            await restricted.execute("SELECT public.custody_connection_begin()")
            await restricted.execute("RESET ROLE")
            assert await restricted.fetchval("SELECT current_user") == role
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await restricted.fetchval("SELECT custody_admission.host_enroll('{}'::jsonb)")
        finally:
            if restricted is not None:
                await restricted.close()
            await admin.execute(f'DROP ROLE "{role}"')

        # Actual restricted dashboard factory -> inherited startup frame ->
        # trusted bootstrap enrollment -> real cookie/CSRF HTTP producer ->
        # real registered MCP over TCP -> owning COMMIT/separate readback.
        # The synthetic inventory target is a positioned admission fixture,
        # not a native provider generation or whole supported deployment proof.
        import asyncio
        import socket

        import httpx
        import uvicorn
        from fastapi import FastAPI

        from butlers.api.owner_auth.config import OwnerAuthConfig
        from butlers.api.owner_auth.http import OwnerAuthMiddleware
        from butlers.api.owner_auth.service import (
            close_owner_auth_service,
            create_owner_auth_service,
        )
        from butlers.api.routers.endpoint_custody import CustodyBrowserDoor, router
        from butlers.core.custody_api import (
            install_api_parent_channel,
            receive_startup_frame,
            send_startup_frame,
            start_api_custody,
        )
        from butlers.core.custody_api_parent import validate_dashboard_manifest

        api_role = "custody_dashboard_" + uuid.uuid4().hex
        api_password = uuid.uuid4().hex
        await admin.execute(
            f'CREATE ROLE "{api_role}" LOGIN NOINHERIT NOSUPERUSER NOCREATEROLE NOBYPASSRLS'
        )
        api_runtime = api_auth = server_task = None
        parent_socket = child_socket = listener = None
        try:
            statement = await admin.fetchval(
                "SELECT format('ALTER ROLE %I PASSWORD %L', $1::text, $2::text)",
                api_role,
                api_password,
            )
            await admin.execute(statement)
            await admin.execute(f'GRANT dashboard_auth_api TO "{api_role}"')
            config = OwnerAuthConfig(
                "https://custody.example.test",
                "custody.example.test",
                "synthetic-custody-key",
                "test",
            )
            await admin.fetchval(
                "SELECT dashboard_auth.host('reconcile_mode',$1::jsonb)",
                {
                    "origin": config.origin,
                    "rp_id": config.rp_id,
                    "key_generation": config.key_generation,
                    "confirm_revoke": True,
                },
            )
            parsed = urlparse(ordinary_url)
            with monkeypatch.context() as environment:
                environment.delenv("DATABASE_URL", raising=False)
                for key, value in {
                    "POSTGRES_HOST": parsed.hostname,
                    "POSTGRES_PORT": str(parsed.port),
                    "POSTGRES_DB": unquote(parsed.path[1:]),
                    "POSTGRES_SSLMODE": "disable",
                    "DASHBOARD_AUTH_DB_USER": api_role,
                    "DASHBOARD_AUTH_DB_PASSWORD": api_password,
                }.items():
                    environment.setenv(key, value)
                api_auth = await create_owner_auth_service(config)
                assert api_auth.pool is not None
                async with api_auth.pool.acquire() as restricted_api:
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await restricted_api.fetchval("SELECT id FROM public.entities LIMIT 1")
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await restricted_api.fetchval(
                            "SELECT custody_admission.host_enroll('{}'::jsonb)"
                        )
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await restricted_api.fetchval(
                            "SELECT custody_admission.sources.source_ref FROM custody_admission.sources"
                        )

                listener = socket.socket()
                listener.bind(("127.0.0.1", 0))
                listener.listen()
                endpoint = f"http://127.0.0.1:{listener.getsockname()[1]}/mcp"
                server = uvicorn.Server(
                    uvicorn.Config(
                        mcp.http_app(path="/mcp"),
                        log_level="warning",
                        access_log=False,
                    )
                )
                server_task = asyncio.create_task(server.serve(sockets=[listener]))
                async with asyncio.timeout(5):
                    while not server.started:
                        if server_task.done():
                            await server_task
                            pytest.fail("Registered MCP server stopped before startup")
                        await asyncio.sleep(0.01)
                parent_socket, child_socket = socket.socketpair()
                parent_socket.setblocking(False)
                child_socket.setblocking(False)
                install_api_parent_channel(child_socket)

                async def parent_enroll():
                    async with asyncio.timeout(10):
                        manifest = await receive_startup_frame(parent_socket)
                        validate_dashboard_manifest(manifest, config)
                        receipt = await admin.fetchval(
                            "SELECT custody_admission.host_enroll($1::jsonb)", manifest
                        )
                        await send_startup_frame(parent_socket, {"receipt": receipt})

                parent_task = asyncio.create_task(parent_enroll())
                try:
                    api_runtime = await start_api_custody(api_auth, endpoint)
                    await parent_task
                finally:
                    if not parent_task.done():
                        parent_task.cancel()
                        await asyncio.gather(parent_task, return_exceptions=True)
                    parent_socket.close()
                assert api_runtime.admission._pool is api_auth.pool
                async with api_runtime.admission._anchor_lock:
                    assert (
                        await api_runtime.admission._anchor.fetchval("SELECT current_user")
                        == "dashboard_auth_api"
                    )
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await api_runtime.admission._anchor.fetchval(
                            "SELECT custody_admission.host_enroll('{}'::jsonb)"
                        )

            browser_target = {
                "target_id": str(uuid.uuid4()),
                "target_kind": "endpoint",
                "binding_digest": digest({"fixture": "browser-owning-target"}),
                "binding_version": 1,
                "generation": 0,
            }
            async with receiver.writer() as inventory_writer:
                await inventory_writer.register_source(
                    "domain_evidence",
                    {
                        "locator": "evidence:" + str(uuid.uuid4()),
                        "revision": 1,
                        "content_digest": digest(browser_target),
                        "origin_digest": digest({"fixture": "browser-inventory"}),
                        "owner_entity_id": str(owner),
                        "issuer_target": None,
                        "target_set": [browser_target],
                        "target_set_version": 1,
                        "expires_at": utc_timestamp(datetime.now(UTC) + timedelta(minutes=5)),
                    },
                )
            app = FastAPI()
            app.state.owner_auth_service = api_auth
            app.state.custody_browser_door = CustodyBrowserDoor(api_runtime.producer)
            app.add_middleware(OwnerAuthMiddleware, config=config)
            app.include_router(router)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=config.origin
            ) as browser:
                first_session = await api_auth.key_session(config.api_key)

                def headers(session):
                    return {
                        "Cookie": f"{config.owner_cookie}={session.token}",
                        "Origin": config.origin,
                        "X-CSRF-Token": session.data["csrf_token"],
                    }

                selection = {
                    "target_set": [browser_target],
                    "target_set_version": 1,
                    "reason": "lost",
                }
                prepared = await browser.post(
                    "/api/endpoint-custody/commands",
                    headers=headers(first_session),
                    json={"operation": "hold", "selection": selection},
                )
                assert prepared.status_code == 201, prepared.text
                old_id = uuid.UUID(prepared.json()["data"]["command_id"])
                # Auth revoke is genuinely committed after command preparation.
                # A NEW current browser cookie cannot revive that old proof.
                await api_auth.revoke(
                    first_session.token, csrf_token=first_session.data["csrf_token"]
                )
                # Position the actual SQL current-auth predicate independently
                # of the HTTP door's frozen-proof comparison below. This is a
                # genuine previously prepared source, never an injected permit.
                old_source = app.state.custody_browser_door._pending[old_id].source
                with pytest.raises(CustodyError, match="refused"):
                    await api_runtime.producer.execute(old_source)
                current = await api_auth.key_session(config.api_key)
                refused = await browser.post(
                    f"/api/endpoint-custody/commands/{old_id}/commit", headers=headers(current)
                )
                assert refused.status_code == 403, refused.text
                assert (
                    await admin.fetchval(
                        "SELECT count(*) FROM custody_admission.receipts WHERE command_id=$1",
                        old_id,
                    )
                    == 0
                )
                assert (
                    await admin.fetchval(
                        "SELECT count(*) FROM public.custody_holds WHERE target_id=$1",
                        uuid.UUID(browser_target["target_id"]),
                    )
                    == 0
                )
                # The positive enters the SAME production HTTP/MCP writer path,
                # with a fresh genuine source and separately read committed row.
                prepared = await browser.post(
                    "/api/endpoint-custody/commands",
                    headers=headers(current),
                    json={"operation": "hold", "selection": selection},
                )
                assert prepared.status_code == 201, prepared.text
                current_id = uuid.UUID(prepared.json()["data"]["command_id"])
                committed = await browser.post(
                    f"/api/endpoint-custody/commands/{current_id}/commit", headers=headers(current)
                )
                assert committed.status_code == 200, committed.text
                assert committed.json()["data"]["status"] == "committed"
                assert (
                    await admin.fetchval(
                        "SELECT count(*) FROM custody_admission.receipts WHERE command_id=$1",
                        current_id,
                    )
                    == 1
                )
                assert (
                    await admin.fetchval(
                        "SELECT count(*) FROM public.custody_holds WHERE target_id=$1 AND released_at IS NULL",
                        uuid.UUID(browser_target["target_id"]),
                    )
                    == 1
                )
                repeated = await browser.post(
                    f"/api/endpoint-custody/commands/{current_id}/commit", headers=headers(current)
                )
                assert repeated.status_code == 202 and repeated.json()["data"]["state"] == "unknown"
                app.state.custody_browser_door.close()
                result = await browser.post(
                    f"/api/endpoint-custody/commands/{current_id}/result", headers=headers(current)
                )
                assert (
                    result.status_code == 200 and result.json()["data"] == committed.json()["data"]
                )
                assert (
                    await admin.fetchval(
                        "SELECT count(*) FROM custody_admission.receipts WHERE command_id=$1",
                        current_id,
                    )
                    == 1
                )
        finally:
            if api_runtime is not None:
                await api_runtime.stop()
            if api_auth is not None:
                await close_owner_auth_service(api_auth)
            if server_task is not None:
                server.should_exit = True
                async with asyncio.timeout(5):
                    await server_task
            if listener is not None:
                listener.close()
            if parent_socket is not None:
                parent_socket.close()
            if child_socket is not None:
                child_socket.close()
            await admin.execute(f'DROP ROLE "{api_role}"')
