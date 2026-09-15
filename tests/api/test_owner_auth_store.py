"""Real verifier + PostgreSQL authority, concurrency and privilege contracts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import asyncpg
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from fido2.cose import ES256, RS256
from fido2.webauthn import AttestationObject, AttestedCredentialData, AuthenticatorData

from butlers.api.owner_auth.service import AuthError, OwnerAuthService, _digest
from butlers.api.owner_auth.verifier import encode

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]
ORIGIN = "https://butlers.example.test"
RP = "butlers.example.test"
SQL = (
    Path(__file__).resolve().parents[2] / "alembic/versions/core/core_239_dashboard_owner_auth.sql"
)


@dataclass
class Passkey:
    key: object
    credential_id: bytes
    be: bool = True
    counter: int = 0

    @classmethod
    def create(cls, *, rsa_key=False, be=True):
        key = (
            rsa.generate_private_key(public_exponent=65537, key_size=2048)
            if rsa_key
            else ec.generate_private_key(ec.SECP256R1())
        )
        return cls(key, secrets.token_bytes(32), be)

    def response(
        self, options, *, register, origin=ORIGIN, rp=RP, uv=True, bs=False, user_handle=None
    ):
        public = options["publicKey"]
        client = json.dumps(
            {
                "type": "webauthn.create" if register else "webauthn.get",
                "challenge": public["challenge"],
                "origin": origin,
                "crossOrigin": False,
            }
        ).encode()
        flags = 1 | (4 if uv else 0) | (8 if self.be else 0) | (16 if bs else 0)
        if register:
            cose = (
                RS256.from_cryptography_key(self.key.public_key())
                if isinstance(self.key, rsa.RSAPrivateKey)
                else ES256.from_cryptography_key(self.key.public_key())
            )
            data = AttestedCredentialData.create(b"\0" * 16, self.credential_id, cose)
            auth = AuthenticatorData.create(
                hashlib.sha256(rp.encode()).digest(), flags | 64, self.counter, data
            )
            response = {
                "clientDataJSON": encode(client),
                "attestationObject": encode(AttestationObject.create("none", auth, {})),
                "transports": ["hybrid"],
            }
        else:
            auth = AuthenticatorData.create(
                hashlib.sha256(rp.encode()).digest(), flags, self.counter
            )
            message = bytes(auth) + hashlib.sha256(client).digest()
            signature = (
                self.key.sign(message, padding.PKCS1v15(), hashes.SHA256())
                if isinstance(self.key, rsa.RSAPrivateKey)
                else self.key.sign(message, ec.ECDSA(hashes.SHA256()))
            )
            response = {
                "clientDataJSON": encode(client),
                "authenticatorData": encode(auth),
                "signature": encode(signature),
                "userHandle": user_handle,
            }
        return {
            "id": encode(self.credential_id),
            "rawId": encode(self.credential_id),
            "type": "public-key",
            "clientExtensionResults": {},
            "response": response,
        }


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
        # Simulate a concurrently committed assertion after crypto read.
        original = store.verifier.verify

        def concurrent(ceremony, credential, stored):
            return original(ceremony, credential, stored)

        store.verifier.verify = concurrent
        await store.pool.execute("UPDATE dashboard_auth.credentials SET counter=5 WHERE active")
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
    with pytest.raises(AuthError, match="rate limit"):
        await store.context()
    await store.pool.execute(
        "UPDATE dashboard_auth.rate_buckets SET minute=clock_timestamp()-interval '2 minutes'; UPDATE dashboard_auth.contexts SET expires_at=clock_timestamp()-interval '1 second'"
    )
    assert await store.context()
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
