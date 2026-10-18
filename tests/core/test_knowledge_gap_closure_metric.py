"""Knowledge-gap closure failures are exported as a bounded OTel counter (bu-q7vx1q.75)."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from opentelemetry import metrics
from opentelemetry.metrics import _internal as _metrics_internal
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.util._once import Once

from butlers.core.metrics import record_knowledge_gap_closure_failure
from butlers.modules.memory import knowledge_gaps

pytestmark = pytest.mark.unit

COUNTER = "butlers.memory.knowledge_gap_closure_failures_total"


@pytest.fixture
def reader():
    _metrics_internal._METER_PROVIDER_SET_ONCE = Once()
    _metrics_internal._METER_PROVIDER = None
    in_memory = InMemoryMetricReader()
    metrics.set_meter_provider(MeterProvider(metric_readers=[in_memory]))
    try:
        yield in_memory
    finally:
        _metrics_internal._METER_PROVIDER_SET_ONCE = Once()
        _metrics_internal._METER_PROVIDER = None


def _points(reader: InMemoryMetricReader) -> list[Any]:
    points: list[Any] = []
    data = reader.get_metrics_data()
    for rm in data.resource_metrics if data else []:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name == COUNTER:
                    points.extend(metric.data.data_points)
    return points


def _close(conn: Any):
    return knowledge_gaps.close_matching_gaps(
        conn,
        entity_id=uuid.uuid4(),
        predicate="lives_in",
        ref="fact:1",
        value="x",
        authority=None,
    )


def test_recorder_uses_only_the_stage_label_and_rejects_unknown_stages(reader) -> None:
    record_knowledge_gap_closure_failure(stage="closure")

    (point,) = _points(reader)
    assert dict(point.attributes) == {"stage": "closure"}
    assert point.value == 1
    for bad in ("entity 42: RuntimeError", "entity", "Closure", ""):
        with pytest.raises(ValueError, match="unsupported knowledge-gap closure failure stage"):
            record_knowledge_gap_closure_failure(stage=bad)
    assert [p.value for p in _points(reader)] == [1]


async def test_a_swallowed_closure_error_is_counted_once_and_not_raised(reader) -> None:
    conn = MagicMock()
    conn.fetchval = AsyncMock(side_effect=RuntimeError("lock service down"))

    await _close(conn)

    (point,) = _points(reader)
    assert dict(point.attributes) == {"stage": "closure"}
    assert point.value == 1


async def test_a_schema_without_the_gap_table_is_not_a_failure(reader) -> None:
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=False)

    await _close(conn)

    assert _points(reader) == []


def test_no_module_level_closure_counter_remains() -> None:
    assert not hasattr(knowledge_gaps, "closure_failures")
