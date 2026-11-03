"""Full registration survivor: the live app must match a line-per-item snapshot.

``tests/api/route_snapshot.txt`` holds one sorted line per route, per OpenAPI
operation, per OpenAPI component schema, plus the middleware stack. It covers
hidden routes, operation IDs, tags, schemas and middleware order. Independent
route-adding PRs touch different lines, so they merge cleanly, and an
unintended change fails with a readable line diff.

Refresh after an intentional route/schema change (one command)::

    BUTLERS_UPDATE_ROUTE_SNAPSHOT=1 uv run pytest tests/api/test_router_registry.py -n 0 -q

then commit the snapshot diff. On a merge conflict in the snapshot, rerun the refresh;
never hand-edit hashes. Route table *order* is not pinned by the (sorted) snapshot;
instead the test asserts the frontend mount is last and that no API route is shadowed
by an earlier route with a different path.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.routing import APIRoute
from starlette.routing import Match

from butlers.api import deps
from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.router_registry import build_router_registry
from tests.api.auth_helpers import create_authenticated_domain_app

pytestmark = pytest.mark.unit

SNAPSHOT_PATH = Path(__file__).with_name("route_snapshot.txt")
UPDATE_ENV = "BUTLERS_UPDATE_ROUTE_SNAPSHOT"


def _digest(value) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:12]


def _shadowed_routes(routes) -> list[str]:
    """API routes fully matched by an earlier route with a different path."""
    shadowed = []
    seen: list[APIRoute] = []
    for route in routes:
        if not isinstance(route, APIRoute):
            continue
        sample = re.sub(r"\{[^}]+\}", "x", route.path)
        for method in route.methods or ():
            scope = {"type": "http", "path": sample, "method": method}
            for earlier in seen:
                if earlier.path != route.path and earlier.matches(scope)[0] == Match.FULL:
                    shadowed.append(f"{method} {route.path} shadowed by {earlier.path}")
                    break
        seen.append(route)
    return shadowed


def _snapshot_lines(routes, openapi, middleware) -> list[str]:
    lines = []
    for row in routes:
        lines.append(
            "ROUTE {type} {methods} {path!r} name={name} op={op} tags={tags} schema={schema}".format(
                type=row["type"],
                methods=",".join(row.get("methods", [])),
                path=row["path"],
                name=row["name"],
                op=row.get("operation_id", "-"),
                tags=",".join(row.get("tags") or []) or "-",
                schema=row.get("include_in_schema", "-"),
            )
        )
    for path, item in openapi["paths"].items():
        for method, operation in item.items():
            lines.append(f"OP {method.upper()} {path} {_digest(operation)}")
    for name, schema in openapi.get("components", {}).get("schemas", {}).items():
        lines.append(f"SCHEMA {name} {_digest(schema)}")
    lines.sort()
    # Middleware order is semantic, so it keeps its position index (ordered, rarely edited).
    lines.extend(f"MIDDLEWARE {index} {name}" for index, name in enumerate(middleware))
    return lines


def test_complete_route_and_openapi_snapshot(tmp_path, monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    (tmp_path / "index.html").write_text("baseline")
    app = create_app(static_dir=str(tmp_path))

    def flatten(routes):
        for route in routes:
            if hasattr(route, "original_router"):
                yield from flatten(route.original_router.routes)
            else:
                yield route

    def walk(routes):
        rows = []
        for route in flatten(routes):
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
    assert _shadowed_routes(flatten(app.routes)) == []
    lines = _snapshot_lines(snapshot["routes"], snapshot["openapi"], snapshot["middleware"])
    rendered = "\n".join(lines) + "\n"
    if os.environ.get(UPDATE_ENV):
        SNAPSHOT_PATH.write_text(rendered)
    expected = SNAPSHOT_PATH.read_text()
    if rendered != expected:
        diff = difflib.unified_diff(
            expected.splitlines(), lines, "route_snapshot.txt", "live app", lineterm="", n=0
        )
        pytest.fail(
            f"Route snapshot drift; if intended run with {UPDATE_ENV}=1:\n" + "\n".join(diff)
        )
    assert "/api/system/background-jobs" in snapshot["openapi"]["paths"]
    assert "/api/health/baselines" in snapshot["openapi"]["paths"]
    assert {"/api/confirms", "/api/confirms/callback", "/api/confirms/{confirm_id}/answer"} <= set(
        snapshot["openapi"]["paths"]
    )
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
