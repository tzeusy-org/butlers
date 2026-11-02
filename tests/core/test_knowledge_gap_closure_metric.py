"""Knowledge-gap closure failures are exported as a bounded OTel counter (bu-q7vx1q.75)."""

from __future__ import annotations

import logging
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.core.logging import reset_butler_context, set_butler_context
from butlers.core.metrics import record_knowledge_gap_closure_failure
from butlers.modules.memory import knowledge_gaps
from tests.core.test_otel_metrics import (
    _collect_metrics,
    _make_in_memory_provider,
    _reset_metrics_global_state,
)

pytestmark = pytest.mark.unit

COUNTER = "butlers.memory.knowledge_gap_closure_failures_total"


@pytest.fixture
def reader():
    _provider, in_memory = _make_in_memory_provider()
    try:
        yield in_memory
    finally:
        _reset_metrics_global_state()


def _points(reader: Any) -> list[Any]:
    if reader.get_metrics_data() is None:  # nothing was ever recorded
        return []
    return list(_collect_metrics(reader).get(COUNTER, []))


def _close(conn: Any):
    return knowledge_gaps.close_matching_gaps(
        conn,
        entity_id=uuid.uuid4(),
        predicate="lives_in",
        ref="fact:1",
        value="x",
        authority=None,
    )


def test_recorder_uses_only_butler_and_stage_labels_and_rejects_unknown_stages(reader) -> None:
    record_knowledge_gap_closure_failure(stage="closure")

    (point,) = _points(reader)
    assert dict(point.attributes) == {"butler": "unknown", "stage": "closure"}
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
    assert dict(point.attributes) == {"butler": "unknown", "stage": "closure"}
    assert point.value == 1


async def test_a_schema_without_the_gap_table_is_not_a_failure(reader) -> None:
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=False)

    await _close(conn)

    assert _points(reader) == []


@pytest.mark.parametrize("open_gap", [1, None])
async def test_a_closure_that_succeeds_or_finds_no_open_gap_is_not_a_failure(
    reader, open_gap
) -> None:
    conn = MagicMock()
    conn.fetchval = AsyncMock(side_effect=[True, open_gap])
    conn.execute = AsyncMock()

    await _close(conn)

    assert _points(reader) == []
    # The pair lock always runs; the UPDATE runs only when a gap was open.
    assert conn.execute.await_count == (2 if open_gap else 1)


async def test_a_metric_failure_never_escapes_the_closure(monkeypatch, caplog) -> None:
    def exporter_down(**_kwargs: Any) -> None:
        raise RuntimeError("OTLP exporter unavailable")

    monkeypatch.setattr(knowledge_gaps, "record_knowledge_gap_closure_failure", exporter_down)
    conn = MagicMock()
    conn.fetchval = AsyncMock(side_effect=RuntimeError("lock service down"))

    with caplog.at_level(logging.WARNING, logger=knowledge_gaps.__name__):
        await _close(conn)

    assert "knowledge gap closure failed" in caplog.text
    assert "metric emission failed" in caplog.text


def test_the_butler_label_is_the_logging_context_name(reader) -> None:
    token = set_butler_context("relationship")
    try:
        record_knowledge_gap_closure_failure(stage="closure")
    finally:
        reset_butler_context(token)

    (point,) = _points(reader)
    assert dict(point.attributes) == {"butler": "relationship", "stage": "closure"}


def test_no_module_level_closure_counter_remains() -> None:
    assert not hasattr(knowledge_gaps, "closure_failures")


async def test_a_swallowed_reservation_error_is_counted_with_the_reservation_stage(
    reader,
) -> None:
    conn = MagicMock()
    conn.transaction = MagicMock(side_effect=RuntimeError("lock service down"))

    reserved = await knowledge_gaps.reserve_gap_pairs(conn, [(uuid.uuid4(), "lives_in")])

    assert reserved is False
    (point,) = _points(reader)
    assert dict(point.attributes) == {"butler": "unknown", "stage": "reservation"}
    assert point.value == 1


async def test_a_metric_failure_never_escapes_the_reservation(monkeypatch, caplog) -> None:
    def exporter_down(**_kwargs: Any) -> None:
        raise RuntimeError("OTLP exporter unavailable")

    monkeypatch.setattr(knowledge_gaps, "record_knowledge_gap_closure_failure", exporter_down)
    conn = MagicMock()
    conn.transaction = MagicMock(side_effect=RuntimeError("lock service down"))

    with caplog.at_level(logging.WARNING, logger=knowledge_gaps.__name__):
        assert await knowledge_gaps.reserve_gap_pairs(conn, [(uuid.uuid4(), "lives_in")]) is False

    assert "metric emission failed" in caplog.text
