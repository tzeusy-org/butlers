"""REQ-module-approvals-003: migrated roles and real synthetic WebAuthn.

The authority and signed_binding_proof fixtures are the dark reusable D13
handoff. No future Vouch/Contain/mode consumer is registered or granted here.
"""

from __future__ import annotations

import asyncio
import secrets
from functools import partial
from types import SimpleNamespace
from urllib.parse import unquote, urlparse
from uuid import UUID, uuid4

import asyncpg
import pytest

from butlers.api.owner_auth.service import OwnerAuthService, _digest
from butlers.core.dashboard_stepup import DashboardStepUpProof, dashboard_stepup_proof
from butlers.db import register_jsonb_codec
from butlers.modules.approvals import operations
from butlers.modules.approvals.stepup import bind_rule_action
from butlers.testing.migration import (
    _bootstrap_migration_prerequisites,
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)
from tests.api.owner_auth_fixtures import ORIGIN, RP, Passkey
from tests.api.test_owner_auth_store import host

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container):
    return create_migrated_test_db(
        postgres_container, migration_db_name(), chains=["core", "approvals"]
    )


@pytest.fixture
async def authority(migrated_db_url, postgres_container):
    parsed = urlparse(migrated_db_url)
    admin_url = migration_bootstrap_db_url(postgres_container, parsed.path.lstrip("/"))
    admin = await asyncpg.create_pool(
        admin_url.replace("postgresql+psycopg2", "postgresql"),
        min_size=1,
        max_size=2,
        init=register_jsonb_codec,
    )
    pool = auth_pool = None
    login_name, password = "stepup_" + uuid4().hex, secrets.token_hex(32)
    database = '"' + unquote(parsed.path.lstrip("/")).replace('"', '""') + '"'
    created_login = False
    granted_connect = False
    try:
        pool = await asyncpg.create_pool(
            migrated_db_url, min_size=1, max_size=4, init=register_jsonb_codec
        )
        # Generated identifiers/password are synthetic and confined to this DB test.
        await admin.execute(
            f"""CREATE ROLE "{login_name}" LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '{password}'"""
        )
        created_login = True
        await admin.execute(f'GRANT CONNECT ON DATABASE {database} TO "{login_name}"')
        granted_connect = True
        await admin.execute(f'GRANT dashboard_auth_api TO "{login_name}"')
        auth_pool = await asyncpg.create_pool(
            host=parsed.hostname,
            port=parsed.port,
            database=unquote(parsed.path.lstrip("/")),
            user=login_name,
            password=password,
            min_size=1,
            max_size=2,
            init=register_jsonb_codec,
        )
        config = SimpleNamespace(origin=ORIGIN, rp_id=RP, api_key=None)
        service, operator = OwnerAuthService(auth_pool, config), OwnerAuthService(admin, config)
        await host(operator, "reconcile_mode", confirm_revoke=True)
        context = await service.context()
        operation = (
            "enroll" if (await service.status())["state"] == "keyless_unenrolled" else "recover"
        )
        intent = await service.intent(context.token, context.data["csrf_token"], operation)
        await host(
            operator,
            "authorize_registration" if operation == "enroll" else "authorize_recovery",
            request_id=intent["request_id"],
            confirm_revoke=True,
        )
        options = (
            await service.registration_options(
                context.token, context.data["csrf_token"], intent["request_id"]
            )
        ).data
        key = Passkey.create()
        session = await service.finish_registration(
            context.token,
            context.data["csrf_token"],
            options["ceremony_id"],
            key.response(options, register=True),
        )
        yield pool, service, key, options["publicKey"]["user"]["id"], session, admin
    finally:
        try:
            if auth_pool is not None:
                await auth_pool.close()
            if pool is not None:
                await pool.close()
        finally:
            try:
                if created_login:
                    try:
                        if granted_connect:
                            await admin.execute(
                                f'REVOKE CONNECT ON DATABASE {database} FROM "{login_name}"'
                            )
                    finally:
                        await admin.execute(
                            f'REVOKE dashboard_auth_api FROM "{login_name}"; DROP ROLE "{login_name}"'
                        )
            finally:
                await admin.close()


async def proof_for(authority, kind, parameters):
    pool, _, _, _, _, _ = authority
    async with pool.acquire() as conn:
        binding = await bind_rule_action(conn, kind, parameters)
    return await signed_binding_proof(authority, binding)


async def signed_binding_proof(authority, binding):
    """Real assertion for an already server-derived binding, never a live device.

    Future owners may reuse this synthetic authority/verification interface;
    the SQL catalog still refuses all unregistered consumer kinds.
    """
    _, service, key, handle, session, _ = authority
    options = await service.stepup_options(_digest(session.token), binding)
    response = key.response(options, register=False, user_handle=handle)
    issued = await service.finish_stepup(
        session.token, session.data["csrf_token"], options["ceremony_id"], response
    )
    return DashboardStepUpProof(_digest(session.token), issued["nonce"])


async def test_real_factor_commits_once_and_hash_is_not_bearer(authority):
    pool, _, _, _, _, _ = authority
    parameters = dict(
        tool_name="synthetic_send",
        arg_constraints={},
        description="Synthetic rule",
        expires_at=None,
        max_uses=None,
    )
    assert (await operations.create_approval_rule(pool, **parameters))[
        "error"
    ] == "dashboard_step_up_required"
    proof = await proof_for(authority, "create", parameters)
    token = dashboard_stepup_proof.set(proof)
    try:
        first = await operations.create_approval_rule(pool, **parameters)
        replay = await operations.create_approval_rule(pool, **parameters)
        assert first["id"] == replay["id"]
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM approval_rules WHERE id=$1", UUID(first["id"])
            )
            == 1
        )
        retained = await pool.fetchrow(
            "SELECT nonce_digest,result FROM dashboard_stepup.bindings WHERE nonce_digest=$1",
            _digest(proof.nonce),
        )
        assert set(retained["result"]) == {"version", "kind", "id", "rule_id"}
        assert "Synthetic rule" not in str(dict(retained))
        dashboard_stepup_proof.set(
            DashboardStepUpProof(proof.session_digest, retained["nonce_digest"])
        )
        assert (await operations.create_approval_rule(pool, **parameters))[
            "error"
        ] == "dashboard_step_up_required"
    finally:
        dashboard_stepup_proof.reset(token)


async def test_runtime_role_commits_real_factor_rule_audit_and_closed_receipt_once(authority):
    """The daemon's real role must consume through the owning rule transaction."""
    pool, _, _, _, _, _ = authority
    parameters = dict(
        tool_name="synthetic_send",
        arg_constraints={},
        description="Runtime role",
        expires_at=None,
        max_uses=1,
    )
    proof = await proof_for(authority, "create", parameters)
    token = dashboard_stepup_proof.set(proof)
    try:
        async with pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute("SET LOCAL ROLE butler_home_rw")
                assert await connection.fetchval("SELECT current_user") == "butler_home_rw"
                first = await operations.create_approval_rule(connection, **parameters)
                assert "error" not in first
                rule_id = UUID(first["id"])
                assert await connection.fetchval("SELECT current_user") == "butler_home_rw"
                assert (
                    await connection.fetchval(
                        "SELECT count(*) FROM approval_events WHERE rule_id=$1 AND event_type='rule_created'",
                        rule_id,
                    )
                    == 1
                )
                # A savepoint alone must not publish any part of this result.
                assert (
                    await pool.fetchval("SELECT count(*) FROM approval_rules WHERE id=$1", rule_id)
                    == 0
                )
                assert (
                    await pool.fetchval(
                        "SELECT consumed_at FROM dashboard_stepup.bindings WHERE nonce_digest=$1",
                        _digest(proof.nonce),
                    )
                    is None
                )
        receipt = await pool.fetchrow(
            "SELECT consumed_at,result FROM dashboard_stepup.bindings WHERE nonce_digest=$1",
            _digest(proof.nonce),
        )
        assert receipt["consumed_at"] is not None
        assert receipt["result"] == {
            "version": 1,
            "kind": "create",
            "id": first["id"],
            "rule_id": first["id"],
        }
        assert await pool.fetchval("SELECT count(*) FROM approval_rules WHERE id=$1", rule_id) == 1
        await operations.revoke_approval_rule(pool, first["id"])
        async with pool.acquire() as connection, connection.transaction():
            await connection.execute("SET LOCAL ROLE butler_home_rw")
            assert await connection.fetchval("SELECT current_user") == "butler_home_rw"
            replay = await operations.create_approval_rule(connection, **parameters)
            assert replay["id"] == first["id"]
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM approval_rules WHERE description='Runtime role'"
                )
                == 1
            )
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM approval_events WHERE rule_id=$1 AND event_type='rule_created'",
                    rule_id,
                )
                == 1
            )
            assert (
                await connection.fetchval("SELECT active FROM approval_rules WHERE id=$1", rule_id)
                is False
            )
        assert dict(
            await pool.fetchrow(
                "SELECT consumed_at,result FROM dashboard_stepup.bindings WHERE nonce_digest=$1",
                _digest(proof.nonce),
            )
        ) == dict(receipt)
    finally:
        dashboard_stepup_proof.reset(token)


@pytest.mark.parametrize(
    "entry",
    [
        "operations_create",
        "operations_from_action",
        "module_create",
        "module_from_action",
        "module_combined",
        "promotion",
    ],
)
async def test_all_six_rule_writers_use_real_factor_and_one_durable_receipt(authority, entry):
    from butlers.modules.approvals import autonomy_suggestions
    from butlers.modules.approvals.module import ApprovalsModule

    pool, _, _, _, _, _ = authority
    action_id = uuid4()
    await pool.execute(
        "INSERT INTO pending_actions(id,tool_name,tool_args,status,requested_at) VALUES($1,'synthetic_send',$2,'pending',now())",
        action_id,
        {"recipient": "synthetic"},
    )
    module = ApprovalsModule()
    module._db = pool
    actor = {"type": "human", "id": "synthetic-owner", "authenticated": True}
    if entry.endswith("create"):
        kind = "create"
        parameters = dict(
            tool_name="synthetic_send",
            arg_constraints={"recipient": {"type": "exact", "value": "synthetic"}},
            description="Synthetic",
            expires_at=None,
            max_uses=1,
        )
        writer = (
            partial(operations.create_approval_rule, pool, **parameters)
            if entry.startswith("operations")
            else partial(module._create_approval_rule, **parameters, actor=actor)
        )
    elif entry == "promotion":
        suggestion = await autonomy_suggestions.create_promotion_suggestion(
            pool,
            uuid4().hex * 2,
            "synthetic_send",
            {"recipient": "synthetic"},
            5,
            action_id=action_id,
        )
        kind, parameters = "promotion", dict(suggestion_id=suggestion["id"])
        writer = partial(
            autonomy_suggestions.confirm_suggestion, pool, suggestion["id"], actor["id"]
        )
    else:
        kind = "approve_with_rule" if entry == "module_combined" else "from_action"
        parameters = (
            dict(action_id=str(action_id))
            if kind == "approve_with_rule"
            else dict(action_id=str(action_id), constraint_overrides=None)
        )
        if entry == "module_combined":
            writer = partial(module._approve_action, str(action_id), create_rule=True, actor=actor)
        elif entry.startswith("operations"):
            writer = partial(operations.create_rule_from_action, pool, **parameters)
        else:
            writer = partial(module._create_rule_from_action, **parameters, actor=actor)
    assert (await writer())["error"] == "dashboard_step_up_required"
    proof = await proof_for(authority, kind, parameters)
    token = dashboard_stepup_proof.set(proof)
    try:
        first, replay = await writer(), await writer()
        assert "error" not in first and first["id"] == replay["id"]
        binding = await pool.fetchrow(
            "SELECT result,consumed_at FROM dashboard_stepup.bindings WHERE nonce_digest=$1",
            _digest(proof.nonce),
        )
        assert binding["consumed_at"] is not None
        rule_id = UUID(binding["result"]["rule_id"])
        assert await pool.fetchval("SELECT count(*) FROM approval_rules WHERE id=$1", rule_id) == 1
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM approval_events WHERE rule_id=$1 AND event_type='rule_created'",
                rule_id,
            )
            == 1
        )
        # Tightening after success does not make a replay create/reactivate a rule.
        await operations.revoke_approval_rule(pool, str(rule_id))
        await writer()
        assert (
            await pool.fetchval("SELECT active FROM approval_rules WHERE id=$1", rule_id) is False
        )
    finally:
        dashboard_stepup_proof.reset(token)


@pytest.mark.parametrize(
    "change",
    [
        "expiry",
        "nonce_revoked",
        "logout",
        "credential_recovery",
        "wrong_session",
        "changed_request",
        "changed_source",
    ],
)
async def test_currentness_refusals_leave_no_rule_or_consumption(authority, change):
    pool, service, _, _, session, admin = authority
    action_id = uuid4()
    await pool.execute(
        "INSERT INTO pending_actions(id,tool_name,tool_args,status,requested_at) VALUES($1,'synthetic_send',$2,'pending',now())",
        action_id,
        {"recipient": "synthetic"},
    )
    parameters = dict(action_id=str(action_id), constraint_overrides=None)
    proof = await proof_for(authority, "from_action", parameters)
    if change == "expiry":
        await admin.execute(
            "UPDATE dashboard_stepup.bindings SET expires_at=now()-interval '1 second' WHERE nonce_digest=$1",
            _digest(proof.nonce),
        )
    elif change == "nonce_revoked":
        await service._stepup_call(
            "revoke", session_digest=proof.session_digest, nonce_digest=_digest(proof.nonce)
        )
    elif change == "logout":
        await service.revoke(session.token, csrf_token=session.data["csrf_token"])
    elif change == "credential_recovery":
        operator = OwnerAuthService(admin, service.config)
        context = await service.context()
        intent = await service.intent(context.token, context.data["csrf_token"], "recover")
        await host(
            operator, "authorize_recovery", request_id=intent["request_id"], confirm_revoke=True
        )
        options = (
            await service.registration_options(
                context.token, context.data["csrf_token"], intent["request_id"]
            )
        ).data
        await service.finish_registration(
            context.token,
            context.data["csrf_token"],
            options["ceremony_id"],
            Passkey.create().response(options, register=True),
        )
    elif change == "wrong_session":
        proof = DashboardStepUpProof("0" * 64, proof.nonce)
    elif change == "changed_request":
        parameters["constraint_overrides"] = {"recipient": {"type": "exact", "value": "different"}}
    else:
        await pool.execute(
            "UPDATE pending_actions SET tool_args=$2 WHERE id=$1",
            action_id,
            {"recipient": "changed"},
        )
    token = dashboard_stepup_proof.set(proof)
    try:
        assert (await operations.create_rule_from_action(pool, **parameters))[
            "error"
        ] == "dashboard_step_up_required"
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM approval_rules WHERE created_from=$1", action_id
            )
            == 0
        )
        assert (
            await pool.fetchval(
                "SELECT consumed_at FROM dashboard_stepup.bindings WHERE nonce_digest=$1",
                _digest(proof.nonce),
            )
            is None
        )
    finally:
        dashboard_stepup_proof.reset(token)


async def test_concurrent_consumers_and_logout_share_the_real_instance_fence(authority):
    pool, service, _, _, session, _ = authority
    parameters = dict(
        tool_name="synthetic_send",
        arg_constraints={},
        description="Race",
        expires_at=None,
        max_uses=1,
    )
    proof = await proof_for(authority, "create", parameters)

    async def consume():
        token = dashboard_stepup_proof.set(proof)
        try:
            return await operations.create_approval_rule(pool, **parameters)
        finally:
            dashboard_stepup_proof.reset(token)

    first, second = await asyncio.gather(consume(), consume())
    assert first["id"] == second["id"]
    assert (
        await pool.fetchval("SELECT count(*) FROM approval_rules WHERE id=$1", UUID(first["id"]))
        == 1
    )
    # A borrowed transaction retains the instance fence until its actual owner
    # commits; a logout cannot pass it after the savepoint alone has released.
    second_proof = await proof_for(authority, "create", dict(parameters, description="Fenced"))
    task = None
    try:
        async with pool.acquire() as connection:
            async with connection.transaction():
                token = dashboard_stepup_proof.set(second_proof)
                try:
                    result = await operations.create_approval_rule(
                        connection, **dict(parameters, description="Fenced")
                    )
                finally:
                    dashboard_stepup_proof.reset(token)
                assert "error" not in result
                task = asyncio.create_task(
                    service.revoke(session.token, csrf_token=session.data["csrf_token"])
                )
                await asyncio.sleep(0)
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), timeout=0.2)
            await asyncio.wait_for(task, timeout=5)
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert not (await service.status(session.token))["authenticated"]


@pytest.mark.parametrize("boundary", ["rule_audit", "receipt", "outer_rollback"])
async def test_authoritative_error_rolls_back_nonce_rule_and_audit(authority, boundary):
    pool, _, _, _, _, admin = authority
    parameters = dict(
        tool_name="synthetic_send",
        arg_constraints={},
        description="Rollback",
        expires_at=None,
        max_uses=1,
    )
    proof = await proof_for(authority, "create", parameters)
    if boundary != "outer_rollback":
        table = (
            "public.approval_events" if boundary == "rule_audit" else "dashboard_stepup.bindings"
        )
        trigger = "BEFORE INSERT" if boundary == "rule_audit" else "BEFORE UPDATE"
        condition = "true" if boundary == "rule_audit" else "NEW.result IS NOT NULL"
        await admin.execute(
            "CREATE FUNCTION public.synthetic_stepup_fault() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='synthetic authoritative refusal'; END $$"
        )
        await admin.execute(
            f"CREATE TRIGGER synthetic_stepup_fault {trigger} ON {table} FOR EACH ROW WHEN ({condition}) EXECUTE FUNCTION public.synthetic_stepup_fault()"
        )
    token = dashboard_stepup_proof.set(proof)
    try:
        if boundary == "outer_rollback":
            async with pool.acquire() as connection:
                with pytest.raises(RuntimeError, match="synthetic outer refusal"):
                    async with connection.transaction():
                        assert "error" not in await operations.create_approval_rule(
                            connection, **parameters
                        )
                        raise RuntimeError("synthetic outer refusal")
        else:
            with pytest.raises(asyncpg.RaiseError):
                await operations.create_approval_rule(pool, **parameters)
        assert (
            await pool.fetchval("SELECT count(*) FROM approval_rules WHERE description='Rollback'")
            == 0
        )
        assert (
            await pool.fetchval(
                "SELECT consumed_at FROM dashboard_stepup.bindings WHERE nonce_digest=$1",
                _digest(proof.nonce),
            )
            is None
        )
    finally:
        dashboard_stepup_proof.reset(token)
        if boundary != "outer_rollback":
            await admin.execute(
                f"DROP TRIGGER synthetic_stepup_fault ON {table}; DROP FUNCTION public.synthetic_stepup_fault()"
            )


@pytest.mark.parametrize(
    "invalid", ["uv_missing", "wrong_origin", "wrong_rp", "different_key", "challenge_replay"]
)
async def test_stepup_requires_a_fresh_valid_signed_assertion(authority, invalid):
    from butlers.api.owner_auth.service import AuthError

    pool, service, key, handle, session, _ = authority
    async with pool.acquire() as connection:
        binding = await bind_rule_action(
            connection,
            "create",
            dict(
                tool_name="synthetic_send",
                arg_constraints={},
                description="Factor",
                expires_at=None,
                max_uses=1,
            ),
        )
    options = await service.stepup_options(_digest(session.token), binding)
    signer = Passkey.create() if invalid == "different_key" else key
    response = signer.response(
        options,
        register=False,
        user_handle=handle,
        uv=invalid != "uv_missing",
        origin="https://other.example.test" if invalid == "wrong_origin" else ORIGIN,
        rp="other.example.test" if invalid == "wrong_rp" else RP,
    )
    if invalid == "challenge_replay":
        await service.finish_stepup(
            session.token, session.data["csrf_token"], options["ceremony_id"], response
        )
    with pytest.raises(AuthError):
        await service.finish_stepup(
            session.token, session.data["csrf_token"], options["ceremony_id"], response
        )
    if invalid != "challenge_replay":
        assert (
            await pool.fetchval(
                "SELECT nonce_digest FROM dashboard_stepup.bindings WHERE id=$1",
                options["ceremony_id"],
            )
            is None
        )


async def test_restricted_login_and_runtime_cannot_mint_or_administer_a_factor(authority):
    pool, service, _, _, _, admin = authority
    assert await pool.fetchval(
        "SELECT NOT rolsuper AND NOT rolcreaterole AND NOT rolcreatedb FROM pg_roles WHERE rolname=current_user"
    )
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute("SET LOCAL ROLE butler_home_rw")
        for sql in (
            "SELECT * FROM dashboard_auth.credentials",
            "SELECT dashboard_stepup.ceremony('options','{}'::jsonb)",
            "UPDATE dashboard_stepup.bindings SET revoked=false",
            "SELECT dashboard_stepup.install_interface()",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with connection.transaction():
                    await connection.execute(sql)
    async with service.pool.acquire() as connection:
        await connection.execute("RESET ROLE")
        assert not await connection.fetchval(
            "SELECT rolsuper OR rolcreaterole OR rolcreatedb FROM pg_roles WHERE rolname=current_user"
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await connection.execute("SELECT * FROM dashboard_auth.credentials")
    from butlers.api.owner_auth.service import AuthError
    from butlers.modules.approvals.stepup import RuleBinding

    # Shared fixtures do not silently activate any future consumer or grant.
    namespace = await pool.fetchval("SELECT current_database() || ':' || current_schema()")
    for future_kind in ("vouch", "contain", "mode_loosen"):
        binding = RuleBinding(future_kind, "new", "a" * 64, "b" * 64, namespace)
        with pytest.raises(AuthError):
            await signed_binding_proof(authority, binding)
    role = await admin.fetchrow(
        "SELECT rolcanlogin,rolsuper,rolcreaterole,rolcreatedb,rolbypassrls FROM pg_roles WHERE rolname='dashboard_stepup_owner'"
    )
    assert not any(role.values())
    assert (
        await admin.fetchval(
            "SELECT count(*) FROM pg_auth_members WHERE roleid='dashboard_stepup_owner'::regrole OR member='dashboard_stepup_owner'::regrole"
        )
        == 0
    )


async def test_options_and_mutation_use_trusted_cookie_projection_not_a_header_boolean(authority):
    import httpx
    from fastapi import FastAPI

    from butlers.api.deps import get_db
    from butlers.api.owner_auth.config import OwnerAuthConfig
    from butlers.api.owner_auth.http import OwnerAuthMiddleware
    from butlers.api.routers import approvals

    pool, service, key, handle, session, _ = authority

    class Manager:
        butler_names = ["home"]

        def pool(self, name):
            assert name == "home"
            return pool

        def butlers_with_module(self, name):
            return ["home"] if name == "approvals" else []

    config = OwnerAuthConfig(ORIGIN, RP)
    app = FastAPI()
    app.state.owner_auth_service = service
    app.add_middleware(OwnerAuthMiddleware, config=config)
    app.include_router(approvals.router)
    app.dependency_overrides[get_db] = Manager
    request = dict(
        butler="home",
        tool_name="synthetic_send",
        arg_constraints={"recipient": {"type": "exact", "value": "synthetic"}},
        description="HTTP factor",
        max_uses=1,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=ORIGIN,
        cookies={config.owner_cookie: session.token},
        headers={"Origin": ORIGIN, "X-CSRF-Token": session.data["csrf_token"]},
    ) as client:
        refused = await client.post(
            "/api/approvals/rules", json=request, headers={"X-Dashboard-Step-Up": "A" * 43}
        )
        assert refused.status_code == 403
        options = await client.post(
            "/api/approvals/step-up/options", json={"kind": "create", "parameters": request}
        )
        assert options.status_code == 200
        options = options.json()["data"]
        finish = await client.post(
            "/api/auth/owner/step-up/finish",
            json={
                "ceremony_id": options["ceremony_id"],
                "credential": key.response(options, register=False, user_handle=handle),
            },
        )
        assert finish.status_code == 200
        proof = {"X-Dashboard-Step-Up": finish.json()["data"]["nonce"]}
        first = await client.post("/api/approvals/rules", json=request, headers=proof)
        second = await client.post("/api/approvals/rules", json=request, headers=proof)
        assert first.status_code == second.status_code == 200
        assert first.json()["data"]["id"] == second.json()["data"]["id"]


async def test_core_only_replay_and_downgrade_retain_history_and_refuse_hostile_owner(
    postgres_container,
    authority,
):
    """Real core-only migration, replay, rollback and provenance, not fixture DDL."""
    from psycopg2.errors import RaiseException

    from alembic import command
    from butlers.migrations import _build_alembic_config

    name = migration_db_name()
    url = await asyncio.to_thread(
        create_migrated_test_db,
        postgres_container,
        name,
        chains=["core"],
        revisions={"core": "core_275"},
    )
    config = _build_alembic_config(url, chains=["core"])
    await asyncio.to_thread(command.upgrade, config, "core@core_277")
    admin = await asyncpg.connect(
        migration_bootstrap_db_url(postgres_container, name).replace(
            "postgresql+psycopg2", "postgresql"
        )
    )
    migration_role = urlparse(url).username
    try:
        assert await admin.fetchval("SELECT to_regclass('public.approval_rules')") is None
        assert await admin.fetchval("SELECT to_regclass('dashboard_stepup.bindings')") is not None
        # A real proof from another owning database cannot fall back to its auth
        # pool or grant anything here, even on the same PostgreSQL cluster.
        parameters = dict(
            tool_name="synthetic_send",
            arg_constraints={},
            description="Cross-database",
            expires_at=None,
            max_uses=1,
        )
        foreign_proof = await proof_for(authority, "create", parameters)
        other = await asyncpg.connect(url)
        await register_jsonb_codec(other)
        token = dashboard_stepup_proof.set(foreign_proof)
        try:
            assert (await operations.create_approval_rule(other, **parameters))[
                "error"
            ] == "dashboard_step_up_required"
            assert await other.fetchval("SELECT count(*) FROM dashboard_stepup.bindings") == 0
        finally:
            dashboard_stepup_proof.reset(token)
            await other.close()
        # Seed an immutable, already-committed receipt under test bootstrap
        # authority only. Actual factor and writer paths are proved above.
        await admin.execute("""INSERT INTO dashboard_stepup.bindings(
          id,session_digest,credential_epoch,session_epoch,kind,target,request_digest,
          action_digest,namespace,expires_at,nonce_digest,consumed_at,consumed_tx,result)
          VALUES('synthetic-history',repeat('a',64),1,1,'create','new',repeat('b',64),
          repeat('c',64),current_database()||':public',clock_timestamp(),repeat('d',64),
          clock_timestamp(),1,'{"version":1,"kind":"create","id":"00000000-0000-0000-0000-000000000001","rule_id":"00000000-0000-0000-0000-000000000001"}')""")
        before = await admin.fetchval(
            "SELECT to_jsonb(b)::text FROM dashboard_stepup.bindings b WHERE id='synthetic-history'"
        )
        await asyncio.to_thread(command.downgrade, config, "core@core_275")
        assert not await admin.fetchval(
            "SELECT has_function_privilege($1,'dashboard_stepup.consume(text,text,text,text,text,text,text,jsonb)','EXECUTE')",
            migration_role,
        )
        assert before == await admin.fetchval(
            "SELECT to_jsonb(b)::text FROM dashboard_stepup.bindings b WHERE id='synthetic-history'"
        )
        await asyncio.to_thread(command.upgrade, config, "core@core_277")
        await asyncio.to_thread(
            _bootstrap_migration_prerequisites,
            migration_bootstrap_db_url(postgres_container, name),
            migration_role,
        )
        assert before == await admin.fetchval(
            "SELECT to_jsonb(b)::text FROM dashboard_stepup.bindings b WHERE id='synthetic-history'"
        )
        # An ordinary owner cannot be laundered into trusted bootstrap by replay.
        await admin.execute(
            f'ALTER FUNCTION dashboard_stepup.consume(text,text,text,text,text,text,text,jsonb) OWNER TO "{migration_role}"'
        )
        with pytest.raises(RaiseException, match="untrusted|intact trusted"):
            await asyncio.to_thread(
                _bootstrap_migration_prerequisites,
                migration_bootstrap_db_url(postgres_container, name),
                migration_role,
            )
    finally:
        await admin.close()
