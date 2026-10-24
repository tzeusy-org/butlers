"""Registered publisher -> current fleet reader -> actual broker consumers.

The stateful SQL protocol stores prove software composition, not PostgreSQL
locks, roles or durability. No snapshot/projection/authority method is mocked.
REQ-capability-authorization-002; REQ-capability-authorization-004.
"""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from butlers.capability_broker import catalog as broker_catalog
from butlers.capability_broker.contracts import BrokerRefused, Reason
from butlers.capability_broker.domains import DomainReceipt
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
from tests.contracts.test_tool_authorization_catalog import _roster_catalog

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
            replace(
                capability,
                effect_class="record",
                recipient_params=RecipientParameters(("recipient",)),
            ),
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


async def registered_notify_broker(monkeypatch):
    """Actual roster finalizer/publication; controlled identity SQL, never notify I/O."""
    monkeypatch.setattr("butlers.db.Database.connect", AsyncMock())
    final = await _roster_catalog("relationship")
    broker, connection, domains, token, store, publishers = await current_broker()
    await publishers["relationship"].publish(final)
    connection.grants = (Grant("relationship__notify"),)
    async with connection.transaction():
        connection.catalog_digest = (await PublishedCatalogProvider().snapshot(connection)).digest
    domains.receipt = DomainReceipt("unknown")
    return broker, connection, domains, token, store, final


@pytest.mark.parametrize(
    "branch",
    [
        "default",
        "entity",
        "channel",
        "non-owner",
        "unknown",
        "ambiguous",
        "deleted",
        "merged",
        "inherited",
        "forged",
        "conflicting",
        "exposed",
    ],
)
async def test_published_native_notify_consumes_same_transaction_identity(monkeypatch, branch):
    broker, connection, domains, token, _, final = await registered_notify_broker(monkeypatch)
    owner, other = uuid4(), uuid4()
    args = {"message": "controlled message"}
    dispatched = branch in {"default", "entity", "channel"}
    if branch in {"entity", "non-owner", "unknown", "deleted", "merged", "conflicting"}:
        args["entity_id"] = str(owner if branch != "non-owner" else other)
    if branch in {"channel", "conflicting"}:
        args.update(channel="email", recipient="owner@example.invalid")
    if branch == "inherited":
        args["request_context"] = {"owner": True}
    if branch == "forged":
        args.update(entity_id=str(other), owner=True, recipient_resolver="core_notify")
    if branch == "exposed":
        connection.exposed = True
    identity_queries = []
    fetch, fetchrow = connection.fetch, connection.fetchrow

    async def identities(sql, *values):
        if "FROM public.entities" not in sql:
            return await fetch(sql, *values)
        assert connection.in_transaction
        assert "FOR SHARE" in sql and "LIMIT 2" in sql
        assert "metadata->>'merged_into' IS NULL" in sql
        assert "metadata->>'deleted_at' IS NULL" in sql
        identity_queries.append((sql, values))
        if branch in {"deleted", "merged"}:
            return []
        return [{"id": owner}, {"id": other}] if branch == "ambiguous" else [{"id": owner}]

    async def target(sql, *values):
        if "public.entities" in sql:
            assert connection.in_transaction and "FOR SHARE" in sql
            assert "metadata->>'merged_into' IS NULL" in sql
            assert "metadata->>'deleted_at' IS NULL" in sql
            identity_queries.append((sql, values))
            return None if branch in {"unknown", "deleted", "merged"} else {"id": values[0]}
        if "public.resolve_owner_triple" in sql:
            assert connection.in_transaction
            assert values[0] == "owner-channel" and values[1]
            identity_queries.append((sql, values))
            return {"entity_id": other if branch == "conflicting" else owner, "is_primary": True}
        return await fetchrow(sql, *values)

    connection.fetch, connection.fetchrow = identities, target
    listed = await broker.list_tools(connection.goal, token)
    assert len(listed) == 1 and listed[0].recipient_resolver == "core_notify"
    assert final["notify"].namespace == "core.notifications"
    assert listed[0].recipient_params == final["notify"].recipient_params
    result = await broker.call(connection.goal, token, "relationship__notify", args)
    assert result.status == ("unknown" if dispatched else "parked")
    assert result.value is None and result.effect_id is not None
    assert connection.used == 1 and len(domains.calls) == 1 and identity_queries
    reservation, received = domains.calls[0][:2]
    assert received == args and reservation.args_digest
    assert reservation.principal.catalog_digest == connection.catalog_digest
    assert reservation.decision.disposition.value == ("dispatch" if dispatched else "park")
    assert not reservation.decision.allow_standing_rule


async def test_published_notify_refuses_unavailable_identity_and_foreign_provenance(monkeypatch):
    broker, connection, domains, token, store, final = await registered_notify_broker(monkeypatch)
    original = connection.fetch

    async def unavailable(sql, *args):
        if "FROM public.entities" in sql:
            raise RuntimeError("controlled identity unavailable")
        return await original(sql, *args)

    connection.fetch = unavailable
    with pytest.raises(BrokerRefused, match=Reason.POLICY.value):
        await broker.call(connection.goal, token, "relationship__notify", {"message": "never sent"})
    assert connection.used == 0 and not domains.calls
    cap = (await broker.list_tools(connection.goal, token))[0]
    with pytest.raises(BrokerRefused, match=Reason.POLICY.value):
        await broker.authorities.policy(connection, None, cap, {})
    foreign = replace(final["notify"], module_name="other", namespace="other.notifications")
    from butlers.core.tool_catalog import ToolCatalog

    projected = broker_catalog.project_catalog(
        ToolCatalog((foreign,), True, final.generation_digest), domain="relationship"
    )
    async with connection.transaction():
        with pytest.raises(BrokerRefused, match=Reason.POLICY.value):
            await broker.authorities.policy(connection, None, projected.capabilities[0], {})
    assert len(store.current) == len(DOMAINS) and connection.used == 0 and not domains.calls


async def registered_health_record(monkeypatch, *, approvals_enabled=False):
    """Real finalizer/originals and current fleet; DB/embedding are software controls."""
    from butlers.daemon import ButlerDaemon
    from butlers.tools.health import measurements

    captured = []
    finalize = ButlerDaemon._finalize_tool_catalog

    async def capture(daemon):
        result = await finalize(daemon)
        captured.append(daemon)
        return result

    monkeypatch.setattr("butlers.db.Database.connect", AsyncMock())
    from tests.contracts import test_tool_authorization_catalog as registration_owner

    load = registration_owner.load_config

    def configured(path):
        config = load(path)
        # An explicit test configuration, not current Health deployment/activation.
        if approvals_enabled:
            config.modules["approvals"]["enabled"] = True
        return config

    with monkeypatch.context() as registration:
        registration.setattr(registration_owner, "load_config", configured)
        registration.setattr(ButlerDaemon, "_finalize_tool_catalog", capture)
        final = await _roster_catalog("health")
    originals = await captured[0]._broker_owning_originals()
    facts = []
    owner = uuid4()
    engine = object()
    monkeypatch.setattr(measurements, "_get_owner_entity_id", AsyncMock(return_value=owner))
    monkeypatch.setattr(measurements, "_get_embedding_engine", lambda: engine)

    async def store_fact(pool, **kwargs):
        assert kwargs["entity_id"] == owner and kwargs["embedding_engine"] is engine
        facts.append(kwargs)
        return {"id": uuid4()}

    monkeypatch.setattr("butlers.modules.memory.storage.store_fact", store_fact)
    return final, originals, facts


async def registered_health_record_broker(monkeypatch, *, park_only=False, approvals_enabled=False):
    final, originals, facts = await registered_health_record(
        monkeypatch, approvals_enabled=approvals_enabled
    )
    broker, conn, domains, token, store, publishers = await current_broker()
    await publishers["health"].publish(final)
    conn.grants = (Grant("health__measurement_log", park_only=park_only),)
    async with conn.transaction():
        conn.catalog_digest = (await PublishedCatalogProvider().snapshot(conn)).digest
    return broker, conn, domains, token, store, final, originals, facts


async def test_real_health_record_registration_dispatches_original_after_central_commit(
    monkeypatch,
):
    (
        broker,
        conn,
        domains,
        token,
        _,
        final,
        originals,
        facts,
    ) = await registered_health_record_broker(monkeypatch)
    descriptor = final["measurement_log"]
    assert descriptor.effect_class == "record" and descriptor.owner_authority is False
    assert descriptor.recipient_params == RecipientParameters()
    calls = []

    async def dispatch(reservation, args, assertion, *, idempotency_key):
        assert not conn.in_transaction and conn.events[-1] == "commit"
        assert reservation.selected_grant == conn.grants[0]
        calls.append(await originals["measurement_log"](**args))
        return DomainReceipt("unknown")

    monkeypatch.setattr(domains, "dispatch", dispatch)
    args = {"type": "weight", "value": 72.5, "notes": "explicit owner entry"}
    result = await broker.call(conn.goal, token, "health__measurement_log", args)
    assert result.status == "unknown" and result.value is None and result.effect_id is not None
    assert len(calls) == len(facts) == 1 and calls[0]["value"] == args["value"]
    assert facts[0]["predicate"] == "measurement_weight"
    assert facts[0]["metadata"] == {
        "value": 72.5,
        "source": "owner_log",
        "notes": "explicit owner entry",
    }
    assert conn.used == 1 and conn.exposed
    with pytest.raises(BrokerRefused):
        await broker.call(conn.goal, token, "health__measurement_log", args)
    assert len(facts) == 1 and conn.used == 1


async def test_actual_health_disabled_approvals_refuse_mandatory_park_before_charge(monkeypatch):
    broker, conn, domains, token, _, final, _, facts = await registered_health_record_broker(
        monkeypatch, park_only=True
    )
    assert final["measurement_log"].approval_policy["enabled"] is False
    with pytest.raises(BrokerRefused) as refused:
        await broker.call(
            conn.goal, token, "health__measurement_log", {"type": "weight", "value": 72}
        )
    assert refused.value.reason is Reason.UNAVAILABLE
    assert facts == [] and domains.calls == [] and conn.used == 0 and conn.effect is None


@pytest.mark.parametrize(
    "fault", ["stopped", "revoked", "epoch", "budget", "resource", "changed", "retired"]
)
async def test_registered_health_record_keeps_current_grant_and_ledger_refusals(monkeypatch, fault):
    broker, conn, domains, token, store, final, _, facts = await registered_health_record_broker(
        monkeypatch
    )
    if fault == "stopped":
        conn.status = "stopped"
    elif fault == "revoked":
        conn.invocation_count = 0
    elif fault == "epoch":
        conn.epoch = 1
    elif fault == "budget":
        conn.budget = 0
    elif fault == "resource":
        conn.grants = (Grant("health__measurement_log", resources=(("fabricated", ("value",)),)),)
    elif fault == "changed":
        conn.catalog_digest = "changed-current-fleet"
    else:
        store.current["health"]["live"] = False
    with pytest.raises(BrokerRefused):
        await broker.call(
            conn.goal, token, "health__measurement_log", {"type": "weight", "value": 72}
        )
    assert conn.used == 0 and conn.effect is None and domains.calls == [] and facts == []


async def test_record_policy_preserves_exposed_owner_authority_and_party_refusals(monkeypatch):
    from butlers.capability_broker.contracts import Disposition
    from butlers.capability_broker.policy import decide

    broker, conn, _, token, _, _, _, _ = await registered_health_record_broker(monkeypatch)
    async with conn.transaction():
        principal = await BrokerLedger(conn).authenticate(conn.goal, token)
        cap = next(
            c
            for c in (await broker.authorities.catalog(conn, principal)).capabilities
            if c.identity == "health__measurement_log"
        )
        evidence = await broker.authorities.policy(conn, principal, cap, {})
        exposed = replace(principal, exposed=True)
        assert decide(exposed, cap, conn.grants[0], evidence).disposition is Disposition.DISPATCH
        assert (
            decide(
                exposed, replace(cap, owner_authority=True), conn.grants[0], evidence
            ).disposition
            is Disposition.REFUSE
        )
        for parties in (
            RecipientParameters(("recipient",)),
            RecipientParameters(undeterminable=True),
            RecipientParameters(default_owner_condition="native"),
            RecipientParameters(undeterminable_condition="native"),
        ):
            with pytest.raises(BrokerRefused) as refused:
                await broker.authorities.policy(
                    conn, principal, replace(cap, recipient_params=parties), {}
                )
            assert refused.value.reason is Reason.POLICY
