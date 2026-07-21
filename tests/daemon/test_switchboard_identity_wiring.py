"""Focused production-wiring coverage for entity-first sender activation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.switchboard_wiring import wire_pipelines

pytestmark = pytest.mark.unit


def _switchboard_daemon() -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(name="switchboard", buffer=SimpleNamespace()),
        spawner=SimpleNamespace(trigger=AsyncMock()),
        _active_modules=[],
        _credential_store=object(),
        _resolve_default_notify_recipient=AsyncMock(return_value="777000"),
        mcp=object(),
    )


async def test_wiring_enables_identity_and_uses_notify_v1_messenger_boundary():
    """Fleet startup must make the existing helper reachable through a real callback."""
    daemon = _switchboard_daemon()
    pool = MagicMock()

    with (
        patch("butlers.modules.pipeline.MessagePipeline") as pipeline_cls,
        patch("butlers.core.buffer.DurableBuffer"),
    ):
        wire_pipelines(daemon, pool)

    kwargs = pipeline_cls.call_args.kwargs
    assert kwargs["enable_identity_resolution"] is True
    notify_owner_fn = kwargs["notify_owner_fn"]
    assert callable(notify_owner_fn)

    with patch(
        "butlers.tools.switchboard.notification.deliver.deliver",
        new=AsyncMock(return_value={"status": "sent"}),
    ) as deliver:
        await notify_owner_fn("Received a message from Chloe L (telegram). Review it.")

    daemon._resolve_default_notify_recipient.assert_awaited_once_with(
        channel="telegram",
        intent="send",
        recipient=None,
    )
    deliver.assert_awaited_once()
    args, delivery_kwargs = deliver.await_args
    assert args == (pool,)
    assert delivery_kwargs["source_butler"] == "switchboard"
    assert set(delivery_kwargs) == {"source_butler", "notify_request"}

    envelope = delivery_kwargs["notify_request"]
    assert envelope == {
        "schema_version": "notify.v1",
        "origin_butler": "switchboard",
        "delivery": {
            "intent": "send",
            "channel": "telegram",
            "recipient": "777000",
            "message": "Received a message from Chloe L (telegram). Review it.",
        },
    }


async def test_wired_owner_callback_surfaces_failed_delivery_to_identity_helper():
    """The callback must reject a failed delivery so the helper can log-and-seal it."""
    daemon = _switchboard_daemon()
    pool = MagicMock()

    with (
        patch("butlers.modules.pipeline.MessagePipeline") as pipeline_cls,
        patch("butlers.core.buffer.DurableBuffer"),
    ):
        wire_pipelines(daemon, pool)

    notify_owner_fn = pipeline_cls.call_args.kwargs["notify_owner_fn"]
    with patch(
        "butlers.tools.switchboard.notification.deliver.deliver",
        new=AsyncMock(return_value={"status": "failed", "error": "messenger unavailable"}),
    ):
        with pytest.raises(RuntimeError, match="messenger unavailable"):
            await notify_owner_fn("Review unknown sender")
