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


# ---------------------------------------------------------------------------
# REQ-runtime-attention-outbox-002 AC 6: producers reach exactly one sink.
# ---------------------------------------------------------------------------

# A producer's whole job is to append a durable episode.  Delivery belongs to
# Switchboard (RFC 0003), so any of these appearing on a producer path would be
# a second, unfenced delivery route around the at-most-once guarantee.
_FORBIDDEN_PRODUCER_SINKS = (
    "telegram",
    "messenger",
    "attention_ledger",
    "record_attention_event",
    "maybe_push_breaker_open_attention",
    "maybe_push_fleet_halt_attention",
    "debounce",
    # Call-shaped (not bare "deliver"/"notify") so this does not trip on the
    # legitimate prose word "delivery" already used elsewhere in this module
    # (e.g. "direct-delivery binary"). Guards against the idiomatic
    # function-local import: `from ...notification.deliver import deliver`
    # then `deliver(...)`, which none of the sinks above would catch.
    "deliver(",
    "notify(",
)


def test_producers_reach_no_delivery_sink_directly() -> None:
    """The breaker/fleet producers append an episode and nothing else."""
    source = inspect.getsource(dispatch_outcomes).lower()

    for sink in _FORBIDDEN_PRODUCER_SINKS:
        assert sink not in source, f"producer path still reaches {sink} directly"

    # And the one thing it *must* reach: the transactional outbox producers.
    assert "append_runtime_attention_model_breaker" in source
    assert "append_runtime_attention_fleet_halt" in source


def test_attention_ledger_carries_no_breaker_debounce() -> None:
    """The ledger stopped being the breaker's delivery-debounce sink.

    Attention-ledger debounce was the old read-send-write path: it decided
    whether to deliver by reading its own prior writes, which cannot be made
    at-most-once across a crash.  Nothing here may know about breakers.
    """
    from butlers.core import attention_ledger

    source = inspect.getsource(attention_ledger).lower()
    assert "breaker" not in source
    assert "fleet_halt" not in source
