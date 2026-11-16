"""Chronicler asks Switchboard to forget through ``location_retention.forget_copies`` (S4c-3).

``relay_forgetter`` speaks as the ``chronicler`` daemon, sends exactly the grant id and its
keys, and turns every refusal or transport failure into ``HoldersUnavailable``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from butlers.chronicler import location_retention_holders as holders
from butlers.core.service_plane import client
from butlers.core.service_plane.registry import LOCATION_RETENTION_FORGET_COPIES
from butlers.core.service_plane.relays import RelayRefused

pytestmark = pytest.mark.unit

_GRANT = UUID("01936a00-0000-7000-8000-000000000001")
_KEYS = ("owntracks:owntracks:alice:1774519200:location",)
_URL = "http://butlers-control:41100/sse"


async def test_the_forgetter_calls_the_relay_as_chronicler(monkeypatch) -> None:
    seen: list[Any] = []

    async def call_relay(name: str, args: dict, *, daemon: str, switchboard_url: str) -> Any:
        seen.append((name, args, daemon, switchboard_url))
        return {"outcome": "complete"}

    monkeypatch.setattr(client, "call_relay", call_relay)
    reply = await holders.relay_forgetter(lambda: _URL)(_GRANT, _KEYS)
    assert reply == {"outcome": "complete"}
    assert seen == [
        (
            LOCATION_RETENTION_FORGET_COPIES,
            {"grant_id": str(_GRANT), "logical_source_keys": list(_KEYS)},
            "chronicler",
            _URL,
        )
    ]


@pytest.mark.parametrize(
    "error",
    [RelayRefused("caller_not_allowed"), client.RelayUnavailable("http_503"), OSError("token")],
)
async def test_every_relay_failure_is_holders_unavailable(monkeypatch, error) -> None:
    async def call_relay(*args: Any, **kwargs: Any) -> Any:
        raise error

    monkeypatch.setattr(client, "call_relay", call_relay)
    with pytest.raises(holders.HoldersUnavailable):
        await holders.relay_forgetter(lambda: _URL)(_GRANT, _KEYS)


async def test_an_unconfigured_switchboard_is_holders_unavailable(monkeypatch) -> None:
    async def call_relay(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("called")

    monkeypatch.setattr(client, "call_relay", call_relay)
    with pytest.raises(holders.HoldersUnavailable, match="unconfigured"):
        await holders.relay_forgetter(lambda: None)(_GRANT, _KEYS)
