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

The readiness tests add the other half of that rule.  ``GET
/_control/runtime-probe/v1/readiness?kid=<kid>`` is the one route on this plane
with a query string, and its whole job is to answer one bit without leaking a
second: an unknown key ID and a malformed one must come back byte-identical,
so a caller cannot use the route to enumerate which key IDs a deployment
loaded.
"""

from __future__ import annotations

import base64
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
    VerifierSnapshot,
    match_signer_to_keyring,
    parse_signer_document,
    parse_verifier_keyring_document,
)
from butlers.testing.runtime_probe_control import (
    current_entry,
    keyring_document,
    retiring_entry,
    signer_document,
    synthetic_keypair,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
_KID = "probe-2026-05a"
_ENTRY_ID = UUID("a1b2c3d4-5566-4788-99aa-bbccddeeff01")

#: Spelled like a real key ID and loaded by nobody.  The readiness route must
#: answer this exactly as it answers input that could never be a key ID at all.
_UNKNOWN_KID = "probe-2026-05z"


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


def _app(coordinator: Any, verifier: VerifierSnapshot | None = None) -> Starlette:
    routes = [endpoint.build_runtime_probe_control_route(coordinator)]
    if verifier is not None:
        routes.append(endpoint.build_runtime_probe_readiness_route(lambda: verifier))
    return Starlette(routes=routes)


def _readiness_app(verifier: VerifierSnapshot) -> Starlette:
    return Starlette(routes=[endpoint.build_runtime_probe_readiness_route(lambda: verifier)])


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
# The readiness gate
# ---------------------------------------------------------------------------


def _mounted(keyring) -> VerifierSnapshot:
    return VerifierSnapshot(keyring=keyring)


def _rotation_keyring(cutover: datetime):
    """A keyring mid-rotation: the retiring entry stopped issuing at *cutover*."""
    _, current_public = synthetic_keypair()
    _, retiring_public = synthetic_keypair()
    return parse_verifier_keyring_document(
        _encode(
            keyring_document(
                current_entry(current_public, kid=_KID, sign_from=cutover),
                [
                    retiring_entry(
                        retiring_public,
                        kid="probe-2026-04a",
                        sign_from=cutover - timedelta(days=1),
                        sign_until=cutover,
                    )
                ],
            )
        )
    )


def _fingerprint(response: httpx.Response) -> tuple[int, bytes, dict[str, str]]:
    """Everything a caller can observe about a readiness answer."""
    return response.status_code, response.content, dict(response.headers)


async def test_readiness_reports_ready_for_a_loaded_issuable_key(keyring):
    """Criterion 1: the private GET, one ``kid``, the exact ``200``/``ready`` body."""
    async with _client(_readiness_app(_mounted(keyring))) as client:
        response = await client.get(endpoint.READINESS_PATH, params={"kid": _KID})

    assert endpoint.READINESS_PATH == "/_control/runtime-probe/v1/readiness"
    assert response.status_code == 200
    assert response.content == b'{"status":"ready"}'
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["cache-control"] == "no-store"
    assert "www-authenticate" not in response.headers


@pytest.mark.parametrize(
    "malformed",
    [
        pytest.param("", id="empty"),
        pytest.param(" ", id="space"),
        pytest.param("probe 2026 05a", id="spaces"),
        pytest.param("../../run/secrets/runtime_probe_control_signing_key", id="traversal"),
        pytest.param("probe\n2026", id="newline"),
        pytest.param("k" * 200, id="overlong"),
        pytest.param("{}", id="json"),
    ],
)
async def test_unknown_and_malformed_key_ids_are_indistinguishable(keyring, malformed):
    """Criterion 4: byte-identical, not merely both non-200.

    Two separately-correct-looking error branches are how this leaks, so the
    assertion compares the two responses to *each other* rather than checking
    each against a fixed expectation.
    """
    async with _client(_readiness_app(_mounted(keyring))) as client:
        unknown = await client.get(endpoint.READINESS_PATH, params={"kid": _UNKNOWN_KID})
        invalid = await client.get(endpoint.READINESS_PATH, params={"kid": malformed})

    assert _fingerprint(unknown) == _fingerprint(invalid)
    assert unknown.status_code == 503
    assert unknown.content == b'{"status":"unavailable"}'


async def test_an_unmounted_keyring_is_indistinguishable_from_an_unknown_key(keyring):
    """Whether a deployment mounted a keyring at all is not the caller's business."""
    unmounted = VerifierSnapshot(unavailable_reason="verifier keyring is not mounted")

    async with _client(_readiness_app(_mounted(keyring))) as client:
        unknown = await client.get(endpoint.READINESS_PATH, params={"kid": _UNKNOWN_KID})
    async with _client(_readiness_app(unmounted)) as client:
        absent = await client.get(endpoint.READINESS_PATH, params={"kid": _KID})

    assert _fingerprint(unknown) == _fingerprint(absent)


async def test_readiness_tracks_issuance_not_acceptance(keyring):
    """Criterion 1: eligible only at/after ``sign_from`` and at/before ``sign_until``."""
    now = datetime.now(UTC)
    future = parse_verifier_keyring_document(
        _encode(
            keyring_document(
                current_entry(synthetic_keypair()[1], kid=_KID, sign_from=now + timedelta(hours=1))
            )
        )
    )
    rotating = _rotation_keyring(now - timedelta(seconds=60))

    async with _client(_readiness_app(_mounted(future))) as client:
        not_yet = await client.get(endpoint.READINESS_PATH, params={"kid": _KID})
    async with _client(_readiness_app(_mounted(rotating))) as client:
        current = await client.get(endpoint.READINESS_PATH, params={"kid": _KID})
        # Still acceptable until ``accept_until``, but it may no longer issue,
        # so a client that signed under it would be signing into a rejection.
        retired = await client.get(endpoint.READINESS_PATH, params={"kid": "probe-2026-04a"})

    assert not_yet.status_code == 503
    assert current.status_code == 200
    assert retired.status_code == 503


@pytest.mark.parametrize(
    "query",
    [
        pytest.param("", id="no-query"),
        pytest.param("?", id="empty-query"),
        pytest.param("?kid", id="valueless"),
        pytest.param(f"?kid={_KID}&kid={_KID}", id="repeated"),
        pytest.param(f"?kid={_KID}&catalog_entry_id={_ENTRY_ID}", id="extra-parameter"),
        pytest.param(f"?key_id={_KID}", id="wrong-name"),
        pytest.param(f"?{_KID}", id="bare-value"),
    ],
)
async def test_readiness_accepts_exactly_one_kid_parameter_and_nothing_else(keyring, query):
    """Criterion 1: the exception to the no-query-string rule is ``kid`` alone."""
    async with _client(_readiness_app(_mounted(keyring))) as client:
        response = await client.get(f"{endpoint.READINESS_PATH}{query}")

    assert response.status_code == 503
    assert response.content == b'{"status":"unavailable"}'


async def test_readiness_accepts_no_capability(signer, keyring):
    """Criterion 3: readiness needs no capability, so it takes none.

    A capability offered here would be a copy of the one thing that may live in
    exactly one place, so the request is refused rather than serviced anyway.
    """
    signer_key, _ = signer
    compact = _capability(signer_key)

    async with _client(_readiness_app(_mounted(keyring))) as client:
        bearer = await client.get(
            endpoint.READINESS_PATH,
            params={"kid": _KID},
            headers={"Authorization": f"Bearer {compact}"},
        )
        cookie = await client.get(
            endpoint.READINESS_PATH,
            params={"kid": _KID},
            headers={"Cookie": f"capability={compact}"},
        )

    assert bearer.status_code == 503
    assert cookie.status_code == 503
    rendered = bearer.text + cookie.text + json.dumps(dict(bearer.headers))
    for segment in compact.split("."):
        assert segment not in rendered


async def test_readiness_refuses_a_request_body(keyring):
    """Criterion 3: the POST route refuses every body; so does this one."""
    async with _client(_readiness_app(_mounted(keyring))) as client:
        response = await client.request(
            "GET", endpoint.READINESS_PATH, params={"kid": _KID}, json={"kid": _KID}
        )

    assert response.status_code == 503


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
async def test_readiness_answers_only_a_get(keyring, method):
    async with _client(_readiness_app(_mounted(keyring))) as client:
        response = await getattr(client, method)(f"{endpoint.READINESS_PATH}?kid={_KID}")

    assert response.status_code == 405


async def test_readiness_discloses_no_configured_key_id_or_key_material(keyring):
    """Criterion 6: neither answer names a key or carries material."""
    public_key = keyring.current.public_key

    async with _client(_readiness_app(_mounted(keyring))) as client:
        ready = await client.get(endpoint.READINESS_PATH, params={"kid": _KID})
        unknown = await client.get(endpoint.READINESS_PATH, params={"kid": _UNKNOWN_KID})

    for response in (ready, unknown):
        rendered = response.text + json.dumps(dict(response.headers))
        assert _KID not in rendered
        assert _UNKNOWN_KID not in rendered
        assert public_key.hex() not in rendered
        assert base64.urlsafe_b64encode(public_key).rstrip(b"=").decode() not in rendered


async def test_readiness_never_reaches_the_coordinator(keyring):
    """Criterion 1: no catalog lookup, no runtime launch --- keyring only."""
    coordinator = _Coordinator(ProbeResult(ProbeStatus.COMPLETED, ok=True))

    async with _client(_app(coordinator, _mounted(keyring))) as client:
        assert (await client.get(endpoint.READINESS_PATH, params={"kid": _KID})).status_code == 200

    assert coordinator.calls == []


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


def _gated(handler) -> httpx.MockTransport:
    """Answer the readiness gate so a test can concentrate on the probe itself.

    Every probe now passes through readiness first, so a transport that only
    scripts the ``POST`` would report "not ready" and the test would be
    measuring the gate instead of what it meant to measure.
    """

    async def _route(request: httpx.Request) -> httpx.Response:
        if request.url.path == endpoint.READINESS_PATH:
            return httpx.Response(200, json={"status": "ready"})
        return await handler(request)

    return httpx.MockTransport(_route)


async def test_client_signs_a_capability_and_sends_it_only_as_a_bearer_token(signer, keyring):
    """Criterion 4: one header, no cookie, no query, no body."""
    signer_key, _ = signer
    coordinator = _Coordinator(ProbeResult(ProbeStatus.COMPLETED, ok=True, latency_ms=77))
    seen: dict[str, Any] = {}

    app = _app(coordinator, _mounted(keyring))

    async def _record(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
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
        transport=_gated(_capture),
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
        transport=_gated(_respond),
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
        transport=_gated(_respond),
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
            transport=_gated(_raise),
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
        transport=_gated(_fail),
    )

    with caplog.at_level("DEBUG"):
        await client.probe(_ENTRY_ID, now=_NOW)

    compact = captured["authorization"].removeprefix("Bearer ")
    for segment in compact.split("."):
        assert segment not in caplog.text
    assert _KID not in caplog.text


# ---------------------------------------------------------------------------
# The client gates on readiness before it signs anything
# ---------------------------------------------------------------------------


class _Gate:
    """A scripted readiness sequence that records every request it saw."""

    def __init__(self, *answers: httpx.Response) -> None:
        self._answers = list(answers)
        self.readiness: list[httpx.Request] = []
        self.probes: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        async def _route(request: httpx.Request) -> httpx.Response:
            if request.url.path == endpoint.READINESS_PATH:
                self.readiness.append(request)
                return self._answers[min(len(self.readiness) - 1, len(self._answers) - 1)]
            self.probes.append(request)
            return httpx.Response(200, json={"status": "completed", "ok": True, "latency_ms": 5})

        return httpx.MockTransport(_route)


def _ready() -> httpx.Response:
    return httpx.Response(200, json={"status": "ready"})


def _not_ready() -> httpx.Response:
    return httpx.Response(503, json={"status": "unavailable"})


def _gated_client(signer_key, keyring, gate: _Gate):
    return control_client.RuntimeProbeControlClient(
        "http://switchboard:9000",
        caller="dashboard",
        signer=lambda: _signer_snapshot(signer_key, keyring),
        transport=gate.transport(),
    )


async def test_client_signs_nothing_until_switchboard_reports_ready(signer, keyring):
    """Criterion 5: not-ready signs nothing and opens no probe connection."""
    signer_key, _ = signer
    gate = _Gate(_not_ready())

    result = await _gated_client(signer_key, keyring, gate).probe(_ENTRY_ID, now=_NOW)

    assert result.status is ProbeStatus.UNAVAILABLE
    assert result.ok is None
    assert gate.probes == []


async def test_client_asks_readiness_about_its_own_key_id_and_offers_no_capability(signer, keyring):
    """The gate carries a key *identifier* and nothing else --- no capability."""
    signer_key, _ = signer
    gate = _Gate(_ready())

    await _gated_client(signer_key, keyring, gate).probe(_ENTRY_ID, now=_NOW)

    request = gate.readiness[0]
    assert request.method == "GET"
    assert str(request.url) == f"http://switchboard:9000{endpoint.READINESS_PATH}?kid={_KID}"
    assert "authorization" not in request.headers
    assert "cookie" not in request.headers
    assert request.content == b""


async def test_client_retries_readiness_until_startup_settles_then_stops_asking(signer, keyring):
    """The launcher may start Dashboard first, so a first "no" is not terminal.

    A "yes" latches: both sides freeze their key snapshots at startup, so the
    answer cannot change without a restart that resets the latch too.
    """
    signer_key, _ = signer
    gate = _Gate(_not_ready(), _ready())
    client = _gated_client(signer_key, keyring, gate)

    first = await client.probe(_ENTRY_ID, now=_NOW)
    second = await client.probe(_ENTRY_ID, now=_NOW)
    third = await client.probe(_ENTRY_ID, now=_NOW)

    assert first.status is ProbeStatus.UNAVAILABLE
    assert second.status is ProbeStatus.COMPLETED
    assert third.status is ProbeStatus.COMPLETED
    assert len(gate.readiness) == 2
    assert len(gate.probes) == 2


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(httpx.Response(200, json={"status": "unavailable"}), id="wrong-word"),
        pytest.param(httpx.Response(200, json={"status": "completed"}), id="probe-vocabulary"),
        pytest.param(httpx.Response(200, json={"status": "ready", "kid": _KID}), id="extra-field"),
        pytest.param(httpx.Response(200, content=b"ready"), id="not-json"),
        pytest.param(httpx.Response(200, json="ready"), id="not-an-object"),
        pytest.param(httpx.Response(404), id="route-absent"),
        pytest.param(httpx.Response(503, json={"status": "unavailable"}), id="unavailable"),
    ],
)
async def test_client_treats_anything_but_the_exact_ready_response_as_not_ready(
    signer, keyring, answer
):
    """Criterion 5: only the exact ``200``/``{"status":"ready"}`` opens the gate."""
    signer_key, _ = signer
    gate = _Gate(answer)

    result = await _gated_client(signer_key, keyring, gate).probe(_ENTRY_ID, now=_NOW)

    assert result.status is ProbeStatus.UNAVAILABLE
    assert gate.probes == []


async def test_client_treats_an_unreachable_readiness_route_as_not_ready(signer, keyring):
    """Switchboard being down is not readiness; it must not become a signature."""
    signer_key, _ = signer
    signed: list[httpx.Request] = []

    async def _route(request: httpx.Request) -> httpx.Response:
        if request.url.path == endpoint.READINESS_PATH:
            raise httpx.ConnectError("refused")
        signed.append(request)
        return httpx.Response(200, json={"status": "completed", "ok": True})

    client = control_client.RuntimeProbeControlClient(
        "http://switchboard:9000",
        caller="dashboard",
        signer=lambda: _signer_snapshot(signer_key, keyring),
        transport=httpx.MockTransport(_route),
    )

    assert (await client.probe(_ENTRY_ID, now=_NOW)).status is ProbeStatus.UNAVAILABLE
    assert signed == []


async def test_readiness_failures_disclose_no_key_id_or_capability_material(
    signer, keyring, caplog
):
    """Criterion 6: the key ID travels in the URL and must reach no log line."""
    signer_key, _ = signer

    async def _route(request: httpx.Request) -> httpx.Response:
        if request.url.path == endpoint.READINESS_PATH:
            raise httpx.ConnectError(f"failed to connect to {request.url}")
        raise AssertionError("a not-ready client must not probe")

    client = control_client.RuntimeProbeControlClient(
        "http://switchboard:9000",
        caller="dashboard",
        signer=lambda: _signer_snapshot(signer_key, keyring),
        transport=httpx.MockTransport(_route),
    )

    with caplog.at_level("DEBUG"):
        await client.probe(_ENTRY_ID, now=_NOW)
        await control_client.RuntimeProbeControlClient(
            "http://switchboard:9000",
            caller="dashboard",
            signer=lambda: _signer_snapshot(signer_key, keyring),
            transport=httpx.MockTransport(
                lambda request: httpx.Response(503, json={"status": "unavailable"})
            ),
        ).probe(_ENTRY_ID, now=_NOW)

    assert _KID not in caplog.text
    assert caplog.text.strip()
