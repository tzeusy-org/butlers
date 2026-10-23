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
