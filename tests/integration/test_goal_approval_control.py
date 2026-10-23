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
        assert await service.lookup(identity(call)) is None
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
