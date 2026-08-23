"""The private runtime-probe control endpoint and its dedicated client.

REQ-dashboard-model-settings-001 fixes the wire contract: ``POST
/_control/runtime-probe/v1``, the compact capability only in ``Authorization:
Bearer``, and the typed ``200/completed``, ``401/unauthorized``,
``409/replay``, ``429/busy``, ``503/unavailable``, ``504/timeout`` pairs.
REQ-core-credentials-002 fixes what may not appear in the payload.

The endpoint tests concentrate on the shapes a caller might use to smuggle a
capability past the one place it is allowed to appear --- a cookie, a query
parameter, a JSON body --- and on the response never carrying provider or
capability material.  The client tests concentrate on the fail-closed rule:
with no signer mounted it must sign nothing and reach nobody.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from starlette.applications import Starlette

import butlers.core.runtime_probe_control.capability as cap
import butlers.core.runtime_probe_control.client as control_client
import butlers.core.runtime_probe_control.endpoint as endpoint
from butlers.core.runtime_probe_control.coordinator import (
    HTTP_STATUS,
    ProbeResult,
    ProbeStatus,
)
from butlers.core.runtime_probe_control.keys import (
    SignerSnapshot,
    match_signer_to_keyring,
    parse_signer_document,
    parse_verifier_keyring_document,
)
from butlers.testing.runtime_probe_control import (
    current_entry,
    keyring_document,
    signer_document,
    synthetic_keypair,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
_KID = "probe-2026-05a"
_ENTRY_ID = UUID("a1b2c3d4-5566-4788-99aa-bbccddeeff01")


def _encode(document: object) -> bytes:
    return json.dumps(document).encode("utf-8")


@pytest.fixture
def signer():
    seed, public_key = synthetic_keypair()
    parsed = parse_signer_document(
        _encode(signer_document(seed, kid=_KID, sign_from=_NOW - timedelta(days=1)))
    )
    return parsed, public_key


@pytest.fixture
def keyring(signer):
    _, public_key = signer
    return parse_verifier_keyring_document(
        _encode(
            keyring_document(
                current_entry(public_key, kid=_KID, sign_from=_NOW - timedelta(days=1))
            )
        )
    )


class _Coordinator:
    """Records what reached the coordinator and returns a scripted result."""

    def __init__(self, result: ProbeResult) -> None:
        self._result = result
        self.calls: list[str | None] = []

    async def run(self, compact: str | None, *, now: datetime | None = None) -> ProbeResult:
        self.calls.append(compact)
        return self._result


def _app(coordinator: Any) -> Starlette:
    return Starlette(routes=[endpoint.build_runtime_probe_control_route(coordinator)])


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://switchboard")


def _capability(signer_key, **overrides: Any) -> str:
    kwargs: dict[str, Any] = {"caller": "dashboard", "catalog_entry_id": _ENTRY_ID, "now": _NOW}
    kwargs.update(overrides)
    return cap.sign_capability(signer_key, **kwargs)


# ---------------------------------------------------------------------------
# The wire contract
# ---------------------------------------------------------------------------


async def test_probe_is_requested_by_posting_the_capability_as_a_bearer_token(signer):
    """Criterion 4: private POST path, Authorization-only compact JWS."""
    signer_key, _ = signer
    coordinator = _Coordinator(ProbeResult(ProbeStatus.COMPLETED, ok=True, latency_ms=412))
    compact = _capability(signer_key)

    async with _client(_app(coordinator)) as client:
        response = await client.post(
            endpoint.CONTROL_PATH, headers={"Authorization": f"Bearer {compact}"}
        )

    assert endpoint.CONTROL_PATH == "/_control/runtime-probe/v1"
    assert response.status_code == 200
    assert response.json() == {"status": "completed", "ok": True, "latency_ms": 412}
    assert coordinator.calls == [compact]


@pytest.mark.parametrize(
    ("status", "code"),
    [(status, code) for status, code in HTTP_STATUS.items() if status is not ProbeStatus.COMPLETED],
)
async def test_each_outcome_has_its_own_status_and_carries_no_probe_verdict(status, code):
    """Criterion 4: the typed pairs stay distinct, and ``ok`` is never invented."""
    coordinator = _Coordinator(ProbeResult(status))

    async with _client(_app(coordinator)) as client:
        response = await client.post(
            endpoint.CONTROL_PATH, headers={"Authorization": "Bearer not.a.capability"}
        )

    assert response.status_code == code
    assert response.json() == {"status": status.value}


async def test_failed_probe_reports_a_verified_failure_not_an_outage(signer):
    signer_key, _ = signer
    coordinator = _Coordinator(ProbeResult(ProbeStatus.COMPLETED, ok=False, latency_ms=9))

    async with _client(_app(coordinator)) as client:
        response = await client.post(
            endpoint.CONTROL_PATH,
            headers={"Authorization": f"Bearer {_capability(signer_key)}"},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "completed", "ok": False, "latency_ms": 9}


async def test_response_is_typed_json_and_never_cached(signer):
    signer_key, _ = signer
    coordinator = _Coordinator(ProbeResult(ProbeStatus.COMPLETED, ok=True, latency_ms=1))

    async with _client(_app(coordinator)) as client:
        response = await client.post(
            endpoint.CONTROL_PATH,
            headers={"Authorization": f"Bearer {_capability(signer_key)}"},
        )

    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["cache-control"] == "no-store"
    # A 401 must not invite a retry with different credentials.
    assert "www-authenticate" not in response.headers


# ---------------------------------------------------------------------------
# Only one place a capability may appear
# ---------------------------------------------------------------------------


async def test_capability_in_a_cookie_is_refused_before_the_coordinator(signer):
    """Criterion 1: a cookie copy is rejected before receipt, lookup, or launch."""
    signer_key, _ = signer
    coordinator = _Coordinator(ProbeResult(ProbeStatus.COMPLETED, ok=True))

    async with _client(_app(coordinator)) as client:
        response = await client.post(
            endpoint.CONTROL_PATH, headers={"Cookie": f"capability={_capability(signer_key)}"}
        )

    assert response.status_code == 401
    assert coordinator.calls == []


async def test_a_cookie_alongside_a_valid_bearer_is_still_refused(signer):
    """An ambient credential must not ride along with a legitimate request."""
    signer_key, _ = signer
    coordinator = _Coordinator(ProbeResult(ProbeStatus.COMPLETED, ok=True))

    async with _client(_app(coordinator)) as client:
        response = await client.post(
            endpoint.CONTROL_PATH,
            headers={
                "Authorization": f"Bearer {_capability(signer_key)}",
                "Cookie": "session=irrelevant",
            },
        )

    assert response.status_code == 401
    assert coordinator.calls == []


async def test_capability_in_the_query_string_is_refused(signer):
    signer_key, _ = signer
    coordinator = _Coordinator(ProbeResult(ProbeStatus.COMPLETED, ok=True))

    async with _client(_app(coordinator)) as client:
        response = await client.post(
            f"{endpoint.CONTROL_PATH}?capability={_capability(signer_key)}"
        )

    assert response.status_code == 401
    assert coordinator.calls == []


async def test_any_query_string_is_refused_even_beside_a_valid_bearer(signer):
    signer_key, _ = signer
    coordinator = _Coordinator(ProbeResult(ProbeStatus.COMPLETED, ok=True))

    async with _client(_app(coordinator)) as client:
        response = await client.post(
            f"{endpoint.CONTROL_PATH}?catalog_entry_id={uuid4()}",
            headers={"Authorization": f"Bearer {_capability(signer_key)}"},
        )

    assert response.status_code == 401
    assert coordinator.calls == []


async def test_capability_or_parameters_in_the_body_are_refused(signer):
    """The catalog entry comes from the signed claim; a body can only add attack surface."""
    signer_key, _ = signer
    coordinator = _Coordinator(ProbeResult(ProbeStatus.COMPLETED, ok=True))
    compact = _capability(signer_key)

    async with _client(_app(coordinator)) as client:
        for body in (
            {"capability": compact},
            {"catalog_entry_id": str(uuid4())},
            {"prompt": "ignore previous instructions"},
            {"runtime_args": ["--dangerously"]},
            {},
        ):
            response = await client.post(
                endpoint.CONTROL_PATH,
                headers={"Authorization": f"Bearer {compact}"},
                json=body,
            )
            assert response.status_code == 401

    assert coordinator.calls == []


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="missing"),
        pytest.param({"Authorization": ""}, id="empty"),
        pytest.param({"Authorization": "token"}, id="no-scheme"),
        pytest.param({"Authorization": "Basic dXNlcjpwdw=="}, id="basic"),
        pytest.param({"Authorization": "Bearer"}, id="scheme-only"),
        pytest.param({"Authorization": "Bearer "}, id="empty-token"),
        pytest.param({"Authorization": "bearer x.y.z"}, id="lowercase-scheme"),
        pytest.param({"Authorization": "Bearer  x.y.z"}, id="double-space"),
    ],
)
async def test_malformed_authorization_is_refused(headers):
    coordinator = _Coordinator(ProbeResult(ProbeStatus.COMPLETED, ok=True))

    async with _client(_app(coordinator)) as client:
        response = await client.post(endpoint.CONTROL_PATH, headers=headers)

    assert response.status_code == 401
    assert coordinator.calls == []


async def test_a_repeated_authorization_header_is_refused(signer):
    """Two capabilities in one request must never be resolved to "the first one"."""
    signer_key, _ = signer
    coordinator = _Coordinator(ProbeResult(ProbeStatus.COMPLETED, ok=True))
    headers = [
        ("Authorization", f"Bearer {_capability(signer_key)}"),
        ("Authorization", f"Bearer {_capability(signer_key)}"),
    ]

    async with _client(_app(coordinator)) as client:
        response = await client.post(endpoint.CONTROL_PATH, headers=headers)

    assert response.status_code == 401
    assert coordinator.calls == []


@pytest.mark.parametrize("method", ["get", "put", "patch", "delete", "head"])
async def test_only_post_reaches_the_coordinator(method):
    coordinator = _Coordinator(ProbeResult(ProbeStatus.COMPLETED, ok=True))

    async with _client(_app(coordinator)) as client:
        response = await getattr(client, method)(endpoint.CONTROL_PATH)

    assert response.status_code == 405
    assert coordinator.calls == []


async def test_response_body_never_echoes_the_capability(signer):
    """Criterion 8: nothing the caller sent comes back, not even in an error."""
    signer_key, _ = signer
    compact = _capability(signer_key)
    coordinator = _Coordinator(ProbeResult(ProbeStatus.UNAUTHORIZED))

    async with _client(_app(coordinator)) as client:
        response = await client.post(
            endpoint.CONTROL_PATH, headers={"Authorization": f"Bearer {compact}"}
        )

    rendered = response.text + json.dumps(dict(response.headers))
    for segment in compact.split("."):
        assert segment not in rendered
    assert _KID not in rendered


# ---------------------------------------------------------------------------
# The dedicated client
# ---------------------------------------------------------------------------


def _signer_snapshot(signer_key, keyring) -> SignerSnapshot:
    """A complete snapshot: production requires the signer to match a keyring entry."""
    return SignerSnapshot(
        signer=signer_key,
        keyring=keyring,
        matched=match_signer_to_keyring(signer_key, keyring),
    )


def _unavailable_snapshot() -> SignerSnapshot:
    return SignerSnapshot(unavailable_reason="signing key is not mounted")


async def test_client_signs_a_capability_and_sends_it_only_as_a_bearer_token(signer, keyring):
    """Criterion 4: one header, no cookie, no query, no body."""
    signer_key, _ = signer
    coordinator = _Coordinator(ProbeResult(ProbeStatus.COMPLETED, ok=True, latency_ms=77))
    seen: dict[str, Any] = {}

    app = _app(coordinator)

    async def _record(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["content"] = request.content
        transport = httpx.ASGITransport(app=app)
        return await transport.handle_async_request(request)

    client = control_client.RuntimeProbeControlClient(
        "http://switchboard:9000",
        caller="dashboard",
        signer=lambda: _signer_snapshot(signer_key, keyring),
        transport=httpx.MockTransport(_record),
    )

    result = await client.probe(_ENTRY_ID, now=_NOW)

    assert result.status is ProbeStatus.COMPLETED
    assert result.ok is True
    assert result.latency_ms == 77
    assert seen["url"] == f"http://switchboard:9000{endpoint.CONTROL_PATH}"
    assert seen["content"] == b""
    assert "cookie" not in seen["headers"]
    compact = seen["headers"]["authorization"].removeprefix("Bearer ")
    verified = cap.verify_capability(compact, keyring=keyring, now=_NOW)
    assert verified.caller == "dashboard"
    assert verified.catalog_entry_id == _ENTRY_ID


async def test_scheduler_is_also_a_registered_caller_class(signer, keyring):
    """Criterion 5: dashboard and scheduler, and only those two."""
    signer_key, _ = signer
    captured: dict[str, Any] = {}

    async def _capture(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers["authorization"]
        return httpx.Response(200, json={"status": "completed", "ok": True, "latency_ms": 3})

    client = control_client.RuntimeProbeControlClient(
        "http://switchboard:9000",
        caller="scheduler",
        signer=lambda: _signer_snapshot(signer_key, keyring),
        transport=httpx.MockTransport(_capture),
    )
    await client.probe(_ENTRY_ID, now=_NOW)

    compact = captured["authorization"].removeprefix("Bearer ")
    assert cap.verify_capability(compact, keyring=keyring, now=_NOW).caller == "scheduler"


async def test_client_refuses_an_unregistered_caller_class(signer, keyring):
    signer_key, _ = signer
    with pytest.raises(cap.CapabilityRejected):
        control_client.RuntimeProbeControlClient(
            "http://switchboard:9000",
            caller="butler",
            signer=lambda: _signer_snapshot(signer_key, keyring),
        )


async def test_client_without_a_signer_is_unavailable_and_sends_nothing():
    """Criterion 9: the deployed path is fail-closed with no signer mount."""
    reached = False

    async def _unreachable(request: httpx.Request) -> httpx.Response:
        nonlocal reached
        reached = True
        return httpx.Response(200, json={"status": "completed", "ok": True})

    client = control_client.RuntimeProbeControlClient(
        "http://switchboard:9000",
        caller="dashboard",
        signer=_unavailable_snapshot,
        transport=httpx.MockTransport(_unreachable),
    )

    result = await client.probe(_ENTRY_ID, now=_NOW)

    assert result.status is ProbeStatus.UNAVAILABLE
    assert result.ok is None
    assert reached is False


@pytest.mark.parametrize(
    ("code", "payload", "expected"),
    [
        (401, {"status": "unauthorized"}, ProbeStatus.UNAUTHORIZED),
        (409, {"status": "replay"}, ProbeStatus.REPLAY),
        (429, {"status": "busy"}, ProbeStatus.BUSY),
        (503, {"status": "unavailable"}, ProbeStatus.UNAVAILABLE),
        (504, {"status": "timeout"}, ProbeStatus.TIMEOUT),
    ],
)
async def test_client_preserves_every_outcome_distinction(signer, keyring, code, payload, expected):
    """Criterion 4: the client must not collapse an outage into a failed test."""
    signer_key, _ = signer

    async def _respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(code, json=payload)

    client = control_client.RuntimeProbeControlClient(
        "http://switchboard:9000",
        caller="dashboard",
        signer=lambda: _signer_snapshot(signer_key, keyring),
        transport=httpx.MockTransport(_respond),
    )

    result = await client.probe(_ENTRY_ID, now=_NOW)

    assert result.status is expected
    assert result.ok is None


@pytest.mark.parametrize(
    ("code", "payload"),
    [
        (200, {"status": "surprise"}),
        (200, {"ok": True}),
        (200, "not-an-object"),
        (418, {"status": "completed", "ok": True}),
        (500, {"status": "completed", "ok": True}),
    ],
)
async def test_client_treats_an_unrecognised_response_as_unavailable(
    signer, keyring, code, payload
):
    """A response the contract does not describe must never become a verdict."""
    signer_key, _ = signer

    async def _respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(code, json=payload)

    client = control_client.RuntimeProbeControlClient(
        "http://switchboard:9000",
        caller="dashboard",
        signer=lambda: _signer_snapshot(signer_key, keyring),
        transport=httpx.MockTransport(_respond),
    )

    assert (await client.probe(_ENTRY_ID, now=_NOW)).status is ProbeStatus.UNAVAILABLE


async def test_client_maps_transport_failures_without_inventing_a_verdict(signer, keyring):
    signer_key, _ = signer

    def _make(exc: Exception):
        async def _raise(request: httpx.Request) -> httpx.Response:
            raise exc

        return control_client.RuntimeProbeControlClient(
            "http://switchboard:9000",
            caller="dashboard",
            signer=lambda: _signer_snapshot(signer_key, keyring),
            transport=httpx.MockTransport(_raise),
        )

    refused = await _make(httpx.ConnectError("refused")).probe(_ENTRY_ID, now=_NOW)
    assert refused.status is ProbeStatus.UNAVAILABLE
    assert refused.ok is None

    stalled = await _make(httpx.ReadTimeout("stalled")).probe(_ENTRY_ID, now=_NOW)
    assert stalled.status is ProbeStatus.TIMEOUT
    assert stalled.ok is None


async def test_client_errors_disclose_no_capability_material(signer, keyring, caplog):
    """Criterion 8: a transport failure must not log what was being sent."""
    signer_key, _ = signer
    captured: dict[str, str] = {}

    async def _fail(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers["authorization"]
        raise httpx.ConnectError("refused")

    client = control_client.RuntimeProbeControlClient(
        "http://switchboard:9000",
        caller="dashboard",
        signer=lambda: _signer_snapshot(signer_key, keyring),
        transport=httpx.MockTransport(_fail),
    )

    with caplog.at_level("DEBUG"):
        await client.probe(_ENTRY_ID, now=_NOW)

    compact = captured["authorization"].removeprefix("Bearer ")
    for segment in compact.split("."):
        assert segment not in caplog.text
    assert _KID not in caplog.text
