"""Isolated browser harness: real auth, synthetic domain, no production lifespan."""

from __future__ import annotations

import asyncio
import contextlib
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.responses import StreamingResponse

from butlers.api.owner_auth.config import OwnerAuthConfig
from butlers.api.owner_auth.http import OwnerAuthMiddleware
from butlers.api.owner_auth.service import close_owner_auth_service, create_owner_auth_service
from butlers.api.routers import events


def create_test_app() -> FastAPI:
    if os.environ.get("POSTGRES_DB") != "test_owner_auth_browser" or os.environ.get("DATABASE_URL"):
        raise RuntimeError("Browser harness requires its isolated synthetic database")
    config = OwnerAuthConfig.from_env()

    @asynccontextmanager
    async def lifespan(app):
        app.state.owner_auth_service = await create_owner_auth_service(config)

        async def pump():
            while True:
                events.emit_event("heartbeat", {"synthetic": True})
                await asyncio.sleep(0.1)

        heartbeat = asyncio.create_task(pump())
        try:
            yield
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            await close_owner_auth_service(app.state.owner_auth_service)

    app = FastAPI(lifespan=lifespan)
    app.state.owner_auth_service = None
    app.add_middleware(OwnerAuthMiddleware, config=config)
    app.include_router(events.router)
    evidence = {"pending_queries": 0, "cancelled_queries": 0, "sse": 0}

    @app.get("/__test__/evidence")
    async def test_evidence():
        return evidence | {"websockets": len(events._events_subscribers)}

    @app.get("/api/butlers")
    async def pending_query(request: Request):
        evidence["pending_queries"] += 1
        try:
            while not await request.is_disconnected():
                await asyncio.sleep(0.05)
        finally:
            evidence["pending_queries"] -= 1
            evidence["cancelled_queries"] += 1
        return {"data": []}

    @app.get("/api/test-stream")
    async def test_stream():
        async def chunks():
            evidence["sse"] += 1
            try:
                while True:
                    yield b"data: synthetic-stream-control\n\n"
                    await asyncio.sleep(0.1)
            finally:
                evidence["sse"] -= 1

        return StreamingResponse(chunks(), media_type="text/event-stream")

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.api_route("/api/test-private", methods=["GET", "POST"])
    async def private():
        return {"data": {"ok": True}}

    @app.get("/api/settings/general/timezone")
    async def timezone():
        return {"data": {"timezone": "UTC"}}

    return app
