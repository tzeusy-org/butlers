"""Registered publisher -> current fleet reader -> actual broker consumers.

The stateful SQL protocol stores prove software composition, not PostgreSQL
locks, roles or durability. No snapshot/projection/authority method is mocked.
REQ-capability-authorization-002; REQ-capability-authorization-004.
"""

import asyncio
from dataclasses import replace

import pytest

from butlers.capability_broker import catalog as broker_catalog
from butlers.capability_broker.contracts import BrokerRefused, Reason
from butlers.capability_broker.forwarding import SignedMCPDomains
from butlers.capability_broker.ledger import BrokerLedger
from butlers.control import SessionRefusalListener
from butlers.core.catalog_reader import DOMAINS, PublishedCatalogProvider
from butlers.core.goal_admission import Grant
from butlers.core.tool_catalog import RecipientParameters
from tests.capability_broker.test_broker import build
from tests.contracts.test_catalog_publication import (
    Connection as CatalogConnection,
)
from tests.contracts.test_catalog_publication import complete_fleet, registered_catalog

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def current_broker():
    store, publishers = await complete_fleet()
    broker, connection, _, domains, token = build(grant=Grant("health__lookup"))
    reader_connection = CatalogConnection(store, "switchboard")
    # Delegate only actual publication SQL. Existing central row controls stay
    # in the broker's same controlled connection/transaction, never a pool.
    original_fetch = connection.fetch

    async def fetch(sql, *args):
        if "FROM pg_class" in sql:
            return await reader_connection.fetch(sql, *args)
        return await original_fetch(sql, *args)

    connection.fetch = fetch
    original = connection.fetchrow

    async def fetchrow(sql, *args):
        if "AS registrations" in sql:
            assert connection.in_transaction
            return await reader_connection.fetchrow(sql, *args)
        row = await original(sql, *args)
        if "FROM switchboard.goals" in sql:
            row["catalog_digest"] = connection.catalog_digest
        return row

    connection.fetchrow = fetchrow
    connection.is_in_transaction = lambda: connection.in_transaction
    async with connection.transaction():
        snapshot = await PublishedCatalogProvider().snapshot(connection)
    connection.catalog_digest = snapshot.digest
    broker.authorities = broker_catalog.PublishedAuthorities()
    return broker, connection, domains, token, store, publishers


async def test_registered_fleet_drives_grant_listing_and_read_dispatch():
    broker, connection, domains, token, store, _ = await current_broker()
    listed = await broker.list_tools(connection.goal, token)
    assert [c.identity for c in listed] == ["health__lookup"]
    assert listed[0].input_schema["required"] == ["query"]
    assert listed[0].description == "Read a native value."
    assert len(store.current) == len(DOMAINS)
    result = await broker.call(connection.goal, token, "health__lookup", {"query": "native"})
    assert result.status == "returned"
    assert connection.used == 1 and connection.exposed
    assert domains.calls[0][0].principal.catalog_digest == connection.catalog_digest
    assert domains.calls[0][1] == {"query": "native"}
    assert domains.calls[0][0].effect_id is None


async def test_current_published_read_reaches_actual_registered_mcp_handler():
    broker, connection, _, token, _, _ = await current_broker()
    mcp, _ = await registered_catalog("health")
    app = mcp.http_app(path="/mcp", stateless_http=False, json_response=False)

    class Listener(SessionRefusalListener):
        def _application(self):
            return app

        def _application_lifespan(self):
            return app.router.lifespan_context(app)

    listener = Listener(host="127.0.0.1", port=0)
    await listener.start()
    try:
        broker.domains = SignedMCPDomains({"health": f"http://127.0.0.1:{listener.port}/mcp"})
        async with asyncio.timeout(10):
            result = await broker.call(
                connection.goal, token, "health__lookup", {"query": "actual registered read"}
            )
        assert result.status == "returned" and result.effect_id is None
        assert result.value["content"][0]["text"] == "actual registered read"
        assert connection.used == 1 and connection.exposed
    finally:
        await listener.close()
    assert listener.task.done()


async def test_new_current_definition_and_retirement_refuse_without_cached_fallback():
    broker, connection, domains, token, store, publishers = await current_broker()
    assert await broker.list_tools(connection.goal, token)
    _, replacement = await registered_catalog("health", description="Changed definition.")
    await publishers["health"].publish(replacement)
    for operation in (
        lambda: broker.list_tools(connection.goal, token),
        lambda: broker.call(connection.goal, token, "health__lookup", {"query": "native"}),
    ):
        with pytest.raises(BrokerRefused) as caught:
            await operation()
        assert caught.value.reason is Reason.CATALOG
    assert connection.used == 0 and not domains.calls
    async with connection.transaction():
        connection.catalog_digest = (await PublishedCatalogProvider().snapshot(connection)).digest
    assert (await broker.list_tools(connection.goal, token))[0].description == "Changed definition."
    store.current["health"]["live"] = False
    with pytest.raises(BrokerRefused, match="catalog_unavailable_or_changed"):
        await broker.list_tools(connection.goal, token)
    assert connection.used == 0 and not domains.calls


@pytest.mark.parametrize("damage", ["disabled", "missing-domain", "bad-body", "bad-membership"])
async def test_complete_current_fleet_failure_is_content_blind_before_reservation(damage):
    broker, connection, domains, token, store, _ = await current_broker()
    if damage == "disabled":
        store.enabled = False
    elif damage == "missing-domain":
        del store.current["travel"]
    elif damage == "bad-body":
        store.current["health"]["body"]["domain"] = "private-invalid-payload"
    else:
        store.members.pop()
    with pytest.raises(BrokerRefused) as caught:
        await broker.call(connection.goal, token, "health__lookup", {"query": "native"})
    assert str(caught.value) == Reason.CATALOG.value
    assert connection.used == 0 and not connection.exposed and not domains.calls


async def test_missing_selection_recipient_rule_and_risk_authority_is_not_invented():
    broker, connection, _, token, _, _ = await current_broker()
    capability = (await broker.list_tools(connection.goal, token))[0]
    async with connection.transaction():
        principal = await BrokerLedger(connection).authenticate(connection.goal, token)
        with pytest.raises(BrokerRefused) as selected:
            await broker.authorities.selection(
                connection, principal, capability, principal.grants[0]
            )
        assert selected.value.reason is Reason.SELECTION
        for changed in (
            replace(capability, effect_class="record"),
            replace(capability, effect_class="actuate"),
            replace(
                capability,
                effect_class="communicate",
                recipient_params=RecipientParameters(undeterminable=True),
            ),
            replace(capability, recipient_params=RecipientParameters(("recipient",))),
            replace(capability, recipient_params=RecipientParameters(undeterminable=True)),
            replace(
                capability, recipient_params=RecipientParameters(default_owner_condition="native")
            ),
        ):
            with pytest.raises(BrokerRefused) as refused:
                await broker.authorities.policy(connection, principal, changed, {})
            assert refused.value.reason is Reason.POLICY


async def test_catalog_requires_owning_transaction():
    broker, connection, _, _, _, _ = await current_broker()
    with pytest.raises(BrokerRefused, match="catalog_unavailable_or_changed"):
        await broker.authorities.catalog(connection, None)
