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
    CONTACTS_REPORT_CHANNEL,
    CONTACTS_RESOLVE_CHANNEL,
    DOMAIN_EVENT_RECEIVE,
    MEDICATION_TRAVEL_SNAPSHOT,
    QA_REPORT_FINDING,
    build_relays,
    roster_daemons,
    roster_module_daemons,
)
from butlers.core.service_plane.relays import (
    RelayEndpoint,
    RelayRefused,
    ServiceCaller,
    target_is_llm_presentable,
)
from butlers.core.service_plane.workload_identity import (
    DELIVERY_AUDIENCE,
    RELAY_AUDIENCE,
)
from tests.core.test_service_plane_workload_identity import _claims, _token, _verifier

_CONTRACTS = load_contract_registry()
_RELAYS = build_relays(
    roster_daemons(), _CONTRACTS, contacts_daemons=roster_module_daemons("contacts")
)
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


async def test_travel_reads_the_medication_snapshot_through_healths_service_only_twin(
    tmp_path: Path,
) -> None:
    harness = _Harness()

    result = await harness.call(
        MEDICATION_TRAVEL_SNAPSHOT, {}, tmp_path, daemon="travel", token=_up_token()
    )

    assert result == {"accepted": True}
    assert harness.calls == [("health", "medication_travel_snapshot_service", {}, False)]


@pytest.mark.parametrize(
    ("args", "daemon", "code"),
    [
        ({}, "finance", "caller_not_allowed"),
        ({}, "health", "caller_not_allowed"),
        ({"source_butler": "travel"}, "travel", "args_invalid"),
        ({"include_notes": True}, "travel", "args_invalid"),
    ],
    ids=["another-daemon", "health-itself", "source_butler-in-body", "extra-field"],
)
async def test_only_travel_reads_the_medication_snapshot_and_with_no_arguments(
    tmp_path: Path, args: dict[str, Any], daemon: str, code: str
) -> None:
    harness = _Harness()

    with pytest.raises(RelayRefused) as refused:
        await harness.call(
            MEDICATION_TRAVEL_SNAPSHOT, args, tmp_path, daemon=daemon, token=_up_token()
        )

    assert refused.value.code == code
    assert harness.calls == []


def test_the_medication_relay_targets_a_non_presentable_twin_only_from_travel() -> None:
    endpoint = _RELAYS[MEDICATION_TRAVEL_SNAPSHOT]

    assert endpoint.allowed_callers == frozenset({ServiceCaller("butlers-up", "travel")})
    assert endpoint.target_butlers == frozenset({"health"})
    assert not target_is_llm_presentable(endpoint.target_tool)
    # The LLM-facing tool stays presentable; only its service twin is relay-reachable.
    assert target_is_llm_presentable("medication_travel_snapshot")


_REQUEST_ID = "6f1c1a52-8f1e-4c43-9a43-2f5d1b0c9d11"
_SUBJECT = "0b8f0a8e-7a0e-4d5e-8a52-3c2b1d0e9f77"
_LOOKUP = {"channel_type": "email", "channel_value": "a@test", "request_id": _REQUEST_ID}
_REPORT = {
    "subject": _SUBJECT,
    "channel_type": "email",
    "channel_value": "a@test",
    "primary": True,
}


@pytest.mark.parametrize(
    ("name", "args", "daemon", "tool"),
    [
        (CONTACTS_RESOLVE_CHANNEL, _LOOKUP, "health", "contacts_resolve_channel"),
        (CONTACTS_REPORT_CHANNEL, _REPORT, "general", "contacts_report_channel"),
    ],
    ids=["resolve", "report"],
)
async def test_a_contacts_daemon_reaches_relationships_contacts_ingress(
    tmp_path: Path, name: str, args: dict[str, Any], daemon: str, tool: str
) -> None:
    harness = _Harness()

    result = await harness.call(name, args, tmp_path, daemon=daemon, token=_up_token())

    assert result == {"accepted": True}
    assert harness.calls == [("relationship", tool, args, False)]


async def test_a_report_without_primary_defaults_it_to_false(tmp_path: Path) -> None:
    harness = _Harness()
    args = {key: value for key, value in _REPORT.items() if key != "primary"}

    await harness.call(CONTACTS_REPORT_CHANNEL, args, tmp_path, daemon="home", token=_up_token())

    assert harness.calls == [
        ("relationship", "contacts_report_channel", {**args, "primary": False}, False)
    ]


@pytest.mark.parametrize(
    ("name", "args", "daemon", "code"),
    [
        # finance has no Contacts module, so it is not on either allowlist.
        (CONTACTS_RESOLVE_CHANNEL, _LOOKUP, "finance", "caller_not_allowed"),
        (CONTACTS_REPORT_CHANNEL, _REPORT, "finance", "caller_not_allowed"),
        (CONTACTS_RESOLVE_CHANNEL, {**_LOOKUP, "source_butler": "x"}, "health", "args_invalid"),
        (CONTACTS_RESOLVE_CHANNEL, {**_LOOKUP, "request_id": "r1"}, "health", "args_invalid"),
        (CONTACTS_REPORT_CHANNEL, {**_REPORT, "subject": "s1"}, "health", "args_invalid"),
        (CONTACTS_REPORT_CHANNEL, {**_REPORT, "src": "owner"}, "health", "args_invalid"),
        (CONTACTS_REPORT_CHANNEL, {**_REPORT, "verified": True}, "health", "args_invalid"),
    ],
    ids=[
        "resolve-from-non-contacts-daemon",
        "report-from-non-contacts-daemon",
        "source_butler-in-body",
        "request_id-not-a-uuid",
        "subject-not-a-uuid",
        "authority-src-in-body",
        "verified-in-body",
    ],
)
async def test_contacts_relays_refuse_other_daemons_and_any_authority_in_the_body(
    tmp_path: Path, name: str, args: dict[str, Any], daemon: str, code: str
) -> None:
    harness = _Harness()

    with pytest.raises(RelayRefused) as refused:
        await harness.call(name, args, tmp_path, daemon=daemon, token=_up_token())

    assert refused.value.code == code
    assert harness.calls == []


def test_contacts_relays_admit_exactly_the_contacts_daemons_to_relationship_service_tools() -> None:
    contacts = {"education", "general", "health", "home", "lifestyle", "relationship"}
    assert roster_module_daemons("contacts") == frozenset(contacts)
    for name, tool in [
        (CONTACTS_RESOLVE_CHANNEL, "contacts_resolve_channel"),
        (CONTACTS_REPORT_CHANNEL, "contacts_report_channel"),
    ]:
        endpoint = _RELAYS[name]
        assert endpoint.allowed_callers == frozenset(
            ServiceCaller("butlers-up", daemon) for daemon in contacts
        )
        assert endpoint.target_butlers == frozenset({"relationship"})
        assert endpoint.target_tool == tool
        assert not endpoint.allow_stale
        # Already service-only: no twin is needed.
        assert not target_is_llm_presentable(tool)


def test_a_contacts_daemon_outside_the_roster_is_never_admitted() -> None:
    relays = build_relays({"health"}, _CONTRACTS, contacts_daemons={"health", "ghost"})

    assert relays[CONTACTS_RESOLVE_CHANNEL].allowed_callers == frozenset(
        {ServiceCaller("butlers-up", "health")}
    )


def test_the_relay_url_is_on_the_switchboard_service_listener() -> None:
    assert (
        relay_url(_SWITCHBOARD_URL, QA_REPORT_FINDING)
        == "http://butlers-control:41100/internal/service-plane/relay/qa.report_finding"
    )
