"""Wellness ingest maps Google Health weight and body fat to measurements (bu-q7vx1q.35)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

_SAMPLE = "2026-10-18T01:30:00Z"


@pytest.fixture(autouse=True)
def _reset_owner_cache():
    import butlers.tools.health.wellness_ingest as wi

    wi._recognised_owner_identities = None
    yield
    wi._recognised_owner_identities = None


def _envelope(resource: str, value: float, unit: str) -> dict:
    user = "me@example.com"
    key = f"google_health:{user}:{resource}:{_SAMPLE}"
    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "wellness",
            "provider": "google_health",
            "endpoint_identity": f"google_health:user:{user}",
        },
        "event": {"external_event_id": key, "observed_at": "2026-10-18T06:00:00Z"},
        "sender": {"identity": user},
        "payload": {
            "raw": {"sampleTime": _SAMPLE, "value": value, "unit": unit},
            "normalized_text": f"{resource}: {value}",
        },
        "control": {"idempotency_key": key},
    }


async def _translate(envelope: dict):
    from butlers.tools.health.wellness_ingest import translate_wellness_envelope

    account = MagicMock()
    account.email = "me@example.com"
    with (
        patch(
            "butlers.tools.health.wellness_ingest.list_health_scoped_accounts",
            new_callable=AsyncMock,
            return_value=[account],
        ),
        patch(
            "butlers.tools.health.wellness_ingest.resolve_owner_entity_info",
            new_callable=AsyncMock,
            return_value=str(uuid.uuid4()),
        ),
        patch(
            "butlers.tools.health.wellness_ingest.memory_store_fact",
            new_callable=AsyncMock,
            return_value={"id": str(uuid.uuid4())},
        ) as store,
        patch("butlers.tools.health.wellness_ingest.health_wellness_ingest_total"),
    ):
        result = await translate_wellness_envelope(AsyncMock(), MagicMock(), envelope)
    return result, store


@pytest.mark.parametrize(
    ("resource", "predicate", "value", "unit"),
    [
        ("weight", "measurement_weight", 70.5, "kg"),
        ("body_fat", "measurement_body_fat", 18.2, "%"),
    ],
)
async def test_measurement_fact_uses_sample_time_unit_and_device_source(
    resource: str, predicate: str, value: float, unit: str
) -> None:
    result, store = await _translate(_envelope(resource, value, unit))

    assert result["status"] == "ok" and result["predicate"] == predicate
    kwargs = store.call_args.kwargs
    assert kwargs["predicate"] == predicate
    assert kwargs["valid_at"] == _SAMPLE
    assert kwargs["scope"] == "health"
    assert kwargs["metadata"]["value"] == value
    assert kwargs["metadata"]["unit"] == unit
    assert kwargs["metadata"]["source"] == "google_health"
    assert kwargs["idempotency_key"] == f"google_health:me@example.com:{resource}:{_SAMPLE}"


async def test_a_reading_without_a_value_is_malformed_not_stored() -> None:
    envelope = _envelope("weight", 70.5, "kg")
    envelope["payload"]["raw"] = {"sampleTime": _SAMPLE}
    result, store = await _translate(envelope)

    assert result["status"] == "skipped_malformed_payload"
    store.assert_not_awaited()
