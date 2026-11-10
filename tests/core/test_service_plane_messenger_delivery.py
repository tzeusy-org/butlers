"""Messenger's delivery endpoint admits only Switchboard, behind a default-off switch."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from butlers.capability_broker.contracts import BrokerRefused
from butlers.capability_broker.verification import DomainVerifier
from butlers.core.runtime_probe_control.keys import VerifierKey, VerifierKeyring
from butlers.core.service_plane.messenger_delivery import (
    REFUSED,
    authenticate_caller,
    messenger_delivery_headers,
    messenger_delivery_refusal,
    service_plane_auth_enabled,
)
from butlers.core.service_plane.workload_identity import (
    DAEMON_HEADER,
    DELIVERY_AUDIENCE,
    RELAY_AUDIENCE,
    TOKEN_HEADER,
)
from tests.core.test_service_plane_workload_identity import _claims, _token, _verifier

_SWITCHBOARD = _token(_claims())


def _headers(token: str = _SWITCHBOARD, daemon: str = "switchboard") -> dict[str, str]:
    return {TOKEN_HEADER: token, DAEMON_HEADER: daemon}


def _up_workload_token() -> str:
    claims = _claims(sub="system:serviceaccount:butlers-dev:butlers-up")
    claims["kubernetes.io"] = {
        **claims["kubernetes.io"],
        "serviceaccount": {"name": "butlers-up", "uid": "u3"},
    }
    return _token(claims)


def _broker_shaped_assertion() -> str:
    """An Ed25519 compact JWS shaped like an ``X-Butlers-Assertion`` call assertion."""
    key = Ed25519PrivateKey.generate()

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    head = b64(json.dumps({"alg": "EdDSA", "kid": "broker"}).encode())
    body = b64(json.dumps({"aud": "messenger", "capability": "route.execute"}).encode())
    return f"{head}.{body}.{b64(key.sign(f'{head}.{body}'.encode()))}"


async def test_switchboard_from_the_control_workload_is_admitted() -> None:
    refusal = await messenger_delivery_refusal(
        "messenger", _headers(), enabled=True, verifier=_verifier(DELIVERY_AUDIENCE)
    )

    assert refusal is None


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {TOKEN_HEADER: _SWITCHBOARD},
        _headers(daemon="health"),
        _headers(token=_up_workload_token()),
        _headers(token=_token(_claims(aud=[RELAY_AUDIENCE]))),
        _headers(token=_broker_shaped_assertion()),
        {"x-butlers-assertion": _SWITCHBOARD, DAEMON_HEADER: "switchboard"},
    ],
    ids=[
        "no-token",
        "no-daemon",
        "other-daemon-same-workload",
        "switchboard-name-from-butlers-up",
        "relay-audience-token",
        "broker-call-assertion-as-delivery",
        "token-under-the-assertion-header",
    ],
)
async def test_every_other_delivery_caller_is_refused(headers: dict[str, str]) -> None:
    refusal = await messenger_delivery_refusal(
        "messenger", headers, enabled=True, verifier=_verifier(DELIVERY_AUDIENCE)
    )

    assert refusal == REFUSED


async def test_a_caller_names_no_one_without_a_presented_daemon() -> None:
    verifier = _verifier(DELIVERY_AUDIENCE)

    assert await authenticate_caller({TOKEN_HEADER: _SWITCHBOARD}, verifier) is None
    assert (
        await authenticate_caller({TOKEN_HEADER: _SWITCHBOARD, DAEMON_HEADER: ""}, verifier) is None
    )


async def test_no_verifier_refuses_when_the_switch_is_on() -> None:
    assert await messenger_delivery_refusal("messenger", _headers(), enabled=True, verifier=None)


def test_a_delivery_token_is_never_a_broker_call_assertion() -> None:
    key = Ed25519PrivateKey.generate()
    keyring = VerifierKeyring(
        VerifierKey("broker", key.public_key().public_bytes_raw(), datetime(2026, 1, 1, tzinfo=UTC))
    )

    with pytest.raises(BrokerRefused):
        DomainVerifier("messenger", keyring).verify(_SWITCHBOARD, "route.execute", {})


async def test_switch_off_pins_legacy_behaviour_on_both_sides(tmp_path: Path) -> None:
    class _Untouchable:
        async def verify(self, token: str) -> str | None:
            raise AssertionError("the key set must not be consulted while the switch is off")

    assert not service_plane_auth_enabled({})
    assert not service_plane_auth_enabled({"BUTLERS_SERVICE_PLANE_AUTH": "true"})
    assert service_plane_auth_enabled({"BUTLERS_SERVICE_PLANE_AUTH": "1"})

    # Messenger accepts a delivery with no workload headers at all, as today.
    assert (
        await messenger_delivery_refusal(
            "messenger",
            {},
            enabled=False,
            verifier=_Untouchable(),  # type: ignore[arg-type]
        )
        is None
    )
    # Switchboard sends no extra headers and reads no token file (none exists here).
    assert (
        messenger_delivery_headers("messenger", "route.execute", enabled=False, token_dir=tmp_path)
        == {}
    )


async def test_other_butlers_are_untouched_when_the_switch_is_on() -> None:
    assert await messenger_delivery_refusal("health", {}, enabled=True, verifier=None) is None


def test_switchboard_attaches_its_delivery_token_only_to_messenger_deliveries(
    tmp_path: Path,
) -> None:
    (tmp_path / DELIVERY_AUDIENCE).write_text(_SWITCHBOARD + "\n")

    assert (
        messenger_delivery_headers("messenger", "route.execute", enabled=True, token_dir=tmp_path)
        == _headers()
    )
    assert (
        messenger_delivery_headers("health", "route.execute", enabled=True, token_dir=tmp_path)
        == {}
    )
    assert messenger_delivery_headers("messenger", "notify", enabled=True, token_dir=tmp_path) == {}
