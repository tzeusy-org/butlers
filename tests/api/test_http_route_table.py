"""Runtime HTTP admission and OpenAPI registration, including non-JSON routes.

This is an executing perimeter check, not success/source-fault coverage for every
domain operation. The snapshot supplies the closed registration census; requests
are generated from the routes on the leased production application itself.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute
from starlette.routing import Mount, Route

from butlers.api.owner_auth.http import CEREMONY_ROUTES, PREFIX, OwnerAuthMiddleware
from butlers.api.readiness import CHECK_KEYS

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

_PROBES = frozenset({"/health", "/api/health", "/ready"})
_FRAMEWORK = frozenset({"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"})
_UUID = "12345678-1234-4678-9234-123456789abc"
_ROSTER_ROUTERS = frozenset(
    path.parents[1].name
    for path in (Path(__file__).resolve().parents[2] / "roster").glob("*/api/router.py")
)


def _routes(routes):
    for route in routes:
        original = getattr(route, "original_router", None)
        if original is not None:
            yield from _routes(original.routes)
        else:
            yield route


def _identity(route):
    # The registration snapshot prints paths with repr, including the empty mount path.
    return (
        type(route).__name__,
        tuple(sorted(getattr(route, "methods", ()) or ())),
        repr(route.path),
    )


def _snapshot_inventory():
    rows = []
    for line in Path(__file__).with_name("route_snapshot.txt").read_text().splitlines():
        if not line.startswith("ROUTE "):
            continue
        match = re.match(r"ROUTE (\S+) (\S*) ('[^']*') ", line)
        assert match is not None, line
        kind, methods, path_repr = match.groups()
        rows.append((kind, tuple(methods.split(",")) if methods else (), path_repr))
    assert rows, "The complete registration survivor must not be empty"
    return Counter(rows)


def _path(route):
    # No domain handler runs on denied requests, but use real converter values so
    # routing regressions cannot hide behind a malformed path.
    values = {}
    for name, converter in route.param_convertors.items():
        kind = type(converter).__name__
        values[name] = (
            1 if kind == "IntegerConvertor" else 1.0 if kind == "FloatConvertor" else _UUID
        )
    return route.path_format.format(**values)


def _error(response, status, code):
    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str)
    assert body["error"]["butler"] is None


async def _unread_body():
    pytest.fail("Denied HTTP request consumed its body")
    yield b"unreachable"  # pragma: no cover - declares an async byte iterator


class _DomainObserver:
    def __init__(self, app):
        self.app = app
        self.entered = []

    async def __call__(self, scope, receive, send):
        self.entered.append((scope["type"], scope["path"]))
        await self.app(scope, receive, send)


async def test_running_route_table_admission_and_openapi(
    api, api_db, production_api_app, subtests, record_property
):
    app = production_api_app
    routes = list(_routes(app.routes))
    assert Counter(map(_identity, routes)) == _snapshot_inventory()
    assert isinstance(routes[-1], Mount) and routes[-1].name == "frontend"
    assert {name for name, _ in app.state.butler_routers} == _ROSTER_ROUTERS, (
        "Roster discovery must not silently shrink the live application"
    )

    # Build middleware and prove that this same application reads the clone.
    await api_db.pool.execute(
        "INSERT INTO state(key,value) VALUES('route-table-control', $1)", {"admitted": True}
    )
    control_path = "/api/butlers/switchboard/state/route-table-control"
    assert (await api.get(control_path)).json()["data"]["value"] == {"admitted": True}
    boundary = app.middleware_stack
    while not isinstance(boundary, OwnerAuthMiddleware):
        boundary = boundary.app
    observer = _DomainObserver(boundary.app)
    boundary.app = observer
    try:
        assert (await api.get(control_path)).status_code == 200
        assert observer.entered == [("http", control_path)]
        observer.entered.clear()
        api.headers.pop("X-API-Key")
        spec = app.openapi()
        requests = 0
        for route in routes:
            with subtests.test(route=_identity(route)):
                if isinstance(route, Mount):
                    assert route.path == ""
                    response = await api.get("/")
                    assert response.status_code == 200 and response.text == "synthetic dashboard"
                    observer.entered.clear()
                    continue
                if isinstance(route, APIWebSocketRoute):
                    sent = []

                    async def no_body():
                        pytest.fail("Unauthenticated websocket reached receive")

                    async def capture(message, sent=sent):
                        sent.append(message)

                    await app(
                        {
                            "type": "websocket",
                            "path": _path(route),
                            "raw_path": _path(route).encode(),
                            "scheme": "wss",
                            "query_string": b"",
                            "headers": [],
                            "client": ("127.0.0.1", 1),
                            "server": ("butlers.example.test", 443),
                            "subprotocols": [],
                            "state": {},
                        },
                        no_body,
                        capture,
                    )
                    assert sent == [{"type": "websocket.close", "code": 4401}]
                    assert observer.entered == []
                    continue
                assert isinstance(route, Route), f"Unclassified running route: {route!r}"
                if not isinstance(route, APIRoute):
                    assert route.path in _FRAMEWORK
                    assert set(route.methods) == {"GET", "HEAD"}
                    response = await api.get(route.path)
                    assert response.status_code == 200
                    expected_media = (
                        "application/json" if route.path == "/openapi.json" else "text/html"
                    )
                    assert response.headers["content-type"].startswith(expected_media)
                    if route.path == "/openapi.json":
                        assert response.json() == spec
                    observer.entered.clear()
                    continue
                if route.include_in_schema:
                    for method in route.methods:
                        operation = spec["paths"][route.path_format][method.lower()]
                        assert operation["operationId"] == route.unique_id
                        assert str(route.status_code or 200) in operation["responses"]
                else:
                    assert route.path_format not in spec["paths"], (
                        "Hidden route unexpectedly published"
                    )
                if route.path in _PROBES:
                    response = await api.get(route.path)
                    assert response.status_code == 503  # Lifespan has not been booted.
                    assert response.json() == (
                        {"status": "starting"}
                        if route.path != "/ready"
                        else {
                            "ready": False,
                            "checks": dict.fromkeys(CHECK_KEYS, False),
                        }
                    )
                    observer.entered.clear()
                else:
                    assert route.path.startswith("/api/"), (
                        "A new perimeter needs an explicit policy"
                    )
                    for method in route.methods:
                        response = await api.request(method, _path(route), content=_unread_body())
                        _error(response, 401, "UNAUTHORIZED")
                        assert observer.entered == [], (
                            "Auth failure entered domain/audit/provider handling"
                        )
                        requests += 1
                        service = app.state.owner_auth_service
                        app.state.owner_auth_service = None
                        try:
                            unavailable = await api.request(
                                method,
                                _path(route),
                                headers={"X-API-Key": app.state.owner_auth_config.api_key},
                                content=_unread_body(),
                            )
                            _error(unavailable, 503, "AUTH_UNAVAILABLE")
                            assert observer.entered == []
                        finally:
                            app.state.owner_auth_service = service
                # A probe exemption must never widen to another method.
                for method in ("HEAD", "OPTIONS"):
                    response = await api.request(method, _path(route))
                    assert response.status_code == 401
                    assert observer.entered == []
        record_property("runtime_route_entries", len(routes))
        record_property("denied_registered_http_operations", requests)
        # Outer-ASGI ceremony operations are deliberately absent from app.routes/OpenAPI.
        assert len(CEREMONY_ROUTES) == 13
        for method, suffix in CEREMONY_ROUTES:
            path = PREFIX + suffix
            assert path not in spec["paths"]
            response = await api.request(method, path)
            assert response.headers["cache-control"] == "no-store"
            assert observer.entered == []
    finally:
        boundary.app = observer.app
