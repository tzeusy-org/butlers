"""Projected ServiceAccount tokens authenticate the calling workload (bu-2c1g7v).

The tokens here are built exactly as the Kubernetes API server issues a bound,
projected ServiceAccount token: RS256 JWS, ``aud`` a one-element list, and the
``kubernetes.io`` claim naming namespace, ServiceAccount and pod.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from butlers.core.service_plane.workload_identity import (
    DELIVERY_AUDIENCE,
    RELAY_AUDIENCE,
    ApiServerJwks,
    JwksKeys,
    JwksUnavailable,
    VerifierUnavailable,
    WorkloadTokenPolicy,
    WorkloadTokenVerifier,
    parse_jwks,
)

_NOW = 1_800_000_000
_ISSUER = "https://kubernetes.default.svc.cluster.local"
_NAMESPACE = "butlers-dev"
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _jwk(key: rsa.RSAPrivateKey, kid: str) -> dict[str, str]:
    numbers = key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "alg": "RS256",
        "use": "sig",
        "kid": kid,
        "n": _b64(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
        "e": _b64(numbers.e.to_bytes(3, "big")),
    }


def _claims(**overrides: Any) -> dict[str, Any]:
    claims: dict[str, Any] = {
        "iss": _ISSUER,
        "aud": [DELIVERY_AUDIENCE],
        "sub": f"system:serviceaccount:{_NAMESPACE}:butlers-control",
        "iat": _NOW - 60,
        "nbf": _NOW - 60,
        "exp": _NOW + 540,
        "kubernetes.io": {
            "namespace": _NAMESPACE,
            "serviceaccount": {"name": "butlers-control", "uid": "u1"},
            "pod": {"name": "butlers-control-0", "uid": "p1"},
        },
    }
    claims.update(overrides)
    return claims


def _token(
    claims: dict[str, Any],
    *,
    key: rsa.RSAPrivateKey = _KEY,
    header: dict[str, Any] | None = None,
) -> str:
    head = _b64(json.dumps(header or {"alg": "RS256", "kid": "k1"}).encode())
    body = _b64(json.dumps(claims).encode())
    signature = key.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{body}.{_b64(signature)}"


def _verifier(audience: str = DELIVERY_AUDIENCE) -> WorkloadTokenVerifier:
    keys = parse_jwks({"keys": [_jwk(_KEY, "k1")]})

    async def fetch() -> JwksKeys:
        return keys

    policy = WorkloadTokenPolicy(
        issuer=_ISSUER,
        audience=audience,
        namespace=_NAMESPACE,
        workloads={"butlers-control": "butlers-control", "butlers-up": "butlers-up"},
    )
    return WorkloadTokenVerifier(policy, fetch, clock=lambda: _NOW)


async def test_a_bound_token_for_this_audience_names_its_workload() -> None:
    assert await _verifier().verify(_token(_claims())) == "butlers-control"


def _other_namespace() -> dict[str, Any]:
    claims = _claims(sub="system:serviceaccount:other:butlers-control")
    claims["kubernetes.io"] = {**claims["kubernetes.io"], "namespace": "other"}
    return claims


def _unbound() -> dict[str, Any]:
    claims = _claims()
    claims["kubernetes.io"] = {
        key: value for key, value in claims["kubernetes.io"].items() if key != "pod"
    }
    return claims


def _claim_namespace_only_differs() -> dict[str, Any]:
    claims = _claims()
    claims["kubernetes.io"] = {**claims["kubernetes.io"], "namespace": "other"}
    return claims


def _empty_pod_binding() -> dict[str, Any]:
    claims = _claims()
    claims["kubernetes.io"] = {**claims["kubernetes.io"], "pod": {}}
    return claims


def _sub_and_claim_disagree() -> dict[str, Any]:
    return _claims(sub=f"system:serviceaccount:{_NAMESPACE}:butlers-up")


@pytest.mark.parametrize(
    "token",
    [
        _token(_claims(exp=_NOW - 1)),
        _token(_claims(aud=[RELAY_AUDIENCE])),
        _token(_claims(aud=[DELIVERY_AUDIENCE, RELAY_AUDIENCE])),
        _token(_claims(aud=DELIVERY_AUDIENCE)),
        _token(_other_namespace()),
        _token(_claims(iss="https://elsewhere.example")),
        _token(_claims(), key=_OTHER_KEY),
        _token(_claims(), header={"alg": "RS256", "kid": "unknown"}),
        _token(_claims(), header={"alg": "none", "kid": "k1"}),
        _token(_claims(), header={"alg": "HS256", "kid": "k1"}),
        _token(_claims(nbf=_NOW + 120)),
        _token(_claims(iat=_NOW - 7200, exp=_NOW + 60)),
        _token(_unbound()),
        _token(_empty_pod_binding()),
        _token(_claim_namespace_only_differs()),
        _token(_sub_and_claim_disagree()),
        _token(
            _claims(
                sub=f"system:serviceaccount:{_NAMESPACE}:default",
                **{
                    "kubernetes.io": {
                        "namespace": _NAMESPACE,
                        "serviceaccount": {"name": "default", "uid": "u2"},
                        "pod": {"name": "x", "uid": "p2"},
                    }
                },
            )
        ),
        "not-a-jwt",
        "",
    ],
    ids=[
        "expired",
        "wrong-audience",
        "extra-audience",
        "audience-not-a-list",
        "other-namespace",
        "wrong-issuer",
        "bad-signature",
        "unknown-kid",
        "alg-none",
        "alg-hs256",
        "not-yet-valid",
        "lifetime-over-an-hour",
        "not-pod-bound",
        "empty-pod-binding",
        "claim-namespace-differs",
        "sub-disagrees-with-claim",
        "unmapped-serviceaccount",
        "malformed",
        "empty",
    ],
)
async def test_every_other_token_is_refused(token: str) -> None:
    assert await _verifier().verify(token) is None


async def test_a_delivery_token_is_not_a_relay_token_or_the_reverse() -> None:
    delivery = _token(_claims(aud=[DELIVERY_AUDIENCE]))
    relay = _token(_claims(aud=[RELAY_AUDIENCE]))

    assert await _verifier(RELAY_AUDIENCE).verify(delivery) is None
    assert await _verifier(DELIVERY_AUDIENCE).verify(relay) is None
    assert await _verifier(RELAY_AUDIENCE).verify(relay) == "butlers-control"


_POLICY = WorkloadTokenPolicy(
    issuer=_ISSUER,
    audience=DELIVERY_AUDIENCE,
    namespace=_NAMESPACE,
    workloads={"butlers-control": "butlers-control"},
)


async def test_an_unreachable_key_set_is_unavailable_never_trusted_or_refused() -> None:
    """A JWKS outage is "cannot verify right now", not "this token is invalid"."""

    async def fetch() -> JwksKeys:
        raise OSError("api server unreachable")

    verifier = WorkloadTokenVerifier(_POLICY, fetch, clock=lambda: _NOW)

    with pytest.raises(VerifierUnavailable):
        await verifier.verify(_token(_claims()))
    # A token that is malformed on its face is still simply invalid.
    assert await verifier.verify("not-a-jwt") is None


def _key_sets(*sets: JwksKeys):
    calls: list[str] = []

    async def fetch() -> JwksKeys:
        calls.append("fetch")
        return sets[0]

    async def refresh() -> JwksKeys:
        calls.append("refresh")
        if len(sets) < 2:
            raise OSError("api server unreachable")
        return sets[1]

    return fetch, refresh, calls


async def test_an_unknown_kid_forces_one_refresh_and_verifies_a_rotated_key() -> None:
    old = parse_jwks({"keys": [_jwk(_OTHER_KEY, "k0")]})
    rotated = parse_jwks({"keys": [_jwk(_OTHER_KEY, "k0"), _jwk(_KEY, "k1")]})
    fetch, refresh, calls = _key_sets(old, rotated)
    verifier = WorkloadTokenVerifier(_POLICY, fetch, refresh_jwks=refresh, clock=lambda: _NOW)

    assert await verifier.verify(_token(_claims())) == "butlers-control"
    assert calls == ["fetch", "refresh"]


async def test_a_kid_still_unknown_after_the_refresh_is_refused() -> None:
    old = parse_jwks({"keys": [_jwk(_OTHER_KEY, "k0")]})
    fetch, refresh, calls = _key_sets(old, old)
    verifier = WorkloadTokenVerifier(_POLICY, fetch, refresh_jwks=refresh, clock=lambda: _NOW)

    assert await verifier.verify(_token(_claims())) is None
    assert calls == ["fetch", "refresh"]


async def test_an_unknown_kid_whose_refresh_fails_is_unavailable() -> None:
    old = parse_jwks({"keys": [_jwk(_OTHER_KEY, "k0")]})
    fetch, refresh, _ = _key_sets(old)
    verifier = WorkloadTokenVerifier(_POLICY, fetch, refresh_jwks=refresh, clock=lambda: _NOW)

    with pytest.raises(VerifierUnavailable):
        await verifier.verify(_token(_claims()))


async def test_a_known_kid_never_forces_a_refresh() -> None:
    keys = parse_jwks({"keys": [_jwk(_KEY, "k1")]})
    fetch, refresh, calls = _key_sets(keys)
    verifier = WorkloadTokenVerifier(_POLICY, fetch, refresh_jwks=refresh, clock=lambda: _NOW)

    assert await verifier.verify(_token(_claims(), key=_OTHER_KEY)) is None
    assert calls == ["fetch"]


async def test_a_forced_refresh_is_rate_limited(tmp_path) -> None:
    import httpx

    (tmp_path / "token").write_text("pod-token\n")
    fetches: list[float] = []
    healthy = [True]
    now = [0.0]

    def handler(request: httpx.Request) -> httpx.Response:
        fetches.append(now[0])
        if not healthy[0]:
            return httpx.Response(503)
        return httpx.Response(200, json={"keys": [_jwk(_KEY, "k1")]})

    jwks = ApiServerJwks(
        account_dir=tmp_path,
        refresh_after=300,
        stale_after=3600,
        min_forced_interval=30,
        clock=lambda: now[0],
        transport=httpx.MockTransport(handler),
    )

    assert set(await jwks()) == {"k1"}
    # Just fetched: a forced refresh reuses the fresh set (the kid really is unknown).
    now[0] = 10
    assert set(await jwks.refresh()) == {"k1"}
    assert fetches == [0.0]
    # Older than the interval: one real fetch.
    now[0] = 100
    assert set(await jwks.refresh()) == {"k1"}
    assert fetches == [0.0, 100]
    # A failed forced refresh raises, and the next one inside the interval does
    # not hit the API server again; it is still unavailable, not "kid unknown".
    healthy[0] = False
    now[0] = 200
    with pytest.raises(httpx.HTTPStatusError):
        await jwks.refresh()
    now[0] = 210
    with pytest.raises(JwksUnavailable):
        await jwks.refresh()
    assert fetches == [0.0, 100, 200]
    # Past the interval it tries again.
    healthy[0] = True
    now[0] = 231
    assert set(await jwks.refresh()) == {"k1"}
    assert fetches == [0.0, 100, 200, 231]


async def test_the_api_server_key_set_is_cached_and_served_stale_only_for_a_bounded_time(
    tmp_path,
) -> None:
    import httpx

    (tmp_path / "token").write_text("pod-token\n")
    seen: list[str] = []
    healthy = [True]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Authorization"])
        if not healthy[0]:
            return httpx.Response(503)
        return httpx.Response(200, json={"keys": [_jwk(_KEY, "k1")]})

    now = [0.0]
    jwks = ApiServerJwks(
        account_dir=tmp_path,
        refresh_after=300,
        stale_after=3600,
        clock=lambda: now[0],
        transport=httpx.MockTransport(handler),
    )

    assert set(await jwks()) == {"k1"}
    now[0] = 100
    assert set(await jwks()) == {"k1"}
    assert seen == ["Bearer pod-token"]

    healthy[0] = False
    now[0] = 1000
    assert set(await jwks()) == {"k1"}  # refresh failed; last set is under an hour old
    now[0] = 3601
    with pytest.raises(httpx.HTTPStatusError):
        await jwks()


def test_the_key_set_keeps_only_rsa_signing_keys() -> None:
    keys = parse_jwks(
        {
            "keys": [
                _jwk(_KEY, "k1"),
                {**_jwk(_KEY, "enc"), "use": "enc"},
                {"kty": "EC", "kid": "ec", "crv": "P-256", "x": "AA", "y": "AA"},
                {**_jwk(_KEY, "rs512"), "alg": "RS512"},
            ]
        }
    )

    assert set(keys) == {"k1"}
