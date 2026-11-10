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
from butlers.core.service_plane.client import (
    RelayUnavailable,
    call_relay,
    refusal_is_permanent,
    relay_url,
)
from butlers.core.service_plane.endpoint import build_relay_route
from butlers.core.service_plane.registry import (
    CONTACTS_REPORT_CHANNEL,
    CONTACTS_RESOLVE_CHANNEL,
    DOMAIN_EVENT_RECEIVE,
    FLEET_CASE_CLOSE,
    FLEET_CASE_OPEN,
    FLEET_CASE_PROPOSE_POSTURE,
    FLEET_CASE_RECORD_LINK,
    MEDICATION_TRAVEL_SNAPSHOT,
    MEMORY_CATALOG_FETCH,
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
    VerifierUnavailable,
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


_DEFAULT = object()


class _UnavailableVerifier:
    """The key set cannot be read right now (API-server blip, stale cache)."""

    async def verify(self, token: str) -> str | None:
        raise VerifierUnavailable("jwks unavailable")


class _Harness:
    def __init__(
        self,
        *,
        enabled: bool = True,
        relays: Mapping[str, RelayEndpoint] = _RELAYS,
        verifier: Any = _DEFAULT,
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
            verifier=(lambda: _verifier(RELAY_AUDIENCE))
            if verifier is _DEFAULT
            else (lambda: verifier),
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
    # Not served is not a judgement on the caller: retryable, never permanent.
    assert refused.value.status == 404
    assert not refusal_is_permanent(refused.value)


@pytest.mark.parametrize(
    ("status", "code", "permanent"),
    [
        (403, "caller_not_allowed", True),
        (403, "args_invalid", True),
        (404, "not_found", False),
        (404, "unknown_relay", False),
        (400, "args_invalid", False),
        (503, "auth_unavailable", False),
    ],
)
async def test_only_a_403_refusal_is_permanent(
    tmp_path: Path, status: int, code: str, permanent: bool
) -> None:
    (tmp_path / RELAY_AUDIENCE).write_text("t")
    reply = httpx.Response(status, json={"status": "refused", "code": code})
    transport = httpx.MockTransport(lambda request: reply)

    with pytest.raises(RelayRefused) as refused:
        await call_relay(
            QA_REPORT_FINDING,
            _FINDING,
            daemon="travel",
            switchboard_url=_SWITCHBOARD_URL,
            token_dir=tmp_path,
            transport=transport,
        )

    assert (refused.value.code, refused.value.status) == (code, status)
    assert refusal_is_permanent(refused.value) is permanent


@pytest.mark.parametrize(
    "verifier", [None, _UnavailableVerifier()], ids=["no-verifier", "key-set-unavailable"]
)
async def test_a_caller_that_cannot_be_verified_now_is_503_and_retryable(
    tmp_path: Path, verifier: Any
) -> None:
    harness = _Harness(verifier=verifier)

    with pytest.raises(RelayRefused) as refused:
        await harness.call(
            QA_REPORT_FINDING, _FINDING, tmp_path, daemon="travel", token=_up_token()
        )

    assert (refused.value.code, refused.value.status) == ("auth_unavailable", 503)
    assert not refusal_is_permanent(refused.value)
    assert harness.calls == []


async def test_an_invalid_token_is_still_a_permanent_403(tmp_path: Path) -> None:
    harness = _Harness()

    with pytest.raises(RelayRefused) as refused:
        await harness.call(
            QA_REPORT_FINDING, _FINDING, tmp_path, daemon="travel", token=_up_token("other.aud")
        )

    assert (refused.value.code, refused.value.status) == ("caller_unauthenticated", 403)
    assert refusal_is_permanent(refused.value)


def test_unavailability_and_other_errors_are_never_permanent() -> None:
    assert not refusal_is_permanent(RelayUnavailable("http_502"))
    assert not refusal_is_permanent(OSError("token missing"))
    assert refusal_is_permanent(RelayRefused("caller_not_allowed"))


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


_CASE = "11111111-1111-1111-1111-111111111111"
_FLEET_WRITES = [
    (FLEET_CASE_OPEN, "open_case_service", {"correlation_key": "health:owner:x"}),
    (FLEET_CASE_CLOSE, "close_case_service", {"case_id": _CASE, "outcome": "resolved"}),
    (
        FLEET_CASE_PROPOSE_POSTURE,
        "propose_case_posture_service",
        {"case_id": _CASE, "posture": "urgent"},
    ),
    (
        FLEET_CASE_RECORD_LINK,
        "record_case_link_service",
        {"case_id": _CASE, "link_kind": "owner_condition", "ref": "c1"},
    ),
]


@pytest.mark.parametrize(("name", "tool", "args"), _FLEET_WRITES, ids=[w[1] for w in _FLEET_WRITES])
async def test_a_domain_daemon_writes_a_fleet_case_through_switchboards_twin(
    tmp_path: Path, name: str, tool: str, args: dict[str, Any]
) -> None:
    harness = _Harness()

    result = await harness.call(name, args, tmp_path, daemon="finance", token=_up_token())

    assert result == {"accepted": True}
    assert harness.calls == [("switchboard", tool, args, False)]


@pytest.mark.parametrize(("name", "tool", "args"), _FLEET_WRITES, ids=[w[1] for w in _FLEET_WRITES])
async def test_fleet_case_relays_refuse_switchboards_name_and_extra_fields(
    tmp_path: Path, name: str, tool: str, args: dict[str, Any]
) -> None:
    harness = _Harness()
    cases = [
        (args, "switchboard", "caller_not_allowed"),
        ({**args, "source_butler": "health"}, "health", "args_invalid"),
        ({**args, "origin_butler": "health"}, "health", "args_invalid"),
    ]
    for body, daemon, code in cases:
        with pytest.raises(RelayRefused) as refused:
            await harness.call(name, body, tmp_path, daemon=daemon, token=_up_token())
        assert refused.value.code == code
    assert harness.calls == []


def test_fleet_case_relays_admit_every_domain_daemon_to_non_presentable_twins() -> None:
    domain = {ServiceCaller("butlers-up", d) for d in roster_daemons() if d != "switchboard"}
    for name, tool, _ in _FLEET_WRITES:
        endpoint = _RELAYS[name]
        assert endpoint.allowed_callers == frozenset(domain)
        assert endpoint.target_butlers == frozenset({"switchboard"})
        assert endpoint.target_tool == tool
        assert not target_is_llm_presentable(tool)
        # The LLM-facing tool stays presentable; only its twin is relay-reachable.
        assert target_is_llm_presentable(tool.removesuffix("_service"))


_FACT = "22222222-2222-2222-2222-222222222222"
_POINTER = {"source_schema": "health", "source_table": "facts", "source_id": _FACT}


class _Catalog:
    """``public.memory_catalog`` as Switchboard reads it: pointer -> owner row."""

    def __init__(self, rows: Mapping[tuple[str, str, str], dict[str, Any]]) -> None:
        self.rows = rows
        self.lookups: list[tuple[str, str, str]] = []

    async def __call__(self, schema: str, table: str, source_id: Any) -> dict[str, Any] | None:
        key = (schema, table, str(source_id))
        self.lookups.append(key)
        return self.rows.get(key)


def _memory_relays(catalog: _Catalog, memory_daemons=("health", "finance")):
    return build_relays(
        roster_daemons(),
        _CONTRACTS,
        memory_daemons=memory_daemons,
        catalog_pointer=catalog,
    )


@pytest.mark.parametrize(
    ("table", "owner", "memory_type"),
    [("facts", "health", "fact"), ("rules", "finance", "rule")],
)
async def test_a_catalog_fetch_reaches_the_owner_its_catalog_row_names(
    tmp_path: Path, table: str, owner: str, memory_type: str
) -> None:
    catalog = _Catalog(
        {("health", table, _FACT): {"source_butler": owner, "memory_type": memory_type}}
    )
    harness = _Harness(relays=_memory_relays(catalog))

    result = await harness.call(
        MEMORY_CATALOG_FETCH,
        {**_POINTER, "source_table": table},
        tmp_path,
        daemon="travel",
        token=_up_token(),
    )

    assert result == {"accepted": True}
    assert catalog.lookups == [("health", table, _FACT)]
    assert harness.calls == [
        (owner, "memory_get_service", {"memory_type": memory_type, "memory_id": _FACT}, False)
    ]


@pytest.mark.parametrize(
    ("rows", "body", "daemon", "code"),
    [
        ({}, _POINTER, "finance", "catalog_pointer_not_found"),
        # The catalog names an owner that hosts no memory module.
        (
            {("health", "facts", _FACT): {"source_butler": "qa", "memory_type": "fact"}},
            _POINTER,
            "finance",
            "target_not_allowed",
        ),
        ({}, {**_POINTER, "source_table": "episodes"}, "finance", "args_invalid"),
        ({}, {**_POINTER, "source_id": "not-a-uuid"}, "finance", "args_invalid"),
        ({}, {**_POINTER, "target_butler": "health"}, "finance", "args_invalid"),
        ({}, {**_POINTER, "memory_type": "rule"}, "finance", "args_invalid"),
        ({}, _POINTER, "switchboard", "caller_not_allowed"),
    ],
    ids=[
        "pointer-not-in-catalog",
        "owner-without-memory",
        "table-outside-facts-rules",
        "id-not-a-uuid",
        "caller-chosen-target",
        "caller-chosen-type",
        "switchboard-name-from-butlers-up",
    ],
)
async def test_a_catalog_fetch_never_reaches_a_target_the_catalog_does_not_name(
    tmp_path: Path, rows: dict, body: dict[str, Any], daemon: str, code: str
) -> None:
    harness = _Harness(relays=_memory_relays(_Catalog(rows)))

    with pytest.raises(RelayRefused) as refused:
        await harness.call(MEMORY_CATALOG_FETCH, body, tmp_path, daemon=daemon, token=_up_token())

    assert refused.value.code == code
    assert refusal_is_permanent(refused.value)
    assert harness.calls == []


def test_the_catalog_fetch_relay_targets_memory_daemons_non_presentable_twin() -> None:
    relays = _memory_relays(_Catalog({}), memory_daemons=roster_module_daemons("memory"))
    endpoint = relays[MEMORY_CATALOG_FETCH]

    assert endpoint.target_butlers == roster_module_daemons("memory")
    assert "qa" not in endpoint.target_butlers
    assert endpoint.allowed_callers == frozenset(
        ServiceCaller("butlers-up", d) for d in roster_daemons() if d != "switchboard"
    )
    assert endpoint.target_tool == "memory_get_service"
    assert not target_is_llm_presentable("memory_get_service")
    assert target_is_llm_presentable("memory_get")


def test_without_a_catalog_reader_no_catalog_fetch_relay_is_served() -> None:
    assert MEMORY_CATALOG_FETCH not in build_relays(
        roster_daemons(), _CONTRACTS, memory_daemons={"health"}
    )


def test_the_relay_url_is_on_the_switchboard_service_listener() -> None:
    assert (
        relay_url(_SWITCHBOARD_URL, QA_REPORT_FINDING)
        == "http://butlers-control:41100/internal/service-plane/relay/qa.report_finding"
    )
