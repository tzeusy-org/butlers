"""Central HTTP security boundary; real persistence and crypto have separate lanes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, Request

from butlers.api.app import create_app
from butlers.api.owner_auth.config import OwnerAuthConfig
from butlers.api.owner_auth.http import OwnerAuthMiddleware
from butlers.api.owner_auth.service import AuthError
from butlers.api.owner_control import require_dashboard_owner_control

pytestmark = pytest.mark.unit
ORIGIN = "https://butlers.example.test"
CONFIG = OwnerAuthConfig(ORIGIN, "butlers.example.test", "synthetic-key", "test", ("127.0.0.1",))
COOKIE = "__Host-butlers-test-owner=session-sentinel"


def app_with_boundary():
    app = FastAPI()
    service = SimpleNamespace()

    async def authorize(session_token=None, api_key=None, csrf_token=None, unsafe=False):
        if api_key is not None:
            if api_key != "synthetic-key":
                raise AuthError("UNAUTHORIZED")
        elif session_token != "session-sentinel":
            raise AuthError("UNAUTHORIZED")
        elif unsafe and csrf_token != "csrf-sentinel":
            raise AuthError("FORBIDDEN")
        return SimpleNamespace(
            method="header" if api_key is not None else "cookie", expires_at=None
        )

    service.authorize = AsyncMock(side_effect=authorize)
    service.status = AsyncMock(
        return_value={"state": "configured_key", "authenticated": False, "session_expires_at": None}
    )
    service.key_session = AsyncMock(
        return_value=SimpleNamespace(
            token="issued-session",
            data={
                "csrf_token": "issued-csrf",
                "csrf_expires_at": "2030-01-01T00:30:00Z",
                "session_expires_at": "2030-01-01T12:00:00Z",
            },
        )
    )
    service.context = AsyncMock(
        return_value=SimpleNamespace(
            token="preauth-sentinel",
            data={"csrf_token": "pre-csrf", "csrf_expires_at": "2030-01-01T00:05:00Z"},
        )
    )
    service.revoke = AsyncMock()
    service.csrf = AsyncMock(
        return_value={"csrf_token": "reload-csrf", "csrf_expires_at": "2030-01-01T00:30:00Z"}
    )
    app.state.owner_auth_service = service
    app.add_middleware(OwnerAuthMiddleware, config=CONFIG)

    @app.api_route("/api/private", methods=["GET", "POST", "OPTIONS"])
    async def private(request: Request):
        require_dashboard_owner_control(request)
        return {"private": "domain-sentinel"}

    return app, service


async def test_no_configuration_or_store_never_disables_auth():
    app = create_app(api_key="")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        denied = await client.get("/api/butlers")
        status = await client.get("/api/auth/owner/status")
        assert denied.status_code == 503
        assert status.json() == {
            "data": {"state": "unavailable", "authenticated": False, "session_expires_at": None}
        }
        assert status.headers["cache-control"] == "no-store"


async def test_header_cookie_and_csrf_precedence():
    app, _ = app_with_boundary()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        assert (await client.get("/api/private")).status_code == 401
        for key in ("wrong", ""):
            assert (
                await client.get("/api/private", headers={"Cookie": COOKIE, "X-API-Key": key})
            ).status_code == 401
        assert (await client.get("/api/private", headers={"Cookie": COOKIE})).status_code == 200
        assert (
            await client.post("/api/private", headers={"Cookie": COOKIE, "Origin": ORIGIN})
        ).status_code == 403
        assert (
            await client.post(
                "/api/private",
                headers={"Cookie": COOKIE, "Origin": ORIGIN, "X-CSRF-Token": "csrf-sentinel"},
            )
        ).status_code == 200
        assert (
            await client.post("/api/private", headers={"X-API-Key": "synthetic-key"})
        ).status_code == 200


async def test_closed_exemptions_and_exact_health_methods():
    app = create_app(api_key="")
    app.state.ready = True
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        for path in ("/health", "/api/health"):
            assert (await client.get(path)).status_code == 200
            assert (await client.post(path)).status_code == 503
        for path in ("/api/health/briefing", "/api/auth/owner/unknown", "/api/auth/owner/status/"):
            assert (await client.get(path)).status_code == 503
        assert (await client.options("/api/butlers")).status_code == 503


async def test_untrusted_proxy_cannot_forge_https():
    app, service = app_with_boundary()
    headers = {"Cookie": COOKIE, "X-Forwarded-Host": CONFIG.rp_id, "X-Forwarded-Proto": "https"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("192.0.2.1", 1234)), base_url="http://proxy"
    ) as client:
        assert (await client.get("/api/private", headers=headers)).status_code == 503
    service.authorize.assert_not_called()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)), base_url="http://proxy"
    ) as client:
        assert (await client.get("/api/private", headers=headers)).status_code == 200
        assert (
            await client.get(
                "/api/private", headers={**headers, "X-Forwarded-Host": "evil.example.test"}
            )
        ).status_code == 503


async def test_origin_rejection_precedes_body_and_store_access():
    app, service = app_with_boundary()
    sent = []

    async def unreadable():
        pytest.fail("Cross-origin request body was consumed")

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "method": "POST",
        "scheme": "https",
        "path": "/api/auth/owner/session",
        "raw_path": b"/api/auth/owner/session",
        "query_string": b"",
        "headers": [(b"host", b"butlers.example.test"), (b"origin", b"https://evil.example.test")],
        "client": ("127.0.0.1", 1234),
        "server": ("butlers.example.test", 443),
        "http_version": "1.1",
    }

    async def send(message):
        sent.append(message)

    await app(scope, unreadable, send)
    assert sent[0]["status"] == 403
    service.key_session.assert_not_called()
    service.authorize.assert_not_called()


async def test_issuance_allowlist_cookie_attributes_and_no_general_audit(caplog):
    app, service = app_with_boundary()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.post(
            "/api/auth/owner/session",
            json={"api_key": "body-key-sentinel"},
            headers={"Origin": ORIGIN},
        )
    service.key_session.assert_awaited_once_with("body-key-sentinel")
    assert set(response.json()["data"]) == {"csrf_token", "csrf_expires_at", "session_expires_at"}
    cookie = response.headers.get_list("set-cookie")[0]
    for part in (
        "__Host-butlers-test-owner=issued-session",
        "HttpOnly",
        "Secure",
        "SameSite=strict",
        "Path=/",
        "Max-Age=43200",
    ):
        assert part in cookie
    assert "Domain=" not in cookie
    assert response.headers["cache-control"] == "no-store"
    assert "body-key-sentinel" not in response.text + caplog.text


async def test_closed_json_and_raw_body_caps():
    app, service = app_with_boundary()
    headers = {"Origin": ORIGIN, "Content-Type": "application/json"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        for raw in ('{"api_key":"a","extra":"b"}', '{"api_key":"a","api_key":"b"}', "[]", "{"):
            response = await client.post("/api/auth/owner/session", content=raw, headers=headers)
            assert response.status_code == 400
        assert (
            await client.post("/api/auth/owner/session", content=b"x" * 4097, headers=headers)
        ).status_code == 413
        assert (
            await client.post("/api/auth/owner/context?token=private", json={}, headers=headers)
        ).status_code == 400
    service.key_session.assert_not_called()
    service.context.assert_not_called()


async def test_csrf_reload_requires_browser_fetch_metadata():
    app, service = app_with_boundary()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        assert (
            await client.get("/api/auth/owner/csrf", headers={"Cookie": COOKIE})
        ).status_code == 403
        response = await client.get(
            "/api/auth/owner/csrf",
            headers={
                "Cookie": COOKIE,
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Dest": "empty",
            },
        )
        assert response.status_code == 200
    service.csrf.assert_awaited_once_with("session-sentinel")


def test_origin_identity_is_strict_and_secret_repr_is_safe(monkeypatch):
    monkeypatch.setenv("DASHBOARD_AUTH_DEPLOYMENT", "test")
    for origin, rp in (
        ("http://butlers.example.test", "butlers.example.test"),
        (ORIGIN + "/", "butlers.example.test"),
        (ORIGIN + ":444", "butlers.example.test"),
        (ORIGIN, "example.test"),
        ("https://127.0.0.1", "127.0.0.1"),
    ):
        monkeypatch.setenv("DASHBOARD_AUTH_ORIGIN", origin)
        monkeypatch.setenv("DASHBOARD_AUTH_RP_ID", rp)
        assert not OwnerAuthConfig.from_env("key-sentinel").origin_valid
    monkeypatch.setenv("DASHBOARD_AUTH_ORIGIN", ORIGIN)
    monkeypatch.setenv("DASHBOARD_AUTH_RP_ID", "butlers.example.test")
    config = OwnerAuthConfig.from_env("key-sentinel")
    assert config.origin_valid
    assert "key-sentinel" not in repr(config)


async def test_all_mounted_api_routes_fail_before_domain_when_auth_store_unavailable():
    app = create_app(api_key="synthetic-key")
    inventory = set()

    def walk(routes):
        for route in routes:
            original = getattr(route, "original_router", None)
            if original is not None:
                walk(original.routes)
            elif getattr(route, "path", "").startswith("/api/"):
                for method in getattr(route, "methods", ()) or ():
                    inventory.add((method, route.path))

    walk(app.routes)
    assert len(inventory) > 500
    assert ("GET", "/api/health/briefing") in inventory
    assert ("POST", "/api/settings/models/verify-all") in inventory
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        for method, path in sorted(inventory):
            if method == "GET" and path == "/api/health":
                continue
            response = await client.request(method, path, headers={"X-API-Key": "synthetic-key"})
            assert response.status_code == 503, (method, path, response.status_code)
            assert response.json()["error"]["code"] == "AUTH_UNAVAILABLE"


async def test_sse_stops_before_next_private_chunk_after_revocation():
    from starlette.responses import StreamingResponse

    app, service = app_with_boundary()

    async def stream():
        yield b"data: first-public-test-event\n\n"
        service.authorize.side_effect = AuthError("UNAUTHORIZED")
        yield b"data: REVOKED_PRIVATE_SENTINEL\n\n"

    @app.get("/api/stream-test")
    async def stream_endpoint():
        return StreamingResponse(stream(), media_type="text/event-stream")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.get("/api/stream-test", headers={"Cookie": COOKIE})
    assert b"first-public-test-event" in response.content
    assert b"REVOKED_PRIVATE_SENTINEL" not in response.content


async def test_oauth_state_is_provider_scoped_and_never_owner_authority():
    from butlers.api.routers.oauth import _state_store, _store_state

    app, service = app_with_boundary()
    _store_state("synthetic-oauth-state", provider="google")

    @app.get("/api/oauth/{provider}/callback")
    async def callback(request: Request, provider: str):
        assert request.state.oauth_callback_authenticated is True
        assert getattr(request.state, "owner_authority", None) is None
        return {"callback": provider}

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as client:
            assert (
                await client.get("/api/oauth/spotify/callback?state=synthetic-oauth-state")
            ).status_code == 401
            assert (
                await client.get("/api/oauth/google/callback?state=synthetic-oauth-state")
            ).status_code == 200
            assert (await client.get("/api/private?state=synthetic-oauth-state")).status_code == 401
    finally:
        _state_store.pop("synthetic-oauth-state", None)
