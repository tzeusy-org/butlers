"""Tests for the dashboard chat-widget lane tools (bu-p6ey8.2).

Covers:
- ``route_to_butler`` deterministically injects conversation_id/page_context
  + interpret-apply-confirm instructions into the routed envelope's
  ``input.context`` when dashboard routing context is present, regardless
  of what the classification session itself wrote.
- A successful dashboard route stamps sticky ``routed_butler`` on the
  conversation.
- Non-dashboard route_to_butler calls are unaffected (no injection, no
  stamping attempt).
- ``file_bug_report`` relays a fingerprinted finding to the QA staffer via
  the internal ``route()`` function (never a domain butler) and posts a
  ``conversation_reply`` ack with the case reference; on relay failure it
  still replies (never routes to a domain butler).

Bootstrap pattern mirrors ``test_route_to_butler_accepted_status.py`` — each
daemon test file keeps its own local copy of the ButlerDaemon bootstrap
patches (established repo convention, no shared fixture exists for this).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastmcp import Client
from fastmcp import FastMCP as RuntimeFastMCP

from butlers.daemon import ButlerDaemon
from butlers.tools.switchboard.routing.contracts import parse_route_envelope

pytestmark = pytest.mark.unit


def _make_switchboard_dir(tmp_path: Path) -> Path:
    toml_lines = [
        "[butler]",
        'name = "switchboard"',
        "port = 9100",
        'description = "Routes messages"',
        "",
        "[butler.db]",
        'name = "butlers"',
        'schema = "switchboard"',
        "",
        "[[butler.schedule]]",
        'name = "daily-check"',
        'cron = "0 9 * * *"',
        'prompt = "Do the daily check"',
    ]
    (tmp_path / "butler.toml").write_text("\n".join(toml_lines))
    return tmp_path


def _make_runtime_config_row(butler_name: str = "switchboard") -> dict:
    return {
        "butler_name": butler_name,
        "core_groups": None,
        "catalog_read_sensitivity": "normal",
        "max_concurrent": 3,
        "max_queued": 10,
        "seeded_at": None,
        "updated_at": None,
    }


def _make_fetchrow_side_effect(butler_name: str = "switchboard"):
    async def _fetchrow(query: str, *args, **kwargs):
        if "runtime_config" in query:
            return _make_runtime_config_row(butler_name)
        return None

    return _fetchrow


def _patch_infra():
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=None)
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_conn.fetchval = AsyncMock(return_value=None)
    mock_conn.fetch = AsyncMock(return_value=[])

    # MagicMock, not AsyncMock: `.acquire` must be a plain sync call returning
    # an async-context-manager double (see TestDeadLetterDashboardUnroutable
    # in test_module_pipeline.py for the same pattern) — every attribute this
    # helper actually needs awaited is explicitly assigned AsyncMock below, so
    # the base class choice only affects unconfigured attributes like
    # `.acquire`. An AsyncMock base made `pool.acquire()` return a coroutine
    # (no `__aenter__`), breaking `async with pool.acquire() as conn:` call
    # sites such as `cannot_answer`'s dead-letter capture.
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_pool.fetchval = AsyncMock(return_value=None)
    mock_pool.execute = AsyncMock(return_value=None)
    mock_pool.fetchrow = AsyncMock(side_effect=_make_fetchrow_side_effect())
    mock_pool.fetch = AsyncMock(return_value=[])

    mock_db = MagicMock()
    mock_db.provision = AsyncMock()
    mock_db.connect = AsyncMock(return_value=mock_pool)
    mock_db.close = AsyncMock()
    mock_db.pool = mock_pool
    mock_db.user = "postgres"
    mock_db.password = "postgres"
    mock_db.host = "localhost"
    mock_db.port = 5432
    mock_db.db_name = "butlers"

    mock_spawner = MagicMock()
    mock_spawner.stop_accepting = MagicMock()
    mock_spawner.drain = AsyncMock()

    mock_adapter = MagicMock()
    mock_adapter.binary_name = "claude"
    mock_adapter_cls = MagicMock(return_value=mock_adapter)

    return {
        "db_from_env": patch("butlers.lifecycle.Database.from_env", return_value=mock_db),
        "run_migrations": patch("butlers.lifecycle.run_migrations", new_callable=AsyncMock),
        "validate_credentials": patch("butlers.lifecycle.validate_credentials"),
        "validate_module_credentials": patch(
            "butlers.lifecycle.validate_module_credentials_async",
            new_callable=AsyncMock,
            return_value={},
        ),
        "init_telemetry": patch("butlers.lifecycle.init_telemetry"),
        "sync_schedules": patch("butlers.lifecycle.sync_schedules", new_callable=AsyncMock),
        "FastMCP": patch("butlers.lifecycle.FastMCP"),
        "Spawner": patch("butlers.lifecycle.Spawner", return_value=mock_spawner),
        "start_mcp_server": patch.object(ButlerDaemon, "_start_mcp_server", new_callable=AsyncMock),
        "connect_switchboard": patch.object(
            ButlerDaemon, "_connect_switchboard", new_callable=AsyncMock
        ),
        "get_adapter": patch("butlers.lifecycle.get_adapter", return_value=mock_adapter_cls),
        "shutil_which": patch("butlers.lifecycle.shutil.which", return_value="/usr/bin/claude"),
        "mock_pool": mock_pool,
        "mock_spawner": mock_spawner,
    }


async def _start_switchboard_and_capture_tools(
    butler_dir: Path,
    patches: dict,
    mock_route: AsyncMock | None = None,
) -> tuple[ButlerDaemon, dict[str, Any]]:
    """Start a switchboard ButlerDaemon and capture named MCP tool functions."""
    captured_tools: dict[str, Any] = {}
    mock_mcp = MagicMock()

    def tool_decorator(*_decorator_args, **decorator_kwargs):
        declared_name = decorator_kwargs.get("name")

        def decorator(fn):
            resolved_name = declared_name or fn.__name__
            captured_tools[resolved_name] = fn
            return fn

        return decorator

    mock_mcp.tool = tool_decorator

    route_patch = (
        patch("butlers.tools.switchboard.routing.route.route", new=mock_route)
        if mock_route is not None
        else patch("butlers.tools.switchboard.routing.route.route")
    )

    with (
        patches["db_from_env"],
        patches["run_migrations"],
        patches["validate_credentials"],
        patches["validate_module_credentials"],
        patches["init_telemetry"],
        patches["sync_schedules"],
        patch("butlers.lifecycle.FastMCP", return_value=mock_mcp),
        patches["Spawner"],
        patches["get_adapter"],
        patches["shutil_which"],
        patches["start_mcp_server"],
        patches["connect_switchboard"],
        route_patch,
    ):
        daemon = ButlerDaemon(butler_dir)
        await daemon.start()

    return daemon, captured_tools


def _set_dashboard_routing_context(
    *,
    conversation_id: str = "c1c1c1c1-0000-7000-8000-000000000001",
    page_context: dict[str, Any] | None = None,
    dashboard_message_id: str | None = None,
) -> None:
    from butlers.core.routing_context import _routing_ctx_var

    source_metadata: dict[str, Any] = {
        "channel": "dashboard",
        "identity": "dashboard:operator",
    }
    dashboard_context: dict[str, Any] = {
        "conversation_id": conversation_id,
        "page_context": page_context,
    }
    if dashboard_message_id is not None:
        source_metadata["dashboard_message_id"] = dashboard_message_id
        dashboard_context["message_id"] = dashboard_message_id
    _routing_ctx_var.set(
        {
            "source_metadata": source_metadata,
            "request_context": None,
            "request_id": "unknown",
            "dashboard_context": dashboard_context,
        }
    )


def _clear_routing_context() -> None:
    from butlers.core.routing_context import _routing_ctx_var

    _routing_ctx_var.set(None)


def _turn_result(outcome: str):
    """Build the small control result shape needed by lane-tool tests."""
    from butlers.core.dashboard_turns import DashboardTurnResult

    return DashboardTurnResult(
        outcome=outcome,
        message_id=UUID("d1d1d1d1-0000-7000-8000-000000000001"),
        conversation_id=UUID("c1c1c1c1-0000-7000-8000-000000000001"),
        request_id=UUID("019c8812-fb0f-77f3-88b9-5763c1336b27"),
        target_butler=None,
        target_kind=None,
        route_inbox_id=None,
        cancel_requested_at=None,
        cancel_confirmed_at=None,
        terminal_state=None,
        terminal_at=None,
    )


# ---------------------------------------------------------------------------
# route_to_butler — dashboard context injection
# ---------------------------------------------------------------------------


async def test_route_to_butler_injects_dashboard_block_deterministically(tmp_path: Path) -> None:
    """conversation_id/page_context + confirm instructions are appended to
    input.context regardless of the classification session's own `context`."""
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    captured: dict[str, Any] = {}

    async def _capture(*_args, **kwargs):
        captured.update(kwargs["args"])
        return {"result": {"status": "accepted"}}

    _, tools = await _start_switchboard_and_capture_tools(
        butler_dir, patches, mock_route=AsyncMock(side_effect=_capture)
    )
    fn = tools["route_to_butler"]

    _set_dashboard_routing_context(
        page_context={"route": "/entities/concentration", "query_params": {"predicate": "child-of"}}
    )
    try:
        result = await fn(
            butler="relationship",
            prompt="Alice's birthday is March 3rd",
            context="owner-provided context",
        )
    finally:
        _clear_routing_context()

    assert result["status"] == "accepted"
    payload = {k: v for k, v in captured.items() if k != "__switchboard_route_context"}
    envelope = parse_route_envelope(payload)
    assert "c1c1c1c1-0000-7000-8000-000000000001" in envelope.input.context
    assert "/entities/concentration" in envelope.input.context
    assert "conversation_reply" in envelope.input.context
    # Original classifier-provided context is preserved, not clobbered.
    assert "owner-provided context" in envelope.input.context
    # bu-0ynlk.1: the injected block carries distinct STATEMENT and ACTION
    # REQUEST instruction sets — an action request must never be applied
    # before the approval gate parks it.
    assert "STATEMENT" in envelope.input.context
    assert "ACTION REQUEST" in envelope.input.context
    assert "approval-gated tool" in envelope.input.context
    assert "FAILURE MODE" in envelope.input.context


async def test_route_to_butler_dashboard_injection_works_without_explicit_context(
    tmp_path: Path,
) -> None:
    """The dashboard block is still injected when the caller passed no `context` at all."""
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    captured: dict[str, Any] = {}

    async def _capture(*_args, **kwargs):
        captured.update(kwargs["args"])
        return {"result": {"status": "accepted"}}

    _, tools = await _start_switchboard_and_capture_tools(
        butler_dir, patches, mock_route=AsyncMock(side_effect=_capture)
    )
    fn = tools["route_to_butler"]

    _set_dashboard_routing_context(page_context=None)
    try:
        result = await fn(butler="relationship", prompt="Alice's birthday is March 3rd")
    finally:
        _clear_routing_context()

    assert result["status"] == "accepted"
    payload = {k: v for k, v in captured.items() if k != "__switchboard_route_context"}
    envelope = parse_route_envelope(payload)
    assert "c1c1c1c1-0000-7000-8000-000000000001" in envelope.input.context


async def test_route_to_butler_no_injection_without_dashboard_context(tmp_path: Path) -> None:
    """Non-dashboard routing context leaves `context` untouched."""
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    captured: dict[str, Any] = {}

    async def _capture(*_args, **kwargs):
        captured.update(kwargs["args"])
        return {"result": {"status": "accepted"}}

    _, tools = await _start_switchboard_and_capture_tools(
        butler_dir, patches, mock_route=AsyncMock(side_effect=_capture)
    )
    fn = tools["route_to_butler"]

    result = await fn(butler="finance", prompt="Track this receipt", context="plain context")

    assert result["status"] == "accepted"
    payload = {k: v for k, v in captured.items() if k != "__switchboard_route_context"}
    envelope = parse_route_envelope(payload)
    assert envelope.input.context == "plain context"


async def test_route_to_butler_stamps_routed_butler_on_success(tmp_path: Path, monkeypatch) -> None:
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    mock_route = AsyncMock(return_value={"result": {"status": "accepted"}})

    _, tools = await _start_switchboard_and_capture_tools(
        butler_dir, patches, mock_route=mock_route
    )
    fn = tools["route_to_butler"]

    fake_stamp = AsyncMock()
    monkeypatch.setattr("butlers.api.conversations.conversation_set_routed_butler", fake_stamp)

    _set_dashboard_routing_context()
    try:
        result = await fn(butler="relationship", prompt="hello")
    finally:
        _clear_routing_context()

    assert result["status"] == "accepted"
    fake_stamp.assert_awaited_once()
    assert fake_stamp.await_args.kwargs["routed_butler"] == "relationship"


async def test_route_to_butler_stamping_failure_does_not_fail_the_call(
    tmp_path: Path, monkeypatch
) -> None:
    """Sticky-stamp is best-effort — a DB error there must not surface as a route failure."""
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    mock_route = AsyncMock(return_value={"result": {"status": "accepted"}})

    _, tools = await _start_switchboard_and_capture_tools(
        butler_dir, patches, mock_route=mock_route
    )
    fn = tools["route_to_butler"]

    monkeypatch.setattr(
        "butlers.api.conversations.conversation_set_routed_butler",
        AsyncMock(side_effect=RuntimeError("connection reset")),
    )

    _set_dashboard_routing_context()
    try:
        result = await fn(butler="relationship", prompt="hello")
    finally:
        _clear_routing_context()

    assert result["status"] == "accepted"


async def test_route_to_butler_propagates_turn_id_and_obeys_a_prior_stop(
    tmp_path: Path,
) -> None:
    """The target envelope carries the immutable ID; a stopped turn never dispatches."""
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    captured: dict[str, Any] = {}

    async def _capture(*_args, **kwargs):
        captured.update(kwargs["args"])
        return {"result": {"status": "accepted"}}

    _, tools = await _start_switchboard_and_capture_tools(
        butler_dir,
        patches,
        mock_route=AsyncMock(side_effect=_capture),
    )
    fn = tools["route_to_butler"]
    message_id = "d1d1d1d1-0000-7000-8000-000000000001"

    with patch(
        "butlers.core.dashboard_turns.dispatch_status",
        new_callable=AsyncMock,
        return_value=_turn_result("active"),
    ):
        _set_dashboard_routing_context(dashboard_message_id=message_id)
        try:
            result = await fn(butler="relationship", prompt="Alice is my sister")
        finally:
            _clear_routing_context()

    assert result["status"] == "accepted"
    envelope = parse_route_envelope(
        {key: value for key, value in captured.items() if key != "__switchboard_route_context"}
    )
    assert str(envelope.source_metadata.dashboard_message_id) == message_id

    blocked_route = AsyncMock(return_value={"result": {"status": "accepted"}})
    patches2 = _patch_infra()
    (tmp_path / "blocked").mkdir()
    _, blocked_tools = await _start_switchboard_and_capture_tools(
        _make_switchboard_dir(tmp_path / "blocked"),
        patches2,
        mock_route=blocked_route,
    )
    with patch(
        "butlers.core.dashboard_turns.dispatch_status",
        new_callable=AsyncMock,
        return_value=_turn_result("cancelled"),
    ):
        _set_dashboard_routing_context(dashboard_message_id=message_id)
        try:
            blocked = await blocked_tools["route_to_butler"](
                butler="relationship",
                prompt="Alice is my sister",
            )
        finally:
            _clear_routing_context()

    assert blocked == {
        "status": "cancelled",
        "butler": "relationship",
        "cancelled": True,
    }
    blocked_route.assert_not_awaited()


# ---------------------------------------------------------------------------
# file_bug_report — QA relay + conversation_reply ack
# ---------------------------------------------------------------------------


async def test_file_bug_report_relays_to_qa_and_replies_with_case_reference(
    tmp_path: Path, monkeypatch
) -> None:
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    mock_route = AsyncMock(return_value={"result": {"accepted": True}})

    _, tools = await _start_switchboard_and_capture_tools(
        butler_dir, patches, mock_route=mock_route
    )
    fn = tools["file_bug_report"]

    fake_reply = AsyncMock(return_value={"id": "msg-1"})
    monkeypatch.setattr("butlers.api.conversations.conversation_reply_create", fake_reply)

    _set_dashboard_routing_context(page_context={"route": "/entities/concentration"})
    try:
        result = await fn(summary="The concentration chart is empty for child-of")
    finally:
        _clear_routing_context()

    assert result["status"] == "ok"
    assert result["filed"] is True
    assert len(result["case_reference"]) == 12

    # Relayed to QA — never a domain butler.
    mock_route.assert_awaited_once()
    route_call_kwargs = mock_route.await_args.kwargs
    assert route_call_kwargs["target_butler"] == "qa"
    assert route_call_kwargs["tool_name"] == "report_finding"
    assert (
        route_call_kwargs["args"]["event_summary"]
        == "The concentration chart is empty for child-of"
    )
    assert "/entities/concentration" in route_call_kwargs["args"]["call_site"]

    fake_reply.assert_awaited_once()
    assert result["case_reference"] in fake_reply.await_args.kwargs["message"]


async def test_file_bug_report_claims_turn_before_qa_relay_and_honors_stop(
    tmp_path: Path, monkeypatch
) -> None:
    """A dashboard bug report has one durable pre-relay commit point."""
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    mock_route = AsyncMock(return_value={"result": {"accepted": True}})
    _, tools = await _start_switchboard_and_capture_tools(
        butler_dir, patches, mock_route=mock_route
    )
    fn = tools["file_bug_report"]
    fake_reply = AsyncMock(return_value={"id": "msg-1"})
    monkeypatch.setattr("butlers.api.conversations.conversation_reply_create", fake_reply)
    claim = AsyncMock(return_value=_turn_result("claimed"))
    terminal = AsyncMock(return_value=_turn_result("finished"))

    with (
        patch("butlers.core.dashboard_turns.claim_bug_report", claim),
        patch("butlers.core.dashboard_turns.mark_terminal", terminal),
    ):
        _set_dashboard_routing_context(dashboard_message_id="d1d1d1d1-0000-7000-8000-000000000001")
        try:
            result = await fn(summary="The dashboard is blank")
        finally:
            _clear_routing_context()

    assert result["status"] == "ok"
    assert result["filed"] is True
    claim.assert_awaited_once()
    assert claim.await_args.kwargs["message_id"] == UUID("d1d1d1d1-0000-7000-8000-000000000001")
    mock_route.assert_awaited_once()
    terminal.assert_awaited_once()
    assert terminal.await_args.kwargs["state"] == "completed"

    stopped_route = AsyncMock()
    patches2 = _patch_infra()
    (tmp_path / "stopped").mkdir()
    _, stopped_tools = await _start_switchboard_and_capture_tools(
        _make_switchboard_dir(tmp_path / "stopped"),
        patches2,
        mock_route=stopped_route,
    )
    stopped_claim = AsyncMock(return_value=_turn_result("cancelled"))
    with patch("butlers.core.dashboard_turns.claim_bug_report", stopped_claim):
        _set_dashboard_routing_context(dashboard_message_id="d1d1d1d1-0000-7000-8000-000000000001")
        try:
            stopped = await stopped_tools["file_bug_report"](summary="The dashboard is blank")
        finally:
            _clear_routing_context()

    assert stopped == {"status": "cancelled", "filed": False, "cancelled": True}
    stopped_route.assert_not_awaited()


@pytest.mark.parametrize(
    ("severity", "expected_severity"),
    [
        ("1", 1),
        (1.0, 1),
        ("1.0", 1),
        (1.5, 2),
        ("1.5", 2),
        ("9", 4),
        ("9.0", 4),
        ("-3", 0),
        ("-3.0", 0),
        pytest.param(10**400, 4, id="huge-integer-clamps"),
        pytest.param("9" * 400, 4, id="huge-integer-string-clamps"),
        pytest.param(
            "1.0000000000000000000000000001",
            2,
            id="precise-fractional-string-defaults",
        ),
        ("not-a-severity", 2),
        (None, 2),
        ("", 2),
        (True, 2),
        (False, 2),
        ("inf", 2),
        ("-inf", 2),
        ("nan", 2),
        (float("inf"), 2),
        (float("-inf"), 2),
        (float("nan"), 2),
    ],
)
async def test_file_bug_report_coerces_clamps_and_defaults_severity(
    tmp_path: Path,
    severity: object,
    expected_severity: int,
) -> None:
    """Caller-shaped severity values reach QA as clamped integer priorities."""
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    mock_route = AsyncMock(return_value={"result": {"accepted": True}})

    _, tools = await _start_switchboard_and_capture_tools(
        butler_dir, patches, mock_route=mock_route
    )

    runtime_mcp = RuntimeFastMCP("test-switchboard")
    runtime_mcp.tool()(tools["file_bug_report"])
    async with Client(runtime_mcp) as client:
        result = await client.call_tool(
            "file_bug_report",
            {"summary": "The dashboard is broken", "severity": severity},
        )

    assert result.data["status"] == "ok"
    mock_route.assert_awaited_once()
    assert mock_route.await_args.kwargs["args"]["severity"] == expected_severity


async def test_file_bug_report_relay_failure_still_replies(tmp_path: Path, monkeypatch) -> None:
    """Even if the QA relay errors, the owner must still get an in-thread reply."""
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    mock_route = AsyncMock(return_value={"error": "Butler 'qa' not found in registry"})

    _, tools = await _start_switchboard_and_capture_tools(
        butler_dir, patches, mock_route=mock_route
    )
    fn = tools["file_bug_report"]

    fake_reply = AsyncMock(return_value={"id": "msg-1"})
    monkeypatch.setattr("butlers.api.conversations.conversation_reply_create", fake_reply)

    _set_dashboard_routing_context()
    try:
        result = await fn(summary="Something is broken")
    finally:
        _clear_routing_context()

    assert result["status"] == "error"
    assert result["filed"] is False
    fake_reply.assert_awaited_once()
    assert "couldn't file" in fake_reply.await_args.kwargs["message"].lower()


async def test_file_bug_report_never_calls_route_to_butler_style_dispatch(tmp_path: Path) -> None:
    """Sanity check: file_bug_report's only cross-butler call targets qa/report_finding,
    proving bug reports never reach a domain butler via this path."""
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    mock_route = AsyncMock(return_value={"result": {"accepted": True}})

    _, tools = await _start_switchboard_and_capture_tools(
        butler_dir, patches, mock_route=mock_route
    )
    fn = tools["file_bug_report"]

    await fn(summary="bug report with no dashboard context set")

    assert mock_route.await_count == 1
    assert mock_route.await_args.kwargs["target_butler"] == "qa"


# ---------------------------------------------------------------------------
# Dashboard lane exclusivity guard (bu-j5jqv gen-1 reconciliation gap G4)
# ---------------------------------------------------------------------------


async def test_route_to_butler_refused_after_file_bug_report_claims_lane(
    tmp_path: Path,
) -> None:
    """Bug-then-route: once file_bug_report claims a dashboard session, a
    later route_to_butler call in the same session must be refused (never
    dispatch to a domain butler) and the conflict must be logged at WARNING.

    Patches the module logger directly (rather than caplog) since this test
    boots a full ButlerDaemon whose logging setup is orthogonal to the
    behavior under test.
    """
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    mock_route = AsyncMock(return_value={"result": {"status": "accepted"}})

    _, tools = await _start_switchboard_and_capture_tools(
        butler_dir, patches, mock_route=mock_route
    )
    bug_fn = tools["file_bug_report"]
    route_fn = tools["route_to_butler"]

    fake_reply = AsyncMock(return_value={"id": "msg-1"})
    mock_logger = MagicMock()
    with (
        patch("butlers.api.conversations.conversation_reply_create", fake_reply),
        patch("butlers.core_tools._switchboard.logger", mock_logger),
    ):
        _set_dashboard_routing_context()
        try:
            bug_result = await bug_fn(summary="The dashboard is broken")
            route_result = await route_fn(butler="relationship", prompt="hello")
        finally:
            _clear_routing_context()

    assert bug_result["status"] == "ok"
    assert bug_result["filed"] is True
    assert "dashboard_lane_conflict" not in bug_result

    assert route_result["status"] == "refused"
    assert route_result["reason"] == "dashboard_lane_conflict"
    assert "file_bug_report" in route_result["error"]

    # route_to_butler must never have actually dispatched — the only
    # _switchboard_route call observed is file_bug_report's QA relay.
    assert mock_route.await_count == 1
    assert mock_route.await_args.kwargs["target_butler"] == "qa"

    warning_calls = [str(call.args[0]) for call in mock_logger.warning.call_args_list]
    assert any("Dashboard lane conflict" in msg for msg in warning_calls)


async def test_file_bug_report_after_route_to_butler_surfaces_co_occurrence(
    tmp_path: Path,
) -> None:
    """Route-then-bug: route_to_butler dispatches normally (it stands — the
    domain butler was already invoked), and a later file_bug_report call in
    the same session still files (bug reports are terminal, never suppressed)
    but surfaces the co-occurrence in its result and logs a WARNING.

    Patches the module logger directly (rather than caplog) since this test
    boots a full ButlerDaemon whose logging setup is orthogonal to the
    behavior under test.
    """
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    mock_route = AsyncMock(return_value={"result": {"status": "accepted"}})

    _, tools = await _start_switchboard_and_capture_tools(
        butler_dir, patches, mock_route=mock_route
    )
    route_fn = tools["route_to_butler"]
    bug_fn = tools["file_bug_report"]

    fake_reply = AsyncMock(return_value={"id": "msg-1"})
    mock_logger = MagicMock()
    with (
        patch("butlers.api.conversations.conversation_reply_create", fake_reply),
        patch("butlers.core_tools._switchboard.logger", mock_logger),
    ):
        _set_dashboard_routing_context()
        try:
            route_result = await route_fn(butler="relationship", prompt="hello")
            bug_result = await bug_fn(summary="Actually this is a bug")
        finally:
            _clear_routing_context()

    assert route_result["status"] == "accepted"

    assert bug_result["status"] == "ok"
    assert bug_result["filed"] is True
    assert bug_result["dashboard_lane_conflict"] == {
        "conflicting_lane": "route_to_butler",
        "conflicting_target": "relationship",
    }

    warning_calls = [str(call.args[0]) for call in mock_logger.warning.call_args_list]
    assert any("Dashboard lane conflict" in msg for msg in warning_calls)


async def test_dashboard_lane_guard_does_not_affect_non_dashboard_sessions(
    tmp_path: Path,
) -> None:
    """Regression: without a dashboard conversation_id, calling both
    file_bug_report and route_to_butler in the same routing context must not
    trigger the lane-exclusivity guard — non-dashboard switchboard flows are
    unaffected."""
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    mock_route = AsyncMock(return_value={"result": {"status": "accepted"}})

    _, tools = await _start_switchboard_and_capture_tools(
        butler_dir, patches, mock_route=mock_route
    )
    bug_fn = tools["file_bug_report"]
    route_fn = tools["route_to_butler"]

    # No dashboard routing context at all (e.g. a non-dashboard MCP caller).
    bug_result = await bug_fn(summary="Something is broken")
    route_result = await route_fn(butler="relationship", prompt="hello")

    assert bug_result["status"] == "ok"
    assert "dashboard_lane_conflict" not in bug_result

    assert route_result["status"] == "accepted"


# ---------------------------------------------------------------------------
# answer_question — question lane (bu-0ynlk.2)
# ---------------------------------------------------------------------------


async def test_answer_question_domain_injects_answer_block_not_confirm_block(
    tmp_path: Path, monkeypatch
) -> None:
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    captured: dict[str, Any] = {}

    async def _capture(*_args, **kwargs):
        captured.update(kwargs["args"])
        return {"result": {"status": "accepted"}}

    _, tools = await _start_switchboard_and_capture_tools(
        butler_dir, patches, mock_route=AsyncMock(side_effect=_capture)
    )
    fn = tools["answer_question"]
    fake_stamp = AsyncMock()
    monkeypatch.setattr("butlers.api.conversations.conversation_set_routed_butler", fake_stamp)

    _set_dashboard_routing_context(page_context={"route": "/entities/concentration"})
    try:
        result = await fn(
            scope="domain", question="How much did I spend on groceries?", target="finance"
        )
    finally:
        _clear_routing_context()

    assert result == {"status": "accepted", "butler": "finance"}
    payload = {k: v for k, v in captured.items() if k != "__switchboard_route_context"}
    envelope = parse_route_envelope(payload)
    assert "c1c1c1c1-0000-7000-8000-000000000001" in envelope.input.context
    assert "How much did I spend on groceries?" in envelope.input.context
    assert "READ-ONLY" in envelope.input.context
    assert "sources" in envelope.input.context
    assert "STATEMENT" not in envelope.input.context
    assert "ACTION REQUEST" not in envelope.input.context
    assert envelope.target.butler == "finance"
    fake_stamp.assert_not_awaited()


async def test_answer_question_domain_requires_target(tmp_path: Path) -> None:
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    mock_route = AsyncMock(return_value={"result": {"status": "accepted"}})

    _, tools = await _start_switchboard_and_capture_tools(
        butler_dir, patches, mock_route=mock_route
    )
    fn = tools["answer_question"]

    _set_dashboard_routing_context()
    try:
        result = await fn(scope="domain", question="How much did I spend?", target=None)
    finally:
        _clear_routing_context()

    assert result["status"] == "error"
    assert "target is required" in result["error"]
    mock_route.assert_not_awaited()


async def test_answer_question_rejects_unknown_scope(tmp_path: Path) -> None:
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    _, tools = await _start_switchboard_and_capture_tools(butler_dir, patches)
    fn = tools["answer_question"]

    result = await fn(scope="galaxy", question="hi?")

    assert result["status"] == "error"
    assert "scope must be" in result["error"]


async def test_answer_question_system_falls_back_to_cannot_answer_dead_letter(
    tmp_path: Path, monkeypatch
) -> None:
    """bu-0ynlk.3 (Concierge) does not exist yet — scope="system" must fall
    back to the same honest-decline dead-letter path as cannot_answer, never
    fabricate a system answer or route to a domain butler."""
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    mock_route = AsyncMock()

    _, tools = await _start_switchboard_and_capture_tools(
        butler_dir, patches, mock_route=mock_route
    )
    fn = tools["answer_question"]

    # Force a real import so the module object exists (register_all_butler_tools()
    # injects the switchboard tools namespace into sys.modules without setting
    # parent attributes, which breaks monkeypatch's dotted-string resolution —
    # patch the imported module object directly instead).
    import butlers.tools.switchboard.dead_letter.capture as capture_mod

    fake_capture = AsyncMock(return_value="dl-system-1")
    fake_reply = AsyncMock(return_value={"id": "msg-1"})
    monkeypatch.setattr(capture_mod, "capture_to_dead_letter", fake_capture)
    monkeypatch.setattr("butlers.api.conversations.conversation_reply_create", fake_reply)

    _set_dashboard_routing_context()
    try:
        result = await fn(scope="system", question="Which model does finance use?")
    finally:
        _clear_routing_context()

    assert result["status"] == "ok"
    assert result["answered"] is False
    assert result["dead_letter_id"] == "dl-system-1"

    fake_capture.assert_awaited_once()
    assert fake_capture.await_args.kwargs["failure_category"] == "unanswerable"

    fake_reply.assert_awaited_once()
    assert "system" in fake_reply.await_args.kwargs["message"]

    # Never routed to any domain butler.
    mock_route.assert_not_awaited()


async def test_answer_question_propagates_turn_id_and_obeys_a_prior_stop(
    tmp_path: Path,
) -> None:
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    captured: dict[str, Any] = {}

    async def _capture(*_args, **kwargs):
        captured.update(kwargs["args"])
        return {"result": {"status": "accepted"}}

    _, tools = await _start_switchboard_and_capture_tools(
        butler_dir, patches, mock_route=AsyncMock(side_effect=_capture)
    )
    fn = tools["answer_question"]
    message_id = "d1d1d1d1-0000-7000-8000-000000000001"

    with patch(
        "butlers.core.dashboard_turns.dispatch_status",
        new_callable=AsyncMock,
        return_value=_turn_result("active"),
    ):
        _set_dashboard_routing_context(dashboard_message_id=message_id)
        try:
            result = await fn(scope="domain", question="What's my budget?", target="finance")
        finally:
            _clear_routing_context()

    assert result["status"] == "accepted"

    blocked_route = AsyncMock(return_value={"result": {"status": "accepted"}})
    patches2 = _patch_infra()
    (tmp_path / "blocked-answer").mkdir()
    _, blocked_tools = await _start_switchboard_and_capture_tools(
        _make_switchboard_dir(tmp_path / "blocked-answer"),
        patches2,
        mock_route=blocked_route,
    )
    with patch(
        "butlers.core.dashboard_turns.dispatch_status",
        new_callable=AsyncMock,
        return_value=_turn_result("cancelled"),
    ):
        _set_dashboard_routing_context(dashboard_message_id=message_id)
        try:
            blocked = await blocked_tools["answer_question"](
                scope="domain", question="What's my budget?", target="finance"
            )
        finally:
            _clear_routing_context()

    assert blocked == {"status": "cancelled", "butler": "finance", "cancelled": True}
    blocked_route.assert_not_awaited()


# ---------------------------------------------------------------------------
# cannot_answer — terminal decline (bu-0ynlk.2)
# ---------------------------------------------------------------------------


async def test_cannot_answer_writes_one_dead_letter_and_replies_naming_scope_checked(
    tmp_path: Path, monkeypatch
) -> None:
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    mock_route = AsyncMock()

    _, tools = await _start_switchboard_and_capture_tools(
        butler_dir, patches, mock_route=mock_route
    )
    fn = tools["cannot_answer"]

    import butlers.tools.switchboard.dead_letter.capture as capture_mod

    fake_capture = AsyncMock(return_value="dl-1")
    fake_reply = AsyncMock(return_value={"id": "msg-1"})
    monkeypatch.setattr(capture_mod, "capture_to_dead_letter", fake_capture)
    monkeypatch.setattr("butlers.api.conversations.conversation_reply_create", fake_reply)

    _set_dashboard_routing_context()
    try:
        result = await fn(
            question_summary="What is the meaning of life?",
            scope_checked=["finance", "health", "system"],
            reason="No butler owns this question.",
        )
    finally:
        _clear_routing_context()

    assert result["status"] == "ok"
    assert result["answered"] is False
    assert result["filed"] is True
    assert result["dead_letter_id"] == "dl-1"

    fake_capture.assert_awaited_once()
    capture_kwargs = fake_capture.await_args.kwargs
    assert capture_kwargs["failure_category"] == "unanswerable"
    assert capture_kwargs["original_payload"]["scope_checked"] == [
        "finance",
        "health",
        "system",
    ]

    fake_reply.assert_awaited_once()
    reply_message = fake_reply.await_args.kwargs["message"]
    assert "finance" in reply_message
    assert "health" in reply_message
    assert "system" in reply_message
    assert fake_reply.await_args.kwargs["request_id"] == capture_kwargs["original_request_id"]

    # Never files a bug report or routes to a domain butler.
    mock_route.assert_not_awaited()


@pytest.mark.parametrize("failed_effect", ["capture", "reply"])
async def test_cannot_answer_reports_partial_effect_failure_truthfully(
    tmp_path: Path, monkeypatch, failed_effect: str
) -> None:
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    _, tools = await _start_switchboard_and_capture_tools(butler_dir, patches)

    import butlers.tools.switchboard.dead_letter.capture as capture_mod

    fake_capture = AsyncMock(return_value="dl-1")
    fake_reply = AsyncMock(return_value={"id": "msg-1"})
    if failed_effect == "capture":
        fake_capture.side_effect = RuntimeError("capture failed")
    else:
        fake_reply.side_effect = RuntimeError("reply failed")
    monkeypatch.setattr(capture_mod, "capture_to_dead_letter", fake_capture)
    monkeypatch.setattr("butlers.api.conversations.conversation_reply_create", fake_reply)
    terminal = AsyncMock(return_value=_turn_result("finished"))

    with (
        patch(
            "butlers.core.dashboard_turns.claim_dead_letter",
            AsyncMock(return_value=_turn_result("claimed")),
        ),
        patch("butlers.core.dashboard_turns.mark_terminal", terminal),
    ):
        _set_dashboard_routing_context(dashboard_message_id="d1d1d1d1-0000-7000-8000-000000000001")
        try:
            result = await tools["cannot_answer"](
                question_summary="What is the meaning of life?",
                scope_checked=["finance", "health"],
                reason="No owning scope.",
            )
        finally:
            _clear_routing_context()

    assert result["status"] == "error"
    assert result["filed"] is (failed_effect != "capture")
    terminal.assert_awaited_once()
    assert terminal.await_args.kwargs["state"] == "failed"
    if failed_effect == "capture":
        assert "couldn't file it" in fake_reply.await_args.kwargs["message"]
        assert "has been filed" not in fake_reply.await_args.kwargs["message"]


async def test_cannot_answer_claims_turn_before_capture_and_honors_stop(
    tmp_path: Path, monkeypatch
) -> None:
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    _, tools = await _start_switchboard_and_capture_tools(butler_dir, patches)
    fn = tools["cannot_answer"]

    fake_reply = AsyncMock(return_value={"id": "msg-1"})
    monkeypatch.setattr("butlers.api.conversations.conversation_reply_create", fake_reply)
    claim = AsyncMock(return_value=_turn_result("claimed"))
    terminal = AsyncMock(return_value=_turn_result("finished"))
    fake_capture = AsyncMock(return_value="dl-2")

    with (
        patch("butlers.core.dashboard_turns.claim_dead_letter", claim),
        patch("butlers.core.dashboard_turns.mark_terminal", terminal),
        patch("butlers.tools.switchboard.dead_letter.capture.capture_to_dead_letter", fake_capture),
    ):
        _set_dashboard_routing_context(dashboard_message_id="d1d1d1d1-0000-7000-8000-000000000001")
        try:
            result = await fn(question_summary="hi?", scope_checked=["finance"], reason="no owner")
        finally:
            _clear_routing_context()

    assert result["status"] == "ok"
    claim.assert_awaited_once()
    assert claim.await_args.kwargs["message_id"] == UUID("d1d1d1d1-0000-7000-8000-000000000001")
    terminal.assert_awaited_once()
    assert terminal.await_args.kwargs["state"] == "completed"

    stopped_claim = AsyncMock(return_value=_turn_result("cancelled"))
    patches2 = _patch_infra()
    (tmp_path / "stopped-cannot-answer").mkdir()
    _, stopped_tools = await _start_switchboard_and_capture_tools(
        _make_switchboard_dir(tmp_path / "stopped-cannot-answer"), patches2
    )
    with patch("butlers.core.dashboard_turns.claim_dead_letter", stopped_claim):
        _set_dashboard_routing_context(dashboard_message_id="d1d1d1d1-0000-7000-8000-000000000001")
        try:
            stopped = await stopped_tools["cannot_answer"](
                question_summary="hi?", scope_checked=["finance"], reason="no owner"
            )
        finally:
            _clear_routing_context()

    assert stopped == {"status": "cancelled", "answered": False, "cancelled": True}


# ---------------------------------------------------------------------------
# Lane exclusivity across all four terminal tools (bu-0ynlk.2)
# ---------------------------------------------------------------------------


async def test_answer_question_refused_after_route_to_butler_claims_lane(tmp_path: Path) -> None:
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    mock_route = AsyncMock(return_value={"result": {"status": "accepted"}})

    _, tools = await _start_switchboard_and_capture_tools(
        butler_dir, patches, mock_route=mock_route
    )
    route_fn = tools["route_to_butler"]
    answer_fn = tools["answer_question"]

    _set_dashboard_routing_context()
    try:
        route_result = await route_fn(butler="relationship", prompt="hello")
        answer_result = await answer_fn(scope="domain", question="hi?", target="finance")
    finally:
        _clear_routing_context()

    assert route_result["status"] == "accepted"
    assert answer_result["status"] == "refused"
    assert answer_result["reason"] == "dashboard_lane_conflict"
    assert mock_route.await_count == 1


async def test_route_to_butler_refused_after_answer_question_claims_lane(tmp_path: Path) -> None:
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    mock_route = AsyncMock(return_value={"result": {"status": "accepted"}})

    _, tools = await _start_switchboard_and_capture_tools(
        butler_dir, patches, mock_route=mock_route
    )
    answer_fn = tools["answer_question"]
    route_fn = tools["route_to_butler"]

    _set_dashboard_routing_context()
    try:
        answer_result = await answer_fn(scope="domain", question="hi?", target="finance")
        route_result = await route_fn(butler="relationship", prompt="hello")
    finally:
        _clear_routing_context()

    assert answer_result["status"] == "accepted"
    assert route_result["status"] == "refused"
    assert route_result["reason"] == "dashboard_lane_conflict"
    assert "answer_question" in route_result["error"]
    assert mock_route.await_count == 1


async def test_route_to_butler_refused_after_cannot_answer_claims_lane(
    tmp_path: Path, monkeypatch
) -> None:
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    mock_route = AsyncMock(return_value={"result": {"status": "accepted"}})

    _, tools = await _start_switchboard_and_capture_tools(
        butler_dir, patches, mock_route=mock_route
    )
    cannot_answer_fn = tools["cannot_answer"]
    route_fn = tools["route_to_butler"]

    import butlers.tools.switchboard.dead_letter.capture as capture_mod

    monkeypatch.setattr(
        "butlers.api.conversations.conversation_reply_create",
        AsyncMock(return_value={"id": "msg-1"}),
    )
    monkeypatch.setattr(capture_mod, "capture_to_dead_letter", AsyncMock(return_value="dl-3"))

    _set_dashboard_routing_context()
    try:
        cannot_answer_result = await cannot_answer_fn(
            question_summary="hi?", scope_checked=["finance"], reason="no owner"
        )
        route_result = await route_fn(butler="relationship", prompt="hello")
    finally:
        _clear_routing_context()

    assert cannot_answer_result["status"] == "ok"
    assert route_result["status"] == "refused"
    assert route_result["reason"] == "dashboard_lane_conflict"
    assert "cannot_answer" in route_result["error"]
    mock_route.assert_not_awaited()


async def test_second_answer_question_call_in_same_turn_is_refused(tmp_path: Path) -> None:
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    mock_route = AsyncMock(return_value={"result": {"status": "accepted"}})

    _, tools = await _start_switchboard_and_capture_tools(
        butler_dir, patches, mock_route=mock_route
    )
    fn = tools["answer_question"]

    _set_dashboard_routing_context()
    try:
        first = await fn(scope="domain", question="hi?", target="finance")
        second = await fn(scope="domain", question="hi again?", target="health")
    finally:
        _clear_routing_context()

    assert first["status"] == "accepted"
    assert second["status"] == "refused"
    assert second["reason"] == "dashboard_lane_conflict"
    assert mock_route.await_count == 1


async def test_second_cannot_answer_call_in_same_turn_is_refused(
    tmp_path: Path, monkeypatch
) -> None:
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    _, tools = await _start_switchboard_and_capture_tools(butler_dir, patches)
    fn = tools["cannot_answer"]

    import butlers.tools.switchboard.dead_letter.capture as capture_mod

    fake_capture = AsyncMock(return_value="dl-4")
    monkeypatch.setattr(
        "butlers.api.conversations.conversation_reply_create",
        AsyncMock(return_value={"id": "msg-1"}),
    )
    monkeypatch.setattr(capture_mod, "capture_to_dead_letter", fake_capture)

    _set_dashboard_routing_context()
    try:
        first = await fn(question_summary="hi?", scope_checked=["finance"], reason="no owner")
        second = await fn(question_summary="hi?", scope_checked=["finance"], reason="no owner")
    finally:
        _clear_routing_context()

    assert first["status"] == "ok"
    assert second["status"] == "refused"
    assert second["reason"] == "dashboard_lane_conflict"
    assert fake_capture.await_count == 1


async def test_question_lane_tools_refuse_non_dashboard_sessions(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    mock_route = AsyncMock(return_value={"result": {"status": "accepted"}})

    _, tools = await _start_switchboard_and_capture_tools(
        butler_dir, patches, mock_route=mock_route
    )
    answer_fn = tools["answer_question"]
    cannot_answer_fn = tools["cannot_answer"]

    import butlers.tools.switchboard.dead_letter.capture as capture_mod

    fake_capture = AsyncMock(return_value="dl-1")
    monkeypatch.setattr(capture_mod, "capture_to_dead_letter", fake_capture)
    sensitive_question = "private-question-sentinel"

    answer = await answer_fn(scope="domain", question=sensitive_question, target="finance")
    decline = await cannot_answer_fn(
        question_summary=sensitive_question,
        scope_checked=["finance"],
        reason="No owning scope.",
    )
    captured = capsys.readouterr()

    assert answer["status"] == "error"
    assert answer["reason"] == "dashboard_context_required"
    assert decline["status"] == "error"
    assert decline["reason"] == "dashboard_context_required"
    mock_route.assert_not_awaited()
    fake_capture.assert_not_awaited()
    assert sensitive_question not in captured.err
