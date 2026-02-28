"""Tests for API error handling middleware."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.deps import ButlerUnreachableError
from butlers.api.models import ErrorResponse

pytestmark = pytest.mark.unit


def _app_with_error_routes(app: FastAPI) -> FastAPI:
    """Add test routes that raise specific exceptions to a FastAPI app."""

    @app.get("/api/test/unreachable")
    async def raise_unreachable():
        raise ButlerUnreachableError("atlas", cause=ConnectionRefusedError("connection refused"))

    @app.get("/api/test/not-found")
    async def raise_not_found():
        raise KeyError("atlas")

    @app.get("/api/test/validation")
    async def raise_validation():
        raise ValueError("port must be positive")

    @app.get("/api/test/internal")
    async def raise_internal():
        raise RuntimeError("something broke")

    @app.get("/api/test/key-error-empty")
    async def raise_key_error_empty():
        raise KeyError()

    return app


class TestButlerUnreachableHandler:
    async def test_returns_502(self, app):
        _app_with_error_routes(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/test/unreachable")

        assert resp.status_code == 502

    async def test_error_code(self, app):
        _app_with_error_routes(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/test/unreachable")

        body = resp.json()
        assert body["error"]["code"] == "BUTLER_UNREACHABLE"

    async def test_includes_butler_name(self, app):
        _app_with_error_routes(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/test/unreachable")

        body = resp.json()
        assert body["error"]["butler"] == "atlas"

    async def test_message_contains_butler_name(self, app):
        _app_with_error_routes(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/test/unreachable")

        body = resp.json()
        assert "atlas" in body["error"]["message"]

    async def test_response_validates_as_error_response(self, app):
        _app_with_error_routes(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/test/unreachable")

        parsed = ErrorResponse.model_validate(resp.json())
        assert parsed.error.code == "BUTLER_UNREACHABLE"
        assert parsed.error.butler == "atlas"


class TestKeyErrorHandler:
    async def test_returns_404(self, app):
        _app_with_error_routes(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/test/not-found")

        assert resp.status_code == 404

    async def test_error_code(self, app):
        _app_with_error_routes(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/test/not-found")

        body = resp.json()
        assert body["error"]["code"] == "BUTLER_NOT_FOUND"

    async def test_includes_butler_name(self, app):
        _app_with_error_routes(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/test/not-found")

        body = resp.json()
        assert body["error"]["butler"] == "atlas"

    async def test_message_contains_butler_name(self, app):
        _app_with_error_routes(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/test/not-found")

        body = resp.json()
        assert "atlas" in body["error"]["message"]

    async def test_empty_key_error(self, app):
        _app_with_error_routes(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/test/key-error-empty")

        assert resp.status_code == 404
        body = resp.json()
        assert body["error"]["code"] == "BUTLER_NOT_FOUND"
        assert body["error"]["butler"] is None

    async def test_response_validates_as_error_response(self, app):
        _app_with_error_routes(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/test/not-found")

        parsed = ErrorResponse.model_validate(resp.json())
        assert parsed.error.code == "BUTLER_NOT_FOUND"
        assert parsed.error.butler == "atlas"


class TestValueErrorHandler:
    async def test_returns_400(self, app):
        _app_with_error_routes(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/test/validation")

        assert resp.status_code == 400

    async def test_error_code(self, app):
        _app_with_error_routes(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/test/validation")

        body = resp.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"

    async def test_no_butler_field(self, app):
        _app_with_error_routes(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/test/validation")

        body = resp.json()
        assert body["error"]["butler"] is None

    async def test_message_from_exception(self, app):
        _app_with_error_routes(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/test/validation")

        body = resp.json()
        assert body["error"]["message"] == "port must be positive"

    async def test_response_validates_as_error_response(self, app):
        _app_with_error_routes(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/test/validation")

        parsed = ErrorResponse.model_validate(resp.json())
        assert parsed.error.code == "VALIDATION_ERROR"


class TestGenericExceptionHandler:
    async def test_returns_500(self, app):
        _app_with_error_routes(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/test/internal")

        assert resp.status_code == 500

    async def test_error_code(self, app):
        _app_with_error_routes(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/test/internal")

        body = resp.json()
        assert body["error"]["code"] == "INTERNAL_ERROR"

    async def test_generic_message_no_leak(self, app):
        _app_with_error_routes(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/test/internal")

        body = resp.json()
        assert body["error"]["message"] == "Internal server error"
        # Must NOT leak internal details
        assert "something broke" not in body["error"]["message"]

    async def test_no_butler_field(self, app):
        _app_with_error_routes(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/test/internal")

        body = resp.json()
        assert body["error"]["butler"] is None

    async def test_response_validates_as_error_response(self, app):
        _app_with_error_routes(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/test/internal")

        parsed = ErrorResponse.model_validate(resp.json())
        assert parsed.error.code == "INTERNAL_ERROR"


class TestHealthStillWorks:
    """Verify that the error handlers don't break normal responses."""

    async def test_health_unaffected(self, app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health")

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
