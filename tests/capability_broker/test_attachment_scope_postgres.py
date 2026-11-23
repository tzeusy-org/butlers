"""bu-jv1moa.64 PR2: admission scopes attachment_materialize to the request's own refs.

Real PostgreSQL, where the grant lives: the goal's ceiling and the invocation's grant snapshot
carry the pair scope, and the broker refuses any other pair with ``Reason.RESOURCE``.
"""

from __future__ import annotations

import shutil

import pytest

from butlers.capability_broker.contracts import Catalog, Reason
from butlers.core.goal_admission import (
    Capability as AdmissionCapability,
)
from butlers.core.goal_admission import CatalogView, Evidence, decode_grants, derive_admission
from tests.capability_broker.helpers import BROKER_DOMAIN, capability
from tests.capability_broker.test_broker_ledger import StaticCatalog, _refused, world

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

PAIR = ("message_id", "attachment_id")
MATERIALIZE = capability(
    "attachment_materialize",
    "read",
    BROKER_DOMAIN,
    resource_params=PAIR,
    resource_pair=PAIR,
    output_trust="untrusted",
)
CATALOG = Catalog("catalog-v1", (MATERIALIZE,))
VIEW = CatalogView(
    "catalog-v1",
    (AdmissionCapability(MATERIALIZE.identity, BROKER_DOMAIN, "read", resource_pair=PAIR),),
)
REFS = (("m1", "a1"), ("m1", "a2"), ("m2", "a1"))


def _plan(refs, *, owner=False):
    return derive_admission(Evidence("message", "mail", owner=owner, attachment_refs=refs), VIEW)


async def _admitted(w, plan):
    goal = await w.goal(*plan.ceiling, origin=plan.origin, exposed=plan.exposed)
    lead, token = await w.invocation(goal, *plan.ceiling)
    return goal, lead, token


@pytest.mark.parametrize("owner", [False, True], ids=["third-party", "owner"])
async def test_the_grant_persists_the_pair_scope_and_the_broker_enforces_it(broker_db_url, owner):
    async with world(broker_db_url, catalog=StaticCatalog(CATALOG)) as w:
        plan = _plan(REFS, owner=owner)
        goal, _, token = await _admitted(w, plan)
        stored = await w.owner.fetchval("SELECT ceiling FROM switchboard.goals WHERE id=$1", goal)
        (grant,) = decode_grants(stored)
        assert grant == plan.ceiling[0] and len(dict(grant.resources)) == 1
        assert [c.identity for c in await w.broker.list_tools(goal, token)] == [
            MATERIALIZE.identity
        ]

        for message_id, attachment_id in REFS:
            args = {"message_id": message_id, "attachment_id": attachment_id}
            await w.broker.call(goal, token, MATERIALIZE.identity, args)
        assert [call[1] for call in w.forwarder.calls] == [
            {"message_id": m, "attachment_id": a} for m, a in REFS
        ]

        # Each id is admitted somewhere in the request, but these pairs are not.
        for message_id, attachment_id in (("m2", "a2"), ("m3", "a1"), ("m1", "a9"), ("", "a1")):
            args = {"message_id": message_id, "attachment_id": attachment_id}
            refused = await _refused(w.broker.call(goal, token, MATERIALIZE.identity, args))
            assert refused.reason is Reason.RESOURCE, args
        assert len(w.forwarder.calls) == len(REFS)


async def test_a_request_with_no_attachment_refs_grants_no_callable_scope(broker_db_url):
    async with world(broker_db_url, catalog=StaticCatalog(CATALOG)) as w:
        plan = _plan(())
        assert [g.resources for g in plan.ceiling] == [()]
        goal, _, token = await _admitted(w, plan)
        assert await w.broker.list_tools(goal, token) == ()
        args = {"message_id": "m1", "attachment_id": "a1"}
        refused = await _refused(w.broker.call(goal, token, MATERIALIZE.identity, args))
        assert refused.reason is Reason.RESOURCE
        assert w.forwarder.calls == []


async def test_a_child_cannot_widen_the_parents_pair_scope(broker_db_url):
    async with world(broker_db_url, catalog=StaticCatalog(CATALOG)) as w:
        goal, lead, _ = await _admitted(w, _plan(REFS[:1]))
        # A child's grant, as intersected with the goal ceiling, can only narrow.
        from butlers.core.goal_admission import Grant, intersect

        asked = (Grant(MATERIALIZE.identity),)
        child = intersect(asked, _plan(REFS[:1]).ceiling)
        assert child == _plan(REFS[:1]).ceiling
        _, child_token = await w.invocation(goal, *child, depth=1, parent=lead)
        ok = {"message_id": "m1", "attachment_id": "a1"}
        await w.broker.call(goal, child_token, MATERIALIZE.identity, ok)
        other = {"message_id": "m1", "attachment_id": "a2"}
        refused = await _refused(w.broker.call(goal, child_token, MATERIALIZE.identity, other))
        assert refused.reason is Reason.RESOURCE


async def test_list_tools_shows_a_pair_capability_only_when_the_grant_has_the_pair_key(
    broker_db_url,
):
    from butlers.core.goal_admission import Grant

    async with world(broker_db_url, catalog=StaticCatalog(CATALOG)) as w:
        scoped = _plan(REFS[:1]).ceiling
        unscoped = (Grant(MATERIALIZE.identity),)
        goal, _, token = await _admitted(w, _plan(REFS[:1]))
        assert [c.identity for c in await w.broker.list_tools(goal, token)] == [
            MATERIALIZE.identity
        ]
        bare_goal = await w.goal(*unscoped)
        _, bare_token = await w.invocation(bare_goal, *unscoped)
        assert await w.broker.list_tools(bare_goal, bare_token) == ()
        assert scoped != unscoped
