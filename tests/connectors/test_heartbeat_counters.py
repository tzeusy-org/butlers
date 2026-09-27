"""Prometheus counter hygiene for connector heartbeat snapshots."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.heartbeat import ConnectorHeartbeat, HeartbeatConfig

pytestmark = pytest.mark.unit


def _heartbeat() -> ConnectorHeartbeat:
    return ConnectorHeartbeat(
        config=HeartbeatConfig(
            connector_type="gmail",
            endpoint_identity="gmail:user:owner@example.com",
        ),
        mcp_client=AsyncMock(),
        metrics=MagicMock(),
        get_health_state=MagicMock(return_value=("healthy", None)),
    )


def _sample(name: str, value: object, **labels: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, value=value, labels=labels)


def _family(name: str, samples: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(name=name, samples=samples)


def test_collect_counters_counts_only_total_samples() -> None:
    heartbeat = _heartbeat()
    families = [
        _family(
            "connector_ingest_submissions",
            [
                _sample(
                    "connector_ingest_submissions_total",
                    "7",
                    connector_type="gmail",
                    endpoint_identity="gmail:user:owner@example.com",
                    status="success",
                ),
                _sample(
                    "connector_ingest_submissions_created",
                    1_735_689_600,
                    connector_type="gmail",
                    endpoint_identity="gmail:user:owner@example.com",
                    status="success",
                ),
                _sample(
                    "connector_ingest_submissions_total",
                    2,
                    connector_type="gmail",
                    endpoint_identity="gmail:user:owner@example.com",
                    status="error",
                ),
                _sample(
                    "connector_ingest_submissions_total",
                    1,
                    connector_type="gmail",
                    endpoint_identity="gmail:user:owner@example.com",
                    status="duplicate",
                ),
            ],
        ),
        _family(
            "connector_source_api_calls",
            [
                _sample(
                    "connector_source_api_calls_total",
                    11,
                    connector_type="gmail",
                    endpoint_identity="gmail:user:owner@example.com",
                ),
                _sample(
                    "connector_source_api_calls_created",
                    1_735_689_600,
                    connector_type="gmail",
                    endpoint_identity="gmail:user:owner@example.com",
                ),
            ],
        ),
        _family(
            "connector_checkpoint_saves",
            [
                _sample(
                    "connector_checkpoint_saves_total",
                    3,
                    connector_type="gmail",
                    endpoint_identity="gmail:user:owner@example.com",
                    status="success",
                ),
                _sample(
                    "connector_checkpoint_saves_created",
                    1_735_689_600,
                    connector_type="gmail",
                    endpoint_identity="gmail:user:owner@example.com",
                    status="success",
                ),
            ],
        ),
    ]

    with patch("prometheus_client.REGISTRY") as registry:
        registry.collect.return_value = families
        counters = heartbeat._collect_counters()

    assert counters["messages_ingested"] == 7
    assert counters["messages_failed"] == 2
    assert counters["dedupe_accepted"] == 1
    assert counters["source_api_calls"] == 11
    assert counters["checkpoint_saves"] == 3
    assert counters.availability == "available"
    assert counters.unavailable_fields == frozenset()


@pytest.mark.parametrize("raw", ["malformed", "NaN", "inf", "-inf", float("nan"), float("inf")])
def test_collect_counters_ignores_invalid_total_samples(raw: object) -> None:
    heartbeat = _heartbeat()
    family = _family(
        "connector_ingest_submissions",
        [
            _sample(
                "connector_ingest_submissions_total",
                raw,
                connector_type="gmail",
                endpoint_identity="gmail:user:owner@example.com",
                status="success",
            )
        ],
    )

    with patch("prometheus_client.REGISTRY") as registry:
        registry.collect.return_value = [family]
        counters = heartbeat._collect_counters()

    assert counters["messages_ingested"] == 0
    assert counters.availability == "unavailable"
    assert "messages_ingested" in counters.unavailable_fields


def test_collect_counters_marks_missing_source_unavailable_instead_of_claiming_zero() -> None:
    heartbeat = _heartbeat()

    with patch("prometheus_client.REGISTRY") as registry:
        registry.collect.return_value = []
        counters = heartbeat._collect_counters()

    assert counters == {
        "messages_ingested": 0,
        "messages_failed": 0,
        "source_api_calls": 0,
        "checkpoint_saves": 0,
        "dedupe_accepted": 0,
    }
    assert counters.availability == "unavailable"
    assert counters.unavailable_fields == frozenset(counters)


def test_collect_counters_ignores_unnamed_created_style_sample() -> None:
    """A family name cannot turn an unnamed timestamp into a total."""
    heartbeat = _heartbeat()
    family = _family(
        "connector_ingest_submissions",
        [
            SimpleNamespace(
                name=None,
                value=1_735_689_600,
                labels={
                    "connector_type": "gmail",
                    "endpoint_identity": "gmail:user:owner@example.com",
                    "status": "success",
                },
            )
        ],
    )

    with patch("prometheus_client.REGISTRY") as registry:
        registry.collect.return_value = [family]
        counters = heartbeat._collect_counters()

    assert counters["messages_ingested"] == 0
    assert "messages_ingested" in counters.unavailable_fields


def test_collect_counters_ignores_samples_without_a_label_mapping() -> None:
    """Malformed labels lower the field to unavailable instead of aborting a heartbeat."""
    heartbeat = _heartbeat()
    family = _family(
        "connector_ingest_submissions",
        [
            SimpleNamespace(
                name="connector_ingest_submissions_total",
                value=7,
                labels=None,
            )
        ],
    )

    with patch("prometheus_client.REGISTRY") as registry:
        registry.collect.return_value = [family]
        counters = heartbeat._collect_counters()

    assert counters["messages_ingested"] == 0
    assert "messages_ingested" in counters.unavailable_fields
