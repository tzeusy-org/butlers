"""Effect-free Switchboard route preflight on the existing backend port."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from butlers.core.control_plane_identity import (
    PROBE_DEADLINE_S,
    ExpectedDaemon,
    ProbeFailure,
    _read_identity,
    expected_from_roster,
    verify_identity,
)
from butlers.tools.switchboard.registry.registry import (
    ControlPlaneTargetDecision,
    resolve_control_plane_target,
)

_MIN_IDENTITY_INTERVAL_S = 5.0


class _NoActiveDomainTarget(Exception):
    """The configured domain set is present but held by policy."""


@dataclass(frozen=True)
class PreflightResult:
    ready: bool
    failure_category: str


class RoutePreflight:
    """Sample a fixed Git-roster target without routing or recording evidence.

    Every request re-reads policy and boot/observation facts before it can use
    a cached positive result.  The lock coalesces overlapping requests; the
    minimum interval bounds network identity GETs, never DB policy checks.
    """

    def __init__(
        self,
        pool: Any,
        configs: list[Any],
        *,
        client_factory: Any = None,
        min_interval_s: float = _MIN_IDENTITY_INTERVAL_S,
        deadline_s: float = PROBE_DEADLINE_S,
    ) -> None:
        self._pool = pool
        expected = expected_from_roster(configs)
        self._domains: tuple[ExpectedDaemon, ...] = tuple(
            sorted(
                (
                    target
                    for config, target in zip(configs, expected, strict=True)
                    if getattr(getattr(config, "type", None), "value", None) == "butler"
                ),
                key=lambda target: target.name,
            )
        )
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(trust_env=False, timeout=deadline_s)
        )
        self._min_interval_s = min_interval_s
        self._deadline_s = deadline_s
        self._lock = asyncio.Lock()
        self._last_get_at = float("-inf")
        self._cached_fingerprint: tuple[Any, ...] | None = None

    async def _fixed_target(self) -> ExpectedDaemon | None:
        if not self._domains:
            return None
        rows = await self._pool.fetch(
            "SELECT name, policy_state FROM switchboard.butler_registry_control_plane "
            "WHERE name = ANY($1::text[])",
            [target.name for target in self._domains],
        )
        policy_by_name = {row["name"]: row["policy_state"] for row in rows}
        if len(policy_by_name) != len(self._domains):
            raise ValueError("incomplete roster policy")
        for target in self._domains:
            policy = policy_by_name[target.name]
            if policy == "active":
                return target
            if policy not in {"paused", "quarantined", "review_required"}:
                raise ValueError("invalid roster policy")
        raise _NoActiveDomainTarget

    @staticmethod
    def _fingerprint(decision: ControlPlaneTargetDecision) -> tuple[Any, ...]:
        return (
            decision.expected.name,
            decision.boot_epoch,
            decision.boot_instance_id,
            decision.healthy_observed_at,
            decision.state_updated_at,
        )

    async def check(self) -> PreflightResult:
        async with self._lock:
            try:
                async with asyncio.timeout(self._deadline_s):
                    expected = await self._fixed_target()
                    if expected is None:
                        self._cached_fingerprint = None
                        return PreflightResult(False, "missing_target")
                    decision = await resolve_control_plane_target(
                        self._pool, expected, required_capability="trigger"
                    )
                    if decision.state != "ready":
                        self._cached_fingerprint = None
                        return PreflightResult(False, decision.reason)

                    now = asyncio.get_running_loop().time()
                    fingerprint = self._fingerprint(decision)
                    if now - self._last_get_at < self._min_interval_s:
                        if self._cached_fingerprint == fingerprint:
                            return PreflightResult(True, "ready")
                        return PreflightResult(False, "rate_limited")

                    self._last_get_at = now
                    self._cached_fingerprint = None
                    async with self._client_factory() as client:
                        payload = await _read_identity(client, expected)
                    verify_identity(
                        payload,
                        expected,
                        decision.boot_epoch,
                        decision.boot_instance_id,
                    )

                    # A successor boot, policy action, or failed observation
                    # during the GET invalidates that answer without any write.
                    still_expected = await self._fixed_target()
                    if still_expected != expected:
                        return PreflightResult(False, "changed_target")
                    current = await resolve_control_plane_target(
                        self._pool, expected, required_capability="trigger"
                    )
                    if current.state != "ready" or self._fingerprint(current) != fingerprint:
                        return PreflightResult(False, "changed_target")
                    self._cached_fingerprint = fingerprint
                    return PreflightResult(True, "ready")
            except ProbeFailure as exc:
                self._cached_fingerprint = None
                return PreflightResult(False, exc.category)
            except _NoActiveDomainTarget:
                self._cached_fingerprint = None
                return PreflightResult(False, "policy_denied")
            except TimeoutError:
                self._cached_fingerprint = None
                return PreflightResult(False, "timeout")
            except (httpx.TimeoutException, httpx.TransportError):
                self._cached_fingerprint = None
                return PreflightResult(False, "unavailable")
            except Exception:
                self._cached_fingerprint = None
                return PreflightResult(False, "unavailable")


def build_route_preflight_route(preflight: RoutePreflight) -> Route:
    """Mount only on Switchboard's backend ASGI app, never as an MCP tool."""

    async def endpoint(request: Request) -> JSONResponse:
        if request.query_params:
            return JSONResponse(
                {"ready": False, "failure_category": "invalid_request"}, status_code=400
            )
        result = await preflight.check()
        return JSONResponse({"ready": result.ready, "failure_category": result.failure_category})

    return Route("/internal/control-plane/route-preflight", endpoint, methods=["GET"])
