"""The named-relay endpoint end to end: daemon client, HTTP route, relay, target (bu-2c1g7v)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette

from butlers.core.domain_event_contracts import (
    DomainEventContractRegistry,
    load_contract_registry,
)
from butlers.core.service_plane.client import RelayUnavailable, call_relay, relay_url
from butlers.core.service_plane.endpoint import build_relay_route
from butlers.core.service_plane.registry import (
    DOMAIN_EVENT_RECEIVE,
    QA_REPORT_FINDING,
    build_relays,
    roster_daemons,
)
from butlers.core.service_plane.relays import RelayEndpoint, RelayRefused, ServiceCaller
from butlers.core.service_plane.workload_identity import (
    DELIVERY_AUDIENCE,
    RELAY_AUDIENCE,
)
from tests.core.test_service_plane_workload_identity import _claims, _token, _verifier

_CONTRACTS = load_contract_registry()
_RELAYS = build_relays(roster_daemons(), _CONTRACTS)
_SWITCHBOARD_URL = "http://butlers-control:41100/sse"
_FINDING = {
    "fingerprint": "fp1",
    "exception_type": "ValueError",
    "call_site": "travel.tools:12",
    "severity": 2,
    "event_summary": "boom",
}


def _up_token(audience: str = RELAY_AUDIENCE) -> str:
    claims = _claims(aud=[audience], sub="system:serviceaccount:butlers-dev:butlers-up")
    claims["kubernetes.io"] = {
        **claims["kubernetes.io"],
        "serviceaccount": {"name": "butlers-up", "uid": "u3"},
    }
    return _token(claims)


class _Harness:
    def __init__(
        self, *, enabled: bool = True, relays: Mapping[str, RelayEndpoint] = _RELAYS
    ) -> None:
        self.calls: list[tuple[str, str, dict[str, Any], bool]] = []
        self.enabled = enabled

        def dispatch_for(endpoint: RelayEndpoint):
            async def dispatch(butler: str, tool: str, args: dict[str, Any]) -> Any:
                self.calls.append((butler, tool, args, endpoint.allow_stale))
                return {"accepted": True}

            return dispatch

        route = build_relay_route(
            enabled=lambda: self.enabled,
            relays=lambda: relays,
            verifier=lambda: _verifier(RELAY_AUDIENCE),
            dispatch_for=dispatch_for,
        )
        self.transport = httpx.ASGITransport(app=Starlette(routes=[route]))

    async def call(
        self, name: str, args: dict[str, Any], tmp_path: Path, *, daemon: str, token: str
    ) -> Any:
        (tmp_path / RELAY_AUDIENCE).write_text(token)
        return await call_relay(
            name,
            args,
            daemon=daemon,
            switchboard_url=_SWITCHBOARD_URL,
            token_dir=tmp_path,
            transport=self.transport,
        )


async def test_the_qa_relay_attributes_the_finding_to_the_authenticated_daemon(
    tmp_path: Path,
) -> None:
    harness = _Harness()

    result = await harness.call(
        QA_REPORT_FINDING, _FINDING, tmp_path, daemon="travel", token=_up_token()
    )

    assert result == {"accepted": True}
    assert harness.calls == [
        ("qa", "report_finding", {**_FINDING, "source_butler": "travel"}, True)
    ]


@pytest.mark.parametrize(
    ("name", "args", "daemon", "token", "code"),
    [
        (
            QA_REPORT_FINDING,
            {**_FINDING, "source_butler": "health"},
            "travel",
            _up_token(),
            "args_invalid",
        ),
        (
            QA_REPORT_FINDING,
            _FINDING,
            "travel",
            _up_token(DELIVERY_AUDIENCE),
            "caller_unauthenticated",
        ),
        # Switchboard's own daemon name presented from butlers-up is not an allowed caller.
        (QA_REPORT_FINDING, _FINDING, "switchboard", _up_token(), "caller_not_allowed"),
        # The control workload is not a domain workload.
        (
            QA_REPORT_FINDING,
            _FINDING,
            "travel",
            _token(_claims(aud=[RELAY_AUDIENCE])),
            "caller_not_allowed",
        ),
        ("no.such.relay", _FINDING, "travel", _up_token(), "unknown_relay"),
    ],
    ids=[
        "source_butler-in-body",
        "delivery-audience-token",
        "switchboard-name-from-butlers-up",
        "control-workload",
        "unknown-relay",
    ],
)
async def test_refusals_reach_the_caller_and_nothing_reaches_a_target(
    tmp_path: Path, name: str, args: dict[str, Any], daemon: str, token: str, code: str
) -> None:
    harness = _Harness()

    with pytest.raises(RelayRefused) as refused:
        await harness.call(name, args, tmp_path, daemon=daemon, token=token)

    assert refused.value.code == code
    assert harness.calls == []


async def test_the_switch_off_serves_no_relay(tmp_path: Path) -> None:
    harness = _Harness(enabled=False)

    with pytest.raises(RelayRefused) as refused:
        await harness.call(
            QA_REPORT_FINDING, _FINDING, tmp_path, daemon="travel", token=_up_token()
        )

    assert refused.value.code == "not_found"
    assert harness.calls == []


def _a_contract():
    return next(c for c in _CONTRACTS.contracts() if c.permitted_subscribers)


async def test_a_publisher_fans_out_only_to_its_contracts_permitted_subscribers(
    tmp_path: Path,
) -> None:
    contract = _a_contract()
    subscriber = contract.permitted_subscribers[0]
    harness = _Harness()
    event = {
        "subscriber": subscriber,
        "event_id": "e1",
        "event_type": contract.event_type,
        "payload": {"k": "v"},
    }

    await harness.call(
        DOMAIN_EVENT_RECEIVE, event, tmp_path, daemon=contract.publisher, token=_up_token()
    )

    assert harness.calls == [
        (
            subscriber,
            "receive_domain_event",
            {
                "event_id": "e1",
                "event_type": contract.event_type,
                "source_butler": contract.publisher,
                "payload": {"k": "v"},
            },
            False,
        )
    ]


async def test_a_domain_event_from_a_non_publisher_or_to_a_non_subscriber_is_refused(
    tmp_path: Path,
) -> None:
    # Synthetic contracts: travel publishes travel.a to health, travel.b to finance;
    # health publishes health.c to nobody. general publishes nothing.
    contracts = DomainEventContractRegistry.from_declarations(
        [
            ("travel", _declaration("travel.a", ["health"])),
            ("travel", _declaration("travel.b", ["finance"])),
            ("health", _declaration("health.c", [])),
        ]
    )
    harness = _Harness(relays=build_relays({"travel", "health", "general"}, contracts))
    event = {"event_id": "e1", "event_type": "travel.a", "payload": {}}

    cases = [
        # finance subscribes to travel.b, not travel.a: a subscriber of *some* contract is not enough.
        ({**event, "subscriber": "finance"}, "travel", "target_not_allowed"),
        # health publishes, but not travel.a.
        ({**event, "subscriber": "health"}, "health", "caller_not_publisher"),
        # general publishes nothing, so it is not on the relay's allowlist at all.
        ({**event, "subscriber": "health"}, "general", "caller_not_allowed"),
    ]
    for args, daemon, code in cases:
        with pytest.raises(RelayRefused) as refused:
            await harness.call(
                DOMAIN_EVENT_RECEIVE, args, tmp_path, daemon=daemon, token=_up_token()
            )
        assert refused.value.code == code
    assert harness.calls == []


def _declaration(event_type: str, subscribers: list[str]) -> dict[str, Any]:
    return {
        "type": event_type,
        "schema_version": 1,
        "summary": "s",
        "retention_policy": "standard",
        "reaction_expectation": "optional",
        "reaction_contract": "r",
        "permitted_subscribers": subscribers,
        "required_fields": [],
        "optional_fields": [],
    }


async def test_a_body_with_anything_beside_args_is_refused(tmp_path: Path) -> None:
    harness = _Harness()
    app_client = httpx.AsyncClient(transport=harness.transport, base_url="http://sb")
    (tmp_path / RELAY_AUDIENCE).write_text(_up_token())

    async with app_client as client:
        response = await client.post(
            f"/internal/service-plane/relay/{QA_REPORT_FINDING}",
            json={"args": _FINDING, "source_butler": "health"},
            headers={
                "x-butlers-workload-token": _up_token(),
                "x-butlers-workload-daemon": "travel",
            },
        )

    assert response.status_code == 400
    assert response.json() == {"status": "refused", "code": "args_invalid"}
    assert harness.calls == []


def test_the_qa_relay_admits_every_domain_daemon_on_butlers_up_and_not_switchboard() -> None:
    callers = _RELAYS[QA_REPORT_FINDING].allowed_callers

    assert ServiceCaller("butlers-up", "travel") in callers
    assert ServiceCaller("butlers-up", "switchboard") not in callers
    assert {caller.workload for caller in callers} == {"butlers-up"}


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(502, text="bad gateway"),
        httpx.Response(500, json={"status": "ok", "result": {"accepted": True}}),
        httpx.Response(200, json={"status": "pending"}),
        httpx.Response(200, json=["not", "an", "object"]),
    ],
    ids=["not-json", "ok-body-on-error-status", "unknown-status", "not-an-object"],
)
async def test_a_reply_outside_the_contract_is_unavailable_not_refused(
    tmp_path: Path, reply: httpx.Response
) -> None:
    (tmp_path / RELAY_AUDIENCE).write_text("t")
    transport = httpx.MockTransport(lambda request: reply)

    with pytest.raises(RelayUnavailable):
        await call_relay(
            QA_REPORT_FINDING,
            _FINDING,
            daemon="travel",
            switchboard_url=_SWITCHBOARD_URL,
            token_dir=tmp_path,
            transport=transport,
        )


def test_the_relay_url_is_on_the_switchboard_service_listener() -> None:
    assert (
        relay_url(_SWITCHBOARD_URL, QA_REPORT_FINDING)
        == "http://butlers-control:41100/internal/service-plane/relay/qa.report_finding"
    )
