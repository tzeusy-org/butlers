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


async def test_registered_calendar_response_is_conservative_per_event_park(monkeypatch):
    """Actual final Calendar registration/current fleet, no provider call or owner shortcut."""
    monkeypatch.setattr("butlers.db.Database.connect", AsyncMock())
    final = await _roster_catalog("relationship")
    broker, conn, domains, token, _, publishers = await current_broker()
    await publishers["relationship"].publish(final)
    entry = str(uuid4())
    conn.grants = (Grant("relationship__calendar_respond", resources=(("entry_id", (entry,)),)),)
    async with conn.transaction():
        conn.catalog_digest = (await PublishedCatalogProvider().snapshot(conn)).digest
    owner = uuid4()
    fetch = conn.fetch

    async def identities(sql, *args):
        if "FROM public.entities" in sql:
            assert conn.in_transaction
            return [{"id": owner}]
        return await fetch(sql, *args)

    conn.fetch = identities
    args = {"entry_id": entry, "request_id": str(uuid4()), "response_status": "accepted"}
    result = await broker.call(conn.goal, token, "relationship__calendar_respond", args)
    assert result.status == "parked" and result.approval_ref is not None
    reservation, actual = domains.calls[0][:2]
    assert actual == args and reservation.selected_grant == conn.grants[0]
    assert reservation.decision.disposition.value == "park"
    assert reservation.decision.allow_standing_rule is False
    assert (
        conn.used == 1 and final["calendar_respond"].approval_policy["mode"] == "always_per_event"
    )


async def registered_native_email(monkeypatch):
    """Actual Messenger/EmailModule registration; SQL controls, never SMTP I/O."""
    from types import SimpleNamespace

    from butlers.daemon import ButlerDaemon
    from butlers.modules.email import EmailModule

    captured = []
    finalize = ButlerDaemon._finalize_tool_catalog

    async def capture(daemon):
        result = await finalize(daemon)
        captured.append(daemon)
        return result

    apply_gates = ButlerDaemon._apply_approval_gates

    async def startup_originals(daemon):
        # Match lifecycle.py's actual assignment; the catalog-only helper does
        # not need it, but an owning replay consumer must keep pre-gate handlers.
        originals = await apply_gates(daemon)
        daemon._gated_tool_originals = originals
        return originals

    monkeypatch.setattr("butlers.db.Database.connect", AsyncMock())
    with monkeypatch.context() as registration:
        registration.setattr(ButlerDaemon, "_finalize_tool_catalog", capture)
        registration.setattr(ButlerDaemon, "_apply_approval_gates", startup_originals)
        final = await _roster_catalog("messenger")
    daemon = captured[0]
    email = next(m for m in daemon._active_modules if type(m) is EmailModule)

    class Permissions:
        granted = True
        reads = 0

        async def fetchrow(self, sql, *args):
            assert "FROM public.permissions" in sql and args == ("messenger", "email.send")
            self.reads += 1
            return {"granted": self.granted, "reason": "controlled permission"}

    permissions, sends = Permissions(), []
    email._pool, email._butler_name, email._audit_pool = permissions, "messenger", None

    def smtp(to, subject, body):
        sends.append({"to": to, "subject": subject, "body": body})
        return {"message_id": "controlled-not-a-commit-receipt"}

    monkeypatch.setattr(email, "_smtp_send", smtp)
    return SimpleNamespace(
        catalog=final, daemon=daemon, email=email, permissions=permissions, sends=sends
    )


async def registered_native_email_broker(monkeypatch):
    from types import SimpleNamespace

    native = await registered_native_email(monkeypatch)
    final, daemon, email = native.catalog, native.daemon, native.email
    permissions, sends = native.permissions, native.sends
    broker, conn, domains, token, store, publishers = await current_broker()
    await publishers["messenger"].publish(final)
    conn.grants = (
        Grant("messenger__email_send_message", resources=(("to", ("owner@example.invalid",)),)),
    )
    owner = uuid4()
    reads = []
    fetch, fetchrow = conn.fetch, conn.fetchrow

    async def identities(sql, *args):
        if "FROM public.entities" in sql:
            assert conn.in_transaction and "FOR SHARE" in sql and "LIMIT 2" in sql
            assert "metadata->>'merged_into' IS NULL" in sql
            assert "metadata->>'deleted_at' IS NULL" in sql
            reads.append("owner")
            return [{"id": owner}]
        return await fetch(sql, *args)

    async def target(sql, *args):
        if "public.resolve_owner_triple" in sql:
            assert conn.in_transaction
            assert args[0] == "owner-channel"
            reads.append("email")
            return (
                {"entity_id": owner, "is_primary": True}
                if args[1] == ["has-email:owner@example.invalid"]
                else None
            )
        return await fetchrow(sql, *args)

    conn.fetch, conn.fetchrow = identities, target
    async with conn.transaction():
        conn.catalog_digest = (await PublishedCatalogProvider().snapshot(conn)).digest
    return SimpleNamespace(
        broker=broker,
        conn=conn,
        domains=domains,
        token=token,
        fleet=store,
        publishers=publishers,
        catalog=final,
        daemon=daemon,
        email=email,
        permissions=permissions,
        sends=sends,
        owner=owner,
        identity_reads=reads,
    )


@pytest.mark.parametrize(
    "branch",
    [
        "owner",
        "non-owner",
        "unknown",
        "ambiguous",
        "deleted",
        "merged",
        "malformed",
        "multipart",
        "display",
        "outage",
        "exposed",
        "park-only",
    ],
)
async def test_native_email_same_transaction_recipient_and_mandatory_park(monkeypatch, branch):
    from butlers.capability_broker.policy import bind_recipient_policy
    from butlers.core.capability_json import arguments_digest

    native = await registered_native_email_broker(monkeypatch)
    broker, conn = native.broker, native.conn
    to = {
        "non-owner": "other@example.invalid",
        "unknown": "unknown@example.invalid",
        "malformed": "not-an-address",
        "multipart": "owner@example.invalid,other@example.invalid",
        "display": "Owner <owner@example.invalid>",
    }.get(branch, "owner@example.invalid")
    conn.grants = (
        Grant(
            "messenger__email_send_message",
            resources=(("to", (to,)),),
            park_only=branch == "park-only",
        ),
    )
    conn.exposed = branch == "exposed"
    args = {"to": to, "subject": "controlled", "body": "untrusted content"}
    fetch, fetchrow = conn.fetch, conn.fetchrow

    async def owners(sql, *values):
        if "FROM public.entities" in sql:
            assert conn.in_transaction and "FOR SHARE" in sql
            if branch == "ambiguous":
                return [{"id": native.owner}, {"id": uuid4()}]
            if branch in {"deleted", "merged"}:
                return []  # Actual SQL above excludes either tombstone.
        return await fetch(sql, *values)

    async def target(sql, *values):
        if "public.resolve_owner_triple" in sql:
            assert conn.in_transaction
            if branch == "outage":
                raise RuntimeError("controlled_identity_unavailable")
            if branch in {"ambiguous", "deleted", "merged", "unknown"}:
                return None
        return await fetchrow(sql, *values)

    conn.fetch, conn.fetchrow = owners, target
    cap = (await broker.list_tools(conn.goal, native.token))[0]
    assert cap.recipient_resolver == "native_email" and cap.idempotency == "none"
    if branch == "outage":
        with pytest.raises(BrokerRefused, match=Reason.POLICY.value):
            await broker.call(conn.goal, native.token, cap.identity, args)
        assert conn.used == 0 and native.domains.calls == [] and native.sends == []
        return
    async with conn.transaction():
        evidence = await broker.authorities.policy(conn, None, cap, args)
        bound = bind_recipient_policy(cap, args, arguments_digest(args), evidence)
    assert bound.recipient == (
        "owner" if branch in {"owner", "exposed", "park-only"} else "non_owner"
    )
    result = await broker.call(conn.goal, native.token, cap.identity, args)
    assert result.status == "parked" and result.value is None and native.sends == []
    assert native.domains.calls[0][0].decision.disposition.value == "park"


@pytest.mark.parametrize(
    "damage", ["module", "namespace", "group", "recipient", "resource", "selection", "gate"]
)
async def test_native_email_cannot_borrow_foreign_or_changed_metadata(monkeypatch, damage):
    from butlers.core.tool_catalog import ToolCatalog

    native = await registered_native_email_broker(monkeypatch)
    descriptor = native.catalog["email_send_message"]
    changes = {
        "module": {"module_name": "other"},
        "namespace": {"namespace": "other.write"},
        "group": {"group_name": "other"},
        "recipient": {"recipient_params": RecipientParameters(("subject",))},
        "resource": {"resource_params": (), "selection_bound": {}},
        "selection": {"selection_bound": {"to": True}},
        "gate": {"approval_policy": {**descriptor.approval_policy, "configured_gate": False}},
    }[damage]
    altered = replace(descriptor, **changes)
    try:
        projected = broker_catalog.project_catalog(
            ToolCatalog((altered,), True, native.catalog.generation_digest), domain="messenger"
        )
    except BrokerRefused as refused:
        assert refused.reason is Reason.CATALOG
    else:
        cap = projected.capabilities[0]
        assert cap.recipient_resolver == "none"
        async with native.conn.transaction():
            with pytest.raises(BrokerRefused, match=Reason.POLICY.value):
                await native.broker.authorities.policy(
                    native.conn, None, cap, {"to": "owner@example.invalid"}
                )
    assert native.sends == [] and native.conn.used == 0


@pytest.mark.parametrize(
    "fault", ["stopped", "revoked", "budget", "resource", "catalog", "retired"]
)
async def test_native_email_reservation_keeps_current_ledger_fences(monkeypatch, fault):
    native = await registered_native_email_broker(monkeypatch)
    conn = native.conn
    if fault == "stopped":
        conn.status = "stopped"
    elif fault == "revoked":
        conn.invocation_count = 0
    elif fault == "budget":
        conn.budget = 0
    elif fault == "resource":
        conn.grants = (
            Grant("messenger__email_send_message", resources=(("to", ("other@example.invalid",)),)),
        )
    elif fault == "catalog":
        conn.catalog_digest = "changed"
    else:
        native.fleet.current["messenger"]["live"] = False
    with pytest.raises(BrokerRefused):
        await native.broker.call(
            conn.goal,
            native.token,
            "messenger__email_send_message",
            {"to": "owner@example.invalid", "subject": "never", "body": "sent"},
        )
    assert conn.used == 0 and native.sends == [] and native.domains.calls == []


async def registered_home_admin(monkeypatch, *, pool=None, approvals_enabled=True):
    """Actual core registrar/finalizer and ungated original, with no provider startup."""
    from types import SimpleNamespace

    from butlers.daemon import ButlerDaemon
    from tests.contracts import test_tool_authorization_catalog as registration_owner

    captured = []
    load = registration_owner.load_config
    apply = ButlerDaemon._apply_approval_gates

    def daemon(*args, **kwargs):
        if pool is not None:
            kwargs["db"].pool = pool
        instance = ButlerDaemon(*args, **kwargs)
        captured.append(instance)
        return instance

    def configured(path):
        config = load(path)
        if approvals_enabled is None:
            config.modules.pop("approvals", None)
        else:
            config.modules["approvals"]["enabled"] = approvals_enabled
        return config

    async def originals(instance):
        value = await apply(instance)
        instance._gated_tool_originals = value
        return value

    monkeypatch.setattr("butlers.db.Database.connect", AsyncMock())
    with monkeypatch.context() as registration:
        registration.setattr(registration_owner, "ButlerDaemon", daemon)
        registration.setattr(registration_owner, "load_config", configured)
        registration.setattr(ButlerDaemon, "_apply_approval_gates", originals)
        catalog = await _roster_catalog("home")
    instance = captured[0]
    return SimpleNamespace(
        catalog=catalog, originals=await instance._broker_owning_originals(), daemon=instance
    )


async def registered_home_admin_broker(monkeypatch, *, pool=None, approvals_enabled=True):
    from types import SimpleNamespace

    native = await registered_home_admin(
        monkeypatch, pool=pool, approvals_enabled=approvals_enabled
    )
    broker, conn, domains, token, fleet, publishers = await current_broker()
    await publishers["home"].publish(native.catalog)
    conn.grants = (Grant("home__schedule_create"),)
    async with conn.transaction():
        conn.catalog_digest = (await PublishedCatalogProvider().snapshot(conn)).digest
    return SimpleNamespace(
        native=native,
        broker=broker,
        conn=conn,
        domains=domains,
        token=token,
        fleet=fleet,
        publishers=publishers,
    )


@pytest.mark.parametrize("restriction", ["plain", "rule", "exposed-rule", "park-only-rule"])
async def test_registered_administer_commits_then_signed_owning_park(monkeypatch, restriction):
    """Actual production policy/PARK/owning rules; SQL state here is software only."""
    import copy
    import time
    from types import SimpleNamespace

    from butlers.capability_broker.owning import PrivateApprovals
    from butlers.capability_broker.transport.client import PrivateClient
    from butlers.capability_broker.transport.config import PeerPolicy, TransportConfig
    from butlers.core.capability_json import arguments_digest
    from butlers.modules.approvals.goal_dispatch import GoalDispatch
    from tests.modules.approvals.test_goal_control import RuleStore

    native = await registered_home_admin_broker(monkeypatch)
    conn, broker = native.conn, native.broker
    descriptor = native.native.catalog["schedule_create"]
    assert descriptor.effect_class == "administer"
    assert descriptor.recipient_params == RecipientParameters()
    assert {k: descriptor.approval_policy[k] for k in ("mode", "enabled", "configured_gate")} == {
        "mode": "default",
        "enabled": True,
        "configured_gate": False,
    }
    conn.exposed = restriction == "exposed-rule"
    if restriction == "park-only-rule":
        conn.grants = (Grant("home__schedule_create", park_only=True),)
    args = {"name": "controlled-admin", "cron": "0 8 * * *", "prompt": "controlled"}
    conn.events.clear()

    class Store(RuleStore):
        async def fetchrow(self, sql, *values):
            if "current_schema()" in sql and "current_user" in sql:
                return {
                    "schema": "home",
                    "role": "butler_home_rw",
                    "unsafe": False,
                    "schemas": ["pg_catalog", "home", "public"],
                }
            if "SET park_authorization=$2" in sql:
                self.gate.update(
                    park_authorization=copy.deepcopy(values[1]),
                    park_authorization_digest=values[2],
                    park_call_id=values[3],
                    state_version=self.gate["state_version"] + 1,
                )
                return copy.deepcopy(self.gate)
            return await super().fetchrow(sql, *values)

    store = Store()
    store.goal_id, store.arguments, store.digest = conn.goal, args, arguments_digest(args)
    store.rule.update(
        tool_name="schedule_create",
        active=restriction != "plain",
        arg_constraints={k: {"type": "exact", "value": v} for k, v in args.items()},
    )
    now = int(time.time())
    broker.clock = lambda: now
    operation = GoalDispatch(
        store,
        "home",
        native.native.catalog,
        native.native.originals,
        {broker.signer.kid: broker.signer.private_key.public_key()},
        clock=lambda: now,
    )
    client = PrivateClient(
        TransportConfig(
            "https://localhost:4200/mcp",
            PeerPolicy("owning-service", "home", frozenset({"DNS:localhost"})),
        ),
        None,
    )
    observed = []

    async def call(self, value, *, assertion=None):
        assert not conn.in_transaction and conn.events[-1] == "commit"
        assert value["operation"] == "PARK" and assertion is None
        store.action_id = conn.effect
        result = await operation(value, SimpleNamespace(role="broker-control", domain="home"))
        observed.append(value)
        return result

    monkeypatch.setattr(PrivateClient, "call", call)
    handler = AsyncMock()
    monkeypatch.setattr("butlers.core_tools._scheduling._schedule_create", handler)
    broker.approvals = PrivateApprovals({"home": client})
    try:
        result = await broker.call(conn.goal, native.token, "home__schedule_create", args)
    finally:
        await client.aclose()
    assert result.status == "parked" and result.approval_ref == str(conn.effect)
    assert len(observed) == 1 and observed[0]["arguments"] == args
    assert conn.used == 1 and conn.events == ["effect", "commit", "parked", "commit"]
    assert native.domains.calls == [] and handler.await_count == 0
    assert store.row["goal_id"] == conn.goal and store.row["invocation_id"] == conn.invocation
    assert store.row["goal_exposed"] == conn.exposed and store.row["goal_depth"] == 0
    allowed = restriction == "rule"
    assert store.row["status"] == ("approved" if allowed else "pending")
    assert store.rule["use_count"] == int(allowed)
    assert {r["outcome"] for r in store.receipts.values()} == (
        {"parked", "pending", "approved"} if allowed else {"parked", "pending"}
    )


@pytest.mark.parametrize(
    "fault",
    [
        "disabled",
        "absent",
        "party",
        "unknown-party",
        "conditional-party",
        "owner-default",
        "class",
        "transact",
        "owner-domain",
        "resource",
        "selection",
        "catalog",
        "retired",
        "grant",
        "epoch",
        "revoked",
        "stopped",
        "budget",
    ],
)
async def test_registered_administer_refuses_missing_current_authority(monkeypatch, fault):
    from butlers.core.tool_catalog import ToolCatalog

    native = await registered_home_admin_broker(
        monkeypatch, approvals_enabled=None if fault == "absent" else fault != "disabled"
    )
    conn = native.conn
    descriptor = native.native.catalog["schedule_create"]
    changes = {
        "party": {"recipient_params": RecipientParameters(("prompt",))},
        "unknown-party": {"recipient_params": RecipientParameters(undeterminable=True)},
        "conditional-party": {
            "recipient_params": RecipientParameters(
                undeterminable_condition="inherited owner-looking text"
            )
        },
        "owner-default": {"recipient_params": RecipientParameters(default_owner_condition="owner")},
        "class": {"effect_class": "actuate"},
        "transact": {"effect_class": "transact"},
        "owner-domain": {"owner_domain": "finance"},
        "selection": {"resource_params": ("prompt",), "selection_bound": {"prompt": True}},
    }.get(fault)
    if changes:
        changed = ToolCatalog(
            tuple(
                replace(d, **changes) if d.canonical_name == "schedule_create" else d
                for d in native.native.catalog.descriptors
            ),
            True,
            native.native.catalog.generation_digest,
        )
        if fault == "owner-domain":
            with pytest.raises(BrokerRefused, match=Reason.CATALOG.value):
                broker_catalog.project_catalog(changed, domain="home")
            assert conn.used == 0 and native.domains.calls == []
            return
        # Publish through the actual repository; the goal pins that complete
        # current snapshot, so the following refusal is not a stale-digest proxy.
        from dataclasses import fields

        from butlers.core.catalog_publication import generation, plain
        from butlers.core.tool_catalog import ToolDescriptor

        changed = replace(
            changed,
            generation_digest=generation(
                [
                    {f.name: plain(getattr(d, f.name)) for f in fields(ToolDescriptor)}
                    for d in sorted(changed.descriptors, key=lambda d: d.canonical_name)
                ]
            ),
        )
        await native.publishers["home"].publish(changed)
        async with conn.transaction():
            conn.catalog_digest = (await PublishedCatalogProvider().snapshot(conn)).digest
    if fault == "resource":
        conn.grants = (Grant("home__schedule_create", resources=(("other", ("owner",)),)),)
    elif fault == "selection":
        conn.grants = (Grant("home__schedule_create", resources=(("prompt", ("owner",)),)),)
    elif fault == "catalog":
        conn.catalog_digest = "changed"
    elif fault == "retired":
        native.fleet.current["home"]["live"] = False
    elif fault == "grant":
        conn.grants = ()
    elif fault == "epoch":
        conn.epoch = 1
    elif fault == "revoked":
        conn.invocation_count = 0
    elif fault == "stopped":
        conn.status = "stopped"
    elif fault == "budget":
        conn.budget = 0
    if fault in {"disabled", "absent"}:
        assert not descriptor.approval_policy["enabled"]
    with pytest.raises(BrokerRefused):
        await native.broker.call(
            conn.goal,
            native.token,
            "home__schedule_create",
            {"name": "refused-admin", "cron": "0 8 * * *", "prompt": "owner"},
        )
    assert conn.used == 0 and conn.effect is None and native.domains.calls == []


@pytest.mark.parametrize("restriction", ["plain", "rule", "exposed-rule", "park-only-rule"])
async def test_registered_scene_commits_then_signed_per_event_park(monkeypatch, restriction):
    """Actual production policy/PARK/owning rules; SQL state here is software only."""
    import copy
    import time
    from types import SimpleNamespace

    from butlers.capability_broker.owning import PrivateApprovals
    from butlers.capability_broker.transport.client import PrivateClient
    from butlers.capability_broker.transport.config import PeerPolicy, TransportConfig
    from butlers.core.capability_json import arguments_digest
    from butlers.modules.approvals.goal_dispatch import GoalDispatch
    from tests.modules.approvals.test_goal_control import RuleStore

    native = await registered_home_admin_broker(monkeypatch)
    conn = native.conn
    conn.grants = (
        Grant("home__ha_activate_scene", resources=(("entity_id", ("scene.controlled",)),)),
    )
    conn, broker = native.conn, native.broker
    descriptor = native.native.catalog["ha_activate_scene"]
    assert descriptor.effect_class == "actuate"
    assert descriptor.recipient_params == RecipientParameters()
    assert {k: descriptor.approval_policy[k] for k in ("mode", "enabled", "configured_gate")} == {
        "mode": "default",
        "enabled": True,
        "configured_gate": False,
    }
    conn.exposed = restriction == "exposed-rule"
    if restriction == "park-only-rule":
        conn.grants = (
            Grant(
                "home__ha_activate_scene",
                resources=(("entity_id", ("scene.controlled",)),),
                park_only=True,
            ),
        )
    args = {"entity_id": "scene.controlled"}
    conn.events.clear()

    class Store(RuleStore):
        async def fetchrow(self, sql, *values):
            if "current_schema()" in sql and "current_user" in sql:
                return {
                    "schema": "home",
                    "role": "butler_home_rw",
                    "unsafe": False,
                    "schemas": ["pg_catalog", "home", "public"],
                }
            if "SET park_authorization=$2" in sql:
                self.gate.update(
                    park_authorization=copy.deepcopy(values[1]),
                    park_authorization_digest=values[2],
                    park_call_id=values[3],
                    state_version=self.gate["state_version"] + 1,
                )
                return copy.deepcopy(self.gate)
            return await super().fetchrow(sql, *values)

    store = Store()
    store.goal_id, store.arguments, store.digest = conn.goal, args, arguments_digest(args)
    store.rule.update(
        tool_name="ha_activate_scene",
        active=restriction != "plain",
        arg_constraints={k: {"type": "exact", "value": v} for k, v in args.items()},
    )
    now = int(time.time())
    broker.clock = lambda: now
    operation = GoalDispatch(
        store,
        "home",
        native.native.catalog,
        native.native.originals,
        {broker.signer.kid: broker.signer.private_key.public_key()},
        clock=lambda: now,
    )
    client = PrivateClient(
        TransportConfig(
            "https://localhost:4200/mcp",
            PeerPolicy("owning-service", "home", frozenset({"DNS:localhost"})),
        ),
        None,
    )
    observed = []

    async def call(self, value, *, assertion=None):
        assert not conn.in_transaction and conn.events[-1] == "commit"
        assert value["operation"] == "PARK" and assertion is None
        store.action_id = conn.effect
        result = await operation(value, SimpleNamespace(role="broker-control", domain="home"))
        observed.append(value)
        return result

    monkeypatch.setattr(PrivateClient, "call", call)
    handler = AsyncMock()
    monkeypatch.setattr("httpx.AsyncClient.post", handler)
    broker.approvals = PrivateApprovals({"home": client})
    try:
        result = await broker.call(conn.goal, native.token, "home__ha_activate_scene", args)
    finally:
        await client.aclose()
    assert result.status == "parked" and result.approval_ref == str(conn.effect)
    assert len(observed) == 1 and observed[0]["arguments"] == args
    assert conn.used == 1 and conn.events == ["effect", "commit", "parked", "commit"]
    assert native.domains.calls == [] and handler.await_count == 0
    assert store.row["goal_id"] == conn.goal and store.row["invocation_id"] == conn.invocation
    assert store.row["goal_exposed"] == conn.exposed and store.row["goal_depth"] == 0
    allowed = False
    assert store.row["status"] == ("approved" if allowed else "pending")
    assert store.rule["use_count"] == int(allowed)
    assert {r["outcome"] for r in store.receipts.values()} == (
        {"parked", "pending", "approved"} if allowed else {"parked", "pending"}
    )


async def test_native_scene_requires_exact_current_provenance_and_granted_entity(monkeypatch):
    from butlers.core.tool_catalog import ToolCatalog

    current = await registered_home_admin_broker(monkeypatch)
    conn, broker = current.conn, current.broker
    conn.grants = (
        Grant("home__ha_activate_scene", resources=(("entity_id", ("scene.controlled",)),)),
    )
    descriptor = current.native.catalog["ha_activate_scene"]
    args = {"entity_id": "scene.controlled"}
    async with conn.transaction():
        principal = await BrokerLedger(conn).authenticate(conn.goal, current.token)
        for changes in (
            {"module_name": "foreign"},
            {"namespace": "foreign.core"},
            {"group_name": "foreign"},
            {"owner_authority": True},
            {"recipient_params": RecipientParameters(("recipient",))},
            {"selection_bound": {"entity_id": True}},
            {"idempotency": "key_required"},
        ):
            foreign = replace(descriptor, **changes)
            try:
                cap = broker_catalog.project_catalog(
                    ToolCatalog((foreign,), True, current.native.catalog.generation_digest),
                    domain="home",
                ).capabilities[0]
            except BrokerRefused as exc:
                assert exc.reason is Reason.CATALOG
                continue
            assert cap.native_actuation == "none"
            with pytest.raises(BrokerRefused):
                await broker.authorities.policy(conn, principal, cap, args)
        generic = broker_catalog.project_catalog(current.native.catalog, domain="home")
        gateway = next(c for c in generic.capabilities if c.name == "ha_call_service")
        with pytest.raises(BrokerRefused):
            await broker.authorities.policy(
                conn, principal, gateway, {"domain": "scene", "service": "turn_on", "target": args}
            )
    for refused in (
        {"entity_id": "scene.outside"},
        {"entity_id": "light.controlled"},
        {"entity_id": "scene.controlled", "recipient": "owner"},
        {"entity_id": "scene.controlled", "target": {"entity_id": "scene.controlled"}},
        {"entity_id": "scene.controlled", "transition": True},
        {"entity_id": "scene.controlled", "transition": float("inf")},
        {"entity_id": "scene.controlled", "transition": "1"},
    ):
        with pytest.raises((BrokerRefused, ValueError)):
            await broker.call(conn.goal, current.token, "home__ha_activate_scene", refused)
        assert conn.used == 0 and conn.effect is None and current.domains.calls == []
    # The exact actual publication remains part of the locked goal identity.
    current.fleet.current["home"]["live"] = False
    with pytest.raises(BrokerRefused):
        await broker.call(conn.goal, current.token, "home__ha_activate_scene", args)
    assert conn.used == 0 and current.domains.calls == []
