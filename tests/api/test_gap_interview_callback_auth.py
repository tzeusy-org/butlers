"""The Telegram gap-interview tap reaches its exact resolve route, and nothing else.

The ``telegram_bot`` connector POSTs ``cgi:`` taps to
``/api/chronicler/gap-interview/resolve``. Owner authentication guards every
``/api`` route, so the connector authenticates with its callback credential;
that credential must not become owner authority or open adjacent routes.
"""

import httpx
import pytest
from fastapi import APIRouter, FastAPI

from butlers.api.owner_auth.config import OwnerAuthConfig
from butlers.api.owner_auth.http import OwnerAuthMiddleware
from butlers.core.approval_callbacks import APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER
from butlers.core.owner_auth_context import verified_http_principal
from tests.api.auth_helpers import _DomainOwnerState

pytestmark = pytest.mark.unit

RESOLVE = "/api/chronicler/gap-interview/resolve"


def _app() -> tuple[FastAPI, list[str | None]]:
    seen: list[str | None] = []
    router = APIRouter(prefix="/api/chronicler")

    @router.post("/gap-interview/resolve")
    async def resolve() -> dict[str, str]:
        seen.append(verified_http_principal.get())
        return {"status": "applied"}

    @router.post("/gap-interview/other")
    async def other() -> dict[str, str]:
        return {"status": "nope"}

    app = FastAPI()
    app.include_router(router)
    app.add_middleware(
        OwnerAuthMiddleware,
        config=OwnerAuthConfig(
            "https://butlers.example.test",
            "butlers.example.test",
            "owner-key",
            "test",
            ("127.0.0.1",),
        ),
    )
    app.state.owner_auth_service = _DomainOwnerState("owner-key")
    app.state.approval_callback_connector_token = "connector-key"
    return app, seen


async def test_connector_credential_authorizes_only_the_exact_resolve_route():
    app, seen = _app()
    body = {"interview_id": "2026-11-01", "answer": "confirm"}
    good = {APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER: "connector-key"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.post(RESOLVE, json=body)).status_code == 401
        wrong = {APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER: "nope"}
        assert (await client.post(RESOLVE, json=body, headers=wrong)).status_code == 401

        response = await client.post(RESOLVE, json=body, headers=good)
        assert response.status_code == 200
        assert seen == [None]  # never an owner principal

        other = "/api/chronicler/gap-interview/other"
        assert (await client.post(other, json=body, headers=good)).status_code == 401
        assert (await client.get(RESOLVE, headers=good)).status_code == 401
