"""Isolated browser harness: real auth, synthetic domain, no production lifespan."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from butlers.api.owner_auth.config import OwnerAuthConfig
from butlers.api.owner_auth.http import OwnerAuthMiddleware
from butlers.api.owner_auth.service import close_owner_auth_service, create_owner_auth_service


def create_test_app() -> FastAPI:
    if os.environ.get("POSTGRES_DB") != "test_owner_auth_browser" or os.environ.get("DATABASE_URL"):
        raise RuntimeError("Browser harness requires its isolated synthetic database")
    config = OwnerAuthConfig.from_env()

    @asynccontextmanager
    async def lifespan(app):
        app.state.owner_auth_service = await create_owner_auth_service(config)
        yield
        await close_owner_auth_service(app.state.owner_auth_service)

    app = FastAPI(lifespan=lifespan)
    app.state.owner_auth_service = None
    app.add_middleware(OwnerAuthMiddleware, config=config)

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
