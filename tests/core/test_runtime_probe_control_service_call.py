"""The dashboard service-call capability (RFC 0038 D7, mechanism A).

A dashboard call to a model-reachable tool reaches Switchboard's service
listener with one capability signed by the runtime-probe control signer under
its own audience, bound to the butler, the tool and the exact arguments. These
tests pin that binding and the audience separation from the runtime-probe
plane. Every key is synthetic and generated here.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from butlers.core.runtime_probe_control import service_call as dc
from butlers.core.runtime_probe_control.capability import (
    CapabilityRejected,
    sign_capability,
    sign_claims,
)
from butlers.core.runtime_probe_control.keys import (
    VerifierKeyring,
    parse_signer_document,
    parse_verifier_keyring_document,
)
from butlers.core.service_plane.dashboard_call import routes_through_control_plane
from butlers.testing.runtime_probe_control import (
    current_entry,
    keyring_document,
    signer_document,
    synthetic_keypair,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)
_KID = "probe-2026-11a"


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
def keyring(signer) -> VerifierKeyring:
    _, public_key = signer
    return parse_verifier_keyring_document(
        _encode(
            keyring_document(
                current_entry(public_key, kid=_KID, sign_from=_NOW - timedelta(days=1))
            )
        )
    )


def _sign(signer, **overrides):
    parsed, _ = signer
    fields = {"butler": "health", "tool": "state_set", "args": {"key": "k", "value": 1}}
    fields.update(overrides)
    return dc.sign_service_call(parsed, now=_NOW, **fields)


def test_a_signed_call_verifies_bound_to_its_butler_tool_and_arguments(signer, keyring) -> None:
    verified = dc.verify_service_call(_sign(signer), keyring=keyring, now=_NOW)

    assert verified.butler == "health"
    assert verified.tool == "state_set"
    assert verified.args_digest == dc.args_digest({"key": "k", "value": 1})
    assert verified.kid == _KID
    assert len(verified.nonce) == 32
    assert verified.expires_at - verified.issued_at == timedelta(seconds=60)


def test_the_argument_digest_is_canonical_and_domain_separated() -> None:
    assert dc.args_digest({"b": 1, "a": [1, "x"]}) == dc.args_digest({"a": [1, "x"], "b": 1})
    assert dc.args_digest({"a": 1}) != dc.args_digest({"a": 2})
    assert dc.args_digest({}) != dc.args_digest({"a": None})
    # Domain separated from the owner-creation digest over the same bytes.
    from butlers.core.owner_creation import argument_digest

    assert dc.args_digest({"a": 1}) != argument_digest({"a": 1})
    # The wire contract both ends compute: SHA-256 over the domain and RFC 8785.
    import hashlib

    import rfc8785

    raw = rfc8785.dumps({"a": 1})
    assert (
        dc.args_digest({"a": 1}) == hashlib.sha256(b"butlers:service-call:v1\0" + raw).hexdigest()
    )
    assert dc.args_digest({"a": 1}) != hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("args", [[1], "x", {1: "a"}, {"a": float("nan")}, {"a": object()}])
def test_arguments_that_are_not_a_closed_json_object_are_refused(args) -> None:
    with pytest.raises(ValueError):
        dc.args_digest(args)


def test_a_runtime_probe_capability_is_not_a_service_call(signer, keyring) -> None:
    parsed, _ = signer
    probe = sign_capability(
        parsed,
        caller="dashboard",
        catalog_entry_id=UUID("a1b2c3d4-5566-4788-99aa-bbccddeeff01"),
        now=_NOW,
    )

    with pytest.raises(CapabilityRejected):
        dc.verify_service_call(probe, keyring=keyring, now=_NOW)


def test_a_service_call_is_not_a_runtime_probe_capability(signer, keyring) -> None:
    from butlers.core.runtime_probe_control.capability import verify_capability

    with pytest.raises(CapabilityRejected):
        verify_capability(_sign(signer), keyring=keyring, now=_NOW)


def test_the_right_shape_under_the_wrong_audience_is_refused(signer, keyring) -> None:
    parsed, _ = signer
    compact = sign_claims(
        parsed,
        audience="switchboard.runtime_probe_control.v1",
        claims={
            "caller": "dashboard",
            "butler": "health",
            "tool": "state_set",
            "args_digest": dc.args_digest({}),
        },
        now=_NOW,
    )

    with pytest.raises(CapabilityRejected, match="audience"):
        dc.verify_service_call(compact, keyring=keyring, now=_NOW)


@pytest.mark.parametrize(
    ("claim", "value"),
    [
        ("caller", "scheduler"),
        ("caller", "switchboard"),
        ("butler", "Health"),
        ("butler", "../health"),
        ("butler", ""),
        ("tool", "state set"),
        ("tool", ""),
        ("args_digest", "0" * 63),
        ("args_digest", "G" * 64),
        ("args_digest", 7),
    ],
)
def test_each_claim_is_checked(signer, keyring, claim, value) -> None:
    parsed, _ = signer
    claims = {
        "caller": "dashboard",
        "butler": "health",
        "tool": "state_set",
        "args_digest": dc.args_digest({}),
    }
    claims[claim] = value
    compact = sign_claims(parsed, audience=dc.SERVICE_CALL_AUDIENCE, claims=claims, now=_NOW)

    with pytest.raises(CapabilityRejected):
        dc.verify_service_call(compact, keyring=keyring, now=_NOW)


def test_an_extra_claim_is_refused(signer, keyring) -> None:
    parsed, _ = signer
    compact = sign_claims(
        parsed,
        audience=dc.SERVICE_CALL_AUDIENCE,
        claims={
            "caller": "dashboard",
            "butler": "health",
            "tool": "state_set",
            "args_digest": dc.args_digest({}),
            "goal_id": None,
        },
        now=_NOW,
    )

    with pytest.raises(CapabilityRejected, match="shape"):
        dc.verify_service_call(compact, keyring=keyring, now=_NOW)


def test_an_expired_call_is_refused(signer, keyring) -> None:
    with pytest.raises(CapabilityRejected, match="expired"):
        dc.verify_service_call(_sign(signer), keyring=keyring, now=_NOW + timedelta(seconds=66))


def test_the_signer_refuses_what_the_verifier_would(signer) -> None:
    with pytest.raises(CapabilityRejected):
        _sign(signer, butler="Not A Butler")
    with pytest.raises(CapabilityRejected):
        _sign(signer, tool="")
    with pytest.raises(ValueError):
        _sign(signer, args={"a": float("inf")})


def test_presentable_and_undeclared_tools_route_through_the_control_plane() -> None:
    from butlers.core.tool_presentation_inventory import TOOL_PRESENTATION_BY_NAME

    presentable = next(n for n, p in TOOL_PRESENTATION_BY_NAME.items() if p.llm_presentable)
    service = next(n for n, p in TOOL_PRESENTATION_BY_NAME.items() if not p.llm_presentable)

    assert routes_through_control_plane(presentable)
    assert not routes_through_control_plane(service)
    # An undeclared tool is assumed model-reachable: fail closed, not direct.
    assert routes_through_control_plane("no_such_tool_declared_anywhere")
