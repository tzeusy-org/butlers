"""The policy_loosen and contain step-up kinds, end to end (bu-dhloi1.29).

Real migrated Switchboard ledger, the real trusted step-up interface and a
real synthetic WebAuthn factor (``authority``). A loosening of owner policy
applies only under a fresh step-up bound to that exact loosening, at that
term version and policy generation, once; tightening needs none. Contain
spends a step-up bound to the partition and its current containment state.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest

from butlers.api.owner_auth.service import AuthError
from butlers.core import source_containment
from butlers.core.dashboard_stepup import consume_stepup, dashboard_stepup_proof
from butlers.core.owner_policy import (
    NotLoosening,
    OwnerPolicyWriter,
    PolicyConflict,
    StepUpRequired,
    loosen_binding,
)
from butlers.core.source_containment import STEP_UP_REQUIRED, Containment, contain_source
from butlers.db import register_jsonb_codec
from butlers.testing.migration import _bootstrap_migration_prerequisites, migration_bootstrap_db_url
from tests.integration.test_dashboard_stepup import authority as authority
from tests.integration.test_dashboard_stepup import signed_binding_proof

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

_SWITCHBOARD = {"core": "switchboard", "switchboard": "switchboard"}


@pytest.fixture
def authority_db_url(fresh_db) -> str:
    """``authority`` on the Switchboard ledger, where both consumers write."""
    return fresh_db("core", "switchboard", schemas=_SWITCHBOARD)


async def _term(pool, constraint="never", expires_at=None):
    async with pool.acquire() as connection:
        receipt = await OwnerPolicyWriter(connection).create(
            uuid4(), "region", "messenger", constraint, expires_at=expires_at
        )
    return UUID(receipt["term_id"])


async def _proof(authority, term_id, action, version, constraint, expires_at=None):
    pool = authority[0]
    async with pool.acquire() as connection:
        binding = await loosen_binding(connection, action, term_id, version, constraint, expires_at)
    return await signed_binding_proof(authority, binding)


async def _loosen(pool, proof, action, term_id, version, constraint, expires_at=None):
    token = dashboard_stepup_proof.set(proof)
    try:
        async with pool.acquire() as connection:
            return await OwnerPolicyWriter(connection).loosen(
                action, term_id, version, constraint=constraint, expires_at=expires_at
            )
    finally:
        dashboard_stepup_proof.reset(token)


async def _state(pool, term_id):
    row = await pool.fetchrow(
        "SELECT term, version, expires_at FROM switchboard.owner_policy_terms WHERE id=$1", term_id
    )
    generation = await pool.fetchval("SELECT generation FROM switchboard.owner_policy_state")
    return tuple(row), generation


async def test_loosening_without_a_step_up_refuses_and_writes_nothing(authority):
    pool = authority[0]
    term_id = await _term(pool)
    before = await _state(pool, term_id)
    async with pool.acquire() as connection:
        writer = OwnerPolicyWriter(connection)
        with pytest.raises(StepUpRequired):
            await writer.loosen(uuid4(), term_id, 1, constraint="always_ask", expires_at=None)
        # The tighten path still refuses a loosening outright.
        with pytest.raises(StepUpRequired):
            await writer.tighten(uuid4(), term_id, 1, constraint="always_ask", expires_at=None)
    assert await _state(pool, term_id) == before


async def test_a_step_up_loosens_once_and_records_a_receipt(authority):
    pool = authority[0]
    term_id = await _term(pool)
    action = uuid4()
    proof = await _proof(authority, term_id, action, 1, "always_ask")
    receipt = await _loosen(pool, proof, action, term_id, 1, "always_ask")
    assert (receipt["action"], receipt["from_version"], receipt["to_version"]) == ("loosen", 1, 2)
    (term, version, _), generation = await _state(pool, term_id)
    assert (term, version, generation) == ("always_ask", 2, receipt["generation"])
    # A retried owner action returns its receipt; it is not a second change.
    assert await _loosen(pool, proof, action, term_id, 1, "always_ask") == receipt
    stored = await pool.fetchrow(
        "SELECT kind, target, result FROM dashboard_stepup.bindings WHERE consumed_at IS NOT NULL "
        "AND kind='policy_loosen'"
    )
    assert stored["kind"] == "policy_loosen"
    assert stored["result"] == {
        "version": 1,
        "kind": "policy_loosen",
        "id": str(action),
        "rule_id": str(term_id),
    }
    # The spent proof cannot authorize another loosening, even of the same term.
    with pytest.raises(StepUpRequired):
        await _loosen(pool, proof, uuid4(), term_id, 2, "always_ask", datetime.now(UTC))
    assert (await _state(pool, term_id))[0][1] == 2


async def test_a_step_up_for_one_loosening_cannot_apply_another(authority):
    pool = authority[0]
    later = datetime.now(UTC) + timedelta(days=7)
    term_id = await _term(pool, "never", later)
    action = uuid4()
    proof = await _proof(authority, term_id, action, 1, "always_ask", later)
    before = await _state(pool, term_id)
    for constraint, expires_at in (
        ("always_ask", later - timedelta(microseconds=1)),  # a different expiry
        ("always_ask", None),  # a different expiry entirely
        ("never", later - timedelta(days=1)),  # a different constraint
    ):
        with pytest.raises(StepUpRequired):
            await _loosen(pool, proof, action, term_id, 1, constraint, expires_at)
        assert await _state(pool, term_id) == before
    # The refused attempts did not spend it: the exact loosening still applies.
    receipt = await _loosen(pool, proof, action, term_id, 1, "always_ask", later)
    assert receipt["to_version"] == 2


async def test_a_stale_step_up_refuses_after_the_policy_changed(authority):
    pool = authority[0]
    term_id = await _term(pool)
    action = uuid4()
    proof = await _proof(authority, term_id, action, 1, "always_ask")
    await _term(pool, "always_ask")  # another change raises the generation
    with pytest.raises(StepUpRequired):
        await _loosen(pool, proof, action, term_id, 1, "always_ask")
    # A tightening of the same term makes the expected version stale.
    stale = await _proof(authority, term_id, uuid4(), 1, "always_ask")
    async with pool.acquire() as connection:
        await OwnerPolicyWriter(connection).tighten(
            uuid4(), term_id, 1, constraint="never", expires_at=None
        )
    with pytest.raises(PolicyConflict):
        await _loosen(pool, stale, uuid4(), term_id, 1, "always_ask")
    assert (await _state(pool, term_id))[0][:2] == ("never", 2)


async def test_tightening_needs_no_step_up_and_a_non_loosening_is_not_one(authority):
    pool = authority[0]
    term_id = await _term(pool, "always_ask")
    async with pool.acquire() as connection:
        receipt = await OwnerPolicyWriter(connection).tighten(
            uuid4(), term_id, 1, constraint="never", expires_at=None
        )
        assert (receipt["action"], receipt["to_version"]) == ("tighten", 2)
        with pytest.raises(NotLoosening):
            await loosen_binding(connection, uuid4(), term_id, 2, "never", None)
    assert await pool.fetchval("SELECT count(*) FROM dashboard_stepup.bindings") == 0


async def test_the_database_refuses_a_loosening_that_no_step_up_bound(authority):
    pool = authority[0]
    term_id = await _term(pool)
    action = uuid4()
    proof = await _proof(authority, term_id, action, 1, "always_ask")
    async with pool.acquire() as connection:
        binding = await loosen_binding(connection, action, term_id, 1, "always_ask", None)
        # Owner SQL outside the writer: refused with no step-up at all.
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await connection.execute(
                "UPDATE switchboard.owner_policy_terms SET term='always_ask', "
                "version=version+1, owner_action_id=$2 WHERE id=$1",
                term_id,
                uuid4(),
            )
        # A step-up admitted in this transaction binds only its own loosening.
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with connection.transaction():
                token = dashboard_stepup_proof.set(proof)
                try:
                    admitted = await consume_stepup(connection, "begin", binding)
                finally:
                    dashboard_stepup_proof.reset(token)
                assert admitted == {"state": "admitted"}
                await connection.execute(
                    "UPDATE switchboard.owner_policy_terms SET term='always_ask', "
                    "expires_at=clock_timestamp(), version=version+1, owner_action_id=$2 "
                    "WHERE id=$1",
                    term_id,
                    uuid4(),
                )
    assert (await _state(pool, term_id))[0][:2] == ("never", 1)


async def test_the_loosen_receipt_keeps_the_downgrade_from_discarding_it(
    authority, authority_db_url
):
    from alembic import command
    from butlers.migrations import _build_alembic_config

    pool = authority[0]
    term_id = await _term(pool)
    action = uuid4()
    proof = await _proof(authority, term_id, action, 1, "always_ask")
    await _loosen(pool, proof, action, term_id, 1, "always_ask")
    config = _build_alembic_config(authority_db_url, ["switchboard"], target_schema="switchboard")
    with pytest.raises(Exception, match="Cannot discard owner policy loosening"):
        await asyncio.to_thread(command.downgrade, config, "switchboard@sw_059")  # pinned-revision
    assert await pool.fetchval("SELECT count(*) FROM switchboard.owner_policy_receipts") == 2


@pytest.fixture
async def runtime(authority_db_url):
    async def role(connection):
        await connection.execute("SET ROLE butler_switchboard_rw")

    pool = await asyncpg.create_pool(
        authority_db_url,
        init=register_jsonb_codec,
        setup=role,
        min_size=1,
        max_size=2,
        server_settings={"search_path": "switchboard,public"},
    )
    try:
        yield pool
    finally:
        await pool.close()


async def _contain_proof(authority, runtime, partition):
    async with runtime.acquire() as connection:
        await connection.execute("SET ROLE butler_switchboard_rw")
        binding = await source_containment.contain_binding(connection, partition)
    return await signed_binding_proof(authority, binding)


async def _contain(runtime, proof, partition):
    token = dashboard_stepup_proof.set(proof)
    try:
        return await contain_source(runtime, partition)
    finally:
        dashboard_stepup_proof.reset(token)


async def test_contain_spends_a_real_step_up_once_for_its_partition(authority, runtime):
    partition = f"message:sender-{uuid4()}"
    proof = await _contain_proof(authority, runtime, partition)
    early = await _contain_proof(authority, runtime, partition)
    other = await _contain_proof(authority, runtime, f"message:sender-{uuid4()}")
    assert await _contain(runtime, other, partition) == STEP_UP_REQUIRED
    contained = await _contain(runtime, proof, partition)
    assert isinstance(contained, Containment) and contained.descendant_goals == ()
    # A retry reads back the same record; it is not a second containment.
    assert await _contain(runtime, proof, partition) == contained
    # A confirmation issued before the containment cannot be spent after it.
    assert await _contain(runtime, early, partition) == STEP_UP_REQUIRED
    result = await authority[0].fetchval(
        "SELECT result FROM dashboard_stepup.bindings WHERE kind='contain' AND result IS NOT NULL"
    )
    assert result == {
        "version": 1,
        "kind": "contain",
        "id": str(contained.id),
        "rule_id": str(contained.id),
    }


async def test_the_bootstrap_upgrades_an_existing_interface_keeping_its_history(
    authority, postgres_container
):
    """The owner's deployment step: replay init-db.sql over a four-kind install."""
    pool, admin = authority[0], authority[5]
    await admin.execute("""
        ALTER TABLE dashboard_stepup.bindings DROP CONSTRAINT bindings_kind_check,
         ADD CONSTRAINT bindings_kind_check CHECK(kind IN
          ('create','from_action','approve_with_rule','promotion'))""")
    # An older consumer routine is replaced in place, keeping its owner and grants.
    await admin.execute("""
        CREATE OR REPLACE FUNCTION dashboard_stepup.consume(
         phase text,raw_nonce text,session_digest text,kind text,target text,
         request_digest text,action_digest text,result jsonb)
        RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp
        AS $$ SELECT '{"state":"refused"}'::jsonb $$""")
    await admin.execute("""INSERT INTO dashboard_stepup.bindings(
      id,session_digest,credential_epoch,session_epoch,kind,target,request_digest,
      action_digest,namespace,expires_at,nonce_digest,consumed_at,consumed_tx,result)
      VALUES('synthetic-history',repeat('a',64),1,1,'create','new',repeat('b',64),
      repeat('c',64),current_database()||':public',clock_timestamp(),repeat('d',64),
      clock_timestamp(),1,'{"version":1,"kind":"create","id":"00000000-0000-0000-0000-000000000001","rule_id":"00000000-0000-0000-0000-000000000001"}')""")
    history = await admin.fetchval(
        "SELECT to_jsonb(b)::text FROM dashboard_stepup.bindings b WHERE id='synthetic-history'"
    )
    term_id = await _term(pool)
    with pytest.raises(AuthError):  # the four-kind catalog refuses the kind
        await _proof(authority, term_id, uuid4(), 1, "always_ask")
    name = await pool.fetchval("SELECT current_database()")
    migration_role = await pool.fetchval("SELECT current_user")
    await asyncio.to_thread(
        _bootstrap_migration_prerequisites,
        migration_bootstrap_db_url(postgres_container, name),
        migration_role,
    )
    assert history == await admin.fetchval(
        "SELECT to_jsonb(b)::text FROM dashboard_stepup.bindings b WHERE id='synthetic-history'"
    )
    assert (
        await admin.fetchval(
            "SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE oid="
            "'dashboard_stepup.consume(text,text,text,text,text,text,text,jsonb)'::regprocedure"
        )
        == "dashboard_stepup_owner"
    )
    action = uuid4()
    proof = await _proof(authority, term_id, action, 1, "always_ask")
    assert (await _loosen(pool, proof, action, term_id, 1, "always_ask"))["action"] == "loosen"
    # Replaying again changes nothing: the upgrade is idempotent.
    await asyncio.to_thread(
        _bootstrap_migration_prerequisites,
        migration_bootstrap_db_url(postgres_container, name),
        migration_role,
    )
    assert history == await admin.fetchval(
        "SELECT to_jsonb(b)::text FROM dashboard_stepup.bindings b WHERE id='synthetic-history'"
    )
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM dashboard_stepup.bindings WHERE kind='policy_loosen' "
            "AND result IS NOT NULL"
        )
        == 1
    )


async def test_the_dashboard_loosens_through_options_finish_and_the_step_up_header(authority):
    import httpx
    from fastapi import FastAPI

    from butlers.api.deps import get_db
    from butlers.api.owner_auth.config import OwnerAuthConfig
    from butlers.api.owner_auth.http import OwnerAuthMiddleware
    from butlers.api.routers import owner_policy
    from butlers.testing.db_manager import db_manager
    from tests.api.owner_auth_fixtures import ORIGIN, RP

    pool, service, key, handle, session, _ = authority
    term_id = await _term(pool)
    config = OwnerAuthConfig(ORIGIN, RP)
    app = FastAPI()
    app.state.owner_auth_service = service
    app.add_middleware(OwnerAuthMiddleware, config=config)
    app.include_router(owner_policy.router)
    app.dependency_overrides[get_db] = lambda: db_manager({"switchboard": pool})
    change = {"owner_action_id": str(uuid4()), "expected_version": 1, "term": "always_ask"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=ORIGIN,
        cookies={config.owner_cookie: session.token},
        headers={"Origin": ORIGIN, "X-CSRF-Token": session.data["csrf_token"]},
    ) as client:
        loosen = f"/api/owner-policy/terms/{term_id}/loosen"
        refused = await client.post(loosen, json=change)
        assert (refused.status_code, refused.json()["detail"]["error"]) == (
            403,
            "dashboard_step_up_required",
        )
        tightening = await client.post(
            "/api/owner-policy/step-up/options",
            json={**change, "term_id": str(term_id), "term": "never"},
        )
        assert tightening.json()["data"] == {"required": False}
        options = await client.post(
            "/api/owner-policy/step-up/options", json={**change, "term_id": str(term_id)}
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
        first = await client.post(loosen, json=change, headers=proof)
        second = await client.post(loosen, json=change, headers=proof)
    assert first.status_code == second.status_code == 200
    assert first.json()["data"] == second.json()["data"]
    assert first.json()["data"]["action"] == "loosen"
    assert (await _state(pool, term_id))[0][:2] == ("always_ask", 2)
