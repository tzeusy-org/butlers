"""Regression test for the Switchboard router's default complexity tier.

The router historically defaulted an unspecified ``complexity`` to the RETIRED
tier ``"medium"`` (core_092 vocabulary). That value gets remapped to
``"workhorse"`` by ``_check_deprecated_tier`` and emits a LOUD deprecation
warning on every dispatch. The canonical default is now ``"workhorse"`` —
matching ``contracts.RouteInputV1.complexity`` and
``scheduler._DEFAULT_COMPLEXITY`` — so no deprecation warning fires.

See bu-wl85d.
"""

from __future__ import annotations

import logging
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import butlers.tools.switchboard.routing.route  # noqa: F401  (ensure submodule is imported)
from butlers.core import model_routing
from butlers.core.model_routing import Complexity

# The ``routing`` package re-exports the ``route`` function, shadowing the
# submodule attribute, so fetch the real module object from sys.modules.
route_module = sys.modules["butlers.tools.switchboard.routing.route"]

pytestmark = pytest.mark.unit


class _RecordingSpan:
    """Minimal span stand-in that records ``set_attribute`` calls."""

    def __init__(self, captured: dict[str, object]) -> None:
        self._captured = captured

    def set_attribute(self, key: str, value: object) -> None:
        self._captured[key] = value

    def set_status(self, *_args: object, **_kwargs: object) -> None:
        pass

    def __enter__(self) -> _RecordingSpan:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


async def test_route_default_complexity_is_workhorse_without_deprecation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A dispatch with no explicit complexity defaults to the canonical
    ``workhorse`` tier and does NOT log the medium->workhorse deprecation
    warning."""
    captured: dict[str, object] = {}

    fake_tracer = MagicMock()
    fake_tracer.start_as_current_span = lambda *_a, **_k: _RecordingSpan(captured)

    telemetry = MagicMock()
    telemetry.attrs.return_value = {}

    pool = AsyncMock()
    target_row = {"endpoint_url": "http://localhost:9999/mcp"}
    call_fn = AsyncMock(return_value={"ok": True})

    with (
        patch.object(route_module.trace, "get_tracer", return_value=fake_tracer),
        patch.object(route_module, "get_switchboard_telemetry", return_value=telemetry),
        patch.object(
            route_module,
            "resolve_routing_target",
            AsyncMock(return_value=(target_row, None)),
        ),
        caplog.at_level(logging.WARNING),
    ):
        result = await route_module.route(
            pool,
            "health",
            "ping",
            {"prompt": "hi"},  # no "complexity" key
            call_fn=call_fn,
        )

    # The route succeeded via the injected call_fn.
    assert result == {"result": {"ok": True}}

    # Default complexity is the canonical workhorse tier, not the retired "medium".
    assert captured["routing.complexity"] == Complexity.WORKHORSE.value
    assert captured["routing.complexity"] == "workhorse"

    # The defaulted value is already canonical: feeding it through the
    # dispatch-time deprecation guard does not remap it or warn.
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=model_routing.logger.name):
        canonical = model_routing._check_deprecated_tier(str(captured["routing.complexity"]))
    assert canonical == "workhorse"
    assert not any(
        "DEPRECATED complexity_tier" in record.getMessage() for record in caplog.records
    ), "default complexity must not trigger the medium->workhorse deprecation warning"
