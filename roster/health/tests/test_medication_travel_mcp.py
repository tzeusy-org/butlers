"""Tests for Health's privacy-minimized travel medication MCP provider."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

pytestmark = pytest.mark.unit


def _fact_row(
    *,
    name: str,
    active: bool = True,
    notes: str | None = None,
    schedule: object = None,
) -> dict:
    return {
        "id": uuid4(),
        "predicate": "medication",
        "content": f"{name} private raw content",
        "valid_at": None,
        "created_at": "2026-07-16T00:00:00Z",
        "metadata": {
            "name": name,
            "dosage": "500 mg",
            "frequency": "twice daily",
            "schedule": ["08:00", "20:00"] if schedule is None else schedule,
            "active": active,
            "notes": notes,
        },
    }


async def test_provider_returns_only_active_prep_fields() -> None:
    from butlers.tools.health.medications import medication_travel_snapshot

    pool = AsyncMock()
    pool.fetch.return_value = [
        _fact_row(name="Metformin", notes="private clinical note"),
        _fact_row(name="Inactive", active=False, notes="also private"),
    ]

    result = await medication_travel_snapshot(pool)

    assert result == {
        "schema_version": "health.medication-travel.v1",
        "status": "ok",
        "medications": [
            {
                "name": "Metformin",
                "dosage": "500 mg",
                "frequency": "twice daily",
                "schedule": ["08:00", "20:00"],
            }
        ],
        "error": None,
    }
    query = pool.fetch.await_args.args[0]
    assert "scope = 'health'" in query
    assert "validity = 'active'" in query
    assert "metadata->>'active'" in query
    assert "health." not in query


async def test_provider_returns_successful_empty_snapshot() -> None:
    from butlers.tools.health.medications import medication_travel_snapshot

    pool = AsyncMock()
    pool.fetch.return_value = []

    result = await medication_travel_snapshot(pool)

    assert result["status"] == "ok"
    assert result["medications"] == []
    assert result["error"] is None


async def test_provider_normalizes_scalar_schedule_without_character_splitting() -> None:
    from butlers.tools.health.medications import medication_travel_snapshot

    pool = AsyncMock()
    pool.fetch.return_value = [_fact_row(name="Lisinopril", schedule="morning")]

    result = await medication_travel_snapshot(pool)

    assert result["medications"][0]["schedule"] == ["morning"]


async def test_provider_drops_non_string_schedule_values_without_exposing_them() -> None:
    from butlers.tools.health.medications import medication_travel_snapshot

    pool = AsyncMock()
    pool.fetch.return_value = [
        _fact_row(
            name="Lisinopril",
            schedule=[" morning ", {"notes": "private schedule metadata"}, None, "morning"],
        )
    ]

    result = await medication_travel_snapshot(pool)

    assert result["medications"][0]["schedule"] == ["morning"]
    assert "private schedule metadata" not in str(result)


async def test_provider_orders_medications_deterministically() -> None:
    from butlers.tools.health.medications import medication_travel_snapshot

    pool = AsyncMock()
    pool.fetch.return_value = [
        _fact_row(name="zinc"),
        _fact_row(name="Aspirin"),
        _fact_row(name="aspirin"),
    ]

    result = await medication_travel_snapshot(pool)

    assert [item["name"] for item in result["medications"]] == [
        "Aspirin",
        "aspirin",
        "zinc",
    ]


async def test_health_module_registers_provider_as_an_mcp_tool() -> None:
    from butlers.modules._roster_health import HealthModuleConfig
    from butlers.modules._roster_health.tools import register_tools

    registered: dict[str, object] = {}

    class _Mcp:
        def tool(self):
            def decorator(fn):
                registered[fn.__name__] = fn
                return fn

            return decorator

    pool = AsyncMock()
    pool.fetch.return_value = []
    module = SimpleNamespace(_get_pool=lambda: pool)

    register_tools(_Mcp(), module, HealthModuleConfig())

    provider = registered["medication_travel_snapshot"]
    result = await provider()  # type: ignore[operator]
    assert result["schema_version"] == "health.medication-travel.v1"
