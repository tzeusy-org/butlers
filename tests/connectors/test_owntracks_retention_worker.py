"""The connector's retention worker: off by default, fail closed (bu-s11n0s.7 S4b-3b).

The worker asks Chronicler for ready grants through the ``location_retention.grants_ready``
named relay and executes each. Anything it cannot trust is no batch: an unreachable relay, a
refusal, a missing workload token, a malformed reply. A grant whose rows no longer match is
skipped and the others still run.
"""

from __future__ import annotations

import importlib
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from starlette.applications import Starlette

from butlers.connectors.filtered_event_buffer import _replay_one
from butlers.connectors.owntracks_retention import (
    NOT_REPLAYABLE,
    WITHHELD_TEXT,
    BatchReceipt,
    GrantMismatch,
    RetentionWorkerConfig,
    fetch_ready_grants,
    run_retention_once,
)
from butlers.core.service_plane import wiring
from butlers.core.service_plane.registry import (
    CONNECTOR_OWNTRACKS_WORKLOAD,
    LOCATION_RETENTION_GRANTS_READY,
)
from butlers.core.service_plane.workload_identity import (
    DAEMON_HEADER,
    RELAY_AUDIENCE,
    TOKEN_HEADER,
)
from butlers.testing.migration import load_migration

_DIGEST = "v1:" + "c" * 64
_ROSTER = Path(__file__).resolve().parents[2] / "roster"


def _grant() -> dict[str, Any]:
    return {
        "grant_id": str(uuid4()),
        "cutoff": "2026-03-01T00:00:00+00:00",
        "items": [
            {
                "raw_source_id": str(uuid4()),
                "logical_source_key": f"k-{uuid4()}",
                "content_digest": _DIGEST,
            }
        ],
    }


class _Pool:
    """Hands out a sentinel connection; the injected executor never touches it."""

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


class _Executor:
    def __init__(self, mismatched: set[str] = frozenset()) -> None:
        self.executed: list[str] = []
        self.mismatched = mismatched

    async def __call__(self, conn, grant) -> BatchReceipt:
        if str(grant.grant_id) in self.mismatched:
            raise GrantMismatch("changed")
        self.executed.append(str(grant.grant_id))
        return BatchReceipt(grant.grant_id, len(grant.items), len(grant.items), 0, False)


def _fetching(result: Any = None, *, error: Exception | None = None):
    async def fetch() -> Any:
        if error is not None:
            raise error
        return result

    return fetch


def test_the_worker_is_off_unless_explicitly_enabled(monkeypatch) -> None:
    monkeypatch.delenv("OWNTRACKS_RAW_RETENTION_ENABLED", raising=False)
    assert not RetentionWorkerConfig.from_env().enabled
    for value in ("", "0", "false", "no", "yes please", "TRUE "):
        monkeypatch.setenv("OWNTRACKS_RAW_RETENTION_ENABLED", value)
        assert RetentionWorkerConfig.from_env().enabled is (value.strip().lower() == "true")
    monkeypatch.setenv("OWNTRACKS_RAW_RETENTION_ENABLED", "true")
    assert RetentionWorkerConfig.from_env().enabled


async def test_each_offered_grant_is_executed() -> None:
    grants = [_grant(), _grant()]
    executor = _Executor()
    receipts = await run_retention_once(_Pool(), _fetching({"grants": grants}), execute=executor)
    assert executor.executed == [g["grant_id"] for g in grants]
    assert [str(r.grant_id) for r in receipts] == executor.executed


async def test_a_mismatched_grant_is_skipped_and_the_rest_still_run() -> None:
    bad, good = _grant(), _grant()
    executor = _Executor(mismatched={bad["grant_id"]})
    receipts = await run_retention_once(
        _Pool(), _fetching({"grants": [bad, good]}), execute=executor
    )
    assert executor.executed == [good["grant_id"]]
    assert len(receipts) == 1


@pytest.mark.parametrize(
    "reply",
    [
        None,
        [],
        {"grants": "nope"},
        {"grants": [_grant()], "extra": 1},
        {"grants": [_grant(), {"grant_id": "x"}]},
        {"grants": [{**_grant(), "lat": 1.3}]},
    ],
    ids=["none", "list", "grants-not-list", "extra-key", "one-malformed", "coordinates"],
)
async def test_a_malformed_reply_executes_nothing(reply) -> None:
    executor = _Executor()
    assert await run_retention_once(_Pool(), _fetching(reply), execute=executor) == []
    assert executor.executed == []


@pytest.mark.parametrize(
    "error", [TimeoutError(), OSError("refused"), RuntimeError("boom")], ids=str
)
async def test_an_unreachable_relay_executes_nothing(error) -> None:
    executor = _Executor()
    assert await run_retention_once(_Pool(), _fetching(error=error), execute=executor) == []
    assert executor.executed == []


def _relay(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


async def test_the_fetch_presents_the_owntracks_daemon_and_its_workload_token(
    tmp_path: Path,
) -> None:
    (tmp_path / RELAY_AUDIENCE).write_text("token-value\n")
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["daemon"] = request.headers[DAEMON_HEADER]
        seen["token"] = request.headers[TOKEN_HEADER]
        seen["body"] = request.content
        return httpx.Response(200, json={"status": "ok", "result": {"grants": []}})

    result = await fetch_ready_grants(
        switchboard_url="http://butlers-control:41100/sse",
        token_dir=tmp_path,
        transport=_relay(handler),
    )
    assert result == {"grants": []}
    assert seen["daemon"] == "owntracks"
    assert seen["token"] == "token-value"
    assert seen["url"].startswith("http://butlers-control:41100/")
    assert LOCATION_RETENTION_GRANTS_READY in seen["url"]
    assert seen["body"] == b'{"args":{}}'


async def test_without_a_workload_token_nothing_is_fetched_or_executed(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"status": "ok", "result": {"grants": [_grant()]}})

    async def fetch() -> Any:
        return await fetch_ready_grants(
            switchboard_url="http://butlers-control:41100/sse",
            token_dir=tmp_path,
            transport=_relay(handler),
        )

    executor = _Executor()
    assert await run_retention_once(_Pool(), fetch, execute=executor) == []
    assert calls == [] and executor.executed == []


async def test_a_refusal_executes_nothing(tmp_path: Path) -> None:
    (tmp_path / RELAY_AUDIENCE).write_text("t")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"status": "refused", "code": "caller_not_allowed"})

    async def fetch() -> Any:
        return await fetch_ready_grants(
            switchboard_url="http://butlers-control:41100/sse",
            token_dir=tmp_path,
            transport=_relay(handler),
        )

    executor = _Executor()
    assert await run_retention_once(_Pool(), fetch, execute=executor) == []
    assert executor.executed == []


@pytest.mark.parametrize(
    "case",
    ["confirmed", "uncertain", "rejected", "malformed", "wrong-workload", "switch-off"],
)
async def test_the_mounted_ready_grant_relay_drives_the_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """Keep the real HTTP relay, route classification and strict worker in the loop."""
    grant = _grant()
    token = "opaque-test-token"
    (tmp_path / RELAY_AUDIENCE).write_text(token)
    if case == "switch-off":
        monkeypatch.delenv("BUTLERS_SERVICE_PLANE_AUTH", raising=False)
    else:
        monkeypatch.setenv("BUTLERS_SERVICE_PLANE_AUTH", "1")
    workload = "butlers-up" if case == "wrong-workload" else CONNECTOR_OWNTRACKS_WORKLOAD
    verifier = SimpleNamespace(verify=AsyncMock(return_value=workload))
    monkeypatch.setattr(wiring, "cached_verifier", lambda audience: verifier)

    routing = importlib.import_module("butlers.tools.switchboard.routing.route")
    monkeypatch.delenv("BUTLERS_RECEIVER_DERIVED_ROUTE_CUTOVER", raising=False)
    monkeypatch.setattr(
        routing,
        "resolve_routing_target",
        AsyncMock(return_value=({"endpoint_url": "http://peer.invalid/mcp"}, None)),
    )
    monkeypatch.setattr(routing, "_log_routing", AsyncMock())
    monkeypatch.setattr(routing, "_record_confirmed_route", AsyncMock())
    grants = ["malformed"] if case == "malformed" else [grant]
    content = [] if case == "uncertain" else [SimpleNamespace(text=json.dumps({"grants": grants}))]
    peer_result = SimpleNamespace(is_error=(case == "rejected"), content=content)
    peer = AsyncMock(return_value=peer_result)
    monkeypatch.setattr(routing, "_call_tool_with_router_client", peer)

    app = Starlette(routes=[wiring.build_switchboard_relay_route(_Pool(), _ROSTER)])

    async def fetch() -> Any:
        return await fetch_ready_grants(
            switchboard_url="http://butlers-control:41100/sse",
            token_dir=tmp_path,
            transport=httpx.ASGITransport(app=app),
        )

    executor = _Executor()
    receipts = await run_retention_once(_Pool(), fetch, execute=executor)

    if case == "switch-off":
        verifier.verify.assert_not_awaited()
    else:
        verifier.verify.assert_awaited_once_with(token)
    if case in {"wrong-workload", "switch-off"}:
        peer.assert_not_awaited()
    else:
        peer.assert_awaited_once_with(
            "http://peer.invalid/mcp", "location_retention_grants_ready_service", {}
        )
    expected = [grant["grant_id"]] if case == "confirmed" else []
    assert executor.executed == expected
    assert [str(receipt.grant_id) for receipt in receipts] == expected


def test_the_withheld_copy_matches_core_345s_trigger() -> None:
    """The deleter and the insert trigger leave the same withheld copy (S4c-4)."""
    migration = load_migration(
        Path(__file__).parents[2]
        / "alembic/versions/core/core_345_filtered_events_withhold_forgotten.py"
    )
    assert (migration._WITHHELD, migration._NOT_REPLAYABLE) == (WITHHELD_TEXT, NOT_REPLAYABLE)


async def test_a_withheld_copy_is_refused_by_the_replay_drain() -> None:
    """A withheld copy is never submitted as a hollow location event."""
    submitted: list[dict[str, Any]] = []

    async def submit(envelope: dict[str, Any]) -> None:
        submitted.append(envelope)

    withheld = {
        "event": {"external_event_id": "1:location"},
        "payload": {"raw": None, "normalized_text": WITHHELD_TEXT},
        "replay_unavailable": NOT_REPLAYABLE,
    }
    row = {"id": uuid4(), "full_payload": json.dumps(withheld)}
    status, detail = await _replay_one(row, submit, logging.getLogger(__name__), 5.0)
    assert (status, detail) == ("replay_failed", f"not replayable: {NOT_REPLAYABLE}")
    assert submitted == []
