"""Workload identity for service-plane callers (RFC 0038 D7, bu-2c1g7v).

A calling workload presents a projected Kubernetes ServiceAccount token: the
kubelet mints it for the pod, binds it to the pod and one audience, and
rotates it. The recipient verifies it offline against the cluster issuer's
JWKS. Butlers holds no private key for this; the API server signs.

What it authenticates is the workload (the pod's ServiceAccount), not a
daemon: co-hosted daemons share one process (RFC 0038, "Switchboard moves out
of the shared butlers-up process"), so no credential inside it could tell them
apart. A daemon name travels beside the token and only narrows an allowlist.

Each use has its own audience, so a token minted for one use is refused by
every other. Neither audience is a broker call assertion: those are Ed25519
JWS under ``X-Butlers-Assertion`` with ``aud`` equal to a domain name, and the
two verifiers share no header, key or audience.

Nothing unverified is ever admitted, and there is no fallback to an unsigned
request or to a caller-supplied identity. Two outcomes are kept apart:

- the token is invalid (bad signature, outside its validity window, not bound
  to a pod, a ``kid`` still unknown after a fresh key set): ``verify`` returns
  ``None`` and the caller is refused for good;
- the token cannot be verified right now (the key set cannot be fetched, or
  only a copy older than its stale window is held): ``verify`` raises
  ``VerifierUnavailable`` and the recipient answers retryably.

An unknown ``kid`` (the issuer may have rotated its key) forces one
rate-limited key-set refresh before the token is judged.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey, RSAPublicNumbers

logger = logging.getLogger(__name__)

#: Audience of a token presented to a named relay on the control plane.
RELAY_AUDIENCE = "butlers.service-relay.v1"
#: Audience of a token Switchboard presents to Messenger's delivery endpoint.
DELIVERY_AUDIENCE = "butlers.messenger-delivery.v1"

#: Header carrying the projected token. Distinct from ``X-Butlers-Assertion``.
TOKEN_HEADER = "x-butlers-workload-token"
#: Header carrying the presented daemon name; a filter, never authority.
DAEMON_HEADER = "x-butlers-workload-daemon"

#: Where the pod's projected token for each audience is mounted.
TOKEN_DIR = Path("/var/run/secrets/butlers/workload")

#: Longest validity window accepted. Projected volumes here request 600 s.
MAX_LIFETIME_SECONDS = 3600
#: Clock skew tolerated on ``iat`` and ``nbf``.
LEEWAY_SECONDS = 30

JwksKeys = Mapping[str, RSAPublicKey]
FetchJwks = Callable[[], Awaitable[JwksKeys]]


class VerifierUnavailable(Exception):
    """A token cannot be verified right now; it was neither admitted nor judged."""


class JwksUnavailable(Exception):
    """A forced key-set refresh failed within the last ``min_forced_interval``."""


@dataclass(frozen=True, slots=True)
class WorkloadTokenPolicy:
    """What a recipient accepts: one issuer, one audience, one namespace."""

    issuer: str
    audience: str
    namespace: str
    #: ServiceAccount name -> workload label. An unmapped account is refused.
    workloads: Mapping[str, str]


def _unb64(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _int(segment: str) -> int:
    return int.from_bytes(_unb64(segment), "big")


def parse_jwks(document: Mapping[str, Any]) -> dict[str, RSAPublicKey]:
    """Keep only RSA signing keys usable for RS256, by ``kid``."""
    keys: dict[str, RSAPublicKey] = {}
    for jwk in document.get("keys", ()):
        if not isinstance(jwk, Mapping):
            continue
        if jwk.get("kty") != "RSA" or jwk.get("alg", "RS256") != "RS256":
            continue
        if jwk.get("use", "sig") != "sig" or not isinstance(jwk.get("kid"), str):
            continue
        try:
            keys[jwk["kid"]] = RSAPublicNumbers(_int(jwk["e"]), _int(jwk["n"])).public_key()
        except (KeyError, TypeError, ValueError, binascii.Error):
            continue
    return keys


#: The cluster's issuer key set. Any authenticated ServiceAccount may read it
#: through the default ``system:service-account-issuer-discovery`` binding.
JWKS_URL = "https://kubernetes.default.svc/openid/v1/jwks"
_POD_ACCOUNT = Path("/var/run/secrets/kubernetes.io/serviceaccount")


class ApiServerJwks:
    """Fetch and cache the issuer key set with the pod's own token.

    A failed refresh keeps serving the last key set until it is
    ``stale_after`` old; after that the key set is unavailable and every
    verification is too. ``refresh`` forces a fetch for an unknown ``kid``, at
    most once per ``min_forced_interval``.
    """

    def __init__(
        self,
        *,
        url: str = JWKS_URL,
        account_dir: Path = _POD_ACCOUNT,
        refresh_after: float = 300.0,
        stale_after: float = 3600.0,
        min_forced_interval: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        transport: Any = None,
    ) -> None:
        self._url = url
        self._account_dir = account_dir
        self._refresh_after = refresh_after
        self._stale_after = stale_after
        self._min_forced_interval = min_forced_interval
        self._clock = clock
        self._transport = transport
        self._keys: dict[str, RSAPublicKey] | None = None
        self._fetched_at = 0.0
        self._forced_at: float | None = None

    async def refresh(self) -> JwksKeys:
        """A key set no older than ``min_forced_interval``, or raise.

        A set fetched within the interval is returned as is: a ``kid`` it lacks
        is really unknown. Otherwise one fetch, unless a forced fetch already
        failed within the interval, which raises ``JwksUnavailable`` without
        contacting the API server.
        """
        now = self._clock()
        if self._keys is not None and now - self._fetched_at < self._min_forced_interval:
            return self._keys
        if self._forced_at is not None and now - self._forced_at < self._min_forced_interval:
            raise JwksUnavailable("forced key-set refresh failed recently")
        self._forced_at = now
        keys = await self._fetch()
        self._keys, self._fetched_at = keys, now
        return keys

    async def __call__(self) -> JwksKeys:
        now = self._clock()
        if self._keys is not None and now - self._fetched_at < self._refresh_after:
            return self._keys
        try:
            keys = await self._fetch()
        except Exception:
            if self._keys is not None and now - self._fetched_at < self._stale_after:
                return self._keys
            raise
        self._keys, self._fetched_at = keys, now
        return keys

    async def _fetch(self) -> dict[str, RSAPublicKey]:
        import httpx

        token = (self._account_dir / "token").read_text(encoding="utf-8").strip()
        verify: Any = str(self._account_dir / "ca.crt") if self._transport is None else True
        async with httpx.AsyncClient(
            transport=self._transport, verify=verify, timeout=5.0
        ) as client:
            response = await client.get(self._url, headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
            return parse_jwks(response.json())


class WorkloadTokenVerifier:
    """Verify one projected ServiceAccount token and name its workload."""

    def __init__(
        self,
        policy: WorkloadTokenPolicy,
        fetch_jwks: FetchJwks,
        *,
        refresh_jwks: FetchJwks | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._policy = policy
        self._fetch = fetch_jwks
        self._refresh = refresh_jwks
        self._clock = clock

    async def verify(self, token: str) -> str | None:
        """The workload label, or ``None`` for any token that does not verify.

        Raises ``VerifierUnavailable`` when the key set cannot be read now.
        """
        try:
            head_b64, body_b64, signature_b64 = token.split(".")
            header = json.loads(_unb64(head_b64))
            claims = json.loads(_unb64(body_b64))
            signature = _unb64(signature_b64)
        except (ValueError, binascii.Error):
            return None
        if not isinstance(header, dict) or not isinstance(claims, dict):
            return None
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            return None
        key = (await self._keys(self._fetch)).get(header["kid"])
        if key is None and self._refresh is not None:
            # The issuer may have rotated its key: judge against a fresh set.
            key = (await self._keys(self._refresh)).get(header["kid"])
        if key is None:
            return None
        try:
            key.verify(
                signature,
                f"{head_b64}.{body_b64}".encode(),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except InvalidSignature:
            return None
        return self._workload(claims)

    @staticmethod
    async def _keys(fetch: FetchJwks) -> JwksKeys:
        try:
            return await fetch()
        except Exception:
            logger.warning("service-plane JWKS unavailable; cannot verify workload token")
            raise VerifierUnavailable("key set unavailable") from None

    def _workload(self, claims: dict[str, Any]) -> str | None:
        policy = self._policy
        now = int(self._clock())
        if claims.get("iss") != policy.issuer or claims.get("aud") != [policy.audience]:
            return None
        exp, iat, nbf = claims.get("exp"), claims.get("iat"), claims.get("nbf", claims.get("iat"))
        if type(exp) is not int or type(iat) is not int or type(nbf) is not int:
            return None
        if exp <= now or iat > now + LEEWAY_SECONDS or nbf > now + LEEWAY_SECONDS:
            return None
        if exp - iat > MAX_LIFETIME_SECONDS:
            return None
        kube = claims.get("kubernetes.io")
        if not isinstance(kube, dict) or kube.get("namespace") != policy.namespace:
            return None
        account, pod = kube.get("serviceaccount"), kube.get("pod")
        if not isinstance(account, dict) or not isinstance(pod, dict) or not pod.get("name"):
            return None
        name = account.get("name")
        if claims.get("sub") != f"system:serviceaccount:{policy.namespace}:{name}":
            return None
        return policy.workloads.get(name) if isinstance(name, str) else None
