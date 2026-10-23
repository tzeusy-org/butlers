"""Real migrated M1 catalog -> admission -> locked broker catalog consumer.

Authored PostgreSQL contract; NOT RUN locally. The source-origin and read-domain
collaborators below are explicit controls, not runtime/owning-effect authority.
REQ-capability-authorization-002; REQ-capability-authorization-004.
"""

from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from butlers.capability_broker.assertions import AssertionSigner
from butlers.capability_broker.catalog import PublishedAuthorities
from butlers.capability_broker.contracts import BrokerRefused, Reason
from butlers.capability_broker.service import Broker
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
