"""Switchboard serves the relays on its service listener and dispatches via route() (bu-2c1g7v)."""

from __future__ import annotations

import importlib
import uuid
from pathlib import Path
from typing import Any

import httpx
from starlette.applications import Starlette

import butlers.core.service_plane.wiring as wiring
from butlers.core.service_plane.registry import (
    CONTACTS_RESOLVE_CHANNEL,
    MEMORY_CATALOG_FETCH,
    QA_REPORT_FINDING,
)
from butlers.core.service_plane.workload_identity import DAEMON_HEADER, TOKEN_HEADER
from butlers.testing.fake_pool import fake_pool
from tests.core.test_service_plane_relay_endpoint import _FINDING, _LOOKUP, _up_token
from tests.core.test_service_plane_workload_identity import _verifier

_ROSTER = Path(__file__).resolve().parents[2] / "roster"


async def _post(
    monkeypatch,
    *,
    enabled: bool,
    name: str = QA_REPORT_FINDING,
    args: dict[str, Any] = _FINDING,
    daemon: str = "travel",
    pool: Any = "POOL",
) -> tuple[httpx.Response, list[dict[str, Any]]]:
    if enabled:
        monkeypatch.setenv("BUTLERS_SERVICE_PLANE_AUTH", "1")
    else:
        monkeypatch.delenv("BUTLERS_SERVICE_PLANE_AUTH", raising=False)
    monkeypatch.setattr(wiring, "cached_verifier", lambda audience: _verifier(audience))
    routed: list[dict[str, Any]] = []

    async def fake_route(pool: Any, butler: str, tool: str, args: dict[str, Any], **kw: Any):
        routed.append({"pool": pool, "butler": butler, "tool": tool, "args": args, **kw})
        return {"result": {"accepted": True}}

    # The package re-exports route() under the module's own name, so patch the module object.
    route_module = importlib.import_module("butlers.tools.switchboard.routing.route")
    monkeypatch.setattr(route_module, "route", fake_route)
    app = Starlette(routes=[wiring.build_switchboard_relay_route(pool, _ROSTER)])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
        response = await client.post(
            f"http://sb/internal/service-plane/relay/{name}",
            json={"args": args},
            headers={TOKEN_HEADER: _up_token(), DAEMON_HEADER: daemon},
        )
    return response, routed


async def test_the_roster_contacts_daemons_reach_relationship_through_route(monkeypatch) -> None:
    # The allowlist comes from the roster's [modules.contacts] declarations.
    response, routed = await _post(
        monkeypatch, enabled=True, name=CONTACTS_RESOLVE_CHANNEL, args=_LOOKUP, daemon="lifestyle"
    )
    assert response.status_code == 200
    assert routed == [
        {
            "pool": "POOL",
            "butler": "relationship",
            "tool": "contacts_resolve_channel",
            "args": _LOOKUP,
            "source_butler": f"relay:{CONTACTS_RESOLVE_CHANNEL}",
            "allow_stale": False,
        }
    ]

    refused, routed = await _post(
        monkeypatch, enabled=True, name=CONTACTS_RESOLVE_CHANNEL, args=_LOOKUP, daemon="finance"
    )
    assert refused.json() == {"status": "refused", "code": "caller_not_allowed"}
    assert routed == []


async def test_a_catalog_fetch_reads_the_owner_from_switchboards_catalog_row(monkeypatch) -> None:
    source_id = "22222222-2222-2222-2222-222222222222"
    pointer = {"source_schema": "health", "source_table": "facts", "source_id": source_id}
    pool = fake_pool(fetchrow={"source_butler": "health", "memory_type": "fact"})

    response, routed = await _post(
        monkeypatch,
        enabled=True,
        name=MEMORY_CATALOG_FETCH,
        args=pointer,
        daemon="finance",
        pool=pool,
    )

    assert response.status_code == 200
    assert [(r["butler"], r["tool"], r["args"]) for r in routed] == [
        ("health", "memory_get_service", {"memory_type": "fact", "memory_id": source_id})
    ]
    # One live, visible catalog row, read by Switchboard with the request's pointer.
    sql, *params = pool.fetchrow.await_args.args
    assert "public.memory_catalog" in sql and "invalid_at IS NULL" in sql
    assert params == ["health", "facts", uuid.UUID(source_id)]

    # A roster butler without the memory module is never a catalog target.
    pool = fake_pool(fetchrow={"source_butler": "qa", "memory_type": "fact"})
    refused, routed = await _post(
        monkeypatch,
        enabled=True,
        name=MEMORY_CATALOG_FETCH,
        args=pointer,
        daemon="finance",
        pool=pool,
    )
    assert refused.json() == {"status": "refused", "code": "target_not_allowed"}
    assert routed == []


async def test_on_the_qa_relay_reaches_qa_through_switchboard_route(monkeypatch) -> None:
    response, routed = await _post(monkeypatch, enabled=True)

    assert response.status_code == 200
    assert routed == [
        {
            "pool": "POOL",
            "butler": "qa",
            "tool": "report_finding",
            "args": {**_FINDING, "source_butler": "travel"},
            "source_butler": f"relay:{QA_REPORT_FINDING}",
            "allow_stale": True,
        }
    ]


async def test_switchboard_serves_relays_on_the_service_listener_and_never_the_session_one(
    monkeypatch,
) -> None:
    from fastmcp import FastMCP

    from butlers.control import SessionRefusalListener
    from butlers.daemon import ButlerDaemon

    monkeypatch.setenv("BUTLERS_SERVICE_PLANE_AUTH", "1")
    monkeypatch.setattr(wiring, "cached_verifier", lambda audience: _verifier(audience))
    path = f"/internal/service-plane/relay/{QA_REPORT_FINDING}"
    service = ButlerDaemon._build_mcp_http_app(
        FastMCP("switchboard"),
        butler_name="switchboard",
        service_relay_route=wiring.build_switchboard_relay_route("POOL", _ROSTER),
    )
    listener = SessionRefusalListener(host="127.0.0.1", port=0)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=service), base_url="http://service"
    ) as current:
        # Unauthenticated: the relay answers with its own typed refusal.
        refused = await current.post(path, json={"args": _FINDING})
        assert refused.status_code == 403
        assert refused.json() == {"status": "refused", "code": "caller_unauthenticated"}
        await listener.start()
        try:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{listener.port}") as session:
                assert (await session.post(path, json={"args": _FINDING})).status_code == 404
        finally:
            await listener.close()


async def test_off_the_route_refuses_and_routes_nothing(monkeypatch) -> None:
    response, routed = await _post(monkeypatch, enabled=False)

    assert response.status_code == 404
    assert routed == []
