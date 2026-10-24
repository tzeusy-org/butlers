"""Real migrated M1 catalog -> admission -> locked broker catalog consumer.

Authored PostgreSQL contract; NOT RUN locally. The source-origin and read-domain
collaborators below are explicit controls, not runtime/owning-effect authority.
REQ-capability-authorization-002; REQ-capability-authorization-004.
"""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from butlers.capability_broker.assertions import AssertionSigner
from butlers.capability_broker.catalog import PublishedAuthorities
from butlers.capability_broker.contracts import BrokerRefused, Reason
from butlers.capability_broker.domains import DomainReceipt
from butlers.capability_broker.ledger import BrokerLedger
from butlers.capability_broker.service import Broker
from butlers.core.capability_json import arguments_digest
from butlers.core.catalog_publication import CatalogPublisher
from butlers.core.goal_admission import (
    Evidence,
    GoalAdmission,
    Grant,
    SourceRef,
    digest,
    encode_grants,
)
from tests.contracts.test_catalog_publication import registered_catalog
from tests.contracts.test_tool_authorization_catalog import _roster_catalog
from tests.integration import test_catalog_publication as publication_fixtures
from tests.integration.test_capability_broker_transactions import ControlledDomain
from tests.switchboard.goal_admission_fixtures import Sources, limits
from tests.switchboard.goal_ledger_fixtures import configure

fleet = publication_fixtures.fleet
publication_url = publication_fixtures.publication_url
publish_all = publication_fixtures.publish_all
runtime_pool = publication_fixtures.runtime_pool

pytestmark = pytest.mark.integration


async def test_real_admitted_digest_token_read_and_current_publication_refusals(
    publication_url, fleet
):
    await publish_all(publication_url)
    await configure(fleet, limits())
    async with runtime_pool(publication_url, "switchboard") as runtime:
        admitted = await GoalAdmission(
            runtime, Sources(Evidence("message", "controlled-owner", owner=True))
        ).admit_submission(SourceRef("message", str(uuid4())), uuid4())
        assert admitted.result == "admitted"
        goal, invocation = admitted.goal_id, uuid4()
        await fleet.execute("UPDATE switchboard.goals SET status='running' WHERE id=$1", goal)
        grants = encode_grants((Grant("health__lookup"),))
        await fleet.execute(
            "INSERT INTO switchboard.invocations "
            "(id,goal_id,depth,role,lead_epoch,status,grant_snapshot,grant_digest,max_turns,max_tokens) "
            "VALUES ($1,$2,0,'lead',0,'launching',$3::jsonb,$4,5,500)",
            invocation,
            goal,
            grants,
            digest(grants),
        )
        domain = ControlledDomain()
        broker = Broker(
            runtime,
            PublishedAuthorities(),
            domain,
            AssertionSigner("test", Ed25519PrivateKey.generate()),
        )
        token = await broker.issue_invocation_token(goal, invocation)
        await fleet.execute(
            "UPDATE switchboard.invocations SET status='running' WHERE id=$1", invocation
        )
        assert [c.identity for c in await broker.list_tools(goal, token)] == ["health__lookup"]
        result = await broker.call(goal, token, "health__lookup", {"query": "read"})
        assert result.status == "returned" and result.effect_id is None
        assert domain.calls == [None]
        # A separate acquisition observes actual commit, not the caller's TX.
        assert (
            await fleet.fetchval(
                "SELECT used_calls FROM switchboard.budgets WHERE goal_id=$1", goal
            )
            == 1
        )
        assert await fleet.fetchval("SELECT exposed FROM switchboard.goals WHERE id=$1", goal)
        async with runtime_pool(publication_url, "health") as owning:
            publisher = CatalogPublisher(owning, "health")
            _, changed = await registered_catalog(
                "health", description="Changed native definition."
            )
            await publisher.publish(changed)
            for operation in (
                lambda: broker.list_tools(goal, token),
                lambda: broker.call(goal, token, "health__lookup", {"query": "never forwarded"}),
            ):
                with pytest.raises(BrokerRefused) as refused:
                    await operation()
                assert refused.value.reason is Reason.CATALOG
            await publisher.retire()
            with pytest.raises(BrokerRefused, match="catalog_unavailable_or_changed"):
                await broker.list_tools(goal, token)
            # The reader checks the actual connection's role, even if its
            # caller already holds a healthy TX with access to catalog rows.
            async with owning.acquire() as connection, connection.transaction():
                with pytest.raises(BrokerRefused, match="catalog_unavailable_or_changed"):
                    await PublishedAuthorities().catalog(connection, None)
        assert domain.calls == [None]
        assert (
            await fleet.fetchval(
                "SELECT used_calls FROM switchboard.budgets WHERE goal_id=$1", goal
            )
            == 1
        )


async def test_real_published_notify_identity_and_retirement_change_locked_decision(
    publication_url, fleet, monkeypatch
):
    """Actual SQL/catalog/token/budget consumer; no owning delivery or receipt claim."""
    monkeypatch.setattr("butlers.db.Database.connect", AsyncMock())
    catalog = await _roster_catalog("relationship")
    await publish_all(publication_url)
    async with runtime_pool(publication_url, "relationship") as owning:
        await CatalogPublisher(owning, "relationship").publish(catalog)
    await configure(fleet, limits())
    owner_id = uuid4()
    assert (
        await fleet.fetchval("SELECT count(*) FROM public.entities WHERE 'owner'=ANY(roles)") == 0
    )
    await fleet.execute(
        "INSERT INTO public.entities(id,canonical_name,entity_type,roles) "
        "VALUES($1,'published-notify-control','person',ARRAY['owner'])",
        owner_id,
    )

    class NoDelivery:
        def __init__(self):
            self.calls = []

        async def dispatch(self, reservation, args, assertion, *, idempotency_key):
            self.calls.append(reservation.effect_id)
            return DomainReceipt("unknown")

        async def park(self, *args):
            raise AssertionError("no_owning_approval_receipt_in_this_contract")

    try:
        async with runtime_pool(publication_url, "switchboard") as runtime:
            admitted = await GoalAdmission(
                runtime, Sources(Evidence("message", "controlled-owner", owner=True))
            ).admit_submission(SourceRef("message", str(uuid4())), uuid4())
            assert admitted.result == "admitted"
            goal, invocation = admitted.goal_id, uuid4()
            await fleet.execute("UPDATE switchboard.goals SET status='running' WHERE id=$1", goal)
            grants = encode_grants((Grant("relationship__notify"),))
            await fleet.execute(
                "INSERT INTO switchboard.invocations "
                "(id,goal_id,depth,role,lead_epoch,status,grant_snapshot,grant_digest,max_turns,max_tokens) "
                "VALUES ($1,$2,0,'lead',0,'launching',$3::jsonb,$4,5,500)",
                invocation,
                goal,
                grants,
                digest(grants),
            )
            domain = NoDelivery()
            broker = Broker(
                runtime,
                PublishedAuthorities(),
                domain,
                AssertionSigner("test", Ed25519PrivateKey.generate()),
            )
            token = await broker.issue_invocation_token(goal, invocation)
            await fleet.execute(
                "UPDATE switchboard.invocations SET status='running' WHERE id=$1", invocation
            )
            assert [c.identity for c in await broker.list_tools(goal, token)] == [
                "relationship__notify"
            ]
            # Resolve the explicit owner call without forwarding first: a
            # successful untrusted call makes exposure sticky for later calls.
            explicit = {"message": "explicit control", "entity_id": str(owner_id)}
            async with runtime.acquire() as connection, connection.transaction():
                ledger = BrokerLedger(connection)
                principal = await ledger.authenticate(goal, token)
                _, decision = await ledger.resolve_call(
                    principal,
                    "relationship__notify",
                    explicit,
                    arguments_digest(explicit),
                    broker.authorities,
                )
                assert decision.disposition.value == "dispatch"
            result = await broker.call(
                goal, token, "relationship__notify", {"message": "default control"}
            )
            assert result.status == "unknown" and result.value is None
            assert result.effect_id is not None and len(domain.calls) == 1
            # Independent committed observations: transport acknowledgement is
            # never promoted to a committed effect, and each call charged once.
            async with fleet.acquire() as observer:
                assert (
                    await observer.fetchval(
                        "SELECT used_calls FROM switchboard.budgets WHERE goal_id=$1", goal
                    )
                    == 1
                )
                assert (
                    await observer.fetchval(
                        "SELECT count(*) FROM switchboard.effects WHERE goal_id=$1 AND status='unknown'",
                        goal,
                    )
                    == 1
                )
            # Dispatch above exposes the goal. No inert-rendering producer is
            # installed, so an owner recipient alone cannot remove PARK.
            async with runtime.acquire() as connection, connection.transaction():
                assert await connection.fetchval("SELECT current_user") == "butler_switchboard_rw"
                ledger = BrokerLedger(connection)
                principal = await ledger.authenticate(goal, token)
                _, decision = await ledger.resolve_call(
                    principal,
                    "relationship__notify",
                    {"message": "exposed control"},
                    arguments_digest({"message": "exposed control"}),
                    broker.authorities,
                )
                assert decision.disposition.value == "park" and not decision.allow_standing_rule
            # Retirement is an actual committed row change, not caller JSON.
            await fleet.execute(
                "UPDATE public.entities SET metadata=jsonb_build_object('deleted_at','control') WHERE id=$1",
                owner_id,
            )
            async with runtime.acquire() as connection, connection.transaction():
                ledger = BrokerLedger(connection)
                principal = await ledger.authenticate(goal, token)
                capability = (await broker.authorities.catalog(connection, principal)).capabilities
                notify = next(c for c in capability if c.identity == "relationship__notify")
                evidence = await broker.authorities.policy(connection, principal, notify, {})
                assert evidence.recipient_binding.owner_id is None
                _, decision = await ledger.resolve_call(
                    principal,
                    "relationship__notify",
                    {},
                    arguments_digest({}),
                    broker.authorities,
                )
                assert decision.disposition.value == "park" and not decision.allow_standing_rule
            assert len(domain.calls) == 1
    finally:
        await fleet.execute("DELETE FROM public.entities WHERE id=$1", owner_id)


@pytest.fixture(scope="module")
def health_approvals_url(postgres_container):
    from butlers.testing.migration import create_migrated_test_db, migration_db_name

    # Independent owning database, with the actual full approvals019 chain.
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        ["core", "approvals"],
        schemas={"core": "health", "approvals": "health"},
    )


@pytest.mark.parametrize(
    "mode", ["ordinary", "disabled", "park", "rollback", "stop", "budget", "race"]
)
async def test_real_published_health_record_reservation_and_owning_replay(
    publication_url, fleet, health_approvals_url, monkeypatch, mode
):
    """Real catalog/central+owning SQL, roles and commits; memory/provider I/O controlled.

    AUTHORED/COLLECT ONLY locally. No original or provider return certifies an
    external committed effect. The enabled approval variant is explicit test
    configuration; checked-in Health remains disabled.
    """
    import asyncio
    from contextlib import AsyncExitStack
    from types import SimpleNamespace

    import asyncpg

    from butlers.capability_broker.owning import PrivateApprovals
    from butlers.capability_broker.transport.client import PrivateClient
    from butlers.capability_broker.transport.config import PeerPolicy, TransportConfig
    from butlers.capability_broker.transport.protocol import identity, request
    from butlers.core.goal_stop import GoalStop
    from butlers.db import register_jsonb_codec
    from butlers.modules.approvals.goal_dispatch import GoalDispatch
    from butlers.modules.approvals.models import ActionStatus
    from tests.capability_broker.test_published_catalog import registered_health_record

    catalog, originals, facts = await registered_health_record(
        monkeypatch, approvals_enabled=mode not in {"ordinary", "disabled"}
    )
    await publish_all(publication_url)
    async with runtime_pool(publication_url, "health") as publisher:
        await CatalogPublisher(publisher, "health").publish(catalog)
    await configure(fleet, limits())

    async def health_role(conn):
        await conn.execute("SET ROLE butler_health_rw")
        await conn.execute("SET search_path TO health,public")

    async def close(pool):
        try:
            await asyncio.wait_for(pool.close(), timeout=10)
        except BaseException:
            pool.terminate()
            raise

    async with AsyncExitStack() as cleanup:
        owner = await asyncpg.create_pool(
            health_approvals_url,
            init=register_jsonb_codec,
            server_settings={"search_path": "health,public"},
            min_size=1,
            max_size=2,
        )
        cleanup.push_async_callback(close, owner)
        owning = await asyncpg.create_pool(
            health_approvals_url,
            init=register_jsonb_codec,
            setup=health_role,
            min_size=1,
            max_size=3,
            command_timeout=5,
        )
        cleanup.push_async_callback(close, owning)
        runtime = await cleanup.enter_async_context(runtime_pool(publication_url, "switchboard"))
        admitted = await GoalAdmission(
            runtime, Sources(Evidence("message", "controlled-owner", owner=True))
        ).admit_submission(SourceRef("message", str(uuid4())), uuid4())
        assert admitted.result == "admitted"
        goal, invocation = admitted.goal_id, uuid4()
        await fleet.execute("UPDATE switchboard.goals SET status='running' WHERE id=$1", goal)
        grants = encode_grants((Grant("health__measurement_log", park_only=mode != "ordinary"),))
        await fleet.execute(
            "INSERT INTO switchboard.invocations "
            "(id,goal_id,depth,role,lead_epoch,status,grant_snapshot,grant_digest,max_turns,max_tokens) "
            "VALUES($1,$2,0,'lead',0,'launching',$3::jsonb,$4,5,500)",
            invocation,
            goal,
            grants,
            digest(grants),
        )
        signer = AssertionSigner("health-record-contract", Ed25519PrivateKey.generate())
        op = GoalDispatch(
            owning, "health", catalog, originals, {signer.kid: signer.private_key.public_key()}
        )
        peer = SimpleNamespace(role="broker-control", domain="health")
        exchanges = []

        async def exchange(self, value, *, assertion=None):
            exchanges.append(value)
            return await op(request(value, "health"), peer)

        monkeypatch.setattr(PrivateClient, "call", exchange)
        client = PrivateClient(
            TransportConfig(
                "https://localhost:4200/mcp",
                PeerPolicy("owning-service", "health", frozenset({"DNS:localhost"})),
            ),
            None,
        )
        cleanup.push_async_callback(client.aclose)

        class RecordDomain:
            async def dispatch(self, reservation, args, assertion, *, idempotency_key):
                await originals["measurement_log"](**args)
                return DomainReceipt("unknown")

        broker = Broker(
            runtime,
            PublishedAuthorities(),
            RecordDomain(),
            signer,
            approvals=PrivateApprovals({"health": client}),
        )
        token = await broker.issue_invocation_token(goal, invocation)
        await fleet.execute(
            "UPDATE switchboard.invocations SET status='running' WHERE id=$1", invocation
        )
        if mode == "budget":
            await fleet.execute("UPDATE switchboard.budgets SET max_calls=0 WHERE goal_id=$1", goal)
        elif mode == "stop":
            await GoalStop(runtime).stop_goal(goal, uuid4(), "a" * 64)
        elif mode == "rollback":
            # Fixture-owner fault; runtime role/grants/production guards stay exact.
            await owner.execute(
                "CREATE FUNCTION health.record_contract_refuse() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'controlled_record_refusal' "
                "USING ERRCODE='23514'; END $$; CREATE TRIGGER record_contract_refuse "
                "BEFORE INSERT ON health.pending_actions FOR EACH ROW "
                "EXECUTE FUNCTION health.record_contract_refuse()"
            )
        try:
            args = {"type": "weight", "value": 72.5}
            if mode in {"disabled", "stop", "budget"}:
                with pytest.raises(BrokerRefused):
                    await broker.call(goal, token, "health__measurement_log", args)
                assert facts == [] and exchanges == []
                assert (
                    await fleet.fetchval(
                        "SELECT used_calls FROM switchboard.budgets WHERE goal_id=$1", goal
                    )
                    == 0
                )
                assert (
                    await fleet.fetchval(
                        "SELECT count(*) FROM switchboard.effects WHERE goal_id=$1", goal
                    )
                    == 0
                )
                return
            if mode == "race":
                results = await asyncio.gather(
                    *(broker.call(goal, token, "health__measurement_log", args) for _ in range(2)),
                    return_exceptions=True,
                )
                assert sum(isinstance(v, BrokerRefused) for v in results) == 1
                result = next(v for v in results if not isinstance(v, BaseException))
            else:
                result = await broker.call(goal, token, "health__measurement_log", args)
            assert (
                await fleet.fetchval(
                    "SELECT used_calls FROM switchboard.budgets WHERE goal_id=$1", goal
                )
                == 1
            )
            assert (
                await fleet.fetchval(
                    "SELECT count(*) FROM switchboard.effects WHERE goal_id=$1", goal
                )
                == 1
            )
            assert result.value is None
            if mode == "ordinary":
                assert result.status == "unknown" and len(facts) == 1 and exchanges == []
                with pytest.raises(BrokerRefused):
                    await broker.call(goal, token, "health__measurement_log", args)
                assert len(facts) == 1
                return
            assert facts == []
            async with owner.acquire() as observer:
                row = await observer.fetchrow(
                    "SELECT * FROM pending_actions WHERE id=$1", result.effect_id
                )
                gate = await observer.fetchrow(
                    "SELECT * FROM approval_goal_effects WHERE effect_id=$1", result.effect_id
                )
                receipts = await observer.fetchval(
                    "SELECT count(*) FROM approval_goal_receipts WHERE effect_id=$1",
                    result.effect_id,
                )
            if mode == "rollback":
                assert result.status == "unknown" and row is None and gate is None and receipts == 0
                return
            assert (
                result.status == "parked"
                and row["tool_args"] == args
                and row["status"] == "pending"
            )
            assert (
                row["invocation_id"] == invocation
                and gate["park_authorization"]["grant"] == grants[0]
            )
            assert gate["phase"] == "pending" and receipts >= 1
            # Real stored edit and real owning decision; no replacement replay args.
            await owner.execute(
                "UPDATE pending_actions SET tool_args=$2::jsonb WHERE id=$1",
                result.effect_id,
                {"type": "weight", "value": 73.0},
            )
            await op.owner.transition(
                identity(exchanges[0]["identity"]),
                target_status=ActionStatus.APPROVED,
                decided_by="human:owner",
                event_actor="owner",
                event_reason="record",
            )
            await fleet.execute(
                "UPDATE switchboard.invocations SET status='returned' WHERE id=$1", invocation
            )
            replay = await broker.replay_approved(goal, result.effect_id)
            assert replay.status == "unknown" and replay.value is None
            assert len(facts) == 1 and facts[0]["metadata"]["value"] == 73.0
            assert await owner.fetchval(
                "SELECT dispatch_consumed_at IS NOT NULL FROM approval_goal_effects WHERE effect_id=$1",
                result.effect_id,
            )
            with pytest.raises(BrokerRefused):
                await broker.replay_approved(goal, result.effect_id)
            assert len(facts) == 1
            assert (
                await fleet.fetchval(
                    "SELECT used_calls FROM switchboard.budgets WHERE goal_id=$1", goal
                )
                == 1
            )
        finally:
            if mode == "rollback":
                await owner.execute(
                    "DROP TRIGGER record_contract_refuse ON health.pending_actions; "
                    "DROP FUNCTION health.record_contract_refuse()"
                )


@pytest.fixture(scope="module")
def native_email_url(postgres_container):
    from butlers.testing.migration import create_migrated_test_db, migration_db_name

    # Actual shared identity/definer and central + owning chains, no hand-made
    # schema substitute. Synchronous provisioning stays outside the async loop.
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        ["core", "switchboard", "relationship", "approvals"],
        schemas={
            "core": "switchboard",
            "switchboard": "switchboard",
            "relationship": "relationship",
            "approvals": "messenger",
        },
    )


@pytest.mark.parametrize(
    "mode",
    [
        "stored",
        "edited",
        "resource",
        "unknown",
        "ambiguous",
        "outage",
        "changed-owner",
        "rollback",
        "stop",
        "ack",
        "race",
        "permission",
    ],
)
async def test_real_native_email_identity_park_and_exact_owning_replay(
    native_email_url, monkeypatch, mode
):
    """AUTHOR/COLLECT ONLY: migrated roles, actual definer, separate committed observers.

    Only SMTP and private transport are controlled. No handler result proves
    provider commit; unknown ACK cannot authorize a second attempt.
    """
    import asyncio
    from contextlib import AsyncExitStack
    from types import SimpleNamespace

    import asyncpg

    from butlers.capability_broker.owning import PrivateApprovals
    from butlers.capability_broker.policy import bind_recipient_policy
    from butlers.capability_broker.transport.client import PrivateClient
    from butlers.capability_broker.transport.config import PeerPolicy, TransportConfig
    from butlers.capability_broker.transport.protocol import identity, request
    from butlers.core.goal_stop import GoalStop
    from butlers.db import register_jsonb_codec
    from butlers.modules.approvals.goal_dispatch import GoalDispatch
    from butlers.modules.approvals.models import ActionStatus
    from tests.capability_broker.test_published_catalog import registered_native_email

    native = await registered_native_email(monkeypatch)
    originals = await native.daemon._broker_owning_originals()

    async def close(pool):
        try:
            await asyncio.wait_for(pool.close(), 10)
        except BaseException:
            pool.terminate()
            raise

    async def owning_role(conn):
        await conn.execute("SET ROLE butler_messenger_rw")
        await conn.execute("SET search_path TO messenger,public")

    async with AsyncExitStack() as cleanup:
        admin = await asyncpg.create_pool(
            native_email_url, init=register_jsonb_codec, min_size=1, max_size=3
        )
        cleanup.push_async_callback(close, admin)
        await admin.execute(
            "ALTER TABLE public.capability_catalog_control DISABLE ROW LEVEL SECURITY"
        )
        try:
            await admin.execute("UPDATE public.capability_catalog_control SET enabled=true")
        finally:
            await admin.execute(
                "ALTER TABLE public.capability_catalog_control ENABLE ROW LEVEL SECURITY"
            )
        await publish_all(native_email_url)
        async with runtime_pool(native_email_url, "messenger") as publisher:
            await CatalogPublisher(publisher, "messenger").publish(native.catalog)
        await configure(admin, limits())
        owner_id, other_id = uuid4(), uuid4()
        address = f"native-{owner_id.hex}@example.invalid"
        other_address = f"native-{other_id.hex}@example.invalid"
        await admin.execute(
            "INSERT INTO public.entities(id,canonical_name,entity_type,roles) VALUES"
            "($1,'email-owner','person',ARRAY['owner']),($2,'email-other','person',ARRAY[]::text[])",
            owner_id,
            other_id,
        )

        async def remove_identity_fixtures():
            await admin.execute(
                "DELETE FROM relationship.entity_facts WHERE subject=ANY($1::uuid[])",
                [owner_id, other_id],
            )
            await admin.execute(
                "DELETE FROM public.entities WHERE id=ANY($1::uuid[])", [owner_id, other_id]
            )

        cleanup.push_async_callback(remove_identity_fixtures)
        await admin.execute(
            "INSERT INTO relationship.entity_facts(subject,predicate,object,object_kind,src) "
            "VALUES($1,'has-email',$2,'literal','native-email-contract')",
            owner_id,
            address,
        )
        if mode == "ambiguous":
            await admin.execute(
                "INSERT INTO relationship.entity_facts(subject,predicate,object,object_kind,src) "
                "VALUES($1,'has-email',$2,'literal','native-email-contract')",
                other_id,
                address,
            )
        owning = await asyncpg.create_pool(
            native_email_url,
            init=register_jsonb_codec,
            setup=owning_role,
            min_size=1,
            max_size=3,
            command_timeout=5,
        )
        cleanup.push_async_callback(close, owning)
        native.email._pool = owning  # Execute the unchanged real permission query.
        await admin.execute(
            "INSERT INTO public.permissions(butler,permission,granted,reason) VALUES('messenger','email.send',$1,'native-email-contract') "
            "ON CONFLICT(butler,permission) DO UPDATE SET granted=EXCLUDED.granted",
            mode != "permission",
        )
        runtime = await cleanup.enter_async_context(runtime_pool(native_email_url, "switchboard"))
        admitted = await GoalAdmission(
            runtime, Sources(Evidence("message", f"native-email-owner:{owner_id}", owner=True))
        ).admit_submission(
            SourceRef("message", str(uuid4())),
            uuid4(),
        )
        assert admitted.result == "admitted"
        goal, invocation = admitted.goal_id, uuid4()
        await admin.execute("UPDATE switchboard.goals SET status='running' WHERE id=$1", goal)
        addresses = (address,) if mode == "resource" else (address, other_address)
        grants = encode_grants(
            (Grant("messenger__email_send_message", resources=(("to", addresses),)),)
        )
        await admin.execute(
            "INSERT INTO switchboard.invocations "
            "(id,goal_id,depth,role,lead_epoch,status,grant_snapshot,grant_digest,max_turns,max_tokens) "
            "VALUES($1,$2,0,'lead',0,'launching',$3::jsonb,$4,5,500)",
            invocation,
            goal,
            grants,
            digest(grants),
        )
        signer = AssertionSigner("native-email-contract", Ed25519PrivateKey.generate())
        op = GoalDispatch(
            owning,
            "messenger",
            native.catalog,
            originals,
            {signer.kid: signer.private_key.public_key()},
        )
        peer, exchanges = SimpleNamespace(role="broker-control", domain="messenger"), []

        async def exchange(self, value, *, assertion=None):
            exchanges.append(value)
            outcome = await op(request(value, "messenger"), peer)
            if mode == "ack" and value["operation"] == "EXECUTE":
                raise ConnectionError("controlled_execute_reply_loss")
            return outcome

        monkeypatch.setattr(PrivateClient, "call", exchange)
        client = PrivateClient(
            TransportConfig(
                "https://localhost:4200/mcp",
                PeerPolicy(
                    "owning-service",
                    "messenger",
                    frozenset({"DNS:localhost"}),
                ),
            ),
            None,
        )
        cleanup.push_async_callback(client.aclose)

        class NeverForward:
            async def dispatch(self, *args, **kwargs):
                raise AssertionError("native_email_requires_owning_approval")

        broker = Broker(
            runtime,
            PublishedAuthorities(),
            NeverForward(),
            signer,
            approvals=PrivateApprovals({"messenger": client}),
        )
        token = await broker.issue_invocation_token(goal, invocation)
        await admin.execute(
            "UPDATE switchboard.invocations SET status='running' WHERE id=$1", invocation
        )
        args = {
            "to": other_address if mode == "unknown" else address,
            "subject": "stored",
            "body": "controlled",
        }
        renamed = mode == "outage"
        if renamed:
            await admin.execute(
                "ALTER FUNCTION public.resolve_owner_triple(text,text[]) RENAME TO native_email_lookup_unavailable"
            )
        if mode == "rollback":
            await admin.execute(
                "CREATE FUNCTION messenger.email_contract_refuse() RETURNS trigger LANGUAGE plpgsql "
                "AS $$ BEGIN RAISE EXCEPTION 'controlled_email_refusal' USING ERRCODE='23514'; END $$; "
                "CREATE TRIGGER email_contract_refuse BEFORE INSERT ON messenger.pending_actions "
                "FOR EACH ROW EXECUTE FUNCTION messenger.email_contract_refuse()"
            )
        try:
            if renamed:
                with pytest.raises(BrokerRefused, match=Reason.POLICY.value):
                    await broker.call(goal, token, "messenger__email_send_message", args)
                assert (
                    await admin.fetchval(
                        "SELECT used_calls FROM switchboard.budgets WHERE goal_id=$1", goal
                    )
                    == 0
                )
                assert (
                    await admin.fetchval(
                        "SELECT count(*) FROM messenger.pending_actions WHERE goal_id=$1", goal
                    )
                    == 0
                )
                assert native.sends == [] and exchanges == []
                return
            contender = None
            async with runtime.acquire() as connection, connection.transaction():
                assert await connection.fetchval("SELECT current_user") == "butler_switchboard_rw"
                ledger = BrokerLedger(connection)
                principal = await ledger.authenticate(goal, token)
                cap = next(
                    c
                    for c in (await broker.authorities.catalog(connection, principal)).capabilities
                    if c.identity == "messenger__email_send_message"
                )
                evidence = await broker.authorities.policy(connection, principal, cap, args)
                bound = bind_recipient_policy(cap, args, arguments_digest(args), evidence)
                assert bound.recipient == (
                    "non_owner" if mode in {"unknown", "ambiguous"} else "owner"
                )
                if mode == "changed-owner":
                    # Actual owning-row lock with exact holder/contender PIDs;
                    # fixture admin observes, runtime gets no monitoring grants.
                    holder = await connection.fetchval("SELECT pg_backend_pid()")
                    pid = asyncio.get_running_loop().create_future()

                    async def retire_owner():
                        async with admin.acquire() as writer:
                            pid.set_result(await writer.fetchval("SELECT pg_backend_pid()"))
                            await writer.execute(
                                "UPDATE public.entities SET roles=ARRAY[]::text[] WHERE id=$1",
                                owner_id,
                            )

                    contender = asyncio.create_task(retire_owner())
                    try:
                        async with asyncio.timeout(3):
                            done, _ = await asyncio.wait(
                                [pid, contender], return_when=asyncio.FIRST_COMPLETED
                            )
                            if contender in done:
                                contender.result()
                                raise AssertionError(
                                    "owner_change_finished_before_lock_observation"
                                )
                            target_pid = pid.result()
                            while not await admin.fetchval(
                                "SELECT $2::int=ANY(pg_blocking_pids($1))", target_pid, holder
                            ):
                                if contender.done():
                                    contender.result()
                                    raise AssertionError(
                                        "owner_change_did_not_wait_for_exact_holder"
                                    )
                                await asyncio.sleep(0.01)
                    except BaseException:
                        contender.cancel()
                        await asyncio.wait_for(asyncio.gather(contender, return_exceptions=True), 5)
                        raise
            if contender is not None:
                await asyncio.wait_for(contender, 5)
            if mode == "changed-owner":
                # The separately committed owner change cannot reuse the binding.
                async with runtime.acquire() as connection, connection.transaction():
                    evidence = await broker.authorities.policy(connection, principal, cap, args)
                    assert (
                        bind_recipient_policy(cap, args, arguments_digest(args), evidence).recipient
                        == "non_owner"
                    )
            result = await broker.call(goal, token, cap.identity, args)
            async with admin.acquire() as observer:
                row = await observer.fetchrow(
                    "SELECT * FROM messenger.pending_actions WHERE id=$1", result.effect_id
                )
                gate = await observer.fetchrow(
                    "SELECT * FROM messenger.approval_goal_effects WHERE effect_id=$1",
                    result.effect_id,
                )
                receipts = await observer.fetchval(
                    "SELECT count(*) FROM messenger.approval_goal_receipts WHERE effect_id=$1",
                    result.effect_id,
                )
            assert native.sends == []
            if mode == "rollback":
                assert result.status == "unknown" and row is None and gate is None and receipts == 0
                return
            assert (
                result.status == "parked"
                and row["tool_args"] == args
                and row["status"] == "pending"
            )
            assert gate["phase"] == "pending" and receipts >= 1
            assert (
                row["invocation_id"] == invocation
                and gate["park_authorization"]["grant"] == grants[0]
            )
            if mode in {"unknown", "ambiguous", "changed-owner"}:
                return
            if mode in {"edited", "resource"}:
                args = {**args, "to": other_address, "subject": "edited"}
                await admin.execute(
                    "UPDATE messenger.pending_actions SET tool_args=$2::jsonb WHERE id=$1",
                    result.effect_id,
                    args,
                )
            await op.owner.transition(
                identity(exchanges[0]["identity"]),
                target_status=ActionStatus.APPROVED,
                decided_by="human:owner",
                event_actor="owner",
                event_reason="email",
            )
            await admin.execute(
                "UPDATE switchboard.invocations SET status='returned' WHERE id=$1", invocation
            )
            if mode == "stop":
                await GoalStop(runtime).stop_goal(goal, uuid4(), "a" * 64)
            if mode in {"resource", "stop"}:
                with pytest.raises(BrokerRefused):
                    await broker.replay_approved(goal, result.effect_id)
                assert native.sends == []
                assert not await admin.fetchval(
                    "SELECT dispatch_consumed_at IS NOT NULL FROM messenger.approval_goal_effects WHERE effect_id=$1",
                    result.effect_id,
                )
                return
            if mode == "race":
                outcomes = await asyncio.gather(
                    *(broker.replay_approved(goal, result.effect_id) for _ in range(2)),
                    return_exceptions=True,
                )
                assert sum(not isinstance(v, BaseException) for v in outcomes) == 1
                replay = next(v for v in outcomes if not isinstance(v, BaseException))
            else:
                replay = await broker.replay_approved(goal, result.effect_id)
            assert replay.status == "unknown" and replay.value is None
            assert native.sends == ([] if mode == "permission" else [args])
            # A separate connection observes consume/receipt after the real TX.
            assert await admin.fetchval(
                "SELECT dispatch_consumed_at IS NOT NULL FROM messenger.approval_goal_effects WHERE effect_id=$1",
                result.effect_id,
            )
            with pytest.raises(BrokerRefused):
                await broker.replay_approved(goal, result.effect_id)
            assert native.sends == ([] if mode == "permission" else [args])
            assert (
                await admin.fetchval(
                    "SELECT used_calls FROM switchboard.budgets WHERE goal_id=$1", goal
                )
                == 1
            )
        finally:
            if renamed:
                await admin.execute(
                    "ALTER FUNCTION public.native_email_lookup_unavailable(text,text[]) RENAME TO resolve_owner_triple"
                )
            if mode == "rollback":
                await admin.execute(
                    "DROP TRIGGER email_contract_refuse ON messenger.pending_actions; DROP FUNCTION messenger.email_contract_refuse()"
                )
