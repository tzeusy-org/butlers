"""Callers move to the named relays only when the switch is on (bu-2c1g7v).

With ``BUTLERS_SERVICE_PLANE_AUTH`` off, the QA relay and domain-event fan-out
use the legacy Switchboard ``route`` path exactly as before; with it on, they
call the relay and never touch the generic route proxy.
"""

from __future__ import annotations

from typing import Any

import butlers.core.service_plane.client as relay_client
from butlers.core.qa.relay import QaRelay
from butlers.core.service_plane.relays import RelayRefused
from butlers.core_tools import _domain_events

_FINDING: dict[str, Any] = dict(
    fingerprint="fp1",
    exception_type="ValueError",
    call_site="travel.tools:12",
    severity=2,
    event_summary="boom",
    context=None,
)


class _LegacyClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        self.calls.append((name, args))
        if name == "list_butlers":
            return [{"name": "qa"}]
        return {"result": {"accepted": True}}


class _Relay:
    def __init__(self, reply: Any = None, refuse: str | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any], str]] = []
        self.urls: list[str] = []
        self.reply = reply
        self.refuse = refuse

    async def __call__(
        self, name: str, args: dict[str, Any], *, daemon: str, switchboard_url: str, **_: Any
    ) -> Any:
        self.calls.append((name, args, daemon))
        self.urls.append(switchboard_url)
        if self.refuse:
            raise RelayRefused(self.refuse)
        return self.reply


async def test_switch_off_keeps_the_qa_relay_on_the_legacy_route(monkeypatch) -> None:
    monkeypatch.delenv("BUTLERS_SERVICE_PLANE_AUTH", raising=False)
    relay = _Relay()
    monkeypatch.setattr(relay_client, "call_relay", relay)
    client = _LegacyClient()

    result = await QaRelay("travel", None, lambda: client, "http://sb:41100/sse")._try_qa_relay(
        **_FINDING
    )

    assert result["accepted"] is True
    assert relay.calls == []
    assert [name for name, _ in client.calls] == ["list_butlers", "route"]
    assert client.calls[1][1]["args"]["source_butler"] == "travel"


async def test_switch_on_sends_the_finding_through_the_relay_without_source_butler(
    monkeypatch,
) -> None:
    monkeypatch.setenv("BUTLERS_SERVICE_PLANE_AUTH", "1")
    relay = _Relay(reply={"result": {"accepted": True}})
    monkeypatch.setattr(relay_client, "call_relay", relay)
    client = _LegacyClient()

    result = await QaRelay("travel", None, lambda: client, "http://sb:41100/sse")._try_qa_relay(
        **_FINDING
    )

    assert result["accepted"] is True
    assert client.calls == []
    ((name, args, daemon),) = relay.calls
    assert (name, daemon) == ("qa.report_finding", "travel")
    assert "source_butler" not in args
    assert "context" not in args


async def test_switch_on_a_relay_refusal_is_an_unconfirmed_reception(monkeypatch) -> None:
    monkeypatch.setenv("BUTLERS_SERVICE_PLANE_AUTH", "1")
    monkeypatch.setattr(relay_client, "call_relay", _Relay(refuse="caller_not_allowed"))

    result = await QaRelay("travel", None, lambda: None, "http://sb:41100/sse")._try_qa_relay(
        **_FINDING
    )

    assert result["accepted"] is False
    assert result["reason"] == "relay_failed"


_EVENT = {"event_id": "e1", "event_type": "t.v1", "source_butler": "health", "payload": {"a": 1}}


class _Transport:
    url = "http://butlers-control:41100/mcp"


class _ConfiguredClient:
    """The daemon's Switchboard client: built from ``config.switchboard_url``."""

    transport = _Transport()


async def test_switch_off_keeps_fan_out_on_the_legacy_route(monkeypatch) -> None:
    monkeypatch.delenv("BUTLERS_SERVICE_PLANE_AUTH", raising=False)
    relay = _Relay()
    monkeypatch.setattr(relay_client, "call_relay", relay)
    legacy: list[dict[str, Any]] = []

    async def fake_dispatch(client: Any, pool: Any, butler: str, **kwargs: Any) -> Any:
        legacy.append(kwargs)
        return {"ok": True}, None, False

    monkeypatch.setattr(_domain_events, "dispatch_via_switchboard_route", fake_dispatch)

    await _domain_events._dispatch_receive_via_switchboard(
        object(), None, "health", target_butler="finance", args=_EVENT
    )

    assert relay.calls == []
    assert legacy[0]["tool_name"] == "receive_domain_event"


async def test_switch_on_fans_out_through_the_relay_and_classifies_refusal_as_permanent(
    monkeypatch,
) -> None:
    monkeypatch.setenv("BUTLERS_SERVICE_PLANE_AUTH", "1")

    async def no_legacy(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the generic route proxy must not be used")

    monkeypatch.setattr(_domain_events, "dispatch_via_switchboard_route", no_legacy)
    relay = _Relay(reply={"result": {"status": "task_created"}})
    monkeypatch.setattr(relay_client, "call_relay", relay)

    data, error, _ = await _domain_events._dispatch_receive_via_switchboard(
        _ConfiguredClient(), None, "health", target_butler="finance", args=_EVENT
    )

    assert error is None and data is not None
    ((name, args, daemon),) = relay.calls
    assert (name, daemon) == ("domain_event.receive", "health")
    # The relay goes to the Switchboard the daemon is configured for, not an env default.
    assert relay.urls == ["http://butlers-control:41100/mcp"]
    assert args == {
        "subscriber": "finance",
        "event_id": "e1",
        "event_type": "t.v1",
        "payload": {"a": 1},
    }

    monkeypatch.setattr(relay_client, "call_relay", _Relay(refuse="target_not_allowed"))
    data, error, retryable = await _domain_events._dispatch_receive_via_switchboard(
        _ConfiguredClient(), None, "health", target_butler="finance", args=_EVENT
    )

    assert data is None and error == "relay_refused: target_not_allowed" and retryable is False


async def test_switch_on_without_a_configured_switchboard_is_retryable_not_legacy(
    monkeypatch,
) -> None:
    monkeypatch.setenv("BUTLERS_SERVICE_PLANE_AUTH", "1")

    async def no_legacy(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the generic route proxy must not be used")

    monkeypatch.setattr(_domain_events, "dispatch_via_switchboard_route", no_legacy)
    relay = _Relay()
    monkeypatch.setattr(relay_client, "call_relay", relay)

    for client in (None, object()):
        data, error, retryable = await _domain_events._dispatch_receive_via_switchboard(
            client, None, "health", target_butler="finance", args=_EVENT
        )
        assert (data, retryable) == (None, True)
        assert error == "relay_unavailable: no configured Switchboard"
    assert relay.calls == []


async def test_switch_on_keeps_the_switchboard_sweep_in_process(monkeypatch) -> None:
    """The reconciliation sweep runs inside Switchboard (control_plane_internal)."""
    monkeypatch.setenv("BUTLERS_SERVICE_PLANE_AUTH", "1")
    relay = _Relay()
    monkeypatch.setattr(relay_client, "call_relay", relay)
    legacy: list[Any] = []

    async def in_process(client: Any, pool: Any, butler: str, **kwargs: Any) -> Any:
        legacy.append(client)
        return {"status": "task_created"}, None, False

    monkeypatch.setattr(_domain_events, "dispatch_via_switchboard_route", in_process)
    sweep_client = _domain_events._SwitchboardInProcessRouteClient(None)

    data, error, _ = await _domain_events._dispatch_receive_via_switchboard(
        sweep_client, None, "health", target_butler="finance", args=_EVENT
    )

    assert error is None and data == {"status": "task_created"}
    assert legacy == [sweep_client]
    assert relay.calls == []
