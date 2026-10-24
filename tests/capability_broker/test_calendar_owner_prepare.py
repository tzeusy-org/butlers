"""Fixed owner preparation transport, never an owning authorization proof."""

from pathlib import Path
from uuid import uuid4

import pytest

from butlers.capability_broker.transport.protocol import ProtocolError
from tests.capability_broker.test_service_transport import material as material

pytestmark = pytest.mark.unit
_ROSTER = Path(__file__).resolve().parents[2] / "roster"


def test_calendar_prepare_wire_is_separate_and_closed():
    from butlers.capability_broker.transport.calendar_prepare import prepare_request

    value = {
        "protocol": "calendar-owner-prepare-v1",
        "domain": "relationship",
        "root_id": str(uuid4()),
        "parent_id": str(uuid4()),
        "parent_digest": "a" * 64,
        "expected_args_digest": "b" * 64,
        "edits": {"response_status": "declined"},
    }
    assert prepare_request(value, "relationship") == value
    for patch in (
        {"owner": "forged"},
        {"operation": "EXECUTE"},
        {"edits": {"_command_id": str(uuid4())}},
    ):
        with pytest.raises(ProtocolError):
            prepare_request({**value, **patch}, "relationship")
    with pytest.raises(ProtocolError):
        prepare_request(value, "messenger")


async def test_actual_preparer_app_is_candidate_only_over_mutual_tls(material):
    from unittest.mock import AsyncMock

    from butlers.capability_broker.transport.calendar_prepare import (
        CalendarPrepareApp,
        CalendarPrepareClient,
    )
    from butlers.capability_broker.transport.config import PeerPolicy, TransportConfig
    from butlers.capability_broker.transport.server import PrivateServer
    from butlers.modules.calendar import CalendarModule

    server_context, client_context = material(
        server_uri="urn:test:relationship", client_uri="urn:test:dashboard"
    )
    module = CalendarModule()
    coordinator = module._response_coordinator
    root, parent, candidate = (str(uuid4()) for _ in range(3))
    # The transport test controls the append-only storage collaborator; the
    # actual coordinator's provider/storage consumer is tested separately.
    coordinator.prepare_owner_candidate = AsyncMock(
        return_value={"root_id": root, "candidate_id": candidate, "candidate_digest": "c" * 64}
    )
    server = PrivateServer(
        CalendarPrepareApp("relationship", coordinator),
        server_context,
        PeerPolicy(
            "dashboard-owner",
            "relationship",
            frozenset({"DNS:localhost", "URI:urn:test:dashboard"}),
            True,
        ),
    )
    async with server.run():
        client = CalendarPrepareClient(
            TransportConfig(
                f"https://localhost:{server.port}/mcp",
                PeerPolicy(
                    "owning-service",
                    "relationship",
                    frozenset({"DNS:localhost", "URI:urn:test:relationship"}),
                ),
            ),
            client_context,
        )
        try:
            value = {
                "protocol": "calendar-owner-prepare-v1",
                "domain": "relationship",
                "root_id": root,
                "parent_id": parent,
                "parent_digest": "a" * 64,
                "expected_args_digest": "b" * 64,
                "edits": {"response_status": "declined"},
            }
            assert (await client.prepare(value))["candidate_id"] == candidate
            coordinator.prepare_owner_candidate.assert_awaited_once_with(value)
            with pytest.raises(ProtocolError):
                await client.prepare({**value, "operation": "EXECUTE"})
            assert coordinator.prepare_owner_candidate.await_count == 1
        finally:
            await client.aclose()
    assert not server.protocols and not server.handshakes and server.slot_count == 0
    assert {tool.name for tool in await server.app.mcp.list_tools()} == {"calendar_owner_prepare"}


async def test_actual_candidate_preserves_root_and_pending_call_until_independent_attach():
    import copy
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from butlers.core.capability_json import arguments_digest
    from butlers.modules.calendar import CalendarConfig, CalendarModule
    from tests.modules.test_calendar_response import _CommandPool, _invitation

    entry, source, event, root = (uuid4() for _ in range(4))
    pool = _CommandPool(
        dict(
            entry_id=entry,
            source_id=source,
            event_id=event,
            instance_status="confirmed",
            event_status="confirmed",
            provider="google",
            source_kind="provider_event",
            lane="user",
            butler_name=None,
            writable=True,
            source_key="google:owner",
            calendar_id="owner@example.test",
            provider_event_id="instance-one",
            source_metadata={"account_email": "owner@example.test"},
        )
    )
    module = CalendarModule()
    module._db = SimpleNamespace(pool=pool)
    module._butler_name = "relationship"
    module._config = CalendarConfig(provider="google", response_enabled=True)
    module._response_ready = True
    module._verified_response_account = AsyncMock(
        return_value={"entity_id": str(uuid4()), "email": "owner@example.test"}
    )
    provider = SimpleNamespace(
        get_response_calendar=AsyncMock(
            return_value={"id": "owner@example.test", "accessRole": "writer"}
        ),
        get_response_event=AsyncMock(return_value=_invitation()),
        respond_to_invitation=AsyncMock(),
    )
    module._provider = provider
    coordinator = module._response_coordinator
    public = {
        "entry_id": str(entry),
        "response_status": "accepted",
        "request_id": str(uuid4()),
        "send_updates": "none",
    }
    prepared = await coordinator.prepare_public(public, root)
    async with pool.acquire() as conn, conn.transaction():
        original = await coordinator.persist_prepared(conn, prepared)
    pool.pending[root] = {
        "tool_name": "calendar_respond",
        "tool_args": original,
        "status": "pending",
    }
    before = copy.deepcopy(pool.commands[root])
    request = {
        "root_id": str(root),
        "parent_id": str(root),
        "parent_digest": prepared["payload"]["digest"],
        "expected_args_digest": arguments_digest(original),
        "edits": {"response_status": "declined"},
    }
    candidate = await coordinator.prepare_owner_candidate(request)
    assert candidate["root_id"] == str(root) and candidate["candidate_id"] != str(root)
    assert pool.commands[root] == before
    assert pool.pending[root]["tool_args"] == original and pool.pending[root]["status"] == "pending"
    assert len(pool.commands) == 2
    stored = pool.commands[next(key for key in pool.commands if key != root)]
    assert stored["action_payload"]["command"]["arguments"]["request_id"] == public["request_id"]
    assert stored["action_payload"]["command"]["arguments"]["response_status"] == "declined"
    assert stored["action_payload"]["parent_digest"] == prepared["payload"]["digest"]
    assert await coordinator.prepare_owner_candidate(request) == candidate
    provider.respond_to_invitation.assert_not_awaited()
    pool.pending[root]["status"] = "approved"
    from butlers.modules.calendar_response import CalendarResponseError

    with pytest.raises(CalendarResponseError, match="no_longer_pending"):
        await coordinator.prepare_owner_candidate(request)
    provider.respond_to_invitation.assert_not_awaited()


async def test_owner_preparation_OFF_does_not_touch_material_or_construct_listener(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from butlers.capability_broker import production
    from butlers.config import load_config

    (tmp_path / "butler.toml").write_text(
        '[butler]\nname="relationship"\nport=9100\n[butler.calendar_owner_prepare]\nenabled=false\nendpoint="${CALENDAR_UNUSED_MATERIAL}"\n'
    )
    config = load_config(tmp_path)
    assert config.calendar_owner_prepare.enabled is False
    assert config.calendar_owner_prepare.endpoint == "${CALENDAR_UNUSED_MATERIAL}"
    material = Mock(side_effect=AssertionError("OFF must not read material"))
    monkeypatch.setattr(production, "material_context", material)
    daemon = SimpleNamespace(config=config)
    await production.start_private_transport(daemon)
    await production.close_private_transport(daemon)
    material.assert_not_called()
    assert not hasattr(daemon, "_calendar_prepare_owner")


@pytest.mark.parametrize("refuse_receipt", [False, True])
async def test_signed_canonical_calendar_park_owns_prepared_command_and_gate_same_transaction(
    monkeypatch, refuse_receipt, material, unused_tcp_port
):
    """Actual registration/signature/GoalDispatch/019 producer, SOFTWARE SQL only."""
    import copy
    import time
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from butlers.capability_broker.assertions import AssertionSigner
    from butlers.capability_broker.catalog import project_catalog
    from butlers.capability_broker.contracts import Decision, Disposition, Principal, Reservation
    from butlers.capability_broker.handoff import sign_handoff
    from butlers.core.capability_json import arguments_digest
    from butlers.core.goal_admission import Grant
    from butlers.modules.calendar import CalendarConfig, CalendarModule
    from tests.contracts.test_tool_authorization_catalog import _roster_catalog
    from tests.modules.approvals.test_goal_control import RuleStore
    from tests.modules.test_calendar_response import _CommandConnection, _CommandPool, _invitation

    monkeypatch.setattr("butlers.db.Database.connect", AsyncMock())
    catalog = await _roster_catalog("relationship")
    cap = next(
        item
        for item in project_catalog(catalog, domain="relationship").capabilities
        if item.name == "calendar_respond"
    )
    entry, source, event = (uuid4() for _ in range(3))
    command_store = _CommandPool(
        dict(
            entry_id=entry,
            source_id=source,
            event_id=event,
            instance_status="confirmed",
            event_status="confirmed",
            provider="google",
            source_kind="provider_event",
            lane="user",
            butler_name=None,
            writable=True,
            source_key="google:owner",
            calendar_id="owner@example.test",
            provider_event_id="instance-one",
            source_metadata={"account_email": "owner@example.test"},
        )
    )

    class Store(RuleStore):
        @asynccontextmanager
        async def transaction(self):
            before = copy.deepcopy(command_store.commands)
            try:
                async with super().transaction():
                    yield self
            except BaseException:
                command_store.commands = before
                raise

        async def fetchrow(self, sql, *args):
            if "calendar_" in sql:
                return await _CommandConnection(command_store).fetchrow(sql, *args)
            if "SET park_authorization=$2" in sql:
                self.gate.update(
                    park_authorization=copy.deepcopy(args[1]),
                    park_authorization_digest=args[2],
                    park_call_id=args[3],
                    state_version=self.gate["state_version"] + 1,
                )
                return copy.deepcopy(self.gate)
            if "current_schema()" in sql and "current_user" in sql:
                return {
                    "schema": "relationship",
                    "role": "butler_relationship_rw",
                    "unsafe": False,
                    "schemas": ["pg_catalog", "relationship", "public"],
                }
            return await super().fetchrow(sql, *args)

        async def execute(self, sql, *args):
            if "calendar_action_log" in sql:
                return await _CommandConnection(command_store).execute(sql, *args)
            return await super().execute(sql, *args)

    store = Store()
    store.arguments = {
        "entry_id": str(entry),
        "response_status": "accepted",
        "request_id": str(uuid4()),
    }
    store.digest = arguments_digest(store.arguments)
    store.refuse_receipt = refuse_receipt
    module = CalendarModule()
    module._db = SimpleNamespace(pool=store)
    module._butler_name = "relationship"
    module._config = CalendarConfig(provider="google", response_enabled=True)
    module._response_ready = True
    module._verified_response_account = AsyncMock(
        return_value={"entity_id": str(uuid4()), "email": "owner@example.test"}
    )
    module._provider = SimpleNamespace(
        get_response_calendar=AsyncMock(
            return_value={"id": "owner@example.test", "accessRole": "writer"}
        ),
        get_response_event=AsyncMock(return_value=_invitation()),
        respond_to_invitation=AsyncMock(),
    )
    grant = Grant(cap.identity, (("entry_id", (str(entry),)),), park_only=True)
    principal = Principal(
        store.goal_id, uuid4(), 0, "owner", False, "fixture-current-fleet", (grant,)
    )
    reservation = Reservation(
        principal, cap, Decision(Disposition.PARK), uuid4(), store.action_id, store.digest, grant
    )
    signer = AssertionSigner("calendar-test", Ed25519PrivateKey.generate())
    now = int(time.time())
    value = {
        "protocol": "broker-approvals-v2",
        "operation": "PARK",
        "request_id": str(uuid4()),
        "identity": {
            "goal_id": str(store.goal_id),
            "effect_id": str(store.action_id),
            "action_id": str(store.action_id),
            "owning_domain": "relationship",
            "capability": cap.identity,
            "args_digest": store.digest,
        },
        "arguments": store.arguments,
        "handoff": sign_handoff(signer, reservation, purpose="PARK", issued_at=now),
    }
    # The actual daemon factory must carry its real active-module property
    # into this signed PARK consumer, not an injected preparer mapping.
    import base64

    from butlers.capability_broker import production
    from butlers.config import CapabilityBrokerTransportConfig
    from butlers.daemon import ButlerDaemon
    from butlers.modules.approvals.module import ApprovalsModule

    daemon = ButlerDaemon(_ROSTER / "relationship")
    daemon.db = SimpleNamespace(pool=store)
    daemon._modules = [module, ApprovalsModule()]
    daemon._tool_catalog = catalog

    async def no_handler(**kwargs):
        raise AssertionError("PARK_must_not_forward")

    daemon._gated_tool_originals = {d.canonical_name: no_handler for d in catalog.descriptors}
    daemon.config = SimpleNamespace(
        name="relationship",
        capability_broker_transport=CapabilityBrokerTransportConfig.model_validate(
            {
                "enabled": True,
                "role": "owning-service",
                "domain": "relationship",
                "listen_host": "localhost",
                "listen_port": unused_tcp_port,
                "peer": {
                    "role": "broker-control",
                    "domain": "relationship",
                    "sans": ["URI:urn:test:control"],
                    "client_certificate": True,
                },
                "material": {
                    "ca_path": "fixture-ca",
                    "certificate_path": "fixture-cert",
                    "private_key_path": "fixture-key",
                },
                "verification_keys": {
                    signer.kid: base64.b64encode(
                        signer.private_key.public_key().public_bytes_raw()
                    ).decode()
                },
            }
        ),
    )
    server_context, _ = material(server_uri="urn:test:relationship", client_uri="urn:test:control")
    monkeypatch.setattr(production, "material_context", lambda *args, **kwargs: server_context)
    await production.start_private_transport(daemon)
    operation = daemon._capability_transport_owner.server.app.operation
    try:
        if refuse_receipt:
            with pytest.raises(ValueError, match="controlled_receipt_refusal"):
                await operation(
                    value, SimpleNamespace(role="broker-control", domain="relationship")
                )
            assert store.row is None and store.gate is None and command_store.commands == {}
        else:
            assert (
                await operation(
                    value, SimpleNamespace(role="broker-control", domain="relationship")
                )
            )["outcome"] == "parked"
            assert store.row["tool_args"]["_command_id"] == str(store.action_id)
            assert store.row["goal_args_digest"] == store.digest
            assert store.row["tool_args"]["send_updates"] == "none"
            assert command_store.commands[store.action_id]["action_payload"]["root_id"] == str(
                store.action_id
            )
            assert store.row["status"] == "pending" and store.gate["phase"] == "pending"
            assert store.gate["park_authorization"]["grant"]["park_only"] is True
        module._provider.respond_to_invitation.assert_not_awaited()
    finally:
        await production.close_private_transport(daemon)


async def test_actual_calendar_factory_owns_separate_listener_and_attempts_all_cleanup(
    material, unused_tcp_port, unused_tcp_port_factory, monkeypatch
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from butlers.api.deps import CalendarPreparationClients
    from butlers.capability_broker import production
    from butlers.config import CalendarOwnerPrepareConfig
    from butlers.modules.calendar import CalendarConfig, CalendarModule

    server_context, _ = material(
        server_uri="urn:test:relationship", client_uri="urn:test:dashboard"
    )
    monkeypatch.setattr(production, "material_context", lambda *args, **kwargs: server_context)
    settings = CalendarOwnerPrepareConfig(
        enabled=True,
        domain="relationship",
        listen_host="localhost",
        listen_port=unused_tcp_port,
        endpoint=f"https://localhost:{unused_tcp_port}/mcp",
        dashboard_peer={
            "role": "dashboard-owner",
            "domain": "relationship",
            "sans": ["URI:urn:test:dashboard"],
            "client_certificate": True,
        },
        service_peer={
            "role": "owning-service",
            "domain": "relationship",
            "sans": ["URI:urn:test:relationship"],
            "client_certificate": False,
        },
        service_material={
            "ca_path": "fixture-ca",
            "certificate_path": "fixture-server",
            "private_key_path": "fixture-server-key",
        },
        dashboard_material={
            "ca_path": "fixture-ca",
            "certificate_path": "fixture-client",
            "private_key_path": "fixture-client-key",
        },
    )
    module = CalendarModule()
    module._config = CalendarConfig(provider="google", response_enabled=True)
    module._butler_name = "relationship"
    module._response_ready = True
    from butlers.daemon import ButlerDaemon
    from butlers.modules.approvals.module import ApprovalsModule

    daemon = ButlerDaemon(_ROSTER / "relationship")
    daemon.config = SimpleNamespace(name="relationship", calendar_owner_prepare=settings)
    daemon._modules = [module, ApprovalsModule()]
    # The real daemon owns .db, never the dict-only fixture's ._db. SQL
    # readiness is a protocol double here; actual migrated controls are separate.
    import base64

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from butlers.config import CapabilityBrokerTransportConfig
    from tests.contracts.test_tool_authorization_catalog import _roster_catalog
    from tests.modules.approvals.test_goal_control import FencedStore

    class ReadyStore(FencedStore):
        async def fetchrow(self, sql, *args):
            if "current_schema()" in sql and "current_user" in sql:
                return {
                    "schema": "relationship",
                    "role": "butler_relationship_rw",
                    "unsafe": False,
                    "schemas": ["pg_catalog", "relationship", "public"],
                }
            return await super().fetchrow(sql, *args)

    daemon.db = SimpleNamespace(pool=ReadyStore())
    module._db = daemon.db
    monkeypatch.setattr("butlers.db.Database.connect", AsyncMock())
    daemon._tool_catalog = await _roster_catalog("relationship")

    async def controlled_original(**kwargs):
        raise AssertionError("factory_does_not_forward")

    daemon._gated_tool_originals = {
        d.canonical_name: controlled_original for d in daemon.tool_catalog.descriptors
    }
    key = Ed25519PrivateKey.generate()
    daemon.config.capability_broker_transport = CapabilityBrokerTransportConfig.model_validate(
        {
            "enabled": True,
            "role": "owning-service",
            "domain": "relationship",
            "listen_host": "localhost",
            "listen_port": unused_tcp_port_factory(),
            "peer": {
                "role": "broker-control",
                "domain": "relationship",
                "sans": ["URI:urn:test:dashboard"],
                "client_certificate": True,
            },
            "material": {
                "ca_path": "fixture-ca",
                "certificate_path": "fixture-server",
                "private_key_path": "fixture-server-key",
            },
            "verification_keys": {
                "fixture": base64.b64encode(key.public_key().public_bytes_raw()).decode()
            },
        }
    )
    assert not hasattr(daemon, "_db")
    try:
        await production.start_private_transport(daemon)
    except BaseException:
        await production.close_private_transport(daemon)
        raise
    assert daemon._capability_transport_owner.server.app.operation.owner.pool is daemon.db
    broker_owner = daemon._capability_transport_owner
    owner = daemon._calendar_prepare_owner
    assert owner.server.app.coordinator is module._response_coordinator
    try:
        assert owner.server.port == unused_tcp_port and not owner._task.done()
    finally:
        await production.close_private_transport(daemon)
    assert owner.closed and owner._task.done() and broker_owner.closed and broker_owner._task.done()
    assert not owner.server.protocols and not owner.server.handshakes
    assert daemon._calendar_prepare_owner is None
    # Failure of one dedicated client must not skip the other actual close
    # owner. Repeated close observes the retained failed result, not success.
    clients = CalendarPreparationClients()
    first = AsyncMock(side_effect=RuntimeError("controlled_cleanup_refusal"))
    second = AsyncMock()
    clients.clients = {
        "relationship": SimpleNamespace(aclose=first),
        "messenger": SimpleNamespace(aclose=second),
    }
    for _ in range(2):
        with pytest.raises(RuntimeError, match="controlled_cleanup_refusal"):
            await clients.aclose()
    assert first.await_count == second.await_count == 1
    assert clients._close_task.done()


@pytest.mark.parametrize("failure", ["startup", "cancel"])
async def test_actual_API_lifespan_retains_calendar_clients_on_early_failure(monkeypatch, failure):
    import asyncio
    import importlib
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    app_module = importlib.import_module("butlers.api.app")
    from butlers.api.deps import CalendarPreparationClients

    closed, entered = [], asyncio.Event()

    class Client:
        async def aclose(self):
            closed.append("calendar")

    async def initialize(self, roster_dir=None):
        self.clients["relationship"] = Client()

    def dependencies():
        if failure == "startup":
            raise RuntimeError("controlled_later_startup_failure")

    async def database(configs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(CalendarPreparationClients, "initialize", initialize)
    monkeypatch.setattr(app_module, "create_owner_auth_service", AsyncMock(return_value=None))
    monkeypatch.setattr(app_module, "init_dependencies", dependencies)
    monkeypatch.setattr(app_module, "check_infra_default_creds", lambda: None)
    monkeypatch.setattr(app_module, "init_pricing", lambda: None)
    monkeypatch.setattr(app_module, "get_butler_configs", lambda: {})
    monkeypatch.setattr(app_module, "init_db_manager", database)
    app = SimpleNamespace(state=SimpleNamespace(owner_auth_config=None))

    async def consume():
        async with app_module.lifespan(app):
            raise AssertionError("startup_must_not_complete")

    if failure == "startup":
        with pytest.raises(RuntimeError, match="controlled_later_startup_failure"):
            await consume()
    else:
        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=5)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert closed == ["calendar"]
    assert app.state.calendar_preparation_clients.clients == {}
