"""Tests for the butler discovery API router and Pydantic models."""

from __future__ import annotations

import json

import httpx
import pytest

from butlers.api.models.butler import ButlerDetail, ButlerSummary, ModuleStatus

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Model serialization tests
# ---------------------------------------------------------------------------


class TestModuleStatus:
    def test_healthy_module(self):
        ms = ModuleStatus(name="telegram", enabled=True, status="ok")
        payload = ms.model_dump()
        assert payload == {
            "name": "telegram",
            "enabled": True,
            "status": "ok",
            "error": None,
            "phase": None,
        }

    def test_failed_module(self):
        ms = ModuleStatus(
            name="email", enabled=True, status="error", error="SMTP connection refused"
        )
        assert ms.error == "SMTP connection refused"

    def test_disabled_module(self):
        ms = ModuleStatus(name="calendar", enabled=False, status="disabled")
        assert ms.enabled is False

    def test_json_round_trip(self):
        ms = ModuleStatus(name="telegram", enabled=True, status="ok")
        json_str = ms.model_dump_json()
        restored = ModuleStatus.model_validate_json(json_str)
        assert restored == ms


class TestButlerSummaryModel:
    def test_minimal(self):
        b = ButlerSummary(name="general", status="running", port=40101, db="butler_general")
        assert b.name == "general"
        assert b.modules == []
        assert b.schedule_count == 0

    def test_with_modules_and_schedule(self):
        b = ButlerSummary(
            name="switchboard",
            status="running",
            port=40100,
            db="butler_switchboard",
            modules=["telegram", "email"],
            schedule_count=3,
        )
        assert b.modules == ["telegram", "email"]
        assert b.schedule_count == 3

    def test_json_round_trip(self):
        b = ButlerSummary(
            name="heartbeat",
            status="idle",
            port=40199,
            db="butler_heartbeat",
            modules=[],
            schedule_count=1,
        )
        json_str = b.model_dump_json()
        restored = ButlerSummary.model_validate_json(json_str)
        assert restored == b


class TestButlerDetailModel:
    def test_inherits_summary_fields(self):
        d = ButlerDetail(
            name="switchboard",
            status="running",
            port=40100,
            db="butler_switchboard",
            modules=["telegram"],
            schedule_count=0,
        )
        assert d.name == "switchboard"
        assert d.config == {}
        assert d.skills == []
        assert d.module_health == []

    def test_full_detail(self):
        d = ButlerDetail(
            name="switchboard",
            status="running",
            port=40100,
            db="butler_switchboard",
            modules=["telegram", "email"],
            schedule_count=2,
            config={"butler": {"description": "Routes messages"}},
            skills=["classify", "route"],
            module_health=[
                ModuleStatus(name="telegram", enabled=True, status="ok"),
                ModuleStatus(name="email", enabled=True, status="error", error="timeout"),
            ],
        )
        assert len(d.module_health) == 2
        assert d.module_health[1].error == "timeout"
        assert d.skills == ["classify", "route"]

    def test_json_round_trip(self):
        d = ButlerDetail(
            name="general",
            status="running",
            port=40101,
            db="butler_general",
            config={"key": "value"},
            skills=["search"],
            module_health=[ModuleStatus(name="x", enabled=True, status="ok")],
        )
        json_str = d.model_dump_json()
        restored = json.loads(json_str)
        assert restored["config"] == {"key": "value"}
        assert restored["skills"] == ["search"]
        assert len(restored["module_health"]) == 1

    def test_is_subclass_of_summary(self):
        assert issubclass(ButlerDetail, ButlerSummary)


# ---------------------------------------------------------------------------
# Router registration and endpoint tests
# ---------------------------------------------------------------------------


class TestButlerRouterRegistration:
    def test_router_is_registered(self, app):
        routes = [route.path for route in app.routes]
        # The list endpoint may be registered as "/api/butlers" or "/api/butlers/"
        butler_routes = [r for r in routes if r.startswith("/api/butlers")]
        assert len(butler_routes) >= 2


class TestListButlersEndpoint:
    async def test_returns_empty_list(self, app):
        from butlers.api.deps import get_butler_configs, get_mcp_manager

        app.dependency_overrides[get_butler_configs] = lambda: []
        app.dependency_overrides[get_mcp_manager] = lambda: None
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers")
        assert response.status_code == 200
        body = response.json()
        assert body["data"] == []
        assert "meta" in body


class TestGetButlerEndpoint:
    async def test_returns_404_for_unknown_butler(self, app):
        from butlers.api.deps import get_butler_configs, get_mcp_manager

        app.dependency_overrides[get_butler_configs] = lambda: []
        app.dependency_overrides[get_mcp_manager] = lambda: None
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/nonexistent")
        assert response.status_code == 404
