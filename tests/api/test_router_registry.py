"""Full registration survivor after the five-operation Desk retirement.

The digest covers the entire normalized OpenAPI document and ordered route table,
including hidden routes, operation IDs, tags, schemas and middleware order. The
full before/after JSON at base9aac956 is retained with the author verification
receipts; all surviving operations, components and middleware are identical.
"""

from __future__ import annotations

import hashlib
import json
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.routing import APIRoute

from butlers.api import deps
from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.router_registry import build_router_registry
from tests.api.auth_helpers import create_authenticated_domain_app

pytestmark = pytest.mark.unit


def test_complete_route_and_openapi_snapshot(tmp_path, monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    (tmp_path / "index.html").write_text("baseline")
    app = create_app(static_dir=str(tmp_path))

    def walk(routes):
        rows = []
        for route in routes:
            if hasattr(route, "original_router"):
                rows.extend(walk(route.original_router.routes))
                continue
            row = {"path": route.path, "name": route.name, "type": type(route).__name__}
            if hasattr(route, "methods"):
                row["methods"] = sorted(route.methods or [])
            if isinstance(route, APIRoute):
                row.update(
                    operation_id=route.unique_id,
                    include_in_schema=route.include_in_schema,
                    tags=route.tags,
                )
            rows.append(row)
        return rows

    snapshot = {
        "routes": walk(app.routes),
        "openapi": app.openapi(),
        "middleware": [middleware.cls.__name__ for middleware in app.user_middleware],
    }
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == (
        "ce392b5dc15e64406a83a00e5494507e96d34fa37e679c36a0eb76c42d2fb657"
    )
    assert "/api/health/baselines" in snapshot["openapi"]["paths"]
    assert {"/api/confirms", "/api/confirms/callback", "/api/confirms/{confirm_id}/answer"} <= set(
        snapshot["openapi"]["paths"]
    )
    assert len(snapshot["routes"]) == 618
    assert len(snapshot["openapi"]["paths"]) == 527
    assert not any(
        row["path"] == "/api/healing/attempts/{attempt_id}/retry" for row in snapshot["routes"]
    )
    assert "RetryResponse" not in snapshot["openapi"]["components"]["schemas"]
    assert snapshot["routes"][-1] == {"path": "", "name": "frontend", "type": "Mount"}
    assert any(row.get("include_in_schema") is False for row in snapshot["routes"])

    assert not any(
        row["path"].startswith(("/api/decisions", "/api/beads")) for row in snapshot["routes"]
    )

    registry = app.state.router_registry
    assert registry == build_router_registry()
    core = [entry for entry in registry if entry.butler_name is None]
    roster = [entry for entry in registry if entry.butler_name is not None]
    assert tuple(core + roster) == registry
    assert [entry.butler_name for entry in roster] == sorted(entry.butler_name for entry in roster)
    assert app.state.butler_routers == [(entry.butler_name, entry.module) for entry in roster]
    assert any(entry.export == "rollup_router" for entry in core)
    assert any(entry.export == "accounts_router" for entry in core)
    assert any(entry.export == "messages_search_router" for entry in core)


async def test_public_db_dependencies_preserve_uninitialized_http_policies(monkeypatch):
    monkeypatch.setattr(deps, "_db_manager", None)
    assert deps.get_db is deps.get_db_manager
    with pytest.raises(RuntimeError, match="DatabaseManager not initialized"):
        deps.get_db()
    assert deps.get_db_optional() is None

    app = create_authenticated_domain_app()
    # Actual required handler: the existing error middleware still reports 500.
    # Actual optional metrics handler: missing DB must not kill non-DB telemetry.
    from butlers.api.routers import ingestion_pipeline

    monkeypatch.setattr(ingestion_pipeline, "_get_prometheus_url", lambda: None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        required = await client.get("/api/ingestion/events")
        assert required.status_code == 500
        assert required.json()["error"]["code"] == "INTERNAL_ERROR"
        optional = await client.get("/api/ingestion/pipeline")
        assert optional.status_code == 200
        assert optional.json()["aggregates_available"] is False

        # Exercise production routing as an owner, beyond the OpenAPI inventory.
        for method, target in (
            ("GET", "/api/decisions"),
            ("GET", "/api/beads/historical-bead"),
            ("POST", "/api/decisions/historical-bead/intent"),
            ("GET", "/api/decisions/prompts/12345678-1234-5678-1234-567812345678"),
            ("POST", "/api/decisions/prompts/12345678-1234-5678-1234-567812345678/choose"),
        ):
            retired = await client.request(method, target)
            assert retired.status_code == 404
            assert retired.json() == {"detail": "Not Found"}

    manager = MagicMock(spec=DatabaseManager)
    monkeypatch.setattr(deps, "_db_manager", manager)
    assert deps.get_db() is manager
    assert deps.get_db_optional() is manager

    # Suppression is limited to the original RuntimeError boundary.
    def broken_manager():
        raise ValueError("not an initialization failure")

    monkeypatch.setattr(deps, "get_db", broken_manager)
    with pytest.raises(ValueError, match="not an initialization failure"):
        deps.get_db_optional()
