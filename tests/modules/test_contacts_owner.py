"""Registered Contacts ownership, complete reads and bounded failure diagnostics.

REQ-module-contacts-001; REQ-module-contacts-002; REQ-relationship-facts-002.
These modeled connections prove software/wiring only, never PostgreSQL roles.
"""

from __future__ import annotations

import copy
import importlib
import logging
import tomllib
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import structlog
from fastmcp import Client, FastMCP

from butlers.core.fact_authority import FactWriteContext, _current_report
from butlers.core.logging import _build_processors
from butlers.modules._roster_relationship import RelationshipModule, RelationshipModuleConfig
from butlers.modules.contacts import ContactsModule
from butlers.modules.contacts.backfill import (
    ContactBackfillEngine,
    ContactBackfillResolver,
    ContactBackfillWriter,
)
from butlers.modules.contacts.owner import (
    ContactsLookupAmbiguous,
    ContactsOwner,
    ContactsOwnerUnavailable,
)
from butlers.modules.contacts.sync import (
    CanonicalContact,
    ContactEmail,
    ContactPhone,
    ContactUrl,
    ContactUsername,
)

_THIS_FILE = Path(__file__).resolve()

pytestmark = pytest.mark.unit


class _Connection:
    def __init__(self):
        self.rows = []
        self.registered = True
        self.failure = False
        self.calls = []
        self.live = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def acquire(self):
        return self

    def transaction(self):
        return self

    async def fetchval(self, query, *args):
        self.calls.append((query, args))
        if self.failure:
            raise PermissionError("synthetic-private-operands")
        if "entity_predicate_registry" in query:
            return args[0] if self.registered else None
        return args[0] if self.live else None

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        if self.failure:
            raise PermissionError("synthetic-private-operands")
        return self.rows


async def _registered(pool, groups=None):
    module = RelationshipModule()
    db = SimpleNamespace(pool=pool)
    mcp = FastMCP("Contacts-owning-test")
    if groups is None:
        configuration = tomllib.loads(
            (_THIS_FILE.parents[2] / "roster/relationship/butler.toml").read_text()
        )
        groups = configuration["modules"]["relationship"]["groups"]
    await module.register_tools(mcp, RelationshipModuleConfig(groups=groups), db, "relationship")
    return mcp


class _Switchboard:
    def __init__(self, mcp):
        self.mcp, self.calls = mcp, []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        assert name == "route"
        assert arguments["target_butler"] == "relationship"
        tool = await self.mcp.get_tool(arguments["tool_name"])
        result = await tool.fn(**arguments["args"])
        return SimpleNamespace(is_error=False, data={"result": result})


async def test_registered_report_fixes_authority_and_preserves_outcomes(monkeypatch):
    # REQ-butler-relationship-002: preserve each approved registered inventory name.
    pool = _Connection()
    mcp = await _registered(pool)
    legacy_groups = [
        "contacts",
        "contacts_extended",
        "interactions",
        "relationships",
        "social",
        "notes",
        "tracking",
        "management",
    ]
    legacy_mcp = await _registered(pool, legacy_groups)
    legacy_names = {t.name for t in await legacy_mcp.list_tools()}
    production_names = {t.name for t in await mcp.list_tools()}
    assert legacy_names == {
        "address_add",
        "address_list",
        "address_remove",
        "address_update",
        "channel_add",
        "channel_list",
        "channel_search",
        "commitment_capture",
        "commitment_resolve_from_utterance",
        "contact_archive",
        "contact_create",
        "contact_export_vcard",
        "contact_get",
        "contact_import_vcard",
        "contact_merge",
        "contact_resolve",
        "contact_search",
        "contact_search_by_label",
        "contact_update",
        "contacts_overdue",
        "date_add",
        "date_list",
        "dunbar_tier_set",
        "fact_list",
        "fact_set",
        "feed_get",
        "gift_add",
        "gift_list",
        "gift_update_status",
        "group_add_member",
        "group_create",
        "group_list",
        "group_members",
        "identity_assert_sender_channel",
        "identity_resolve_channels",
        "interaction_list",
        "interaction_log",
        "interaction_log_group",
        "label_assign",
        "label_create",
        "life_event_list",
        "life_event_log",
        "life_event_types_list",
        "loan_create",
        "loan_list",
        "loan_settle",
        "meeting_debrief_answer",
        "meeting_debrief_pending",
        "note_create",
        "note_list",
        "note_search",
        "relationship_add",
        "relationship_assert_fact",
        "relationship_list",
        "relationship_remove",
        "relationship_type_get",
        "relationship_types_list",
        "stay_in_touch_set",
        "task_complete",
        "task_create",
        "task_delete",
        "task_list",
        "upcoming_dates",
    }
    assert production_names == legacy_names | {
        "contacts_report_channel",
        "contacts_resolve_channel",
        "entity_resolve",
        "entity_get",
        "entity_neighbors",
        "relationship_fact_evidence",
        "relationship_predicate_coverage",
        "relationship_lookup",
    }
    assert len(legacy_names - {"identity_resolve_channels", "identity_assert_sender_channel"}) == 61
    assert not {"contacts_report_channel", "contacts_resolve_channel"} & legacy_names
    assert "relationship_assert_fact" in legacy_names
    assert "entity_update" not in production_names
    assert "relationship_record_coverage" not in production_names
    assert "entity_set_posture" not in production_names
    client = _Switchboard(mcp)
    owner = ContactsOwner(lambda: client, "general")
    ingress = importlib.import_module("butlers.tools.relationship.contacts_ingress")
    subject, fact, action = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    ambient = FactWriteContext("owner", original_entity_id=uuid.uuid4())
    seen = []

    async def writer(*args, **kwargs):
        seen.append((args, kwargs, _current_report.get()))
        payload = {"outcome": outcome, "fact_id": str(fact), "action_id": None}
        if outcome == "pending_approval":
            payload.update(fact_id=None, action_id=str(action))
        return SimpleNamespace(as_dict=lambda: payload)

    monkeypatch.setattr(ingress, "relationship_assert_fact", writer)
    token = _current_report.set(ambient)
    try:
        for outcome in ["inserted", "unchanged", "superseded", "candidate", "pending_approval"]:
            result = await owner.report(subject, "email", "synthetic@example.test", True)
            assert result["outcome"] == outcome
            args, kwargs, context = seen[-1]
            assert args[:4] == (pool, subject, "has-email", "synthetic@example.test")
            assert kwargs == {
                "src": "contacts-backfill",
                "object_kind": "literal",
                "primary": True,
                "conn": pool,
            }
            assert context == FactWriteContext("third_party")
            assert _current_report.get() is ambient
        report = await mcp.get_tool("contacts_report_channel")
        for forbidden in ["src", "verified", "reporter", "authority", "approval_action_id"]:
            with pytest.raises(TypeError):
                await report.fn(
                    subject=subject, channel_type="email", channel_value="a", **{forbidden: "owner"}
                )
        before = len(seen)
        for kind, value in [("telegram_chat_id", "123"), ("email", ""), ("email", "x" * 1025)]:
            with pytest.raises(ValueError):
                await report.fn(subject=subject, channel_type=kind, channel_value=value)
        pool.live = False
        with pytest.raises(ValueError, match="subject unavailable"):
            await report.fn(subject=subject, channel_type="email", channel_value="a")
        assert len(seen) == before
        pool.live = True
        monkeypatch.setattr(
            ingress, "relationship_assert_fact", AsyncMock(side_effect=RuntimeError("private"))
        )
        with pytest.raises(ContactsOwnerUnavailable):
            await owner.report(subject, "email", "a", False)
        assert _current_report.get() is ambient
    finally:
        _current_report.reset(token)


async def test_registered_lookup_complete_normalization_and_unavailability():
    pool = _Connection()
    client = _Switchboard(await _registered(pool))
    owner = ContactsOwner(lambda: client, "home")
    person = uuid.uuid4()
    pool.rows = [{"entity_id": person}, {"entity_id": person}]
    assert await owner.lookup("email", " Mixed@Example.TEST ") == person
    query, args = pool.calls[-1]
    assert args[:2] == ("has-email", "mixed@example.test")
    assert "DISTINCT" in query and "LIMIT" not in query
    assert "ef.validity='active'" in query and "entity_type='person'" in query
    assert "merged_into" in query and "deleted_at" in query
    assert await owner.lookup("phone", " +١٢-34 ") == person
    assert pool.calls[-1][1][2] == ["+١٢-34", "+١٢34"]
    pool.rows += [{"entity_id": uuid.uuid4()}]
    with pytest.raises(ContactsLookupAmbiguous):
        await owner.lookup("phone", "123")
    pool.rows = []
    assert await owner.lookup("email", "empty@example.test") is None
    pool.registered = False
    with pytest.raises(ContactsOwnerUnavailable):
        await owner.lookup("email", "empty@example.test")
    pool.registered = True
    pool.failure = True
    with pytest.raises(ContactsOwnerUnavailable):
        await owner.lookup("email", "empty@example.test")


async def test_resolver_priority_ambiguity_and_unavailable_never_create():
    local, email_id, phone_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    pool = SimpleNamespace(
        fetchrow=AsyncMock(return_value={"local_entity_id": local}),
        fetch=AsyncMock(return_value=[]),
    )
    owner = SimpleNamespace(lookup=AsyncMock(return_value=email_id))
    resolver = ContactBackfillResolver(pool, provider="google", account_id="work", owner=owner)
    contact = CanonicalContact(
        external_id="synthetic",
        display_name="Synthetic",
        emails=[ContactEmail(value="first@test"), ContactEmail(value="primary@test", primary=True)],
        phones=[ContactPhone(value="first-phone"), ContactPhone(value="second-phone")],
    )
    assert await resolver.resolve(contact) == (local, "source_link")
    owner.lookup.assert_not_awaited()
    pool.fetchrow.return_value = None
    assert await resolver.resolve(contact) == (email_id, "email")
    owner.lookup.assert_awaited_once_with("email", "primary@test")
    owner.lookup.reset_mock()
    owner.lookup.side_effect = [None, None, phone_id]
    assert await resolver.resolve(contact) == (phone_id, "phone")
    assert [c.args for c in owner.lookup.await_args_list] == [
        ("email", "primary@test"),
        ("phone", "first-phone"),
        ("phone", "second-phone"),
    ]
    owner.lookup.side_effect = ContactsLookupAmbiguous()
    assert await resolver.resolve(contact) == (None, "ambiguous_channel")
    pool.fetch.assert_not_awaited()
    owner.lookup.side_effect = ContactsOwnerUnavailable("contacts owner unavailable")
    engine = ContactBackfillEngine(pool, provider="google", account_id="work", owner=owner)
    engine._writer.create_contact = AsyncMock()
    with pytest.raises(ContactsOwnerUnavailable):
        await engine(contact)
    engine._writer.create_contact.assert_not_awaited()
    pool.fetch.assert_not_awaited()
    owner.lookup.side_effect = None
    owner.lookup.return_value = None
    assert await resolver.resolve(contact) == (None, "new")
    pool.fetch.assert_awaited_once()


async def test_transport_refusal_correlations_and_late_wiring():
    module = ContactsModule()
    owner = ContactsOwner(lambda: module._switchboard_client, "health")
    with pytest.raises(ContactsOwnerUnavailable):
        await owner.lookup("email", "a")
    client = SimpleNamespace(call_tool=AsyncMock())
    module.wire_runtime(None, None, switchboard_client=client)
    for raw in [
        None,
        {"error": "private"},
        {"result": None},
        {"result": {"outcome": "empty", "request_id": "stale", "entity_id": None}},
    ]:
        client.call_tool.return_value = SimpleNamespace(is_error=False, data=raw)
        with pytest.raises(ContactsOwnerUnavailable):
            await owner.lookup("email", "a")
    for failure in [TimeoutError("possible committed private"), PermissionError("private")]:
        client.call_tool.reset_mock()
        client.call_tool.side_effect = failure
        with pytest.raises(ContactsOwnerUnavailable, match="^contacts owner unavailable$"):
            await owner.report(uuid.uuid4(), "email", "a", False)
        assert client.call_tool.await_count == 1

    # A complete typed empty result alone may reach name/new fallback. Omitting
    # entity_id is transport uncertainty, even with the current request id.
    contact = CanonicalContact(
        external_id="transport-synthetic",
        display_name="Transport Synthetic",
        emails=[ContactEmail(value="empty@test")],
    )
    local_pool = SimpleNamespace(
        fetchrow=AsyncMock(return_value=None), fetch=AsyncMock(return_value=[])
    )
    resolver = ContactBackfillResolver(
        local_pool, provider="google", account_id="synthetic", owner=owner
    )
    for include_entity in [False, True, False, True]:

        async def empty_response(name, request, *, include_entity=include_entity):
            result = {"outcome": "empty", "request_id": request["args"]["request_id"]}
            if include_entity:
                result["entity_id"] = None
            return SimpleNamespace(is_error=False, data={"result": result})

        client.call_tool.side_effect = empty_response
        local_pool.fetch.reset_mock()
        if include_entity:
            assert await resolver.resolve(contact) == (None, "new")
            local_pool.fetch.assert_awaited_once()
        else:
            with pytest.raises(ContactsOwnerUnavailable):
                await resolver.resolve(contact)
            local_pool.fetch.assert_not_awaited()

    # Actual daemon ordering: startup builds the runtime before registration
    # binds its hosting name, and wire_runtime supplies its transport afterward.
    contacts_package = importlib.import_module("butlers.modules.contacts")
    for hosting_name in ["general", "health"]:
        started = ContactsModule()
        config = contacts_package.ContactsConfig(provider="google", sync={"enabled": True})
        database = SimpleNamespace(pool=SimpleNamespace())
        started._create_provider = AsyncMock(return_value=SimpleNamespace(name="google"))
        with patch.object(contacts_package.ContactsSyncRuntime, "start", AsyncMock()):
            await started.on_startup(config, database)
        runtime_owner = started._runtimes["google"]._sync_engine._apply_contact._resolver._owner
        with pytest.raises(ContactsOwnerUnavailable):
            await runtime_owner.lookup("email", "empty@test")

        requests = []

        async def complete_empty(name, request, *, requests=requests):
            requests.append((name, request))
            return SimpleNamespace(
                is_error=False,
                data={
                    "result": {
                        "outcome": "empty",
                        "entity_id": None,
                        "request_id": request["args"]["request_id"],
                    }
                },
            )

        runtime_client = SimpleNamespace(call_tool=complete_empty)
        started.wire_runtime(None, None, switchboard_client=runtime_client)
        with pytest.raises(ContactsOwnerUnavailable):
            await runtime_owner.lookup("email", "empty@test")
        assert not requests
        await started.register_tools(FastMCP("startup-source"), config, database, hosting_name)
        assert await runtime_owner.lookup("email", "empty@test") is None
        assert requests[0][0] == "route"
        assert requests[0][1]["source_butler"] == hosting_name
        assert requests[0][1]["target_butler"] == "relationship"
        assert set(requests[0][1]) == {"source_butler", "target_butler", "tool_name", "args"}
        assert set(requests[0][1]["args"]) == {"channel_type", "channel_value", "request_id"}
        started.wire_runtime(None, None, switchboard_client=None)
        with pytest.raises(ContactsOwnerUnavailable):
            await runtime_owner.lookup("email", "empty@test")
        assert len(requests) == 1


async def test_actual_switchboard_route_and_mcp_argument_admission(monkeypatch):
    """Actual route + MCP consumer; registry/connection are bounded stand-ins."""
    routing = importlib.import_module("butlers.tools.switchboard.routing.route")
    mcp = await _registered(_Connection())
    ingress = importlib.import_module("butlers.tools.relationship.contacts_ingress")
    writer = AsyncMock(
        return_value=SimpleNamespace(
            as_dict=lambda: {
                "outcome": "candidate",
                "fact_id": str(uuid.uuid4()),
                "action_id": None,
            }
        )
    )
    monkeypatch.setattr(ingress, "relationship_assert_fact", writer)
    monkeypatch.setattr(routing, "receiver_route_cutover_enabled", lambda: False)
    monkeypatch.setattr(
        routing,
        "resolve_routing_target",
        AsyncMock(
            return_value=(
                {"endpoint_url": "http://relationship:41104/mcp"},
                None,
            )
        ),
    )
    monkeypatch.setattr(routing, "_record_confirmed_route", AsyncMock())
    monkeypatch.setattr(routing, "_log_routing", AsyncMock())
    monkeypatch.setattr(routing, "inject_trace_context", lambda: {"traceparent": "synthetic"})
    async with Client(mcp) as target:

        async def call(endpoint, name, args):
            assert endpoint == "http://relationship:41104/mcp"
            result = await target.call_tool(name, args, raise_on_error=False)
            if result.is_error:
                raise RuntimeError("fixed test rejection")
            return result.data

        async def route(name, args):
            assert name == "route"
            return SimpleNamespace(
                is_error=False,
                data=await routing.route(
                    SimpleNamespace(),
                    **args,
                    call_fn=call,
                ),
            )

        owner = ContactsOwner(lambda: SimpleNamespace(call_tool=route), "education")
        assert (await owner.report(uuid.uuid4(), "email", "a@test", False))[
            "outcome"
        ] == "candidate"
        for forbidden in ["src", "verified", "authority", "reporter", "approval_action_id"]:
            before = writer.await_count
            result = await target.call_tool(
                "contacts_report_channel",
                {
                    "subject": str(uuid.uuid4()),
                    "channel_type": "email",
                    "channel_value": "a@test",
                    forbidden: "forged",
                },
                raise_on_error=False,
            )
            assert result.is_error and writer.await_count == before


async def test_warning_all_channels_omits_operands_and_keeps_progress(caplog):
    """bu-y81k45 actual warning species, including exception-only operand path."""
    operands = [f"private-species-{i}" for i in range(8)]
    contact = CanonicalContact(
        external_id=operands[6],
        emails=[ContactEmail(value=operands[0], primary=True)],
        phones=[ContactPhone(value=operands[1], primary=True)],
        urls=[ContactUrl(value=operands[2])],
        usernames=[
            ContactUsername(value="@" + operands[3], service="telegram"),
            ContactUsername(value=operands[4], service="other"),
        ],
    )
    calls, contexts = [], []

    async def report(*args):
        calls.append(args)
        contexts.append(_current_report.get())
        try:
            raise ValueError(operands[7])
        except ValueError as cause:
            raise RuntimeError(operands[5]) from cause

    writer = ContactBackfillWriter(
        SimpleNamespace(),
        provider="telegram",
        account_id="default",
        owner=SimpleNamespace(report=report),
    )
    subject = uuid.uuid4()
    ambient = FactWriteContext("owner")
    token = _current_report.set(ambient)
    try:
        with caplog.at_level(logging.WARNING):
            await writer.upsert_contact_info(subject, contact)
        assert len(calls) == 6
        assert [c[1:] for c in calls] == [
            ("email", operands[0], True),
            ("phone", operands[1], True),
            ("website", operands[2], False),
            ("telegram_username", operands[3], False),
            ("other", operands[4], False),
            ("telegram_user_id", operands[6], False),
        ]
        assert contexts == [FactWriteContext("third_party")] * 6
        assert _current_report.get() is ambient
        records = [r for r in caplog.records if r.name.endswith("contacts.backfill")]
        assert len(records) == 6
        for record in records:
            rendered = logging.Formatter("%(levelname)s %(message)s").format(record)
            assert all(
                value not in rendered and value not in repr(record.__dict__) for value in operands
            )
            assert record.exc_info is None and record.stack_info is None
            assert record.levelno == logging.WARNING
            assert "contacts_channel_report_failed" in rendered and str(subject) in rendered
            assert "ci_type=" in rendered and "predicate=" in rendered
            for renderer in [structlog.processors.JSONRenderer(), structlog.dev.ConsoleRenderer()]:
                formatter = structlog.stdlib.ProcessorFormatter(
                    processors=[
                        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                        renderer,
                    ],
                    foreign_pre_chain=_build_processors(time_fmt="iso"),
                )
                emitted = formatter.format(copy.copy(record))
                assert all(value not in emitted for value in operands)
                assert "contacts_channel_report_failed" in emitted and str(subject) in emitted
        caplog.clear()
        writer._owner = SimpleNamespace(report=AsyncMock(return_value={"outcome": "inserted"}))
        await writer.upsert_contact_info(subject, contact)
        assert not caplog.records
        assert _current_report.get() is ambient
    finally:
        _current_report.reset(token)
