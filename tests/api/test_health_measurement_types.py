"""Contract tests for the active Health measurement-type vocabulary endpoint.

The vocabulary is observed from active temporal facts; it is deliberately
separate from the fixed five-type manual measurement writer allowlist.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit


# Trigger router discovery once. FastAPI dependency overrides are keyed by the
# exact dependency function object registered by the auto-discovered router.
_APP_SEED = create_app(api_key="")
_health_get_db_manager = sys.modules["health_api_router"]._get_db_manager


class _Row(dict):
    """dict subclass mimicking an asyncpg Record."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


def _row(data: dict[str, Any]) -> _Row:
    return _Row(data)


def _make_app(*, rows: list[_Row]) -> tuple[Any, AsyncMock, MagicMock]:
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=rows)

    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool

    app = create_app(api_key="")
    app.dependency_overrides[_health_get_db_manager] = lambda: db
    return app, pool, db


async def test_measurement_types_observe_mixed_active_types_from_health_facts() -> None:
    """Unknown observed types appear without widening the manual writer vocabulary."""
    latest = datetime(2026, 7, 20, 1, 2, 3, tzinfo=UTC)
    rows = [
        _row(
            {
                "predicate": "measurement_blood_pressure",
                "latest_at": latest,
                "metadata": {"value": {"systolic": 120, "diastolic": 80}, "unit": "mmHg"},
                "sample_count": 4,
            }
        ),
        _row(
            {
                "predicate": "measurement_hrv",
                "latest_at": latest,
                "metadata": {"value": 42.5, "unit": "ms"},
                "sample_count": 2,
            }
        ),
        _row(
            {
                "predicate": "measurement_recovery_note",
                "latest_at": latest,
                "metadata": {"value": {"note": "rest day"}},
                "sample_count": 1,
            }
        ),
        _row(
            {
                "predicate": "measurement_weight",
                "latest_at": latest,
                "metadata": {"value": 71.2, "unit": "kg"},
                "sample_count": 8,
            }
        ),
    ]
    app, pool, db = _make_app(rows=rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/health/measurements/types")

    assert response.status_code == 200
    assert response.json() == {
        "types": [
            {
                "type": "blood_pressure",
                "label": "Blood pressure",
                "sample_count": 4,
                "latest_at": latest.isoformat(),
                "unit": "mmHg",
                "value_shape": "compound",
                "chart_eligible": True,
                "kpi_eligible": True,
            },
            {
                "type": "hrv",
                "label": "Hrv",
                "sample_count": 2,
                "latest_at": latest.isoformat(),
                "unit": "ms",
                "value_shape": "scalar",
                "chart_eligible": True,
                "kpi_eligible": False,
            },
            {
                "type": "recovery_note",
                "label": "Recovery note",
                "sample_count": 1,
                "latest_at": latest.isoformat(),
                "unit": None,
                "value_shape": "compound",
                "chart_eligible": False,
                "kpi_eligible": False,
            },
            {
                "type": "weight",
                "label": "Weight",
                "sample_count": 8,
                "latest_at": latest.isoformat(),
                "unit": "kg",
                "value_shape": "scalar",
                "chart_eligible": True,
                "kpi_eligible": True,
            },
        ]
    }

    db.pool.assert_called_once_with("health")
    sql = pool.fetch.call_args.args[0]
    assert "FROM facts" in sql
    assert "FROM measurements" not in sql
    assert "validity = 'active'" in sql
    assert "valid_at IS NOT NULL" in sql
    assert "butler_name" not in sql


async def test_measurement_types_treats_overflowing_numeric_metadata_as_not_chartable() -> None:
    """Pathological fact metadata must not turn the vocabulary endpoint into a 500."""
    latest = datetime(2026, 7, 20, 1, 2, 3, tzinfo=UTC)
    overflowing_value = 10**309
    rows = [
        _row(
            {
                "predicate": "measurement_overflowing_integer",
                "latest_at": latest,
                "metadata": {"value": overflowing_value},
                "sample_count": 1,
            }
        ),
        _row(
            {
                "predicate": "measurement_overflowing_numeric_text",
                "latest_at": latest,
                "metadata": {"value": str(overflowing_value)},
                "sample_count": 1,
            }
        ),
    ]
    app, _, _ = _make_app(rows=rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/health/measurements/types")

    assert response.status_code == 200
    chart_eligibility = {
        measurement["type"]: measurement["chart_eligible"]
        for measurement in response.json()["types"]
    }
    assert chart_eligibility == {
        "overflowing_integer": False,
        "overflowing_numeric_text": False,
    }


async def test_measurement_types_returns_an_empty_vocabulary_when_no_active_facts() -> None:
    app, _, _ = _make_app(rows=[])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/health/measurements/types")

    assert response.status_code == 200
    assert response.json() == {"types": []}


def test_measurement_types_openapi_contract_preserves_the_writer_allowlist() -> None:
    schema = create_app(api_key="").openapi()
    response_schema = schema["paths"]["/api/health/measurements/types"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    assert response_schema == {"$ref": "#/components/schemas/MeasurementTypesResponse"}

    descriptor = schema["components"]["schemas"]["MeasurementTypeInfo"]
    assert set(descriptor["required"]) == {
        "type",
        "label",
        "sample_count",
        "latest_at",
        "unit",
        "value_shape",
        "chart_eligible",
        "kpi_eligible",
    }
    assert descriptor["properties"]["value_shape"]["enum"] == ["scalar", "compound", "unknown"]

    writer_type = schema["components"]["schemas"]["MeasurementCreateRequest"]["properties"]["type"]
    assert writer_type["enum"] == [
        "weight",
        "blood_pressure",
        "heart_rate",
        "blood_sugar",
        "temperature",
    ]
