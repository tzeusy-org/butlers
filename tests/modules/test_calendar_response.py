"""Calendar participant-response admission and one-attempt HTTP contract."""

from __future__ import annotations

import copy
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from butlers.modules.calendar import (
    CalendarConfig,
    CalendarRequestError,
    _GoogleOAuthCredentials,
    _GoogleProvider,
)
from butlers.modules.calendar_response import (
    CalendarResponseError,
    CalendarResponseUncertainError,
    invitation_snapshot,
)

pytestmark = pytest.mark.unit


def _invitation() -> dict:
    return {
        "id": "instance-one",
        "status": "confirmed",
        "etag": '"version-one"',
        "summary": "Same event",
        "start": {"dateTime": "2026-10-11T10:00:00Z"},
        "end": {"dateTime": "2026-10-11T11:00:00Z"},
        "organizer": {"email": "organizer@example.test"},
        "recurringEventId": "series-one",
        "originalStartTime": {"dateTime": "2026-10-11T10:00:00Z"},
        "attendees": [
            {"email": "owner@example.test", "self": True, "responseStatus": "needsAction"},
            {"email": "other@example.test", "responseStatus": "accepted"},
        ],
    }


def test_response_admission_uses_explicit_account_self_and_occurrence():
    # REQ-module-calendar-029
    resource = _invitation()
    snapshot = invitation_snapshot(
        resource, event_id="instance-one", account_email="owner@example.test"
    )
    assert snapshot.self_email == "owner@example.test"
    assert snapshot.response_status == "needsAction"
    assert snapshot.recurring_event_id == "series-one"
    assert snapshot.original_start == resource["originalStartTime"]
    for original in (
        {"date": "2026-10-11"},
        {"dateTime": "2026-10-11T10:00:00", "timeZone": "UTC"},
    ):
        valid = copy.deepcopy(resource)
        valid["originalStartTime"] = original
        assert (
            invitation_snapshot(
                valid, event_id="instance-one", account_email="owner@example.test"
            ).original_start
            == original
        )
    variants = []
    for status in (None, "invalid", [], False):
        candidate = copy.deepcopy(resource)
        candidate["attendees"][0]["responseStatus"] = status
        variants.append(candidate)
    for field, value in (
        ("attendees", []),
        ("attendeesOmitted", True),
        ("organizer", {"self": True, "email": "organizer@example.test"}),
        ("organizer", {"email": "OWNER@example.test"}),
        ("status", "cancelled"),
        ("status", []),
        ("etag", None),
        ("recurrence", ["RRULE:FREQ=DAILY"]),
        ("originalStartTime", {}),
        ("originalStartTime", {"dateTime": "2026-10-11T10:00:00"}),
        ("originalStartTime", {"dateTime": "2026-10-11", "timeZone": "UTC"}),
        ("originalStartTime", {"dateTime": "2026-10-11T10:00:00", "timeZone": "unrecognized/zone"}),
        ("originalStartTime", {"date": "20261011"}),
    ):
        candidate = copy.deepcopy(resource)
        candidate[field] = value
        variants.append(candidate)
    for field, value in (("self", False), ("organizer", True), ("email", "foreign@example.test")):
        candidate = copy.deepcopy(resource)
        candidate["attendees"][0][field] = value
        variants.append(candidate)
    duplicate = copy.deepcopy(resource)
    duplicate["attendees"].append(copy.deepcopy(duplicate["attendees"][0]))
    variants.append(duplicate)
    duplicate_email = copy.deepcopy(resource)
    duplicate_email["attendees"].append({"email": "OWNER@example.test"})
    variants.append(duplicate_email)
    for candidate in variants:
        with pytest.raises(CalendarResponseError):
            invitation_snapshot(
                candidate, event_id="instance-one", account_email="owner@example.test"
            )
    with pytest.raises(CalendarResponseError, match="response_target_mismatch"):
        invitation_snapshot(resource, event_id="other-instance", account_email="owner@example.test")
    # Restoring the exact real provider shape restores admission, without
    # changing the pre-existing defaulted AttendeeInfo/tool payload contract.
    assert (
        invitation_snapshot(resource, event_id="instance-one", account_email="owner@example.test")
        == snapshot
    )


async def test_response_http_is_conditional_single_attempt_and_verifies_own_result():
    # REQ-module-calendar-029
    before_resource = _invitation()
    before = invitation_snapshot(
        before_resource, event_id="instance-one", account_email="owner@example.test"
    )
    after = copy.deepcopy(before_resource)
    after["etag"] = '"version-two"'
    after["attendees"][0]["responseStatus"] = "accepted"
    requests = []
    mode: object = after

    async def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if isinstance(mode, Exception):
            raise mode
        if isinstance(mode, int):
            return httpx.Response(mode, json={"error": {"message": "not public"}})
        return httpx.Response(200, json=mode)

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = _GoogleProvider(
            CalendarConfig(provider="google", account="owner@example.test"),
            _GoogleOAuthCredentials(
                client_id="test-client", client_secret="test-secret", refresh_token="test-refresh"
            ),
            http_client=client,
        )
        provider._oauth.get_access_token = AsyncMock(return_value="test-access")
        token = await provider.prepare_response_write()
        result = await provider.respond_to_invitation(
            calendar_id="owner@example.test",
            before=before,
            response_status="accepted",
            send_updates="none",
            access_token=token,
        )
        assert result == after
        assert len(requests) == 1
        assert requests[0].headers["If-Match"] == '"version-one"'
        assert requests[0].url.params["sendUpdates"] == "none"
        assert json.loads(requests[0].content) == {
            "attendeesOmitted": True,
            "attendees": [{"email": "owner@example.test", "responseStatus": "accepted"}],
        }
        assert result["attendees"][1] == before_resource["attendees"][1]
        for key in (
            "organizer",
            "summary",
            "start",
            "end",
            "recurringEventId",
            "originalStartTime",
        ):
            assert result[key] == before_resource[key]
        for status in (401, 412, 429, 503, 408):
            requests.clear()
            mode = status
            with pytest.raises((CalendarRequestError, CalendarResponseUncertainError)):
                await provider.respond_to_invitation(
                    calendar_id="owner@example.test",
                    before=before,
                    response_status="accepted",
                    send_updates="all",
                    access_token=token,
                )
            assert len(requests) == 1
        for incorrect in (before_resource, {**after, "id": "other-instance"}, []):
            requests.clear()
            mode = incorrect
            with pytest.raises(CalendarResponseUncertainError, match="response_result_unverified"):
                await provider.respond_to_invitation(
                    calendar_id="owner@example.test",
                    before=before,
                    response_status="accepted",
                    send_updates="none",
                    access_token=token,
                )
            assert len(requests) == 1
        requests.clear()
        mode = httpx.ReadTimeout("not public")
        with pytest.raises(CalendarResponseUncertainError, match="response_transport_uncertain"):
            await provider.respond_to_invitation(
                calendar_id="owner@example.test",
                before=before,
                response_status="accepted",
                send_updates="none",
                access_token=token,
            )
        assert len(requests) == 1
        mode = after
        assert (
            await provider.respond_to_invitation(
                calendar_id="owner@example.test",
                before=before,
                response_status="accepted",
                send_updates="none",
                access_token=token,
            )
            == after
        )
        provider._oauth.get_access_token.assert_awaited_once()


async def test_registered_response_gate_parks_before_owner_rules_and_excludes_private_args(
    monkeypatch,
):
    # REQ-module-calendar-029
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from fastmcp import FastMCP

    from butlers.config import parse_approval_config
    from butlers.modules.approvals.gate import apply_approval_gates
    from butlers.modules.calendar import CalendarModule

    mcp = FastMCP("response-registration")
    module = CalendarModule()
    pool = SimpleNamespace()
    await module.register_tools(
        mcp,
        {"provider": "google", "response_enabled": True},
        SimpleNamespace(pool=pool),
        "messenger",
    )
    tool = await mcp.get_tool("calendar_respond")
    assert set(tool.parameters["properties"]) == {
        "entry_id",
        "response_status",
        "request_id",
        "send_updates",
    }
    assert "needsAction" not in tool.parameters["properties"]["response_status"]["enum"]
    private = module._response_coordinator
    private.prepare_and_park = AsyncMock(return_value={"status": "refused_test"})
    resolve = AsyncMock(side_effect=AssertionError("owner bypass must not run"))
    rules = AsyncMock(side_effect=AssertionError("standing rules must not run"))
    monkeypatch.setattr("butlers.modules.approvals.gate.resolve_action_target_contact", resolve)
    monkeypatch.setattr("butlers.modules.approvals.gate.match_standing_rule", rules)
    config = parse_approval_config({"enabled": True, "gated_tools": {"calendar_respond": {}}})
    originals = await apply_approval_gates(
        mcp,
        config,
        pool,
        "messenger",
        tool_metadata=module.tool_metadata(),
        canonical_preparers=module.canonical_approval_preparers(),
    )
    wrapped = (await mcp.get_tool("calendar_respond")).fn
    arguments = {"entry_id": "entry", "request_id": "request", "response_status": "accepted"}
    dossier = {
        "_why": "Owner chose this occurrence",
        "_evidence": [],
        "_blast_radius": "self",
        "_reversibility": "compensable",
    }
    assert (await wrapped(**arguments, **dossier))["status"] == "refused_test"
    assert private.prepare_and_park.await_count == 1
    from fastmcp import Client

    async with Client(mcp) as client:
        transported = await client.call_tool("calendar_respond", {**arguments, **dossier})
        assert transported.data["status"] == "refused_test"
        forged = await client.call_tool(
            "calendar_respond", {**arguments, **dossier, "_command_id": "caller"}
        )
        assert forged.data["error"] == "response_public_arguments_invalid"
    assert private.prepare_and_park.await_count == 2
    assert isinstance(private.prepare_and_park.call_args.kwargs["requested_at"], datetime)
    assert private.prepare_and_park.call_args.kwargs["requested_at"].tzinfo == UTC
    assert (await wrapped(**arguments, **dossier, _command_id="forged"))[
        "error"
    ] == "response_public_arguments_invalid"
    assert "error" in await wrapped(**arguments)
    assert private.prepare_and_park.await_count == 2
    import uuid

    events = AsyncMock()
    monkeypatch.setattr("butlers.fleet_events.publish_fleet_event", events)
    for terminal in ("rejected", "expired", "approved"):
        private.prepare_and_park.return_value = {
            "status": "approved" if terminal == "approved" else "rejected",
            "command_id": str(uuid.uuid4()),
        }
        assert (await wrapped(**arguments, **dossier))["status"] != "pending_approval"
    events.assert_not_awaited()
    resolve.assert_not_awaited()
    rules.assert_not_awaited()
    # The captured real pre-gate handler still requires the owning executor
    # context; invocation of a private Python handler is not approval authority.
    assert (await originals["calendar_respond"](**arguments))[
        "error"
    ] == "response_approval_required"
    assert isinstance(module.tool_metadata()["calendar_respond"].arg_sensitivities, dict)
    assert module.tool_metadata()["calendar_respond"].arg_sensitivities["_write"] is True
    # Exercise the actual captured registration boundary consumed by the
    # unchanged executor's success:false refusal. Outcome recording alone must
    # not mark a failed or uncertain provider attempt executed.
    for status in ("failed", "uncertain"):
        private.execute = AsyncMock(return_value={"status": status, "reason": "closed_test"})
        outcome = await originals["calendar_respond"](**arguments)
        assert outcome["status"] == status and outcome["success"] is False
    for status in ("applied", "noop"):
        private.execute = AsyncMock(return_value={"status": status})
        assert await originals["calendar_respond"](**arguments) == {"status": status}


class _CommandPool:
    """Software SQL carrier; real migrated locks/codec are covered separately."""

    def __init__(self, target):
        import asyncio

        self.target = target
        self.commands = {}
        self.pending = {}
        self.lock = asyncio.Lock()
        self.acquisitions = []

    def acquire(self):
        pool = self

        class Acquisition:
            async def __aenter__(self):
                connection = _CommandConnection(pool)
                pool.acquisitions.append(connection)
                return connection

            async def __aexit__(self, *args):
                pass

        return Acquisition()

    async def fetchrow(self, sql, *args):
        return await _CommandConnection(self).fetchrow(sql, *args)


class _CommandConnection:
    def __init__(self, pool):
        self.pool = pool

    def transaction(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def transaction():
            async with self.pool.lock:
                commands = copy.deepcopy(self.pool.commands)
                pending = copy.deepcopy(self.pool.pending)
                try:
                    yield
                except BaseException:
                    self.pool.commands = commands
                    self.pool.pending = pending
                    raise

        return transaction()

    async def fetchrow(self, sql, *args):
        if "FROM calendar_event_instances i" in sql:
            return (
                copy.deepcopy(self.pool.target)
                if str(args[0]) == str(self.pool.target["entry_id"])
                else None
            )
        if "FROM pending_actions" in sql:
            return copy.deepcopy(self.pool.pending.get(args[0]))
        if "FROM calendar_action_log" in sql:
            if "idempotency_key" in sql:
                return next(
                    (
                        copy.deepcopy(row)
                        for row in self.pool.commands.values()
                        if row["idempotency_key"] == args[0]
                    ),
                    None,
                )
            return copy.deepcopy(self.pool.commands.get(args[0]))
        raise AssertionError("unexpected source query")

    async def fetchval(self, sql, *args):
        assert "egress_started" in sql and "uncertain" in sql
        return any(
            row["id"] != args[0]
            and row["action_payload"].get("target_key") == args[1]
            and row["action_payload"].get("phase") in {"egress_started", "uncertain"}
            for row in self.pool.commands.values()
        )

    async def execute(self, sql, *args):
        if "pg_advisory_xact_lock" in sql:
            return "SELECT 1"
        if sql.startswith("INSERT INTO calendar_action_log"):
            if any(row["idempotency_key"] == args[1] for row in self.pool.commands.values()):
                return "INSERT 0 0"
            self.pool.commands[args[0]] = {
                "id": args[0],
                "idempotency_key": args[1],
                "action_type": "workspace_user_respond",
                "action_status": "pending",
                "action_payload": copy.deepcopy(args[-1]),
                "action_result": None,
            }
            return "INSERT 0 1"
        row = self.pool.commands[args[0]]
        if "SET action_status=$2" in sql:
            row["action_status"] = args[1]
            row["action_result"] = copy.deepcopy(args[2])
            row["action_payload"].update(args[3])
        elif "SET action_payload=action_payload ||" in sql:
            row["action_payload"].update(args[1])
        elif "SET action_payload=$2" in sql:
            row["action_payload"] = copy.deepcopy(args[1])
        elif "SET action_result=action_result ||" in sql:
            row["action_result"].update(args[1])
        else:
            raise AssertionError("unexpected source update")
        return "UPDATE 1"


async def test_response_command_admission_execution_fence_replay_and_projection(monkeypatch):
    # REQ-module-calendar-029: actual coordinator with a closed software SQL
    # carrier; no PostgreSQL lock/rollback/role claim from these controls.
    import asyncio
    import uuid
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from butlers.core.approvals_hooks import DecisionDossier
    from butlers.modules.approvals.execution_context import (
        ApprovalExecutionContext,
        approval_tool_args_digest,
        reset_approval_execution_context,
        set_approval_execution_context,
    )
    from butlers.modules.calendar import CalendarModule

    entry_id, source_id, event_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    target = dict(
        entry_id=entry_id,
        source_id=source_id,
        event_id=event_id,
        instance_status="confirmed",
        event_status="confirmed",
        provider="google",
        source_kind="provider_event",
        lane="user",
        butler_name=None,
        writable=True,
        source_key="provider:google:owner",
        calendar_id="owner@example.test",
        provider_event_id="instance-one",
        source_metadata={"account_email": "owner@example.test"},
    )
    pool = _CommandPool(target)
    module = CalendarModule()
    module._db = SimpleNamespace(pool=pool)
    module._butler_name = "messenger"
    module._config = CalendarConfig(provider="google", response_enabled=True)
    module._response_ready = True
    account = {"entity_id": str(uuid.uuid4()), "email": "owner@example.test"}
    module._verified_response_account = AsyncMock(return_value=account)
    resource = _invitation()
    after = copy.deepcopy(resource)
    after["etag"] = '"version-two"'
    after["attendees"][0]["responseStatus"] = "accepted"
    provider = SimpleNamespace(
        get_response_calendar=AsyncMock(
            return_value={"id": "owner@example.test", "accessRole": "writer"}
        ),
        get_response_event=AsyncMock(return_value=resource),
        prepare_response_write=AsyncMock(return_value="test-token"),
        respond_to_invitation=AsyncMock(return_value=after),
    )
    module._provider = provider
    module._project_provider_changes = AsyncMock(
        side_effect=RuntimeError("synthetic projection failure")
    )
    coordinator = module._response_coordinator

    async def park(connection, **kwargs):
        pool.pending[kwargs["action_id"]] = {
            "tool_name": kwargs["tool_name"],
            "tool_args": kwargs["tool_args"],
            "status": "pending",
        }

    monkeypatch.setattr("butlers.modules.calendar_response.park_pending_action", park)
    request = dict(
        entry_id=str(entry_id),
        request_id=str(uuid.uuid4()),
        response_status="accepted",
        send_updates="none",
    )
    now = datetime.now(UTC)
    dossier = DecisionDossier("Owner chose this occurrence", [], "self", "compensable")

    async def prepare(args):
        return await coordinator.prepare_and_park(
            tool_args=args, requested_at=now, expires_at=now + timedelta(hours=1), dossier=dossier
        )

    admitted = await prepare(request)
    assert admitted["status"] == "pending_approval" and len(pool.commands) == len(pool.pending) == 1
    assert await prepare(request) == admitted
    assert "error" in await prepare({**request, "response_status": "declined"})
    assert "error" in await prepare({**request, "_command_id": "caller"})
    command_id = uuid.UUID(admitted["command_id"])
    for terminal in ("rejected", "expired", "approved"):
        pool.pending[command_id]["status"] = terminal
        expected = "approved" if terminal == "approved" else "rejected"
        assert (await prepare(request))["status"] == expected
        # Force the actual concurrent locked-row path, after the first read
        # misses an already-admitted command/decision.
        with monkeypatch.context() as race:
            real_read = pool.fetchrow

            async def missing_prelock(sql, *values):
                if "idempotency_key" in sql:
                    return None
                return await real_read(sql, *values)

            race.setattr(pool, "fetchrow", AsyncMock(side_effect=missing_prelock))
            assert (await prepare(request))["status"] == expected
    pool.pending[command_id]["status"] = "pending"
    provider.respond_to_invitation.assert_not_awaited()
    args = pool.pending[command_id]["tool_args"]
    assert (await coordinator.execute(args))["error"] == "response_approval_required"

    async def execute(identifier, dispatch_args=None):
        actual = dispatch_args or pool.pending[identifier]["tool_args"]
        pool.pending[identifier]["status"] = "approved"
        pool.pending[identifier]["decided_by"] = "human:dashboard:rest-api"
        context = ApprovalExecutionContext(
            action_id=identifier,
            session_id=None,
            actor="human:dashboard:rest-api",
            tool_name="calendar_respond",
            tool_args_digest=approval_tool_args_digest(actual),
            authorized_task=asyncio.current_task(),
        )
        token = set_approval_execution_context(context)
        try:
            child = await asyncio.create_task(coordinator.execute(actual))
            assert child.get("error") == "response_approval_required"
            return await coordinator.execute(actual)
        finally:
            reset_approval_execution_context(token)

    result = await execute(command_id)
    assert result["status"] == "applied" and result["projection_available"] is False
    assert (
        result["before"]["response_status"] == "needsAction"
        and result["after"]["response_status"] == "accepted"
    )
    assert pool.commands[command_id]["action_status"] == "applied"
    assert len(pool.acquisitions) >= 3  # admission, committed start, applied receipt
    provider.respond_to_invitation.assert_awaited_once()
    assert await execute(command_id) == result
    provider.respond_to_invitation.assert_awaited_once()

    # An actual started unresolved command refuses a different key even after
    # restoring provider read state. No current-status inference clears it.
    request2 = {**request, "request_id": str(uuid.uuid4())}
    admitted2 = await prepare(request2)
    command2 = uuid.UUID(admitted2["command_id"])
    provider.respond_to_invitation.side_effect = CalendarResponseUncertainError(
        "response_result_unverified"
    )
    assert (await execute(command2))["status"] == "uncertain"
    request3 = {**request, "request_id": str(uuid.uuid4())}
    admitted3 = await prepare(request3)
    command3 = uuid.UUID(admitted3["command_id"])
    calls = provider.respond_to_invitation.await_count
    assert (await execute(command3)).get("error") == "response_target_uncertain"
    assert provider.respond_to_invitation.await_count == calls
    assert pool.commands[command2]["action_payload"]["phase"] == "uncertain"
    assert (await prepare(request2))["status"] == "uncertain"
    old_original = resource["originalStartTime"]
    resource["originalStartTime"] = {"dateTime": "2026-10-11T10:00:00+00:00", "timeZone": "UTC"}
    old_source = pool.target["source_id"]
    pool.target["source_id"] = uuid.uuid4()
    rebound_id = uuid.UUID(
        (await prepare({**request, "request_id": str(uuid.uuid4())}))["command_id"]
    )
    assert (await execute(rebound_id)).get("error") == "response_target_uncertain"
    assert provider.respond_to_invitation.await_count == calls
    resource["originalStartTime"] = old_original
    pool.target["source_id"] = old_source

    # Both explicit foreign owner and credential/source mismatch are actual
    # admission controls, with a planted NULL-owning-pool healthy companion.
    for field, value in (
        ("butler_name", "foreign"),
        ("calendar_id", None),
        ("source_metadata", {"account_email": "foreign@example.test"}),
        ("source_kind", "internal_scheduler"),
        ("lane", "butler"),
        ("provider", "unverified"),
        ("writable", False),
        ("instance_status", "cancelled"),
    ):
        original = pool.target[field]
        pool.target[field] = value
        refused = await prepare({**request, "request_id": str(uuid.uuid4())})
        assert "error" in refused
        pool.target[field] = original
    assert (await prepare({**request, "request_id": str(uuid.uuid4())}))[
        "status"
    ] == "pending_approval"
    assert all("test-token" not in json.dumps(row, default=str) for row in pool.commands.values())


async def test_real_daemon_binds_canonical_response_to_captured_owning_executor(monkeypatch):
    # REQ-module-calendar-029: actual installed registry/startup, not a mocked
    # apply_approval_gates result. Missing gate and executor are planted refusals.
    from pathlib import Path
    from types import SimpleNamespace

    from fastmcp import FastMCP

    from butlers.daemon import ButlerDaemon
    from butlers.modules.approvals.module import ApprovalsModule
    from butlers.modules.calendar import CalendarModule

    module = CalendarModule()
    module._config = CalendarConfig(provider="google", response_enabled=True)
    module._butler_name = "response-test"
    db = SimpleNamespace(pool=AsyncMock())
    module._db = db
    mcp = FastMCP("actual-calendar-startup")
    await module.register_tools(
        mcp, {"provider": "google", "response_enabled": True}, db, "response-test"
    )
    approvals = ApprovalsModule()
    daemon = ButlerDaemon(Path(__file__).resolve().parents[2] / "roster" / "messenger", db=db)
    daemon.mcp = mcp
    daemon.config = SimpleNamespace(
        name="response-test",
        modules={
            "approvals": {
                "enabled": True,
                "gated_tools": {"calendar_respond": {"risk_tier": "medium"}},
            }
        },
    )
    daemon._modules = [module, approvals]
    monkeypatch.setattr(daemon, "_build_approval_push_runtime", lambda: None)
    originals = await daemon._apply_approval_gates()
    assert set(originals) == {"calendar_respond"}
    assert module._response_ready is True
    assert callable(approvals._tool_executor)
    with pytest.raises(RuntimeError, match="response_approval_required"):
        await approvals._tool_executor(
            "calendar_respond",
            {
                "entry_id": "00000000-0000-0000-0000-000000000001",
                "response_status": "accepted",
                "request_id": "00000000-0000-0000-0000-000000000002",
                "send_updates": "none",
            },
        )
    for broken in ({"enabled": False}, {"enabled": True, "gated_tools": {}}):
        module.set_response_approval_ready(False)
        daemon.config.modules["approvals"] = broken
        with pytest.raises(RuntimeError, match="owning approval gate and executor"):
            await daemon._apply_approval_gates()
        assert module._response_ready is False
    # Restored real registration uses a new registry rather than double wrapping
    # the previous gate. The public capability is opt-in and only declared by
    # the two actual server-configured response owners.
    from butlers.config import load_config

    root = Path(__file__).resolve().parents[2]
    for name in ("messenger", "relationship"):
        config = load_config(root / "roster" / name)
        assert config.modules["calendar"]["response_enabled"] is True
        assert "calendar_respond" in config.modules["approvals"]["gated_tools"]
    assert CalendarModule().canonical_approval_preparers() == {}

    import uuid

    entity_id = uuid.uuid4()
    account = SimpleNamespace(
        entity_id=entity_id,
        email="owner@example.test",
        status="active",
        is_primary=True,
        granted_scopes=["https://www.googleapis.com/auth/calendar"],
    )
    module._response_credential_entity_id = entity_id
    db.pool.fetchrow.return_value = {"granted": True}
    lookup = AsyncMock(return_value=account)
    monkeypatch.setattr("butlers.google_account_registry.get_google_account", lookup)
    assert await module._verified_response_account() == {
        "entity_id": str(entity_id),
        "email": account.email,
    }
    for field, value in (
        ("entity_id", uuid.uuid4()),
        ("status", "revoked"),
        ("is_primary", False),
        ("granted_scopes", []),
    ):
        prior = getattr(account, field)
        setattr(account, field, value)
        with pytest.raises(CalendarResponseError, match="response_credentials_changed"):
            await module._verified_response_account()
        setattr(account, field, prior)
    db.pool.fetchrow.return_value = {"granted": False}
    with pytest.raises(CalendarResponseError, match="response_permission_denied"):
        await module._verified_response_account()
    db.pool.fetchrow.side_effect = RuntimeError("controlled database unavailable")
    with pytest.raises(RuntimeError):
        await module._verified_response_account()
    db.pool.fetchrow.side_effect = None
    db.pool.fetchrow.return_value = {"granted": True}
    assert (await module._verified_response_account())["entity_id"] == str(entity_id)
