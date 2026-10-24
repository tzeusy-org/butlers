"""REQ-capability-authorization-009: owning goal evidence under real PostgreSQL.

These bodies are authored for hosted execution, NOT RUN locally. They exercise
the owning repository under the real runtime role, not authenticated ingress,
central replay, a production profile or a committed external tool effect.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote, urlparse
from uuid import uuid4

import asyncpg
import pytest

from butlers.core.capability_json import arguments_digest
from butlers.core.goal_stop import CancelIdentity
from butlers.db import register_jsonb_codec
from butlers.modules.approvals.delivery_lifecycle import transition_pending_action
from butlers.modules.approvals.goal_control import GoalApprovalsUnavailable, OwningGoalApprovals
from butlers.modules.approvals.models import ActionStatus, GoalCallLineage
from butlers.modules.approvals.park import ParkRequest
from butlers.testing.migration import (
    create_migrated_test_db,
    init_db_sql_for_dbapi,
    migration_bootstrap_db_url,
    migration_db_name,
)

# Synchronous provisioning stays outside the event loop; the Home fixture is unchanged.

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture(scope="module")
def owning_url(postgres_container):
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "approvals"],
        schemas={"core": "home", "approvals": "home"},
    )


async def _close(pool):
    try:
        await asyncio.wait_for(pool.close(), timeout=10)
    except BaseException:
        pool.terminate()
        raise


@pytest.fixture
async def stores(owning_url):
    async def runtime_role(connection):
        await connection.execute("SET ROLE butler_home_rw")

    async with AsyncExitStack() as cleanup:
        pools = []
        for setup in (None, None, runtime_role):
            pool = await asyncpg.create_pool(
                owning_url,
                init=register_jsonb_codec,
                setup=setup,
                server_settings={"search_path": "home,public"},
                min_size=1,
                max_size=3,
                command_timeout=5,
            )
            cleanup.push_async_callback(_close, pool)
            pools.append(pool)
        owner, observer, runtime = pools
        async with owner.acquire() as first, observer.acquire() as second:
            assert await first.fetchval("SELECT pg_backend_pid()") != await second.fetchval(
                "SELECT pg_backend_pid()"
            )
        yield owner, observer, runtime


def request():
    action = uuid4()
    args = {"message": "controlled owning call"}
    lineage = GoalCallLineage(
        uuid4(), uuid4(), 0, True, "owner", action, "home__send", arguments_digest(args)
    )
    now = datetime.now(UTC)
    return ParkRequest(
        action,
        "send",
        args,
        None,
        now,
        now + timedelta(hours=1),
        origin_butler="home",
        goal_lineage=lineage,
    )


def identity(call):
    line = call.goal_lineage
    return CancelIdentity(
        line.goal_id,
        line.goal_effect_id,
        "home",
        call.action_id,
        line.goal_capability,
        line.goal_args_digest,
    )


async def snapshot(observer, action):
    async with observer.acquire() as conn, conn.transaction(isolation="repeatable_read"):
        row = await conn.fetchrow("SELECT * FROM pending_actions WHERE id=$1", action)
        gate = await conn.fetchrow("SELECT * FROM approval_goal_effects WHERE effect_id=$1", action)
        receipts = await conn.fetch(
            "SELECT * FROM approval_goal_receipts WHERE effect_id=$1 "
            "ORDER BY state_version,outcome",
            action,
        )
        return row, gate, receipts


async def test_real_park_aliases_current_lookup_history_and_off_delivery(stores):
    owner, observer, runtime = stores
    service, call = OwningGoalApprovals(runtime, "home"), request()
    # The fresh migration must replace bootstrap's broad default table ACLs,
    # before any later bootstrap replay has an opportunity to repair them.
    assert (
        await runtime.fetchval(
            """
        SELECT count(*)=2 AND bool_and(
          has_table_privilege(current_user,c.oid,'SELECT')
          AND has_table_privilege(current_user,c.oid,'INSERT')
          AND NOT has_table_privilege(current_user,c.oid,'DELETE')
          AND NOT has_table_privilege(current_user,c.oid,'TRIGGER')
          AND NOT has_table_privilege(current_user,c.oid,'TRUNCATE')
          AND NOT has_table_privilege(current_user,c.oid,'REFERENCES')
          AND has_table_privilege(current_user,c.oid,'UPDATE')=
              (c.relname='approval_goal_effects'))
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=current_schema()
          AND c.relname IN ('approval_goal_effects','approval_goal_receipts')
        """
        )
        is True
    )
    assert await service.lookup(identity(call)) is None
    assert await snapshot(observer, call.action_id) == (None, None, [])
    admission, parked = await service.park(call)
    duplicate, repeated = await service.park(call)
    pending = await service.lookup(identity(call))
    assert admission.action_id == call.action_id and not admission.duplicate
    assert duplicate.duplicate and repeated == parked
    assert parked["outcome"] == "parked" and pending["outcome"] == "pending"
    assert parked["receipt_id"] != pending["receipt_id"]
    row, gate, receipts = await snapshot(observer, call.action_id)
    assert row["tool_args"] == call.tool_args and row["goal_exposed"] is True
    assert gate["state_version"] == 1 and not gate["stop_fenced"]
    assert {r["outcome"] for r in receipts} == {"parked", "pending"}
    assert (
        await owner.fetchval(
            "SELECT count(*) FROM approval_delivery_intents WHERE action_id=$1", call.action_id
        )
        == 0
    )
    before = await snapshot(observer, call.action_id)
    with pytest.raises(ValueError, match="goal_identity_mismatch"):
        await service.cancel(replace(identity(call), goal_id=uuid4()))
    assert await snapshot(observer, call.action_id) == before


@pytest.mark.parametrize("decision", ["approved", "rejected", "expired"])
async def test_real_decision_and_stop_preserve_historical_receipts(stores, decision):
    owner, observer, runtime = stores
    service, call = OwningGoalApprovals(runtime, "home"), request()
    _, parked = await service.park(call)
    # A real owning SQL edit, not caller replacement arguments supplied to replay.
    edited = {"message": "controlled owner edit"}
    await owner.execute(
        "UPDATE pending_actions SET tool_args=$2 WHERE id=$1", call.action_id, edited
    )
    result = await service.transition(
        identity(call),
        target_status=ActionStatus(decision),
        decided_by="owner",
        event_actor="owner",
        event_reason="controlled owning decision",
    )
    assert result.changed and result.action.status.value == decision
    row, gate, receipts = await snapshot(observer, call.action_id)
    assert gate["state_version"] == 2 and row["status"] == decision
    assert row["goal_args_digest"] == call.goal_lineage.goal_args_digest
    historical = next(r for r in receipts if r["outcome"] == "parked")
    assert historical["digest"] == parked["digest"] and historical["state_version"] == 1
    if decision == "approved":
        assert row["goal_approved_args_digest"] == arguments_digest(edited)
        assert (await service.lookup(identity(call)))["outcome"] == "approved"
    else:
        assert row["goal_approved_args_digest"] is None
        negative = await service.lookup(identity(call))
        expected = identity(call)
        assert negative["outcome"] == decision and negative["approval_ref"] == str(call.action_id)
        assert negative["identity"] == {
            "goal_id": str(expected.goal_id),
            "effect_id": str(expected.effect_id),
            "owning_domain": expected.owning_domain,
            "action_id": str(expected.action_id),
            "capability": expected.capability,
            "args_digest": expected.args_digest,
        }
        committed_row, committed_gate, committed_receipts = await snapshot(observer, call.action_id)
        current = next(r for r in committed_receipts if r["outcome"] == decision)
        assert committed_row["status"] == decision and committed_gate["state_version"] == 2
        assert current["state_version"] == 2 and current["digest"] == negative["digest"]
        assert str(current["receipt_id"]) == negative["receipt_id"]
        assert all(
            current[field] == getattr(expected, field)
            for field in (
                "goal_id",
                "effect_id",
                "owning_domain",
                "action_id",
                "capability",
                "args_digest",
            )
        )
        assert current["pending_action_id"] == call.action_id
        assert next(r for r in committed_receipts if r["outcome"] == "parked") == historical
        assert await service.lookup(expected) == negative
        assert await snapshot(observer, call.action_id) == (
            committed_row,
            committed_gate,
            committed_receipts,
        )
    cancelled = await service.cancel(identity(call))
    assert cancelled == await service.lookup(identity(call)) == await service.cancel(identity(call))
    row, gate, receipts = await snapshot(observer, call.action_id)
    assert cancelled["outcome"] == "cancelled" and gate["stop_fenced"]
    assert row["status"] == ("cancelled" if decision == "approved" else decision)
    assert gate["state_version"] == 3
    assert (
        await owner.fetchval(
            "SELECT count(*) FROM approval_events WHERE action_id=$1 AND event_type='action_cancelled'",
            call.action_id,
        )
        == 1
    )
    with pytest.raises(ValueError, match="goal_stop_fenced"):
        await service.park(call)


class LostCommitReply:
    """Drop one response AFTER the actual connection transaction committed."""

    def __init__(self, connection):
        self.connection = connection

    def __getattr__(self, name):
        return getattr(self.connection, name)

    @asynccontextmanager
    async def transaction(self):
        async with self.connection.transaction():
            yield
        raise ConnectionError("controlled_postcommit_response_loss")


async def test_real_commit_loss_readback_and_receipt_failure_rollback(stores):
    owner, observer, runtime = stores
    call = request()
    async with runtime.acquire() as connection:
        with pytest.raises(ConnectionError, match="controlled_postcommit_response_loss"):
            await OwningGoalApprovals(LostCommitReply(connection), "home").cancel(identity(call))
    service = OwningGoalApprovals(runtime, "home")
    receipt = await service.lookup(identity(call))
    assert receipt["outcome"] == "fenced_absent"
    assert receipt == await service.cancel(identity(call))
    row, gate, receipts = await snapshot(observer, call.action_id)
    assert row is None and gate["stop_fenced"] and len(receipts) == 1
    with pytest.raises(ValueError, match="goal_stop_fenced"):
        await service.park(call)
    failed = request()
    # Deliberate database refusal after action/gate writes. It is installed by
    # the disposable schema owner, never by the runtime role.
    await owner.execute("""
        CREATE FUNCTION home.controlled_goal_receipt_refusal() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          RAISE EXCEPTION 'controlled receipt refusal' USING ERRCODE='23514';
        END $$;
        CREATE TRIGGER controlled_goal_receipt_refusal BEFORE INSERT
          ON home.approval_goal_receipts FOR EACH ROW
          EXECUTE FUNCTION home.controlled_goal_receipt_refusal()
    """)
    try:
        with pytest.raises(asyncpg.CheckViolationError, match="controlled receipt refusal"):
            await service.park(failed)
        assert await snapshot(observer, failed.action_id) == (None, None, [])
    finally:
        await owner.execute(
            "DROP TRIGGER controlled_goal_receipt_refusal ON home.approval_goal_receipts; "
            "DROP FUNCTION home.controlled_goal_receipt_refusal()"
        )
    assert (await service.park(failed))[0].action_id == failed.action_id


async def _wait_for_lock(observer, application):
    async with asyncio.timeout(2):
        while not await observer.fetchval(  # noqa: ASYNC110 -- bounded actual PostgreSQL wait observation
            "SELECT EXISTS(SELECT FROM pg_stat_activity WHERE application_name=$1 "
            "AND wait_event_type='Lock')",
            application,
        ):
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("first", ["park", "cancel"])
async def test_real_park_stop_both_lock_orders(stores, owning_url, first):
    owner, observer, runtime = stores
    call = request()
    service = OwningGoalApprovals(runtime, "home")
    tasks = []
    async with AsyncExitStack() as cleanup:
        connections = []
        applications = ["controlled_owning_race_" + uuid4().hex for _ in range(2)]
        for application in applications:
            connection = await asyncpg.connect(
                owning_url,
                server_settings={"search_path": "home,public", "application_name": application},
                command_timeout=5,
            )
            cleanup.push_async_callback(_close_connection, connection)
            await register_jsonb_codec(connection)
            await connection.execute("SET ROLE butler_home_rw")
            connections.append(connection)
        try:
            async with owner.acquire() as held, held.transaction():
                await held.execute(
                    "SELECT pg_advisory_xact_lock(hashtext('approval-delivery:' || current_schema()))"
                )
                # Observe each contender actually waiting before admitting the
                # next. Both real transactions are queued before lock release.
                for index, operation in enumerate((first, "cancel" if first == "park" else "park")):
                    contender = OwningGoalApprovals(connections[index], "home")
                    tasks.append(
                        asyncio.create_task(
                            contender.park(call)
                            if operation == "park"
                            else contender.cancel(identity(call))
                        )
                    )
                    await _wait_for_lock(observer, applications[index])
                assert all(not task.done() for task in tasks)
                assert (
                    await observer.fetchval(
                        "SELECT count(*) FROM pg_stat_activity WHERE application_name=ANY($1::text[]) "
                        "AND wait_event_type='Lock'",
                        applications,
                    )
                    == 2
                )
            outcomes = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), timeout=5
            )
            assert not isinstance(outcomes[0], BaseException)
            if first == "park":
                assert outcomes[1]["outcome"] == "cancelled"
            else:
                assert isinstance(outcomes[1], ValueError)
                assert str(outcomes[1]) == "goal_stop_fenced"
            row, gate, receipts = await snapshot(observer, call.action_id)
            assert gate["stop_fenced"] and gate["dispatch_intent_id"] is None
            assert row is None if first == "cancel" else row["status"] == "cancelled"
            assert (await service.lookup(identity(call)))["outcome"] == (
                "fenced_absent" if first == "cancel" else "cancelled"
            )
            assert len(receipts) == (1 if first == "cancel" else 3)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=5)


async def _close_connection(connection):
    try:
        await asyncio.wait_for(connection.close(), timeout=5)
    finally:
        if not connection.is_closed():
            connection.terminate()


async def test_real_bootstrap_roles_null_lineage_and_immutable_history(
    stores,
    owning_url,
    postgres_container,
):
    owner, observer, runtime = stores
    parsed = urlparse(owning_url)
    admin_url = migration_bootstrap_db_url(postgres_container, parsed.path.lstrip("/"))
    admin = await asyncpg.connect(
        urlparse(admin_url)._replace(scheme="postgresql").geturl(), timeout=10
    )
    try:
        await admin.execute(
            "SELECT set_config('butlers.connecting_user',$1,false)", unquote(parsed.username)
        )
        await admin.execute(init_db_sql_for_dbapi())
    finally:
        await asyncio.wait_for(admin.close(), timeout=5)
    service, call = OwningGoalApprovals(runtime, "home"), request()
    await service.park(call)
    before = await snapshot(observer, call.action_id)
    async with runtime.acquire() as connection:
        assert await connection.fetchval("SELECT current_user") == "butler_home_rw"
        for query in (
            "DELETE FROM approval_goal_effects WHERE effect_id=$1",
            "UPDATE approval_goal_receipts SET digest=repeat('a',64) WHERE effect_id=$1",
            "UPDATE approval_goal_effects SET args_digest=repeat('a',64),"
            "state_version=state_version+1 WHERE effect_id=$1",
            "UPDATE pending_actions SET goal_exposed=false WHERE id=$1",
            "UPDATE pending_actions SET goal_id=NULL WHERE id=$1",
        ):
            with pytest.raises(asyncpg.PostgresError):
                async with connection.transaction():
                    await connection.execute(query, call.action_id)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with connection.transaction():
                await connection.execute("ALTER TABLE approval_goal_effects DISABLE TRIGGER ALL")
        assert await snapshot(observer, call.action_id) == before
        # An ordinary legacy row remains valid; partial goal lineage cannot use
        # SQL NULL's CHECK-unknown result to hide a missing binding.
        legacy = uuid4()
        await connection.execute(
            "INSERT INTO pending_actions(id,tool_name,tool_args) VALUES($1,'send',$2)",
            legacy,
            {},
        )
        with pytest.raises(asyncpg.CheckViolationError):
            async with connection.transaction():
                await connection.execute(
                    "UPDATE pending_actions SET goal_id=$2 WHERE id=$1",
                    legacy,
                    uuid4(),
                )
        # Both trigger invocations on an ordinary legacy UPDATE must retain
        # the original execution contract without an assigned goal RECORD.
        edited = {"message": "controlled legacy owner edit"}
        await connection.execute(
            "UPDATE pending_actions SET tool_args=$2 WHERE id=$1", legacy, edited
        )
        decision = await transition_pending_action(
            connection,
            action_id=legacy,
            target_status=ActionStatus.APPROVED,
            decided_by="controlled_legacy_owner",
            event_actor="controlled_legacy_owner",
            event_reason="controlled legacy approval",
        )
        assert decision.changed and decision.action.status is ActionStatus.APPROVED
        legacy_row, legacy_gate, legacy_receipts = await snapshot(observer, legacy)
        assert legacy_row["tool_args"] == edited and legacy_row["status"] == "approved"
        assert legacy_row["goal_origin"] == "legacy" and legacy_row["goal_id"] is None
        assert legacy_gate is None and legacy_receipts == []
        assert (
            await observer.fetchval(
                "SELECT count(*) FROM approval_events WHERE action_id=$1 "
                "AND event_type='action_approved'",
                legacy,
            )
            == 1
        )
        # Retained legacy fixtures use row deletion, never TRUNCATE CASCADE:
        # the latter reaches immutable goal evidence even when tables are empty.
        await connection.execute("DELETE FROM pending_actions WHERE goal_origin = 'legacy'")
        assert await observer.fetchrow("SELECT id FROM pending_actions WHERE id=$1", legacy) is None
        assert await snapshot(observer, call.action_id) == before
        assert (
            await observer.fetchval(
                "SELECT count(*) FROM approval_events WHERE action_id=$1", legacy
            )
            == 1
        )
        with pytest.raises(asyncpg.CheckViolationError):
            async with owner.acquire() as cleanup_owner, cleanup_owner.transaction():
                await cleanup_owner.execute("TRUNCATE pending_actions CASCADE")
        assert await snapshot(observer, call.action_id) == before
        with pytest.raises(GoalApprovalsUnavailable, match="owning_goal_role_unavailable"):
            await OwningGoalApprovals(owner, "home").lookup(identity(call))
        # Runtime TEMP is deliberately unavailable; a shadow attack must be
        # prepared by the administrator before assuming the tested role.
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with connection.transaction():
                await connection.execute("CREATE TEMP TABLE pending_actions(id uuid)")
    shadow = await asyncpg.connect(
        urlparse(admin_url)._replace(scheme="postgresql").geturl(),
        timeout=10,
        command_timeout=5,
        server_settings={"search_path": "home,public"},
    )
    try:
        await shadow.execute("CREATE TEMP TABLE pending_actions(id uuid)")
        shadow_backend = await shadow.fetchval("SELECT pg_backend_pid()")
        async with shadow.transaction():
            await shadow.execute("SET LOCAL ROLE butler_home_rw")
            assert await shadow.fetchval("SELECT current_user") == "butler_home_rw"
            assert await shadow.fetchval("SELECT pg_backend_pid()") == shadow_backend
            assert "pg_temp_" in await shadow.fetchval(
                "SELECT pg_my_temp_schema()::regnamespace::text"
            )
            with pytest.raises(GoalApprovalsUnavailable, match="owning_goal_role_unavailable"):
                await OwningGoalApprovals(shadow, "home").lookup(identity(call))
    finally:
        # Closing this owned backend also removes its temporary relation.
        try:
            await asyncio.wait_for(shadow.close(), timeout=5)
        except BaseException:
            shadow.terminate()
            raise
    assert await snapshot(observer, call.action_id) == before


@pytest.mark.parametrize(
    "table,privilege",
    [
        ("approval_goal_effects", "SELECT"),
        ("approval_goal_effects", "INSERT"),
        ("approval_goal_effects", "UPDATE"),
        ("approval_goal_receipts", "SELECT"),
        ("approval_goal_receipts", "INSERT"),
    ],
)
async def test_real_partial_runtime_acl_refuses_lookup_and_restores(stores, table, privilege):
    owner, observer, runtime = stores
    service, call = OwningGoalApprovals(runtime, "home"), request()
    # A real committed fence is the healthy positive, never a canned receipt.
    receipt = await service.cancel(identity(call))
    before = await snapshot(observer, call.action_id)
    assert receipt == await service.lookup(identity(call))
    # Names come only from the closed test parameter matrix. Each REVOKE is
    # committed before a different runtime backend executes the real consumer.
    await owner.execute(f"REVOKE {privilege} ON home.{table} FROM butler_home_rw")
    try:
        async with runtime.acquire() as connection:
            assert not await connection.fetchval(
                "SELECT has_table_privilege(current_user,$1,$2)", f"home.{table}", privilege
            )
        with pytest.raises(GoalApprovalsUnavailable, match="owning_goal_schema_unavailable"):
            await service.lookup(identity(call))
        assert await snapshot(observer, call.action_id) == before
    finally:
        await owner.execute(f"GRANT {privilege} ON home.{table} TO butler_home_rw")
    assert receipt == await service.lookup(identity(call))
    assert await snapshot(observer, call.action_id) == before


async def _standing_rule_case(owner):
    # A distinct exact content pin prevents this retained module-scoped DB's
    # standing rules from matching another case. No database reset is needed.
    from tests.modules.approvals.test_goal_control import registered_send_catalog

    args = {"message": "controlled-" + uuid4().hex, "recipient": "pinned-target"}
    call = request()
    call = replace(
        call,
        tool_args=args,
        goal_lineage=replace(
            call.goal_lineage,
            goal_args_digest=arguments_digest(args),
        ),
    )
    rule = uuid4()
    await owner.execute(
        "INSERT INTO approval_rules(id,tool_name,arg_constraints,description,max_uses) "
        "VALUES($1,'send',$2::jsonb,'controlled owner rule',1)",
        rule,
        {name: {"type": "exact", "value": value} for name, value in args.items()},
    )
    return call, rule, await registered_send_catalog(domain="home")


async def test_real_standing_rule_commit_loss_duplicate_and_stop_readback(stores):
    from butlers.core.goal_admission import Grant

    owner, observer, runtime = stores
    call, rule, catalog = await _standing_rule_case(owner)
    # An actual commit followed by lost response; lookup, not retry arithmetic,
    # must recover the stored decision and immutable historical aliases.
    async with runtime.acquire() as connection:
        with pytest.raises(ConnectionError, match="controlled_postcommit_response_loss"):
            await OwningGoalApprovals(LostCommitReply(connection), "home", catalog=catalog).park(
                call,
                grant=Grant("home__send"),
            )
    service = OwningGoalApprovals(runtime, "home", catalog=catalog)
    row, gate, receipts = await snapshot(observer, call.action_id)
    assert row["status"] == "approved" and row["approval_rule_id"] == rule
    assert row["goal_approved_args_digest"] == call.goal_lineage.goal_args_digest
    assert gate["phase"] == "approved" and gate["state_version"] == 2
    assert {r["outcome"] for r in receipts} == {"pending", "parked", "approved"}
    assert (await service.lookup(identity(call)))["outcome"] == "approved"
    duplicate, parked = await service.park(call, grant=Grant("home__send"))
    assert duplicate.duplicate and parked["outcome"] == "parked"
    assert await observer.fetchval("SELECT use_count FROM approval_rules WHERE id=$1", rule) == 1
    assert (
        await observer.fetchval(
            "SELECT count(*) FROM approval_events WHERE action_id=$1 "
            "AND event_type='action_auto_approved' AND rule_id=$2",
            call.action_id,
            rule,
        )
        == 1
    )
    assert (await service.cancel(identity(call)))["outcome"] == "cancelled"
    assert (await snapshot(observer, call.action_id))[0]["status"] == "cancelled"
    assert await observer.fetchval("SELECT use_count FROM approval_rules WHERE id=$1", rule) == 1
    with pytest.raises(ValueError, match="goal_stop_fenced"):
        await service.park(call, grant=Grant("home__send"))


async def test_real_standing_rule_receipt_refusal_rolls_back_claim_then_stop_fences(stores):
    from butlers.core.goal_admission import Grant

    owner, observer, runtime = stores
    call, rule, catalog = await _standing_rule_case(owner)
    service = OwningGoalApprovals(runtime, "home", catalog=catalog)
    await owner.execute("""
        CREATE FUNCTION home.controlled_rule_receipt_refusal() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          IF NEW.outcome='approved' THEN
            RAISE EXCEPTION 'controlled rule receipt refusal' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER controlled_rule_receipt_refusal BEFORE INSERT
          ON home.approval_goal_receipts FOR EACH ROW
          EXECUTE FUNCTION home.controlled_rule_receipt_refusal()
    """)
    try:
        with pytest.raises(asyncpg.CheckViolationError, match="controlled rule receipt refusal"):
            await service.park(call, grant=Grant("home__send"))
        assert await snapshot(observer, call.action_id) == (None, None, [])
        assert (
            await observer.fetchval("SELECT use_count FROM approval_rules WHERE id=$1", rule) == 0
        )
        assert (
            await observer.fetchval(
                "SELECT count(*) FROM approval_events WHERE action_id=$1",
                call.action_id,
            )
            == 0
        )
    finally:
        await owner.execute(
            "DROP TRIGGER controlled_rule_receipt_refusal ON home.approval_goal_receipts; "
            "DROP FUNCTION home.controlled_rule_receipt_refusal()"
        )
    assert (await service.cancel(identity(call)))["outcome"] == "fenced_absent"
    with pytest.raises(ValueError, match="goal_stop_fenced"):
        await service.park(call, grant=Grant("home__send"))
    assert await observer.fetchval("SELECT use_count FROM approval_rules WHERE id=$1", rule) == 0


@pytest.mark.parametrize("revoke", [False, True])
async def test_real_standing_rule_contenders_observe_exhaustion_or_revoke(
    stores, owning_url, revoke
):
    from butlers.core.goal_admission import Grant

    owner, observer, runtime = stores
    call, rule, catalog = await _standing_rule_case(owner)
    other = request()
    other = replace(
        other,
        tool_args=call.tool_args,
        goal_lineage=replace(
            other.goal_lineage,
            goal_args_digest=call.goal_lineage.goal_args_digest,
        ),
    )
    tasks = []
    async with AsyncExitStack() as cleanup:
        connections = []
        applications = ["controlled_rule_race_" + uuid4().hex for _ in range(2)]
        for application in applications:
            connection = await asyncpg.connect(
                owning_url,
                server_settings={
                    "search_path": "home,public",
                    "application_name": application,
                },
                command_timeout=5,
            )
            cleanup.push_async_callback(_close_connection, connection)
            await register_jsonb_codec(connection)
            await connection.execute("SET ROLE butler_home_rw")
            connections.append(connection)
        try:
            async with owner.acquire() as held, held.transaction():
                await held.fetchrow("SELECT id FROM approval_rules WHERE id=$1 FOR UPDATE", rule)
                for index, contender_call in enumerate((call, other)):
                    contender = OwningGoalApprovals(connections[index], "home", catalog=catalog)
                    tasks.append(
                        asyncio.create_task(
                            contender.park(
                                contender_call,
                                grant=Grant("home__send"),
                            )
                        )
                    )
                    # First waits on the rule; second waits on the same-schema
                    # serializer. Both actual backends must be waiting, not just
                    # Python tasks started, before the owner lock is released.
                    await _wait_for_lock(observer, applications[index])
                assert (
                    await observer.fetchval(
                        "SELECT count(*) FROM pg_stat_activity WHERE application_name=ANY($1::text[]) "
                        "AND wait_event_type='Lock'",
                        applications,
                    )
                    == 2
                )
                assert all(not task.done() for task in tasks)
                if revoke:
                    await held.execute("UPDATE approval_rules SET active=false WHERE id=$1", rule)
            outcomes = await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
            assert len(outcomes) == 2
            snapshots = [await snapshot(observer, c.action_id) for c in (call, other)]
            assert sorted(s[0]["status"] for s in snapshots) == (
                ["pending", "pending"] if revoke else ["approved", "pending"]
            )
            assert await observer.fetchval(
                "SELECT use_count FROM approval_rules WHERE id=$1",
                rule,
            ) == (0 if revoke else 1)
            for row, gate, receipts in snapshots:
                assert gate["phase"] == row["status"] and not gate["stop_fenced"]
                assert len(receipts) == (3 if row["status"] == "approved" else 2)
                assert row["approval_rule_id"] == (rule if row["status"] == "approved" else None)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=5)


@pytest.mark.parametrize("case", ["race", "claim_reply_loss", "consume_reply_loss", "stop"])
async def test_real_signed_owner_witness_consume_commit_and_ambiguity(stores, case):
    """Real roles/019/transactions; handler result never claims an external effect."""
    from types import SimpleNamespace

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from butlers.capability_broker.assertions import AssertionSigner
    from butlers.capability_broker.catalog import project_catalog
    from butlers.capability_broker.contracts import Decision, Disposition, Principal, Reservation
    from butlers.capability_broker.handoff import sign_handoff
    from butlers.core.goal_admission import Grant
    from butlers.modules.approvals.execution_context import get_approval_execution_context
    from butlers.modules.approvals.goal_dispatch import GoalDispatch
    from tests.modules.approvals.test_goal_control import registered_send_catalog

    _, observer, runtime = stores
    call = request()
    arguments = {"message": "original", "recipient": "fixed-test-recipient"}
    line = replace(call.goal_lineage, goal_args_digest=arguments_digest(arguments))
    call = replace(call, tool_args=arguments, goal_lineage=line)
    catalog = await registered_send_catalog(domain="home")
    capability = project_catalog(catalog, domain="home").capabilities[0]
    grant = Grant(capability.identity, park_only=True)
    principal = Principal(
        line.goal_id, line.invocation_id, 0, "owner", True, "test-fleet", (grant,)
    )
    original = Reservation(
        principal,
        capability,
        Decision(Disposition.PARK),
        uuid4(),
        call.action_id,
        line.goal_args_digest,
        grant,
    )
    signer = AssertionSigner("fixture", Ed25519PrivateKey.generate())
    invocations = []

    async def handler(**kwargs):
        # Independent connection observes COMMITTED consume before handler entry.
        consumed = await observer.fetchval(
            "SELECT dispatch_consumed_at IS NOT NULL FROM approval_goal_effects WHERE effect_id=$1",
            call.action_id,
        )
        assert consumed is True
        assert get_approval_execution_context(tool_name="send", tool_args=kwargs) is not None
        invocations.append(dict(kwargs))
        return {"status": "fixture-only-not-an-effect-receipt"}

    dispatch = GoalDispatch(
        runtime, "home", catalog, {"send": handler}, {"fixture": signer.private_key.public_key()}
    )
    peer = SimpleNamespace(role="broker-control", domain="home")
    version = None
    prepared = original

    def envelope(purpose):
        value = {
            "protocol": "broker-approvals-v2",
            "operation": purpose,
            "request_id": str(uuid4()),
            "identity": {
                "goal_id": str(line.goal_id),
                "effect_id": str(call.action_id),
                "action_id": str(call.action_id),
                "owning_domain": "home",
                "capability": capability.identity,
                "args_digest": line.goal_args_digest,
            },
            "handoff": sign_handoff(
                signer,
                prepared,
                purpose=purpose,
                issued_at=int(datetime.now(UTC).timestamp()),
                approval_ref=str(call.action_id) if version is not None else None,
                original_args_digest=line.goal_args_digest,
                snapshot_version=version,
            ),
        }
        if purpose == "PARK":
            value["arguments"] = arguments
        return value

    await dispatch(envelope("PARK"), peer)
    await dispatch.owner.transition(
        identity(call),
        target_status=ActionStatus.APPROVED,
        decided_by="human:test-owner",
        event_actor="test-owner",
        event_reason="test",
    )
    observed = await dispatch(envelope("SNAPSHOT"), peer)
    version = observed["receipt"]["version"]
    prepared = replace(original, call_id=uuid4(), decision=Decision(Disposition.DISPATCH))
    if case == "stop":
        await dispatch.owner.cancel(identity(call))
        with pytest.raises(GoalApprovalsUnavailable):
            await dispatch(envelope("CLAIM"), peer)
        row, gate, _ = await snapshot(observer, call.action_id)
        assert row["status"] == "cancelled" and gate["dispatch_intent_id"] is None
        assert not invocations
        return
    if case == "claim_reply_loss":
        async with runtime.acquire() as conn:
            dispatch.owner.pool = LostCommitReply(conn)
            with pytest.raises(ConnectionError, match="controlled_postcommit_response_loss"):
                await dispatch(envelope("CLAIM"), peer)
        dispatch.owner.pool = runtime
        witness = await dispatch(envelope("WITNESS"), peer)
        assert witness["receipt"]["intent_id"] == str(prepared.call_id)
        assert not invocations
    else:
        await dispatch(envelope("CLAIM"), peer)
    if case == "consume_reply_loss":
        async with runtime.acquire() as conn:
            dispatch.owner.pool = LostCommitReply(conn)
            with pytest.raises(ConnectionError, match="controlled_postcommit_response_loss"):
                await dispatch(envelope("EXECUTE"), peer)
        dispatch.owner.pool = runtime
        with pytest.raises(GoalApprovalsUnavailable):
            await dispatch(envelope("EXECUTE"), peer)
        with pytest.raises(GoalApprovalsUnavailable):
            await dispatch(envelope("WITNESS"), peer)
        assert not invocations
    else:
        results = await asyncio.gather(
            dispatch(envelope("EXECUTE"), peer),
            dispatch(envelope("EXECUTE"), peer),
            return_exceptions=True,
        )
        assert sum(isinstance(v, dict) and v["outcome"] == "unknown" for v in results) == 1
        assert sum(isinstance(v, GoalApprovalsUnavailable) for v in results) == 1
        assert invocations == [arguments]
    row, gate, receipts = await snapshot(observer, call.action_id)
    assert row["status"] == "approved" and gate["phase"] == "dispatch_intent"
    assert (
        gate["dispatch_intent_id"] == prepared.call_id and gate["dispatch_consumed_at"] is not None
    )
    assert {r["outcome"] for r in receipts} == {"pending", "parked", "approved"}


@pytest.mark.parametrize(
    "entry",
    [
        "reject",
        "module_reject",
        "expire",
        "module_expire",
        "defer_expire",
        "rollback",
        "stale_gate",
    ],
)
async def test_real_negative_goal_decision_commits_matching_gate_or_rolls_everything_back(
    stores, entry
):
    """Real019 deferred final guard/runtime role and independent committed readback."""
    from butlers.modules.approvals import operations
    from butlers.modules.approvals.delivery_lifecycle import defer_pending_action
    from butlers.modules.approvals.models import PendingAction
    from butlers.modules.approvals.module import ApprovalsModule

    owner, observer, runtime = stores
    call = request()
    expired = entry in {"expire", "module_expire", "defer_expire"}
    if expired:
        call = replace(call, expires_at=datetime.now(UTC) - timedelta(hours=1))
    await OwningGoalApprovals(runtime, "home").park(call)
    before = await snapshot(observer, call.action_id)
    before_events = await observer.fetchval(
        "SELECT count(*) FROM approval_events WHERE action_id=$1", call.action_id
    )
    if entry == "stale_gate":
        with pytest.raises(asyncpg.CheckViolationError, match="invalid final goal state"):
            async with runtime.acquire() as conn, conn.transaction():
                await conn.execute(
                    "UPDATE pending_actions SET status='rejected' WHERE id=$1", call.action_id
                )
        assert await snapshot(observer, call.action_id) == before
        return
    if entry == "rollback":
        await owner.execute("""
            CREATE FUNCTION home.controlled_negative_gate_refusal() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
              IF NEW.phase='negative_terminal' THEN
                RAISE EXCEPTION 'controlled negative gate refusal' USING ERRCODE='23514';
              END IF;
              RETURN NEW;
            END $$;
            CREATE TRIGGER controlled_negative_gate_refusal BEFORE UPDATE
              ON home.approval_goal_effects FOR EACH ROW
              EXECUTE FUNCTION home.controlled_negative_gate_refusal()
        """)
        try:
            with pytest.raises(
                asyncpg.CheckViolationError, match="controlled negative gate refusal"
            ):
                await operations.reject_action(runtime, str(call.action_id), actor_id="owner")
            assert await snapshot(observer, call.action_id) == before
            assert (
                await observer.fetchval(
                    "SELECT count(*) FROM approval_events WHERE action_id=$1", call.action_id
                )
                == before_events
            )
        finally:
            await owner.execute(
                "DROP TRIGGER controlled_negative_gate_refusal ON home.approval_goal_effects; "
                "DROP FUNCTION home.controlled_negative_gate_refusal()"
            )
        return
    if entry.startswith("module_"):
        module = ApprovalsModule()
        module._db = runtime
        module._decision_memory_writer = None
        if entry == "module_reject":
            await module._reject_action(
                str(call.action_id), actor={"type": "human", "id": "owner", "authenticated": True}
            )
        else:
            result = await module._expire_stale_actions()
            assert str(call.action_id) in result["expired_ids"]
    elif entry == "reject":
        assert "error" not in await operations.reject_action(
            runtime, str(call.action_id), actor_id="owner"
        )
    elif entry == "expire":
        result = await operations.expire_pending_action_if_stale(
            runtime, PendingAction.from_row(before[0])
        )
        assert "expired" in result["error"]
    else:
        result = await defer_pending_action(
            runtime, action_id=call.action_id, hours=2, actor="owner"
        )
        assert result.changed and result.expired_instead
    row, gate, receipts = await snapshot(observer, call.action_id)
    assert row["status"] == ("expired" if expired else "rejected")
    assert gate["phase"] == "negative_terminal" and gate["state_version"] == 2
    assert gate["dispatch_intent_id"] is None and gate["stop_fenced"] is False
    assert receipts == before[2]  # Preserve immutable parked/pending history.
    assert (
        await observer.fetchval(
            "SELECT count(*) FROM approval_events WHERE action_id=$1", call.action_id
        )
        > before_events
    )


@pytest.mark.parametrize("fault", [None, "event", "audit"])
async def test_real_goal_abandon_HTTP_audit_and_event_share_gate_commit(stores, fault):
    """Actual owner HTTP route/019/runtime SQL; failed event or audit cannot commit."""
    import httpx
    from fastapi import FastAPI

    from butlers.api.deps import get_db
    from butlers.api.owner_auth.config import OwnerAuthConfig
    from butlers.api.owner_auth.http import OwnerAuthMiddleware
    from butlers.api.routers import approvals as route
    from tests.api.auth_helpers import _DomainOwnerState

    owner, observer, runtime = stores
    call = request()
    service = OwningGoalApprovals(runtime, "home")
    await service.park(call)
    await service.transition(
        identity(call),
        target_status=ActionStatus.APPROVED,
        decided_by="controlled-owner",
        event_actor="controlled-owner",
        event_reason="controlled",
    )
    before = await snapshot(observer, call.action_id)
    before_events = await observer.fetchval(
        "SELECT count(*) FROM approval_events WHERE action_id=$1", call.action_id
    )
    table = "home.approval_events" if fault == "event" else "public.audit_log"
    condition = (
        "NEW.event_type='action_abandoned'" if fault == "event" else "NEW.action='approval.abandon'"
    )
    if fault is not None:
        await owner.execute(f"""
          CREATE FUNCTION home.controlled_abandon_refusal() RETURNS trigger
          LANGUAGE plpgsql AS $$ BEGIN
            IF {condition} THEN
              RAISE EXCEPTION 'controlled abandon refusal' USING ERRCODE='23514';
            END IF;
            RETURN NEW;
          END $$;
          CREATE TRIGGER controlled_abandon_refusal BEFORE INSERT ON {table}
            FOR EACH ROW EXECUTE FUNCTION home.controlled_abandon_refusal()
        """)

    class Manager:
        butler_names = ["home"]

        def pool(self, name):
            assert name == "home"
            return runtime

    key = "synthetic-goal-abandon-owner-key"
    app = FastAPI()
    app.state.owner_auth_service = _DomainOwnerState(key)
    app.add_middleware(
        OwnerAuthMiddleware, config=OwnerAuthConfig(origin=None, rp_id=None, api_key=key)
    )
    app.include_router(route.router)
    app.dependency_overrides[get_db] = lambda: Manager()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
            headers={"X-API-Key": key},
        ) as client:
            response = await client.post(
                f"/api/approvals/{call.action_id}/abandon",
                json={"reason": "controlled owner decision"},
            )
        assert response.status_code == (200 if fault is None else 409 if fault == "event" else 500)
        after = await snapshot(observer, call.action_id)
        audits = await observer.fetch(
            "SELECT actor,result FROM public.audit_log "
            "WHERE action='approval.abandon' AND target=$1",
            str(call.action_id),
        )
        event_count = await observer.fetchval(
            "SELECT count(*) FROM approval_events WHERE action_id=$1", call.action_id
        )
        if fault is not None:
            assert after == before and event_count == before_events and audits == []
        else:
            row, gate, receipts = after
            assert row["status"] == "abandoned" and gate["phase"] == "negative_terminal"
            assert gate["state_version"] == before[1]["state_version"] + 1
            assert gate["approved_args_digest"] == before[1]["approved_args_digest"]
            assert receipts == before[2] and event_count == before_events + 1
            assert len(audits) == 1 and audits[0]["result"] == "success"
            assert audits[0]["actor"] == "dashboard:rest-api"
    finally:
        if fault is not None:
            await owner.execute(
                f"DROP TRIGGER controlled_abandon_refusal ON {table}; "
                "DROP FUNCTION home.controlled_abandon_refusal()"
            )


@pytest.mark.parametrize("refuse_finish", [False, True])
async def test_real_module_goal_commit_precedes_independent_observations(
    stores, monkeypatch, refuse_finish
):
    """Real019/runtime rollback/readback; memory store is an independent recorder.

    Authored/collected ONLY locally. The actual DecisionMemoryWriter invokes the
    recorder after commit; this does not claim real memory-store/model execution.
    REQ-capability-authorization-009.
    """
    from butlers.modules.approvals import decision_memory, delivery_lifecycle
    from butlers.modules.approvals.decision_memory import DecisionMemoryWriter
    from butlers.modules.approvals.module import ApprovalsModule

    owner, observer, runtime = stores
    call = request()
    await OwningGoalApprovals(runtime, "home").park(call)
    before = await snapshot(observer, call.action_id)
    before_events = await observer.fetchval(
        "SELECT count(*) FROM approval_events WHERE action_id=$1", call.action_id
    )
    facts, observations = [], []

    async def independent_store(pool, **values):
        assert pool is observer
        # This observer is a physically separate pool from the owning runtime.
        row, gate, receipts = await snapshot(observer, call.action_id)
        count = await observer.fetchval(
            "SELECT count(*) FROM approval_rules WHERE created_from=$1", call.action_id
        )
        facts.append((row["status"], gate["phase"], count, values["metadata"], receipts))

    monkeypatch.setattr(decision_memory, "store_fact", independent_store)
    monkeypatch.setattr(delivery_lifecycle, "emit_lifecycle_success", observations.append)
    module = ApprovalsModule()
    module._db, module._butler_name = runtime, "home"
    module._decision_memory_writer = DecisionMemoryWriter(
        butler_name="home",
        memory_pool_provider=lambda: observer,
        resolution_pool_provider=lambda: runtime,
        embedding_engine_provider=object,
        tool_meta_provider=lambda _: None,
    )
    if refuse_finish:
        await owner.execute("""
            CREATE FUNCTION home.controlled_module_finish_refusal() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
              IF NEW.phase='approved' THEN
                RAISE EXCEPTION 'controlled module finish refusal' USING ERRCODE='23514';
              END IF;
              RETURN NEW;
            END $$;
            CREATE TRIGGER controlled_module_finish_refusal BEFORE UPDATE
              ON home.approval_goal_effects FOR EACH ROW
              EXECUTE FUNCTION home.controlled_module_finish_refusal()
        """)
    try:
        if refuse_finish:
            with pytest.raises(
                asyncpg.CheckViolationError, match="controlled module finish refusal"
            ):
                await module._approve_action(
                    str(call.action_id),
                    create_rule=True,
                    actor={"type": "human", "id": "owner", "authenticated": True},
                )
            assert await snapshot(observer, call.action_id) == before
            assert facts == observations == []
            assert (
                await observer.fetchval(
                    "SELECT count(*) FROM approval_rules WHERE created_from=$1", call.action_id
                )
                == 0
            )
            assert (
                await observer.fetchval(
                    "SELECT count(*) FROM approval_events WHERE action_id=$1", call.action_id
                )
                == before_events
            )
        else:
            result = await module._approve_action(
                str(call.action_id),
                create_rule=True,
                actor={"type": "human", "id": "owner", "authenticated": True},
            )
            assert result["status"] == "approved" and "created_rule" in result
            assert len(facts) == len(observations) == 1
            assert facts[0][:3] == ("approved", "approved", 1)
            assert facts[0][3]["rule_id"] == result["created_rule"]["id"]
            assert facts[0][3]["state"] == "active"
            assert len(facts[0][4]) == len(before[2]) + 1
            assert observations[0].terminal_status == "approved"
            assert (
                await observer.fetchval(
                    "SELECT count(*) FROM approval_events WHERE action_id=$1", call.action_id
                )
                == before_events + 2
            )
    finally:
        if refuse_finish:
            await owner.execute(
                "DROP TRIGGER controlled_module_finish_refusal ON home.approval_goal_effects; "
                "DROP FUNCTION home.controlled_module_finish_refusal()"
            )


@pytest.fixture(scope="module")
def administer_publication_url(postgres_container):
    """Independent central DB; synchronous migration runs outside the async loop."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        ["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
    )


@pytest.mark.parametrize(
    "mode",
    ["stored", "edited", "rule", "exposed-rule", "park-only-rule", "drift", "stop", "rollback"],
)
async def test_real_registered_home_administer_park_and_stored_replay(
    stores, administer_publication_url, monkeypatch, mode
):
    """AUTHOR/COLLECT ONLY: actual central and Home SQL, roles and originals.

    Only the fixed private transport is in-process. An isolated fixture source
    supplies owner admission; this does not authenticate a deployed caller or
    prove a provider effect. No scheduler tick, runner or live daemon is started.
    """
    from types import SimpleNamespace
    from uuid import UUID

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from butlers.capability_broker.assertions import AssertionSigner
    from butlers.capability_broker.catalog import PublishedAuthorities
    from butlers.capability_broker.contracts import BrokerRefused
    from butlers.capability_broker.owning import PrivateApprovals
    from butlers.capability_broker.service import Broker
    from butlers.capability_broker.transport.client import PrivateClient
    from butlers.capability_broker.transport.config import PeerPolicy, TransportConfig
    from butlers.capability_broker.transport.protocol import identity as wire_identity
    from butlers.capability_broker.transport.protocol import request as wire_request
    from butlers.core.catalog_publication import CatalogPublisher
    from butlers.core.goal_admission import (
        Evidence,
        GoalAdmission,
        Grant,
        SourceRef,
        digest,
        encode_grants,
    )
    from butlers.core.goal_stop import GoalStop
    from butlers.modules.approvals.goal_dispatch import GoalDispatch
    from tests.capability_broker.test_published_catalog import registered_home_admin
    from tests.integration.test_catalog_publication import publish_all, runtime_pool
    from tests.switchboard.goal_admission_fixtures import Sources, limits
    from tests.switchboard.goal_ledger_fixtures import configure

    owner, observer, owning = stores
    native = await registered_home_admin(monkeypatch, pool=owning)
    async with owning.acquire() as role_check:
        assert await role_check.fetchval("SELECT current_user") == "butler_home_rw"
    async with AsyncExitStack() as cleanup:
        admin = await asyncpg.create_pool(
            administer_publication_url,
            init=register_jsonb_codec,
            min_size=1,
            max_size=3,
            command_timeout=5,
        )
        cleanup.push_async_callback(_close, admin)
        # Explicit fixture owner setup, never runtime catalog-control authority.
        await admin.execute(
            "ALTER TABLE public.capability_catalog_control DISABLE ROW LEVEL SECURITY"
        )
        try:
            await admin.execute("UPDATE public.capability_catalog_control SET enabled=true")
        finally:
            await admin.execute(
                "ALTER TABLE public.capability_catalog_control ENABLE ROW LEVEL SECURITY"
            )
        await publish_all(administer_publication_url)
        publisher = await cleanup.enter_async_context(
            runtime_pool(administer_publication_url, "home")
        )
        await CatalogPublisher(publisher, "home").publish(native.catalog)
        await configure(admin, limits())
        runtime = await cleanup.enter_async_context(
            runtime_pool(administer_publication_url, "switchboard")
        )
        case = uuid4()
        admitted = await GoalAdmission(
            runtime, Sources(Evidence("message", f"administer-owner:{case}", owner=True))
        ).admit_submission(SourceRef("message", str(case)), uuid4())
        assert admitted.result == "admitted"
        goal, invocation = admitted.goal_id, uuid4()
        exposed = mode == "exposed-rule"
        await admin.execute(
            "UPDATE switchboard.goals SET status='running',exposed=$2 WHERE id=$1", goal, exposed
        )
        grant = Grant("home__schedule_create", park_only=mode == "park-only-rule")
        grants = encode_grants((grant,))
        await admin.execute(
            "INSERT INTO switchboard.invocations "
            "(id,goal_id,depth,role,lead_epoch,status,grant_snapshot,grant_digest,max_turns,max_tokens,exposed) "
            "VALUES($1,$2,0,'lead',0,'launching',$3::jsonb,$4,5,500,$5)",
            invocation,
            goal,
            grants,
            digest(grants),
            exposed,
        )
        args = {"name": f"administer-{case.hex}", "cron": "0 8 * * *", "prompt": "original"}
        rule = None
        if "rule" in mode:
            rule = uuid4()
            await owner.execute(
                "INSERT INTO approval_rules(id,tool_name,arg_constraints,description,max_uses) "
                "VALUES($1,'schedule_create',$2::jsonb,'controlled administer rule',1)",
                rule,
                {k: {"type": "exact", "value": v} for k, v in args.items()},
            )
        signer = AssertionSigner("home-administer-contract", Ed25519PrivateKey.generate())
        invoked = []
        original = native.originals["schedule_create"]

        async def observed_original(**kwargs):
            # This physically independent observer sees consume COMMITTED before
            # entering the unchanged registered scheduling implementation.
            assert (
                await observer.fetchval(
                    "SELECT dispatch_consumed_at IS NOT NULL FROM approval_goal_effects WHERE effect_id=$1",
                    result.effect_id,
                )
                is True
            )
            invoked.append(dict(kwargs))
            return await original(**kwargs)

        op = GoalDispatch(
            owning,
            "home",
            native.catalog,
            {**native.originals, "schedule_create": observed_original},
            {signer.kid: signer.private_key.public_key()},
        )
        peer, exchanges = SimpleNamespace(role="broker-control", domain="home"), []

        async def exchange(self, value, *, assertion=None):
            assert assertion is None
            # A different connection observes the central intent before owning
            # PARK. The caller cannot keep its central transaction over this hop.
            if value["operation"] == "PARK":
                effect = await admin.fetchrow(
                    "SELECT status FROM switchboard.effects WHERE id=$1",
                    UUID(value["identity"]["effect_id"]),
                )
                assert effect is not None and effect["status"] == "requested"
            exchanges.append(value)
            return await op(wire_request(value, "home"), peer)

        monkeypatch.setattr(PrivateClient, "call", exchange)
        client = PrivateClient(
            TransportConfig(
                "https://localhost:4200/mcp",
                PeerPolicy("owning-service", "home", frozenset({"DNS:localhost"})),
            ),
            None,
        )
        cleanup.push_async_callback(client.aclose)

        class NeverDirect:
            async def dispatch(self, *args, **kwargs):
                raise AssertionError("administer_requires_committed_owning_approval")

        broker = Broker(
            runtime,
            PublishedAuthorities(),
            NeverDirect(),
            signer,
            approvals=PrivateApprovals({"home": client}),
        )
        token = await broker.issue_invocation_token(goal, invocation)
        await admin.execute(
            "UPDATE switchboard.invocations SET status='running' WHERE id=$1", invocation
        )
        if mode == "rollback":
            await owner.execute(
                "CREATE FUNCTION home.administer_contract_refuse() RETURNS trigger LANGUAGE plpgsql "
                "AS $$ BEGIN RAISE EXCEPTION 'controlled_administer_refusal' USING ERRCODE='23514'; END $$; "
                "CREATE TRIGGER administer_contract_refuse BEFORE INSERT ON home.pending_actions "
                "FOR EACH ROW EXECUTE FUNCTION home.administer_contract_refuse()"
            )
        try:
            result = await broker.call(goal, token, "home__schedule_create", args)
            assert result.value is None and len(exchanges) == 1 and invoked == []
            assert (
                await admin.fetchval(
                    "SELECT used_calls FROM switchboard.budgets WHERE goal_id=$1", goal
                )
                == 1
            )
            assert (
                await observer.fetchval(
                    "SELECT count(*) FROM scheduled_tasks WHERE name=$1", args["name"]
                )
                == 0
            )
            row, gate, receipts = await snapshot(observer, result.effect_id)
            if mode == "rollback":
                assert (
                    result.status == "unknown" and row is None and gate is None and receipts == []
                )
                return
            assert result.status == "parked" and result.approval_ref == str(result.effect_id)
            approved_rule = mode == "rule"
            assert row["status"] == ("approved" if approved_rule else "pending")
            assert row["tool_args"] == args and row["goal_args_digest"] == arguments_digest(args)
            assert row["invocation_id"] == invocation and row["goal_exposed"] == exposed
            assert (
                gate["park_authorization"] is not None
                and gate["pending_action_id"] == result.effect_id
            )
            assert {r["outcome"] for r in receipts} == (
                {"pending", "parked", "approved"} if approved_rule else {"pending", "parked"}
            )
            if rule is not None:
                assert await observer.fetchval(
                    "SELECT use_count FROM approval_rules WHERE id=$1", rule
                ) == int(approved_rule)
            # Exact signed duplicate reuses the immutable owning PARK evidence.
            duplicate = await op(wire_request(exchanges[0], "home"), peer)
            assert duplicate["outcome"] == "parked"
            assert await snapshot(observer, result.effect_id) == (row, gate, receipts)
            if not approved_rule:
                if mode == "edited":
                    await owner.execute(
                        "UPDATE pending_actions SET tool_args=$2::jsonb WHERE id=$1",
                        result.effect_id,
                        {**args, "prompt": "owner edited"},
                    )
                await op.owner.transition(
                    wire_identity(exchanges[0]["identity"]),
                    target_status=ActionStatus.APPROVED,
                    decided_by="human:controlled-owner",
                    event_actor="controlled-owner",
                    event_reason="controlled approval",
                )
            await admin.execute(
                "UPDATE switchboard.invocations SET status='returned',token_hash=NULL WHERE id=$1",
                invocation,
            )
            if mode == "stop":
                await GoalStop(runtime).stop_goal(goal, uuid4(), "a" * 64)
            elif mode == "drift":
                # Real committed retirement, not a caller-selected catalog DTO.
                await admin.execute(
                    "UPDATE public.capability_catalog_current SET active=false WHERE domain='home'"
                )
            if mode in {"stop", "drift"}:
                before = await snapshot(observer, result.effect_id)
                with pytest.raises(BrokerRefused):
                    await broker.replay_approved(goal, result.effect_id)
                assert await snapshot(observer, result.effect_id) == before and invoked == []
            else:
                replay = await broker.replay_approved(goal, result.effect_id)
                assert replay.status == "unknown" and replay.value is None
                final, consumed, _ = await snapshot(observer, result.effect_id)
                assert consumed["dispatch_consumed_at"] is not None
                actual = await observer.fetch(
                    "SELECT prompt,source FROM scheduled_tasks WHERE name=$1", args["name"]
                )
                assert len(actual) == 1 and actual[0]["source"] == "db"
                assert actual[0]["prompt"] == ("owner edited" if mode == "edited" else "original")
                assert invoked == [{**args, "prompt": actual[0]["prompt"]}]
                assert final["tool_args"]["prompt"] == actual[0]["prompt"]
                with pytest.raises(BrokerRefused):
                    await broker.replay_approved(goal, result.effect_id)
                assert (
                    await observer.fetchval(
                        "SELECT count(*) FROM scheduled_tasks WHERE name=$1", args["name"]
                    )
                    == 1
                )
            assert (
                await admin.fetchval(
                    "SELECT used_calls FROM switchboard.budgets WHERE goal_id=$1", goal
                )
                == 1
            )
        finally:
            if mode == "rollback":
                await owner.execute(
                    "DROP TRIGGER administer_contract_refuse ON home.pending_actions; "
                    "DROP FUNCTION home.administer_contract_refuse()"
                )


@pytest.fixture(scope="module")
def scene_owning_url(postgres_container):
    """Real Home actuation tables plus approvals; independent of central publication."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "approvals", "home"],
        schemas={"core": "public", "approvals": "home", "home": "home"},
    )


@pytest.fixture
async def scene_stores(scene_owning_url):
    async def runtime_role(connection):
        await connection.execute("SET ROLE butler_home_rw")

    async with AsyncExitStack() as cleanup:
        pools = []
        for setup in (None, None, runtime_role):
            pool = await asyncpg.create_pool(
                scene_owning_url,
                init=register_jsonb_codec,
                setup=setup,
                server_settings={"search_path": "home,public"},
                min_size=1,
                max_size=3,
                command_timeout=5,
            )
            cleanup.push_async_callback(_close, pool)
            pools.append(pool)
        async with pools[0].acquire() as first, pools[1].acquire() as second:
            assert await first.fetchval("SELECT pg_backend_pid()") != await second.fetchval(
                "SELECT pg_backend_pid()"
            )
        yield tuple(pools)


@pytest.mark.parametrize(
    "mode",
    [
        "stored",
        "edited",
        "rule",
        "exposed-rule",
        "park-only-rule",
        "drift",
        "stop",
        "rollback",
        "race",
        "edited-outside",
        "http-loss",
        "reply-loss",
    ],
)
async def test_real_registered_home_scene_park_and_stored_replay(
    scene_stores, administer_publication_url, monkeypatch, mode
):
    """AUTHOR/COLLECT ONLY: actual central and Home SQL, roles and originals.

    Only the fixed private transport is in-process. An isolated fixture source
    supplies owner admission; this does not authenticate a deployed caller or
    prove a provider effect. No scheduler tick, runner or live daemon is started.
    """
    from types import SimpleNamespace
    from uuid import UUID

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from butlers.capability_broker.assertions import AssertionSigner
    from butlers.capability_broker.catalog import PublishedAuthorities
    from butlers.capability_broker.contracts import BrokerRefused
    from butlers.capability_broker.owning import PrivateApprovals
    from butlers.capability_broker.service import Broker
    from butlers.capability_broker.transport.client import PrivateClient
    from butlers.capability_broker.transport.config import PeerPolicy, TransportConfig
    from butlers.capability_broker.transport.protocol import identity as wire_identity
    from butlers.capability_broker.transport.protocol import request as wire_request
    from butlers.core.catalog_publication import CatalogPublisher
    from butlers.core.goal_admission import (
        Evidence,
        GoalAdmission,
        Grant,
        SourceRef,
        digest,
        encode_grants,
    )
    from butlers.core.goal_stop import GoalStop
    from butlers.modules.approvals.goal_dispatch import GoalDispatch
    from tests.capability_broker.test_published_catalog import registered_home_admin
    from tests.integration.test_catalog_publication import publish_all, runtime_pool
    from tests.switchboard.goal_admission_fixtures import Sources, limits
    from tests.switchboard.goal_ledger_fixtures import configure

    owner, observer, owning = scene_stores
    native = await registered_home_admin(monkeypatch, pool=owning)
    async with owning.acquire() as role_check:
        assert await role_check.fetchval("SELECT current_user") == "butler_home_rw"
    async with AsyncExitStack() as cleanup:
        admin = await asyncpg.create_pool(
            administer_publication_url,
            init=register_jsonb_codec,
            min_size=1,
            max_size=3,
            command_timeout=5,
        )
        cleanup.push_async_callback(_close, admin)
        # Explicit fixture owner setup, never runtime catalog-control authority.
        await admin.execute(
            "ALTER TABLE public.capability_catalog_control DISABLE ROW LEVEL SECURITY"
        )
        try:
            await admin.execute("UPDATE public.capability_catalog_control SET enabled=true")
        finally:
            await admin.execute(
                "ALTER TABLE public.capability_catalog_control ENABLE ROW LEVEL SECURITY"
            )
        await publish_all(administer_publication_url)
        publisher = await cleanup.enter_async_context(
            runtime_pool(administer_publication_url, "home")
        )
        await CatalogPublisher(publisher, "home").publish(native.catalog)
        await configure(admin, limits())
        runtime = await cleanup.enter_async_context(
            runtime_pool(administer_publication_url, "switchboard")
        )
        case = uuid4()
        admitted = await GoalAdmission(
            runtime, Sources(Evidence("message", f"scene-owner:{case}", owner=True))
        ).admit_submission(SourceRef("message", str(case)), uuid4())
        assert admitted.result == "admitted"
        goal, invocation = admitted.goal_id, uuid4()
        exposed = mode == "exposed-rule"
        await admin.execute(
            "UPDATE switchboard.goals SET status='running',exposed=$2 WHERE id=$1", goal, exposed
        )
        grant = Grant(
            "home__ha_activate_scene",
            park_only=mode == "park-only-rule",
            resources=(("entity_id", ("scene.controlled", "scene.edited")),),
        )
        grants = encode_grants((grant,))
        await admin.execute(
            "INSERT INTO switchboard.invocations "
            "(id,goal_id,depth,role,lead_epoch,status,grant_snapshot,grant_digest,max_turns,max_tokens,exposed) "
            "VALUES($1,$2,0,'lead',0,'launching',$3::jsonb,$4,5,500,$5)",
            invocation,
            goal,
            grants,
            digest(grants),
            exposed,
        )
        args = {"entity_id": "scene.controlled"}
        rule = None
        if "rule" in mode:
            rule = uuid4()
            await owner.execute(
                "INSERT INTO approval_rules(id,tool_name,arg_constraints,description,max_uses) "
                "VALUES($1,'ha_activate_scene',$2::jsonb,'controlled scene rule',1)",
                rule,
                {k: {"type": "exact", "value": v} for k, v in args.items()},
            )
        signer = AssertionSigner("home-scene-contract", Ed25519PrivateKey.generate())
        invoked, physical = [], []
        from unittest.mock import AsyncMock

        import httpx

        home = next(m for m in native.daemon._active_modules if m.name == "home_assistant")

        async def physical_post(path, **kwargs):
            assert path == "/api/services/scene/turn_on"
            # Independent committed receipt exists BEFORE any provider request.
            receipt = await observer.fetchrow(
                "SELECT status FROM ha_command_log WHERE approval_id=$1", result.effect_id
            )
            assert receipt is not None and receipt["status"] == "attempting"
            physical.append(dict(kwargs["json"]))
            if mode == "http-loss":
                raise httpx.ReadTimeout("controlled_scene_reply_loss")
            return httpx.Response(200, json=[], request=httpx.Request("POST", "https://ha.invalid"))

        home._client = SimpleNamespace(post=physical_post, get=AsyncMock())
        home._emit_actuation_event = AsyncMock()
        original = native.originals["ha_activate_scene"]

        async def observed_original(**kwargs):
            # This physically independent observer sees consume COMMITTED before
            # entering the unchanged registered scheduling implementation.
            assert (
                await observer.fetchval(
                    "SELECT dispatch_consumed_at IS NOT NULL FROM approval_goal_effects WHERE effect_id=$1",
                    result.effect_id,
                )
                is True
            )
            invoked.append(dict(kwargs))
            return await original(**kwargs)

        op = GoalDispatch(
            owning,
            "home",
            native.catalog,
            {**native.originals, "ha_activate_scene": observed_original},
            {signer.kid: signer.private_key.public_key()},
        )
        peer, exchanges = SimpleNamespace(role="broker-control", domain="home"), []

        async def exchange(self, value, *, assertion=None):
            assert assertion is None
            # A different connection observes the central intent before owning
            # PARK. The caller cannot keep its central transaction over this hop.
            if value["operation"] == "PARK":
                effect = await admin.fetchrow(
                    "SELECT status FROM switchboard.effects WHERE id=$1",
                    UUID(value["identity"]["effect_id"]),
                )
                assert effect is not None and effect["status"] == "requested"
            exchanges.append(value)
            outcome = await op(wire_request(value, "home"), peer)
            if mode == "reply-loss" and value["operation"] == "EXECUTE":
                raise ConnectionError("controlled_scene_execute_reply_loss")
            return outcome

        monkeypatch.setattr(PrivateClient, "call", exchange)
        client = PrivateClient(
            TransportConfig(
                "https://localhost:4200/mcp",
                PeerPolicy("owning-service", "home", frozenset({"DNS:localhost"})),
            ),
            None,
        )
        cleanup.push_async_callback(client.aclose)

        class NeverDirect:
            async def dispatch(self, *args, **kwargs):
                raise AssertionError("scene_requires_committed_owning_approval")

        broker = Broker(
            runtime,
            PublishedAuthorities(),
            NeverDirect(),
            signer,
            approvals=PrivateApprovals({"home": client}),
        )
        token = await broker.issue_invocation_token(goal, invocation)
        await admin.execute(
            "UPDATE switchboard.invocations SET status='running' WHERE id=$1", invocation
        )
        if mode == "rollback":
            await owner.execute(
                "CREATE FUNCTION home.scene_contract_refuse() RETURNS trigger LANGUAGE plpgsql "
                "AS $$ BEGIN RAISE EXCEPTION 'controlled_scene_refusal' USING ERRCODE='23514'; END $$; "
                "CREATE TRIGGER scene_contract_refuse BEFORE INSERT ON home.pending_actions "
                "FOR EACH ROW EXECUTE FUNCTION home.scene_contract_refuse()"
            )
        try:
            result = await broker.call(goal, token, "home__ha_activate_scene", args)
            assert result.value is None and len(exchanges) == 1 and invoked == []
            assert (
                await admin.fetchval(
                    "SELECT used_calls FROM switchboard.budgets WHERE goal_id=$1", goal
                )
                == 1
            )
            assert (
                await observer.fetchval(
                    "SELECT count(*) FROM ha_command_log WHERE approval_id=$1", result.effect_id
                )
                == 0
            )
            row, gate, receipts = await snapshot(observer, result.effect_id)
            if mode == "rollback":
                assert (
                    result.status == "unknown" and row is None and gate is None and receipts == []
                )
                return
            assert result.status == "parked" and result.approval_ref == str(result.effect_id)
            approved_rule = False
            assert row["status"] == ("approved" if approved_rule else "pending")
            assert row["tool_args"] == args and row["goal_args_digest"] == arguments_digest(args)
            assert row["invocation_id"] == invocation and row["goal_exposed"] == exposed
            assert (
                gate["park_authorization"] is not None
                and gate["pending_action_id"] == result.effect_id
            )
            assert {r["outcome"] for r in receipts} == (
                {"pending", "parked", "approved"} if approved_rule else {"pending", "parked"}
            )
            if rule is not None:
                assert await observer.fetchval(
                    "SELECT use_count FROM approval_rules WHERE id=$1", rule
                ) == int(approved_rule)
            # Exact signed duplicate reuses the immutable owning PARK evidence.
            duplicate = await op(wire_request(exchanges[0], "home"), peer)
            assert duplicate["outcome"] == "parked"
            assert await snapshot(observer, result.effect_id) == (row, gate, receipts)
            if not approved_rule:
                if mode in {"edited", "edited-outside"}:
                    await owner.execute(
                        "UPDATE pending_actions SET tool_args=$2::jsonb WHERE id=$1",
                        result.effect_id,
                        {
                            "entity_id": "scene.edited" if mode == "edited" else "scene.outside",
                            "transition": None,
                        },
                    )
                await op.owner.transition(
                    wire_identity(exchanges[0]["identity"]),
                    target_status=ActionStatus.APPROVED,
                    decided_by="human:controlled-owner",
                    event_actor="controlled-owner",
                    event_reason="controlled approval",
                )
            await admin.execute(
                "UPDATE switchboard.invocations SET status='returned',token_hash=NULL WHERE id=$1",
                invocation,
            )
            if mode == "stop":
                await GoalStop(runtime).stop_goal(goal, uuid4(), "a" * 64)
            elif mode == "drift":
                # Real committed retirement, not a caller-selected catalog DTO.
                await admin.execute(
                    "UPDATE public.capability_catalog_current SET active=false WHERE domain='home'"
                )
            if mode in {"stop", "drift", "edited-outside"}:
                before = await snapshot(observer, result.effect_id)
                with pytest.raises(BrokerRefused):
                    await broker.replay_approved(goal, result.effect_id)
                assert await snapshot(observer, result.effect_id) == before and invoked == []
            else:
                if mode == "race":
                    outcomes = await asyncio.gather(
                        broker.replay_approved(goal, result.effect_id),
                        broker.replay_approved(goal, result.effect_id),
                        return_exceptions=True,
                    )
                    assert sum(isinstance(v, BrokerRefused) for v in outcomes) == 1
                    replay = next(v for v in outcomes if not isinstance(v, BaseException))
                else:
                    replay = await broker.replay_approved(goal, result.effect_id)
                assert replay.status == "unknown" and replay.value is None
                final, consumed, _ = await snapshot(observer, result.effect_id)
                assert consumed["dispatch_consumed_at"] is not None
                actual = await observer.fetch(
                    "SELECT target,data,risk,status,requested_state FROM ha_command_log WHERE approval_id=$1",
                    result.effect_id,
                )
                expected = (
                    {"entity_id": "scene.edited", "transition": None} if mode == "edited" else args
                )
                assert len(actual) == 1 and actual[0]["risk"] == "consequential"
                assert actual[0]["status"] == "unverified"
                assert actual[0]["target"] == {"entity_id": expected["entity_id"]}
                assert actual[0]["data"] is None
                assert actual[0]["requested_state"]["domain"] == "scene"
                assert invoked == [expected] and final["tool_args"] == expected
                assert physical == [{"target": {"entity_id": expected["entity_id"]}}]
                with pytest.raises(BrokerRefused):
                    await broker.replay_approved(goal, result.effect_id)
                assert (
                    await observer.fetchval(
                        "SELECT count(*) FROM ha_command_log WHERE approval_id=$1", result.effect_id
                    )
                    == 1
                )
            assert (
                await admin.fetchval(
                    "SELECT used_calls FROM switchboard.budgets WHERE goal_id=$1", goal
                )
                == 1
            )
        finally:
            if mode == "rollback":
                await owner.execute(
                    "DROP TRIGGER scene_contract_refuse ON home.pending_actions; "
                    "DROP FUNCTION home.scene_contract_refuse()"
                )
