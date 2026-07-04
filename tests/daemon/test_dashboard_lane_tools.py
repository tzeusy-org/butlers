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

import pytest

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

    mock_pool = AsyncMock()
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
) -> None:
    from butlers.core.routing_context import _routing_ctx_var

    _routing_ctx_var.set(
        {
            "source_metadata": {"channel": "dashboard", "identity": "dashboard:operator"},
            "request_context": None,
            "request_id": "unknown",
            "dashboard_context": {
                "conversation_id": conversation_id,
                "page_context": page_context,
            },
        }
    )


def _clear_routing_context() -> None:
    from butlers.core.routing_context import _routing_ctx_var

    _routing_ctx_var.set(None)


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
