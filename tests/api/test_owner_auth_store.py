"""Real verifier + PostgreSQL authority, concurrency and privilege contracts."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import asyncpg
import pytest

from butlers.api.owner_auth.service import AuthError, OwnerAuthService, _digest
from butlers.api.owner_auth.verifier import encode
from tests.api.owner_auth_fixtures import ORIGIN, RP, Passkey

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]
SQL = (
    Path(__file__).resolve().parents[2] / "alembic/versions/core/core_239_dashboard_owner_auth.sql"
)


@pytest.fixture
async def store(provisioned_postgres_pool):
    async with provisioned_postgres_pool() as pool:
        await pool.execute(SQL.read_text())
        config = SimpleNamespace(origin=ORIGIN, rp_id=RP, api_key=None)
        service = OwnerAuthService(pool, config)
        await host(service, "reconcile_mode", confirm_revoke=True)
        yield service


async def host(service, action, **values):
    raw = await service.pool.fetchval(
        "SELECT dashboard_auth.host($1,$2::jsonb)", action, service._config() | values
    )
    data = json.loads(raw) if isinstance(raw, str) else raw
    if "error" in data:
        raise AuthError(data["error"])
    return data


async def registration(service, *, operation="enroll", key=None):
    context = await service.context()
    csrf = context.data["csrf_token"]
    intent = await service.intent(context.token, csrf, operation)
    await host(
        service,
        "authorize_registration" if operation == "enroll" else "authorize_recovery",
        request_id=intent["request_id"],
        confirm_revoke=True,
    )
    options = (await service.registration_options(context.token, csrf, intent["request_id"])).data
    key = key or Passkey.create()
    response = key.response(options, register=True)
    return context, intent, options, key, response


async def enrolled(service, **kwargs):
    context, intent, options, key, response = await registration(service, **kwargs)
    issued = await service.finish_registration(
        context.token, context.data["csrf_token"], options["ceremony_id"], response
    )
    return key, options["publicKey"]["user"]["id"], issued


async def login(service, key, handle):
    context = await service.context()
    options = await service.login_options(context.token, context.data["csrf_token"])
    response = key.response(options, register=False, user_handle=handle)
    return context, options, response


async def test_complete_registration_login_logout_restart_and_digest_storage(store):
    key, handle, issued = await enrolled(store)
    assert (await store.status(issued.token))["authenticated"]
    assert (await store.authorize(issued.token)).method == "cookie"
    refreshed = await store.csrf(issued.token)
    await store.authorize(issued.token, csrf_token=refreshed["csrf_token"], unsafe=True)
    restarted = OwnerAuthService(store.pool, store.config)
    assert await restarted.status(issued.token) == await store.status(issued.token)
    await store.revoke(issued.token, csrf_token=issued.data["csrf_token"])
    assert not (await store.status(issued.token))["authenticated"]
    context, options, response = await login(store, key, handle)
    assert "allowCredentials" not in options["publicKey"]
    daily = await store.finish_login(
        context.token, context.data["csrf_token"], options["ceremony_id"], response
    )
    assert (await store.status(daily.token))["authenticated"]
    for table in ("contexts", "sessions", "csrf", "audit"):
        rows = await store.pool.fetch(f"SELECT to_jsonb(t)::text FROM dashboard_auth.{table} t")
        serialized = str(rows)
        for secret in (issued.token, issued.data["csrf_token"], context.token, daily.token):
            assert secret not in serialized
    audit = await store.pool.fetch("SELECT action,outcome,actor FROM dashboard_auth.audit")
    assert any(row["action"] == "registration" for row in audit)
    assert all(set(row.keys()) == {"action", "outcome", "actor"} for row in audit)


async def test_host_authorization_is_bound_idempotent_and_not_public(store):
    first, other = await store.context(), await store.context()
    intent = await store.intent(first.token, first.data["csrf_token"], "enroll")
    pending = await store.registration_options(
        first.token, first.data["csrf_token"], intent["request_id"]
    )
    assert pending.status_code == 202
    await host(store, "authorize_registration", request_id=intent["request_id"])
    deadline = await store.pool.fetchval(
        "SELECT expires_at FROM dashboard_auth.intents WHERE id=$1", intent["request_id"]
    )
    assert not (await host(store, "authorize_registration", request_id=intent["request_id"]))[
        "changed"
    ]
    assert deadline == await store.pool.fetchval(
        "SELECT expires_at FROM dashboard_auth.intents WHERE id=$1", intent["request_id"]
    )
    with pytest.raises(AuthError, match="Restart"):
        await store.registration_options(
            other.token, other.data["csrf_token"], intent["request_id"]
        )
    async with store.pool.acquire() as connection:
        for statement in (
            "SELECT * FROM dashboard_auth.instance",
            "UPDATE dashboard_auth.intents SET authorized=true",
            "SELECT dashboard_auth.host('reconcile_mode','{}')",
            "INSERT INTO dashboard_auth.instance(singleton) VALUES(true)",
        ):
            async with connection.transaction():
                await connection.execute("SET LOCAL ROLE dashboard_auth_api")
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await connection.execute(statement)
    assert not await store.pool.fetchval(
        "SELECT has_schema_privilege('public','dashboard_auth','USAGE')"
    )


async def test_duplicate_finishes_and_recovery_epoch_revocation(store):
    context, _, options, key, response = await registration(store)
    results = await asyncio.gather(
        *(
            store.finish_registration(
                context.token, context.data["csrf_token"], options["ceremony_id"], response
            )
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    sessions = [result for result in results if not isinstance(result, Exception)]
    assert len(sessions) == 1
    old = sessions[0]
    assert (
        await store.pool.fetchval("SELECT count(*) FROM dashboard_auth.credentials WHERE active")
        == 1
    )
    handle = options["publicKey"]["user"]["id"]
    login_context, login_options, assertion = await login(store, key, handle)
    recovery, intent, recovery_options, _, replacement = await registration(
        store, operation="recover"
    )
    assert (await store.status(old.token))["state"] == "recovery_pending"
    assert not (await store.status(old.token))["authenticated"]
    with pytest.raises(AuthError):
        await store.finish_login(
            login_context.token,
            login_context.data["csrf_token"],
            login_options["ceremony_id"],
            assertion,
        )
    installed = await store.finish_registration(
        recovery.token, recovery.data["csrf_token"], recovery_options["ceremony_id"], replacement
    )
    assert (await store.status(installed.token))["state"] == "keyless_enrolled"
    assert not (await store.status(old.token))["authenticated"]


@pytest.mark.parametrize("fault", ["audit", "session"])
async def test_registration_failure_rolls_back_credential_intent_and_challenge(store, fault):
    context, intent, options, _, response = await registration(store)
    table = "audit" if fault == "audit" else "sessions"
    await store.pool.execute(
        f"CREATE FUNCTION public.reject_auth_test() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic failure'; END $$; CREATE TRIGGER reject_auth BEFORE INSERT ON dashboard_auth.{table} FOR EACH ROW EXECUTE FUNCTION public.reject_auth_test()"
    )
    with pytest.raises(AuthError, match="unavailable"):
        await store.finish_registration(
            context.token, context.data["csrf_token"], options["ceremony_id"], response
        )
    assert await store.pool.fetchval("SELECT count(*) FROM dashboard_auth.credentials") == 0
    assert not await store.pool.fetchval(
        "SELECT consumed FROM dashboard_auth.intents WHERE id=$1", intent["request_id"]
    )
    assert not await store.pool.fetchval(
        "SELECT consumed FROM dashboard_auth.ceremonies WHERE id=$1", options["ceremony_id"]
    )
    await store.pool.execute(f"DROP TRIGGER reject_auth ON dashboard_auth.{table}")
    issued = await store.finish_registration(
        context.token, context.data["csrf_token"], options["ceremony_id"], response
    )
    assert (await store.status(issued.token))["authenticated"]


@pytest.mark.parametrize(
    "failure", ["origin", "rp", "uv", "signature", "handle", "unknown", "backup"]
)
async def test_real_assertion_rejects_wrong_security_proofs(store, failure):
    key, handle, _ = await enrolled(store)
    context, options, _ = await login(store, key, handle)
    kwargs = (
        {"origin": "https://evil.example.test"}
        if failure == "origin"
        else {"rp": "example.test"}
        if failure == "rp"
        else {"uv": False}
        if failure == "uv"
        else {}
    )
    if failure == "backup":
        key.be = False
    response = key.response(
        options,
        register=False,
        user_handle=encode(b"x" * 32) if failure == "handle" else handle,
        **kwargs,
    )
    if failure == "signature":
        response["response"]["signature"] = encode(b"invalid signature")
    if failure == "unknown":
        response["id"] = response["rawId"] = encode(b"unknown credential")
    with pytest.raises(AuthError, match="required"):
        await store.finish_login(
            context.token, context.data["csrf_token"], options["ceremony_id"], response
        )
    assert not await store.pool.fetchval(
        "SELECT consumed FROM dashboard_auth.ceremonies WHERE id=$1", options["ceremony_id"]
    )


@pytest.mark.parametrize("be", [True, False])
async def test_synced_and_device_bound_counter_rules_are_rechecked_at_commit(store, be):
    key = Passkey.create(be=be)
    key.counter = 4
    key, handle, _ = await enrolled(store, key=key)
    context, options, response = await login(store, key, handle)
    if be:
        issued = await store.finish_login(
            context.token, context.data["csrf_token"], options["ceremony_id"], response
        )
        assert (await store.status(issued.token))["authenticated"]
        assert (
            await store.pool.fetchval(
                "SELECT count(*) FROM dashboard_auth.audit WHERE outcome='counter_anomaly'"
            )
            == 1
        )
    else:
        with pytest.raises(AuthError):
            await store.finish_login(
                context.token, context.data["csrf_token"], options["ceremony_id"], response
            )
        key.counter = 5
        response = key.response(options, register=False, user_handle=handle)
        # Advance the counter after the signature was verified against counter=4.
        original_call = store._call

        async def concurrent(action, **values):
            if action == "finish_login":
                await store.pool.execute(
                    "UPDATE dashboard_auth.credentials SET counter=5 WHERE active"
                )
            return await original_call(action, **values)

        store._call = concurrent
        with pytest.raises(AuthError):
            await store.finish_login(
                context.token, context.data["csrf_token"], options["ceremony_id"], response
            )


async def test_configured_key_reconciliation_stale_workers_and_header_precedence(store):
    old = OwnerAuthService(store.pool, store.config)
    store.config = SimpleNamespace(origin=ORIGIN, rp_id=RP, api_key="synthetic configured key")
    with pytest.raises(AuthError):
        await store.authorize(api_key=store.config.api_key)
    await host(store, "reconcile_mode", confirm_revoke=True)
    issued = await store.key_session(store.config.api_key)
    assert (await store.authorize(api_key=store.config.api_key)).method == "header"
    with pytest.raises(AuthError):
        await store.authorize(issued.token, api_key="")
    with pytest.raises(AuthError):
        await store.authorize(issued.token, api_key="wrong")
    assert (await old.status())["state"] == "unavailable"
    await store.revoke(api_key=store.config.api_key, all_sessions=True)
    assert not (await store.status(issued.token))["authenticated"]
    store.config = SimpleNamespace(origin=None, rp_id=None, api_key=store.config.api_key)
    assert (await store.authorize(api_key=store.config.api_key)).method == "header"
    assert (await store.status())["state"] == "unavailable"
    with pytest.raises(AuthError):
        await store.key_session(store.config.api_key)


async def test_csrf_rotation_cap_independent_proof_and_absolute_expiry(store):
    _, _, issued = await enrolled(store)
    for _ in range(6):
        await store.csrf(issued.token)
    assert (
        await store.pool.fetchval(
            "SELECT count(*) FROM dashboard_auth.csrf WHERE session_digest=$1",
            _digest(issued.token),
        )
        == 4
    )
    with pytest.raises(AuthError, match="forbidden"):
        await store.authorize(issued.token, csrf_token=issued.data["csrf_token"], unsafe=True)
    with pytest.raises(AuthError, match="forbidden"):
        await store.authorize(issued.token, csrf_token=issued.token, unsafe=True)
    await store.pool.execute(
        "UPDATE dashboard_auth.sessions SET expires_at=clock_timestamp()-interval '1 second'"
    )
    assert not (await store.status(issued.token))["authenticated"]
    with pytest.raises(AuthError):
        await store.csrf(issued.token)


async def test_expiry_capacity_cancel_and_missing_singleton_fail_closed(store):
    context = await store.context()
    intent = await store.intent(context.token, context.data["csrf_token"], "enroll")
    await store.cancel(context.token, context.data["csrf_token"], request_id=intent["request_id"])
    with pytest.raises(AuthError):
        await host(store, "authorize_registration", request_id=intent["request_id"])
    # The global rate is shared across independent service instances.
    second = OwnerAuthService(store.pool, store.config)
    for _ in range(29):
        await second.context()
    await store.pool.execute(
        "UPDATE dashboard_auth.rate_buckets SET count=30,minute=date_trunc('minute',clock_timestamp()) WHERE kind='context'"
    )
    with pytest.raises(AuthError, match="rate limit"):
        await store.context()
    await store.pool.execute(
        "UPDATE dashboard_auth.rate_buckets SET minute=clock_timestamp()-interval '2 minutes'; UPDATE dashboard_auth.contexts SET expires_at=clock_timestamp()-interval '1 second'"
    )
    assert await store.context()
    await store.pool.execute("UPDATE dashboard_auth.instance SET state='configured_key'")
    assert (await store.status())["state"] == "unavailable"
    with pytest.raises(AuthError):
        await host(store, "reconcile_mode", confirm_revoke=True)
    await store.pool.execute("UPDATE dashboard_auth.instance SET state='keyless_unenrolled'")
    await store.pool.execute("DELETE FROM dashboard_auth.instance")
    assert (await store.status())["state"] == "unavailable"
    with pytest.raises(AuthError):
        await host(store, "reconcile_mode", confirm_revoke=True)
    assert await store.pool.fetchval("SELECT count(*) FROM dashboard_auth.instance") == 0


async def test_cleanup_removes_retired_material_and_receipts_never_reopen(store):
    key, handle, issued = await enrolled(store)
    await registration(store, operation="recover")
    await store.pool.execute(
        "UPDATE dashboard_auth.credentials SET retired_at=clock_timestamp()-interval '2 days' WHERE NOT active; UPDATE dashboard_auth.contexts SET expires_at=clock_timestamp()-interval '2 days'; UPDATE dashboard_auth.intents SET expires_at=clock_timestamp()-interval '2 days'; UPDATE dashboard_auth.ceremonies SET expires_at=clock_timestamp()-interval '2 days'; UPDATE dashboard_auth.sessions SET expires_at=clock_timestamp()-interval '2 days'; UPDATE dashboard_auth.csrf SET expires_at=clock_timestamp()-interval '2 days'; UPDATE dashboard_auth.audit SET ts=clock_timestamp()-interval '31 days'"
    )
    await store.cleanup()
    retired = await store.pool.fetchrow("SELECT * FROM dashboard_auth.credentials WHERE NOT active")
    assert retired["credential_id"] is retired["credential_data"] is retired["user_handle"] is None
    assert retired["retired_at"] is None
    assert (
        retired["counter"] == 0 and not retired["backup_eligible"] and not retired["backup_state"]
    )
    assert await store.pool.fetchval("SELECT count(*) FROM dashboard_auth.audit") == 0
    assert not (await store.status(issued.token))["authenticated"]
    assert (await store.status())["state"] == "recovery_pending"


@pytest.mark.parametrize("rsa_key", [False, True])
async def test_both_adopted_algorithms_and_options_retry(store, rsa_key):
    context, intent, options, key, response = await registration(
        store, key=Passkey.create(rsa_key=rsa_key)
    )
    again = (
        await store.registration_options(
            context.token, context.data["csrf_token"], intent["request_id"]
        )
    ).data
    assert again["ceremony_id"] == options["ceremony_id"]
    assert again["publicKey"]["challenge"] == options["publicKey"]["challenge"]
    issued = await store.finish_registration(
        context.token, context.data["csrf_token"], options["ceremony_id"], response
    )
    assert (await store.status(issued.token))["authenticated"]
    context, options, response = await login(store, key, options["publicKey"]["user"]["id"])
    assert await store.finish_login(
        context.token, context.data["csrf_token"], options["ceremony_id"], response
    )


async def test_host_session_revoke_and_origin_rebind_preserve_exact_authority_boundaries(store):
    key, handle, issued = await enrolled(store)
    context, options, response = await login(store, key, handle)
    await host(store, "clear_pending", confirm_revoke=True)
    assert (await store.status(issued.token))["authenticated"]
    with pytest.raises(AuthError):
        await store.finish_login(
            context.token, context.data["csrf_token"], options["ceremony_id"], response
        )
    context, options, response = await login(store, key, handle)
    await host(store, "revoke_sessions", confirm_revoke=True)
    assert not (await store.status(issued.token))["authenticated"]
    assert (await store.status())["state"] == "keyless_enrolled"
    with pytest.raises(AuthError):
        await store.finish_login(
            context.token, context.data["csrf_token"], options["ceremony_id"], response
        )
    fresh, fresh_options, fresh_response = await login(store, key, handle)
    daily = await store.finish_login(
        fresh.token, fresh.data["csrf_token"], fresh_options["ceremony_id"], fresh_response
    )
    assert (await store.status(daily.token))["authenticated"]
    store.config = SimpleNamespace(
        origin="https://replacement.example.test", rp_id="replacement.example.test", api_key=None
    )
    assert (await store.status())["state"] == "unavailable"
    with pytest.raises(AuthError):
        await host(store, "reconcile_mode", confirm_revoke=True)
    await host(store, "rebind_origin", confirm_revoke=True)
    assert (await store.status())["state"] == "recovery_pending"
    assert not (await store.status(daily.token))["authenticated"]
    assert not await store.pool.fetchval(
        "SELECT EXISTS(SELECT FROM dashboard_auth.credentials WHERE active)"
    )


async def test_recovery_wins_between_signature_verification_and_final_commit(store):
    key, handle, old = await enrolled(store)
    context, options, response = await login(store, key, handle)
    recovery = await store.context()
    intent = await store.intent(recovery.token, recovery.data["csrf_token"], "recover")
    original_call = store._call
    verified_then_revoked = False

    async def revoke_at_commit(action, **values):
        nonlocal verified_then_revoked
        if action == "finish_login":
            verified_then_revoked = True
            await host(
                store, "authorize_recovery", request_id=intent["request_id"], confirm_revoke=True
            )
        return await original_call(action, **values)

    store._call = revoke_at_commit
    with pytest.raises(AuthError):
        await store.finish_login(
            context.token, context.data["csrf_token"], options["ceremony_id"], response
        )
    assert verified_then_revoked
    assert await store.pool.fetchval("SELECT count(*) FROM dashboard_auth.sessions") == 1
    assert not (await store.status(old.token))["authenticated"]


async def test_capacity_limits_remain_global_and_host_control_works_while_saturated(store):
    context = await store.context()
    intent = await store.intent(context.token, context.data["csrf_token"], "enroll")
    await store.pool.execute(
        "INSERT INTO dashboard_auth.contexts(digest,csrf_digest,expires_at,credential_epoch) SELECT 'capacity-'||n,'csrf-'||n,clock_timestamp()+interval '5 minutes',credential_epoch FROM dashboard_auth.instance,generate_series(1,63) n"
    )
    with pytest.raises(AuthError) as limited:
        await OwnerAuthService(store.pool, store.config).context()
    assert limited.value.status_code == 429
    await host(store, "authorize_registration", request_id=intent["request_id"])
    assert (
        await store.registration_options(
            context.token, context.data["csrf_token"], intent["request_id"]
        )
    ).status_code == 200
    await store.pool.execute("UPDATE dashboard_auth.rate_buckets SET count=60 WHERE kind='options'")
    with pytest.raises(AuthError) as limited:
        await store.registration_options(
            context.token, context.data["csrf_token"], intent["request_id"]
        )
    assert limited.value.status_code == 429
    await host(store, "revoke_sessions", confirm_revoke=True)
    await host(store, "clear_pending", confirm_revoke=True)
    assert (
        await store.pool.fetchval("SELECT count(*) FROM dashboard_auth.contexts WHERE NOT revoked")
        == 0
    )
    assert not await store.pool.fetchval(
        "SELECT authorized FROM dashboard_auth.intents WHERE id=$1", intent["request_id"]
    )
    assert (await store.status())["state"] == "keyless_unenrolled"
    assert await store.context()


async def test_failed_finishes_consume_durable_rate_budget_without_consuming_proof(store):
    key, handle, _ = await enrolled(store)
    context, options, response = await login(store, key, handle)
    response["response"]["signature"] = encode(b"wrong-signature")
    for _ in range(10):
        with pytest.raises(AuthError) as denied:
            await store.finish_login(
                context.token, context.data["csrf_token"], options["ceremony_id"], response
            )
        assert denied.value.status_code == 401
    assert await store.pool.fetchval(
        "SELECT finish_count>0 FROM dashboard_auth.contexts WHERE digest=$1", _digest(context.token)
    )
    await store.pool.execute(
        "UPDATE dashboard_auth.contexts SET finish_count=10,finish_minute=date_trunc('minute',clock_timestamp()) WHERE digest=$1",
        _digest(context.token),
    )
    with pytest.raises(AuthError) as limited:
        await OwnerAuthService(store.pool, store.config).finish_login(
            context.token, context.data["csrf_token"], options["ceremony_id"], response
        )
    assert limited.value.status_code == 429
    assert not await store.pool.fetchval(
        "SELECT consumed FROM dashboard_auth.ceremonies WHERE id=$1", options["ceremony_id"]
    )


async def test_migration_refuses_preexisting_privileged_auth_role_without_repairing_it(
    provisioned_postgres_pool,
):
    async with provisioned_postgres_pool() as pool:
        await pool.execute(
            "DO $$ BEGIN IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='dashboard_auth_api') THEN CREATE ROLE dashboard_auth_api NOLOGIN; END IF; END $$"
        )
        await pool.execute(
            "CREATE ROLE synthetic_host_owner NOLOGIN; GRANT synthetic_host_owner TO dashboard_auth_api"
        )
        try:
            with pytest.raises(asyncpg.RaiseError, match="role must be restricted"):
                await pool.execute(SQL.read_text())
            assert await pool.fetchval(
                "SELECT pg_has_role('dashboard_auth_api','synthetic_host_owner','MEMBER')"
            )
            assert not await pool.fetchval(
                "SELECT EXISTS(SELECT FROM pg_namespace WHERE nspname='dashboard_auth')"
            )
        finally:
            await pool.execute(
                "REVOKE synthetic_host_owner FROM dashboard_auth_api; DROP ROLE synthetic_host_owner"
            )
        await pool.execute("ALTER ROLE dashboard_auth_api BYPASSRLS")
        try:
            with pytest.raises(asyncpg.RaiseError, match="role must be restricted"):
                await pool.execute(SQL.read_text())
            assert await pool.fetchval(
                "SELECT rolbypassrls FROM pg_roles WHERE rolname='dashboard_auth_api'"
            )
        finally:
            await pool.execute("ALTER ROLE dashboard_auth_api NOBYPASSRLS")
        await pool.execute(SQL.read_text())
        assert await pool.fetchval("SELECT count(*) FROM dashboard_auth.instance") == 1


async def test_factory_requires_real_restricted_login_and_reset_role_cannot_restore_host(
    store, postgres_container, monkeypatch
):
    from butlers.api.owner_auth.service import close_owner_auth_service, create_owner_auth_service

    monkeypatch.delenv("DATABASE_URL", raising=False)
    for name, value in {
        "POSTGRES_HOST": postgres_container.get_container_host_ip(),
        "POSTGRES_PORT": str(postgres_container.get_exposed_port(5432)),
        "POSTGRES_DB": await store.pool.fetchval("SELECT current_database()"),
        "POSTGRES_USER": postgres_container.username,
        "POSTGRES_PASSWORD": postgres_container.password,
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("DASHBOARD_AUTH_DB_USER", raising=False)
    monkeypatch.delenv("DASHBOARD_AUTH_DB_PASSWORD", raising=False)
    absent = await create_owner_auth_service(store.config)
    assert absent.pool is None
    await close_owner_auth_service(absent)
    # Even explicitly supplying the admin login cannot turn an API connection into host authority.
    monkeypatch.setenv("DASHBOARD_AUTH_DB_USER", postgres_container.username)
    monkeypatch.setenv("DASHBOARD_AUTH_DB_PASSWORD", postgres_container.password)
    admin = await create_owner_auth_service(store.config)
    assert admin.pool is None
    await close_owner_auth_service(admin)
    await store.pool.execute(
        "CREATE ROLE synthetic_dashboard_login LOGIN NOINHERIT PASSWORD 'disposable-synthetic-password'; GRANT dashboard_auth_api TO synthetic_dashboard_login"
    )
    monkeypatch.setenv("DASHBOARD_AUTH_DB_USER", "synthetic_dashboard_login")
    monkeypatch.setenv("DASHBOARD_AUTH_DB_PASSWORD", "disposable-synthetic-password")
    restricted = None
    try:
        restricted = await create_owner_auth_service(store.config)
        assert restricted.pool is not None
        assert (await restricted.status())["state"] == "keyless_unenrolled"
        async with restricted.pool.acquire() as connection:
            await connection.execute("RESET ROLE")
            assert await connection.fetchval("SELECT session_user") == "synthetic_dashboard_login"
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await connection.execute("SELECT dashboard_auth.host('revoke_sessions','{}')")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await connection.execute(f"SET ROLE {postgres_container.username}")
        await close_owner_auth_service(restricted)
        restricted = None
        await store.pool.execute("ALTER ROLE synthetic_dashboard_login REPLICATION")
        try:
            replication = await create_owner_auth_service(store.config)
            assert replication.pool is None
            await close_owner_auth_service(replication)
        finally:
            await store.pool.execute("ALTER ROLE synthetic_dashboard_login NOREPLICATION")
        await store.pool.execute("GRANT pg_execute_server_program TO synthetic_dashboard_login")
        try:
            executor = await create_owner_auth_service(store.config)
            accepted_program_authority = executor.pool is not None
            await close_owner_auth_service(executor)
            assert not accepted_program_authority
        finally:
            await store.pool.execute(
                "REVOKE pg_execute_server_program FROM synthetic_dashboard_login"
            )
        await store.pool.execute(
            "GRANT USAGE ON SCHEMA dashboard_auth TO synthetic_dashboard_login; "
            "GRANT UPDATE(authorized) ON dashboard_auth.intents TO synthetic_dashboard_login"
        )
        try:
            direct_writer = await create_owner_auth_service(store.config)
            accepted_direct_write = direct_writer.pool is not None
            await close_owner_auth_service(direct_writer)
            assert not accepted_direct_write
        finally:
            await store.pool.execute(
                "REVOKE UPDATE(authorized) ON dashboard_auth.intents FROM synthetic_dashboard_login; "
                "REVOKE USAGE ON SCHEMA dashboard_auth FROM synthetic_dashboard_login"
            )
        # NOINHERIT does not help: a SET ROLE-capable ancestor still grants host authority.
        await store.pool.execute(
            "CREATE ROLE synthetic_host_login_capability NOLOGIN; GRANT EXECUTE ON FUNCTION dashboard_auth.host(text,jsonb) TO synthetic_host_login_capability; GRANT synthetic_host_login_capability TO synthetic_dashboard_login"
        )
        unsafe = await create_owner_auth_service(store.config)
        assert unsafe.pool is None
        await close_owner_auth_service(unsafe)
        await store.pool.execute(
            "REVOKE synthetic_host_login_capability FROM synthetic_dashboard_login; REVOKE EXECUTE ON FUNCTION dashboard_auth.host(text,jsonb) FROM synthetic_host_login_capability; DROP ROLE synthetic_host_login_capability"
        )
    finally:
        if restricted:
            await close_owner_auth_service(restricted)
        await store.pool.execute("DROP ROLE synthetic_dashboard_login")


async def _wait_for_lock_then_deadline(connection):
    for _ in range(100):
        await connection.execute("SELECT pg_stat_clear_snapshot()")
        waiting = await connection.fetchval(
            "SELECT EXISTS(SELECT FROM pg_stat_activity WHERE datname=current_database() "
            "AND wait_event_type='Lock' AND pid<>pg_backend_pid())"
        )
        if waiting:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("The competing authentication transaction never waited for its lock")
    await connection.execute("SELECT pg_sleep(0.3)")


@pytest.mark.parametrize("held_row", ["instance", "ceremonies"])
async def test_finish_rechecks_absolute_deadline_after_final_lock_wait(
    store, monkeypatch, held_row
):
    context, intent, options, key, response = await registration(store)
    final_ready = asyncio.Event()
    release_verifier = asyncio.Event()
    call = store._call

    async def pause_before_final(action, **values):
        if action == "finish_registration":
            final_ready.set()
            await release_verifier.wait()
        return await call(action, **values)

    monkeypatch.setattr(store, "_call", pause_before_final)
    finish = asyncio.create_task(
        store.finish_registration(
            context.token,
            context.data["csrf_token"],
            options["ceremony_id"],
            response,
        )
    )
    await asyncio.wait_for(final_ready.wait(), 2)
    async with store.pool.acquire() as blocker:
        async with blocker.transaction():
            await blocker.execute(f"SELECT 1 FROM dashboard_auth.{held_row} FOR UPDATE")
            await blocker.execute(
                "UPDATE dashboard_auth.ceremonies SET expires_at=clock_timestamp()+interval '200 milliseconds'"
            )
            release_verifier.set()
            await _wait_for_lock_then_deadline(blocker)
    with pytest.raises(AuthError) as denied:
        await asyncio.wait_for(finish, 2)
    assert denied.value.code == "AUTH_RESTART_REQUIRED"
    assert await store.pool.fetchval("SELECT count(*) FROM dashboard_auth.credentials") == 0
    assert await store.pool.fetchval("SELECT count(*) FROM dashboard_auth.sessions") == 0
    assert not await store.pool.fetchval("SELECT consumed FROM dashboard_auth.ceremonies")


async def test_host_approval_rechecks_deadline_after_singleton_lock_wait(store):
    context = await store.context()
    intent = await store.intent(context.token, context.data["csrf_token"], "enroll")
    async with store.pool.acquire() as blocker:
        async with blocker.transaction():
            await blocker.execute("SELECT 1 FROM dashboard_auth.instance FOR UPDATE")
            await blocker.execute(
                "UPDATE dashboard_auth.intents SET expires_at=clock_timestamp()+interval '200 milliseconds'"
            )
            approval = asyncio.create_task(
                host(store, "authorize_registration", request_id=intent["request_id"])
            )
            await _wait_for_lock_then_deadline(blocker)
    with pytest.raises(AuthError) as denied:
        await asyncio.wait_for(approval, 2)
    assert denied.value.code == "AUTH_RESTART_REQUIRED"
    assert not await store.pool.fetchval("SELECT authorized FROM dashboard_auth.intents")


async def test_bad_configured_key_attempts_share_budget_and_do_not_mint_sessions(store):
    store.config.api_key = "synthetic-current-key"
    await host(store, "reconcile_mode", confirm_revoke=True)
    second = OwnerAuthService(store.pool, store.config)
    for i in range(120):
        with pytest.raises(AuthError) as denied:
            await (store if i % 2 else second).key_session("wrong-synthetic-key")
        assert denied.value.code == "UNAUTHORIZED"
    with pytest.raises(AuthError) as limited:
        await second.key_session("synthetic-current-key")
    assert limited.value.code == "RATE_LIMITED"
    assert await store.pool.fetchval("SELECT count(*) FROM dashboard_auth.sessions") == 0
    await store.pool.execute("UPDATE dashboard_auth.rate_buckets SET count=0")
    await store.key_session("synthetic-current-key")
    assert (
        await store.pool.fetchval(
            "SELECT count FROM dashboard_auth.rate_buckets WHERE kind='finish'"
        )
        == 1
    )


async def test_consumed_receipts_remain_for_full_day_before_payload_cleanup(store):
    await enrolled(store)
    await store.pool.execute(
        "UPDATE dashboard_auth.intents SET expires_at=clock_timestamp()-interval '23 hours 30 minutes'; UPDATE dashboard_auth.ceremonies SET expires_at=clock_timestamp()-interval '23 hours 30 minutes'"
    )
    await store.cleanup()
    assert await store.pool.fetchval("SELECT count(*) FROM dashboard_auth.intents") == 1
    assert await store.pool.fetchval("SELECT count(*) FROM dashboard_auth.ceremonies") == 1
