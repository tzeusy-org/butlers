"""The mounted informational callback never becomes dashboard owner authority."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.owner_auth.config import OwnerAuthConfig
from butlers.api.owner_auth.http import OwnerAuthMiddleware
from butlers.api.routers.confirms import router
from butlers.core.owner_auth_context import verified_http_principal
from tests.api.auth_helpers import _DomainOwnerState

pytestmark = pytest.mark.unit


def app_with_confirms():
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

    async def callback(*args, **kwargs):
        assert verified_http_principal.get() is None
        return {"status": "answered", "application_status": "pending"}

    service = SimpleNamespace(
        answer_callback=AsyncMock(side_effect=callback),
        list_owner=AsyncMock(return_value={"rows": [], "next_cursor": None}),
        answer_owner=AsyncMock(return_value={"status": "answered"}),
    )
    app.state.confirm_service = service
    return app, service


async def test_exact_callback_credential_does_not_authorize_owner_or_adjacent_routes():
    app, service = app_with_confirms()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        body = {"token": "synthetic", "sender_id": "42", "chat_id": "42"}
        assert (await client.post("/api/confirms/callback", json=body)).status_code == 401
        headers = {"X-Butlers-Confirm-Callback-Token": "connector-key"}
        response = await client.post("/api/confirms/callback", json=body, headers=headers)
        assert response.status_code == 200
        service.answer_callback.assert_awaited_once_with("synthetic", sender_id="42", chat_id="42")
        for path in (
            "/api/confirms",
            f"/api/confirms/{uuid4()}/answer",
            "/api/confirms/callback/extra",
        ):
            response = await client.post(path, json={"option_index": 0}, headers=headers)
            assert response.status_code == 401
        assert (
            await client.post(
                "/api/confirms/callback",
                json=body,
                headers=[
                    ("X-Butlers-Confirm-Callback-Token", "connector-key"),
                    ("X-Butlers-Confirm-Callback-Token", "connector-key"),
                ],
            )
        ).status_code == 400
        assert (
            await client.post(
                "/api/confirms/callback", json=body, headers={"X-API-Key": "owner-key"}
            )
        ).status_code == 403
        assert (
            await client.get("/api/confirms", headers={"X-API-Key": "owner-key"})
        ).status_code == 200


async def test_uncomposed_runtime_is_explicitly_unavailable():
    app, _ = app_with_confirms()
    app.state.confirm_service = None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/confirms", headers={"X-API-Key": "owner-key"})
        assert response.status_code == 503
