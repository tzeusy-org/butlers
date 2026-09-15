"""Owner authentication before body buffering, domain access, audit and CORS.

The closed ceremony surface terminates here. Its bodies never enter general
request logging, validation exceptions, or domain mutation audit middleware.
"""

from __future__ import annotations

import asyncio
import hmac
import json

import anyio
from starlette.requests import ClientDisconnect, HTTPConnection, Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from butlers.api.owner_auth.config import OwnerAuthConfig
from butlers.api.owner_auth.context import in_http_request, verified_http_principal
from butlers.api.owner_auth.service import AuthError

PREFIX = "/api/auth/owner"
CEREMONY_ROUTES = {
    ("GET", "/status"): 0,
    ("POST", "/context"): 1024,
    ("POST", "/registration/intent"): 1024,
    ("POST", "/registration/options"): 1024,
    ("POST", "/registration/finish"): 65536,
    ("POST", "/login/options"): 1024,
    ("POST", "/login/finish"): 16384,
    ("POST", "/ceremony/cancel"): 1024,
    ("POST", "/session"): 4096,
    ("GET", "/csrf"): 0,
    ("DELETE", "/session"): 0,
    ("DELETE", "/sessions"): 0,
}
_NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
_SAFE = {"GET", "HEAD", "OPTIONS"}


def _unavailable() -> AuthError:
    return AuthError("AUTH_UNAVAILABLE")


def _forbidden() -> AuthError:
    return AuthError("FORBIDDEN")


def _bad_request() -> AuthError:
    return AuthError("INVALID_REQUEST")


def _response(data: dict, status: int = 200) -> JSONResponse:
    return JSONResponse({"data": data}, status_code=status, headers=_NO_STORE)


def _error(exc: AuthError) -> JSONResponse:
    headers = dict(_NO_STORE)
    if exc.status_code == 429:
        headers["Retry-After"] = "60"
    return JSONResponse(
        {"error": {"code": exc.code, "message": exc.message, "butler": None}},
        status_code=exc.status_code,
        headers=headers,
    )


def _fields(body: dict, required: dict[str, type]) -> None:
    if set(body) != set(required) or any(
        type(body[key]) is not kind for key, kind in required.items()
    ):
        raise _bad_request()


def _single_headers(connection: HTTPConnection) -> None:
    for name in (
        "host",
        "origin",
        "x-api-key",
        "x-csrf-token",
        "content-type",
        "content-length",
        "x-forwarded-proto",
        "x-forwarded-host",
    ):
        values = connection.headers.getlist(name)
        if len(values) > 1 or (values and len(values[0]) > 8192):
            raise _bad_request()


def trusted_https(connection: HTTPConnection, config: OwnerAuthConfig) -> bool:
    """Use raw ASGI peer identity; Uvicorn proxy-header rewriting is disabled."""
    if not config.origin_valid:
        return False
    if connection.scope.get("scheme") in {"https", "wss"}:
        return connection.headers.get("host") == config.rp_id
    peer = connection.scope.get("client")
    return bool(
        peer
        and peer[0] in config.trusted_proxy_peers
        and connection.headers.get("x-forwarded-proto") == "https"
        and connection.headers.get("x-forwarded-host") == config.rp_id
    )


def _cookie(connection: HTTPConnection, name: str) -> str | None:
    # Reject duplicate cookie identities rather than selecting one ambiguously.
    values = [
        part.strip().partition("=")
        for raw in connection.headers.getlist("cookie")
        for part in raw.split(";")
    ]
    if sum(key == name for key, _, _ in values) > 1:
        raise _bad_request()
    token = connection.cookies.get(name)
    if token and len(token) > 256:
        raise _bad_request()
    return token


async def _body(request: Request, limit: int) -> dict:
    if request.scope.get("query_string") or request.headers.get("content-encoding"):
        raise _bad_request()
    if limit:
        if (
            request.headers.get("content-type", "").split(";")[0].strip().lower()
            != "application/json"
        ):
            raise _bad_request()
    raw = bytearray()
    try:
        async with asyncio.timeout(5):
            async for chunk in request.stream():
                if len(raw) + len(chunk) > limit:
                    raise AuthError("REQUEST_TOO_LARGE")
                raw.extend(chunk)
        if not limit:
            return {}

        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError
                result[key] = value
            return result

        body = json.loads(raw, object_pairs_hook=unique)
        if type(body) is not dict:
            raise ValueError
        return body
    except AuthError:
        raise
    except (ValueError, UnicodeError, TimeoutError, RecursionError, ClientDisconnect):
        raise _bad_request() from None


class OwnerAuthMiddleware:
    def __init__(self, app: ASGIApp, config: OwnerAuthConfig) -> None:
        self.app = app
        self.config = config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        connection = HTTPConnection(scope)
        method = scope.get("method", "GET")
        path = scope["path"]
        public_probe = method == "GET" and path in {"/health", "/api/health"}
        if scope["type"] == "http" and (
            public_probe or not (path == "/api" or path.startswith("/api/") or path == "/health")
        ):
            await self.app(scope, receive, send)
            return
        request_context = in_http_request.set(True)
        principal_context = verified_http_principal.set(None)
        try:
            _single_headers(connection)
            service = getattr(scope["app"].state, "owner_auth_service", None)
            route = (method, path.removeprefix(PREFIX)) if path.startswith(PREFIX) else None
            if scope["type"] == "http" and route in CEREMONY_ROUTES:
                response = await self._ceremony(Request(scope, receive), service, route)
                await response(scope, receive, send)
                return
            # Connector callback authority remains exact, independently verified,
            # and does not become a dashboard-owner principal or session.
            if scope["type"] == "http":
                from butlers.api.middleware import _is_approval_callback_route
                from butlers.core.approval_callbacks import APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER

                expected = getattr(scope["app"].state, "approval_callback_connector_token", None)
                supplied = connection.headers.get(APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER)
                if (
                    expected
                    and supplied
                    and _is_approval_callback_route(Request(scope))
                    and hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))
                ):
                    scope.setdefault("state", {})["approval_callback_authenticated"] = True
                    verified_http_principal.set("owner")
                    await self.app(scope, receive, send)
                    return
                if method == "GET" and path == "/api/connectors/spotify/oauth/callback":
                    from butlers.api.routers.spotify import has_valid_callback_state

                    states = connection.query_params.getlist("state")
                    if len(states) == 1 and has_valid_callback_state(states[0]):
                        scope.setdefault("state", {})["oauth_callback_authenticated"] = True
                        await self.app(scope, receive, send)
                        return
                parts = path.split("/")
                if (
                    method == "GET"
                    and len(parts) == 5
                    and parts[1:3] == ["api", "oauth"]
                    and parts[4] == "callback"
                ):
                    from butlers.api.routers.oauth import has_valid_callback_state

                    states = connection.query_params.getlist("state")
                    if len(states) == 1 and has_valid_callback_state(states[0], parts[3]):
                        scope.setdefault("state", {})["oauth_callback_authenticated"] = True
                        await self.app(scope, receive, send)
                        return
            if service is None:
                raise _unavailable()
            key = connection.headers.get("x-api-key")
            token = _cookie(connection, self.config.owner_cookie)
            if key is None:
                if token is None:
                    # No browser authority is being exercised. Preserve the
                    # authoritative unavailable/unauthorized distinction.
                    await service.authorize(session_token=None)
                    raise AuthError("UNAUTHORIZED")
                if not trusted_https(connection, self.config):
                    raise _unavailable()
                if method not in _SAFE or scope["type"] == "websocket":
                    if connection.headers.get("origin") != self.config.origin:
                        raise _forbidden()
            authority = await service.authorize(
                session_token=token,
                api_key=key,
                csrf_token=connection.headers.get("x-csrf-token"),
                unsafe=method not in _SAFE,
            )
            if path.startswith(PREFIX):
                # Unknown auth methods/paths never fall into generic body audit.
                raise _bad_request()
            scope.setdefault("state", {})["owner_authority"] = authority
            verified_http_principal.set("owner")

            streaming = False
            stream_closed = False
            with anyio.CancelScope() as stream_scope:

                async def authorized_send(message):
                    nonlocal streaming, stream_closed
                    if stream_closed:
                        return
                    if message["type"] == "http.response.start":
                        headers = dict(message.get("headers", []))
                        streaming = headers.get(b"content-type", b"").startswith(
                            b"text/event-stream"
                        )
                    if (scope["type"] == "websocket" and message["type"] == "websocket.send") or (
                        streaming and message["type"] == "http.response.body"
                    ):
                        try:
                            await service.authorize(session_token=token, api_key=key)
                        except AuthError:
                            stream_closed = True
                            if scope["type"] == "websocket":
                                await send({"type": "websocket.close", "code": 4401})
                            else:
                                await send(
                                    {"type": "http.response.body", "body": b"", "more_body": False}
                                )
                            stream_scope.cancel()
                            return
                    await send(message)

                await self.app(scope, receive, authorized_send)
        except AuthError as exc:
            if scope["type"] == "websocket":
                await send(
                    {"type": "websocket.close", "code": 4403 if exc.status_code == 403 else 4401}
                )
            else:
                await _error(exc)(scope, receive, send)
        finally:
            verified_http_principal.reset(principal_context)
            in_http_request.reset(request_context)

    async def _ceremony(self, request: Request, service, route: tuple[str, str]) -> JSONResponse:
        method, path = route
        if method == "POST" or (method == "DELETE" and "x-api-key" not in request.headers):
            if request.headers.get("origin") != self.config.origin or not self.config.origin_valid:
                raise _forbidden()
        if method != "DELETE" or "x-api-key" not in request.headers:
            if path != "/status" and not trusted_https(request, self.config):
                raise _unavailable()
        if path == "/status" and service is None:
            await _body(request, 0)
            return _response(
                {"state": "unavailable", "authenticated": False, "session_expires_at": None}
            )
        if service is None:
            raise _unavailable()
        owner = _cookie(request, self.config.owner_cookie)
        preauth = _cookie(request, self.config.preauth_cookie)
        csrf = request.headers.get("x-csrf-token")
        key = request.headers.get("x-api-key")
        if path == "/csrf":
            if (
                request.headers.get("sec-fetch-site") != "same-origin"
                or request.headers.get("sec-fetch-mode") != "cors"
                or request.headers.get("sec-fetch-dest") != "empty"
            ):
                raise _forbidden()
        # Every cookie-bound ceremony proves CSRF before buffering its body.
        if method == "POST" and path not in {"/context", "/session"}:
            if not preauth or not csrf:
                raise _forbidden()
        if method == "DELETE":
            await service.authorize(session_token=owner, api_key=key, csrf_token=csrf, unsafe=True)
        body = await _body(request, CEREMONY_ROUTES[route])
        if path == "/status":
            return _response(
                await service.status(owner if trusted_https(request, self.config) else None)
            )
        if path == "/context":
            _fields(body, {})
            issued = await service.context()
            response = _response(issued.data)
            response.set_cookie(
                self.config.preauth_cookie,
                issued.token,
                max_age=300,
                secure=True,
                httponly=True,
                samesite="strict",
                path="/",
            )
            return response
        if path == "/registration/intent":
            _fields(body, {"operation": str})
            if body["operation"] not in {"enroll", "recover"}:
                raise _bad_request()
            return _response(await service.intent(preauth, csrf, body["operation"]))
        if path == "/registration/options":
            _fields(body, {"request_id": str})
            result = await service.registration_options(preauth, csrf, body["request_id"])
            return _response(result.data, result.status_code)
        if path == "/login/options":
            _fields(body, {})
            return _response(await service.login_options(preauth, csrf))
        if path in {"/registration/finish", "/login/finish"}:
            _fields(body, {"ceremony_id": str, "credential": dict})
            finish = (
                service.finish_registration
                if path == "/registration/finish"
                else service.finish_login
            )
            issued = await finish(preauth, csrf, body["ceremony_id"], body["credential"])
        elif path == "/session" and method == "POST":
            _fields(body, {"api_key": str})
            issued = await service.key_session(body["api_key"])
        elif path == "/csrf":
            return _response(await service.csrf(owner))
        elif method == "DELETE":
            await service.revoke(
                session_token=owner, api_key=key, all_sessions=path == "/sessions", csrf_token=csrf
            )
            response = _response({})
            response.delete_cookie(
                self.config.owner_cookie, path="/", secure=True, httponly=True, samesite="strict"
            )
            return response
        elif path == "/ceremony/cancel":
            if set(body) not in ({"request_id"}, {"ceremony_id"}) or not all(
                type(value) is str for value in body.values()
            ):
                raise _bad_request()
            await service.cancel(preauth, csrf, **body)
            return _response({})
        else:
            raise _bad_request()
        response = _response(issued.data)
        response.set_cookie(
            self.config.owner_cookie,
            issued.token,
            max_age=43200,
            secure=True,
            httponly=True,
            samesite="strict",
            path="/",
        )
        response.delete_cookie(
            self.config.preauth_cookie, path="/", secure=True, httponly=True, samesite="strict"
        )
        return response
