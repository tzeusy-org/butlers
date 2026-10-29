"""Butler summaries expose observed facts without an invented schedule count."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api import models
from butlers.api.deps import ButlerConnectionInfo, get_butler_configs, get_db, get_mcp_manager
from butlers.api.models import butler as butler_models

from .conftest import make_mock_mcp_manager

pytestmark = pytest.mark.unit


def _provide(value):
    """Dependency override returning ``value`` as bound now, not at request time."""
    return lambda: value


async def test_list_and_summary_models_omit_unobserved_schedule_count(app):
    configs = [ButlerConnectionInfo("general", 41101)]
    db = MagicMock()
    db.fan_out_with_status = AsyncMock(return_value=({"general": [(7,)]}, []))
    app.dependency_overrides[get_butler_configs] = lambda: configs
    app.dependency_overrides[get_db] = lambda: db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for online, status in [(True, "ok"), (False, "down")]:
            manager = make_mock_mcp_manager(online=online)
            app.dependency_overrides[get_mcp_manager] = _provide(manager)
            response = await client.get("/api/butlers")
            assert response.status_code == 200
            payload = response.json()
            assert "meta" in payload
            summary = payload["data"][0]
            assert summary["name"] == "general"
            assert summary["status"] == status
            assert summary["port"] == 41101
            assert summary["sessions_24h"] == 7
            assert "schedule_count" not in summary

    for module in (models, butler_models):
        for model in (module.ButlerSummary, module.ButlerDetail):
            summary = model(name="general", status="ok", port=41101, db="general")
            assert "schedule_count" not in summary.model_dump(mode="json")
            assert "schedule_count" not in model.model_json_schema()["properties"]

    schema = app.openapi()
    for name, component in schema["components"]["schemas"].items():
        if "ButlerSummary" in name or "ButlerDetail" in name:
            assert "schedule_count" not in component.get("properties", {})
