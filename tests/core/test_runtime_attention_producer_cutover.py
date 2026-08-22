"""Producer-cutover regressions for runtime attention episodes.

REQ-model-catalog-001 and REQ-runtime-attention-outbox-001 require the
Spawner to stop invoking the legacy direct-delivery helpers once the durable
transactional producers are active.
"""

from __future__ import annotations

import inspect
import uuid
from unittest.mock import AsyncMock, Mock

import pytest

from butlers.core import dispatch_outcomes, spawner

pytestmark = pytest.mark.unit


def test_spawner_contains_no_legacy_direct_attention_delivery_hooks() -> None:
    """The producer cutover removes both read-send-write debounce paths."""
    source = inspect.getsource(spawner)

    assert "maybe_push_breaker_open_attention" not in source
    assert "maybe_push_fleet_halt_attention" not in source


async def test_recorder_rejection_emits_only_bounded_outcome_telemetry(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    counter = Mock()
    counter.labels.return_value.inc = Mock()
    monkeypatch.setattr(dispatch_outcomes, "runtime_attention_recorder_total", counter)

    result = await dispatch_outcomes.record_dispatch_attempt(
        AsyncMock(),
        catalog_entry_id=uuid.uuid4(),
        butler="general",
        outcome="runtime_failure",
        attempt_index=0,
        error_message="raw provider sentinel must not be logged",
        produce_fleet_halt=True,
    )

    assert result is None
    counter.labels.assert_called_once_with(outcome="rejected", edge="fleet_halt")
    counter.labels.return_value.inc.assert_called_once_with()
    assert "raw provider sentinel" not in caplog.text
