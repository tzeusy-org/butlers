"""Producer-cutover regressions for runtime attention episodes.

REQ-model-catalog-001 and REQ-runtime-attention-outbox-001 require the
Spawner to stop invoking the legacy direct-delivery helpers once the durable
transactional producers are active.
"""

from __future__ import annotations

import inspect

import pytest

from butlers.core import spawner

pytestmark = pytest.mark.unit


def test_spawner_contains_no_legacy_direct_attention_delivery_hooks() -> None:
    """The producer cutover removes both read-send-write debounce paths."""
    source = inspect.getsource(spawner)

    assert "maybe_push_breaker_open_attention" not in source
    assert "maybe_push_fleet_halt_attention" not in source
