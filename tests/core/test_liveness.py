"""Tests for butlers.core.liveness — the shared last_seen_at +
liveness_ttl_seconds staleness formula (bu-dvzya).

Cross-consumer parity coverage (registry.py's _derive_eligibility_state vs.
InfraStateSource's heartbeat-stale check) lives in
tests/core/qa/test_infra_state.py, alongside the InfraStateSource test
helpers it reuses.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from butlers.core.control_plane_identity import (
    ExpectedDaemon,
    ProbeFailure,
    ProbeOutcome,
    SingleFlightProber,
    _effective_observer_interval_s,
    probe_once,
    run_shadow_cycle,
    verify_identity,
)
from butlers.core.liveness import (
    CLOCK_SKEW_TOLERANCE,
    DEFAULT_LIVENESS_TTL_SECONDS,
    is_liveness_stale,
    normalize_liveness_ttl_seconds,
)
from butlers.core.utils import generate_uuid7_string

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# is_liveness_stale
# ---------------------------------------------------------------------------


def test_missing_last_seen_is_stale():
    assert is_liveness_stale(None, ttl_seconds=300, now=_NOW) is True


def test_fresh_within_ttl_is_not_stale():
    last_seen = _NOW - timedelta(seconds=100)
    assert is_liveness_stale(last_seen, ttl_seconds=300, now=_NOW) is False


def test_exactly_at_ttl_boundary_is_not_stale():
    # last_seen_at + ttl == now is still fresh (>=, not strictly >).
    last_seen = _NOW - timedelta(seconds=300)
    assert is_liveness_stale(last_seen, ttl_seconds=300, now=_NOW) is False


def test_just_past_ttl_boundary_is_stale():
    last_seen = _NOW - timedelta(seconds=301)
    assert is_liveness_stale(last_seen, ttl_seconds=300, now=_NOW) is True


def test_future_within_skew_tolerance_is_not_stale():
    last_seen = _NOW + timedelta(minutes=1)
    assert is_liveness_stale(last_seen, ttl_seconds=300, now=_NOW) is False


def test_exactly_at_skew_tolerance_boundary_is_not_stale():
    last_seen = _NOW + CLOCK_SKEW_TOLERANCE
    assert is_liveness_stale(last_seen, ttl_seconds=300, now=_NOW) is False


def test_future_beyond_skew_tolerance_is_stale():
    # Without the skew guard, the unbounded TTL window would keep this
    # "fresh" forever; a future-dated timestamp must not evade detection.
    last_seen = _NOW + timedelta(minutes=10)
    assert is_liveness_stale(last_seen, ttl_seconds=300, now=_NOW) is True


def test_custom_ttl_overrides_default():
    last_seen = _NOW - timedelta(seconds=100)
    assert is_liveness_stale(last_seen, ttl_seconds=50, now=_NOW) is True
    assert is_liveness_stale(last_seen, ttl_seconds=150, now=_NOW) is False


@pytest.mark.parametrize("raw_ttl", [None, "not-a-number", 0, -5])
def test_malformed_ttl_falls_back_to_default(raw_ttl):
    stale_anchor = _NOW - timedelta(seconds=DEFAULT_LIVENESS_TTL_SECONDS + 10)
    fresh_anchor = _NOW - timedelta(seconds=DEFAULT_LIVENESS_TTL_SECONDS - 10)
    assert is_liveness_stale(stale_anchor, ttl_seconds=raw_ttl, now=_NOW) is True
    assert is_liveness_stale(fresh_anchor, ttl_seconds=raw_ttl, now=_NOW) is False


def test_now_defaults_to_current_time_when_omitted():
    # A last_seen_at from "just now" against the real wall clock must read fresh.
    assert is_liveness_stale(datetime.now(UTC), ttl_seconds=300) is False


# ---------------------------------------------------------------------------
# normalize_liveness_ttl_seconds
# ---------------------------------------------------------------------------


def test_normalize_liveness_ttl_seconds_positive_passthrough():
    assert normalize_liveness_ttl_seconds(120) == 120


@pytest.mark.parametrize("raw", [None, "bogus", 0, -1, [], {}])
def test_normalize_liveness_ttl_seconds_defaults_on_bad_input(raw):
    assert normalize_liveness_ttl_seconds(raw) == DEFAULT_LIVENESS_TTL_SECONDS


def test_normalize_liveness_ttl_seconds_custom_default():
    assert normalize_liveness_ttl_seconds(None, default=60) == 60


def _identity_payload(*, epoch: int = 7) -> dict:
    return {
        "schema_version": "butler.control.v1",
        "butler_name": "health",
        "boot_instance_id": generate_uuid7_string(),
        "boot_epoch": epoch,
        "route_contract": {"min": 1, "max": 1},
        "accepting_routes": True,
    }


def test_identity_verifier_rejects_untrusted_or_not_ready_facts():
    expected = ExpectedDaemon("health", 41103, "butlers-up")
    with pytest.raises(ValueError, match="backend host"):
        ExpectedDaemon("health", 41103, "http://caller-selected.example").url
    for change, category in [
        ({"butler_name": "finance"}, "invalid_identity"),
        ({"boot_epoch": 6}, "invalid_identity"),
        ({"observed_at": "2026-09-23T00:00:00Z"}, "invalid_identity"),
        ({"route_contract": {"min": 2, "max": 2}}, "invalid_contract"),
        ({"accepting_routes": False}, "not_accepting"),
    ]:
        original = _identity_payload()
        payload = {**original, **change}
        with pytest.raises(ProbeFailure) as exc:
            verify_identity(payload, expected, 7, uuid.UUID(original["boot_instance_id"]))
        assert exc.value.category == category

    payload = _identity_payload()
    with pytest.raises(ProbeFailure) as exc:
        verify_identity(payload, expected, 7, uuid.UUID(generate_uuid7_string()))
    assert exc.value.category == "invalid_identity"


async def test_receiver_probe_uses_exact_roster_url_and_db_fence():
    expected = ExpectedDaemon("health", 41103, "butlers-up")
    payload = _identity_payload()
    pool = AsyncMock()
    pool.fetchrow.side_effect = [
        {"boot_epoch": 7, "probe_sequence": 3},
        {"boot_epoch": 7, "probe_sequence": 4},
        {"boot_epoch": 7, "probe_sequence": 5},
    ]
    pool.fetchval.side_effect = [uuid.UUID(payload["boot_instance_id"]), True] * 3

    async def respond(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == expected.url
        await asyncio.sleep(0.01)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        prober = SingleFlightProber(pool, client=client)
        outcome = await prober.probe(expected)
        again = await prober.probe(expected)
        concurrent = await asyncio.gather(prober.probe(expected), prober.probe(expected))

    assert outcome.category == "healthy" and outcome.recorded
    assert again.category == "healthy" and again.recorded
    assert all(result.category == "healthy" and result.recorded for result in concurrent)
    assert pool.fetchrow.await_count == 3
    assert pool.fetchrow.await_args.args == (
        "SELECT boot_epoch, probe_sequence FROM public.reserve_butler_probe($1)",
        "health",
    )
    assert pool.fetchval.await_args.args[1:7] == ("health", 7, 5, True, True, True)


def _ready_control_plane_row(instance_id: uuid.UUID, *, now: datetime) -> dict:
    return {
        "name": "health",
        "policy_state": "active",
        "observed_state": "healthy",
        "boot_instance_id": instance_id,
        "boot_epoch": 7,
        "observed_boot_epoch": 7,
        "healthy_observed_at": now - timedelta(seconds=1),
        "server_now": now,
        "liveness_ttl_seconds": 300,
        "route_compatible": True,
        "accepting_routes": True,
        "route_contract_min": 1,
        "route_contract_max": 1,
        "capabilities": ["trigger"],
    }


async def test_separated_route_resolver_uses_policy_and_current_epoch_without_writes():
    from butlers.tools.switchboard.registry.registry import (
        expected_route_target,
        resolve_control_plane_target,
    )

    instance_id = uuid.UUID(generate_uuid7_string())
    row = _ready_control_plane_row(instance_id, now=_NOW)
    pool = AsyncMock()
    pool.fetchrow.return_value = row
    expected = ExpectedDaemon("health", 41103, "butlers-up")

    ready = await resolve_control_plane_target(pool, expected, required_capability="trigger")
    assert ready.state == "ready"
    assert ready.endpoint_url == "http://localhost:41103/mcp"

    pool.fetchrow.return_value = {**row, "observed_state": "stale"}
    stale = await resolve_control_plane_target(pool, expected, required_capability="trigger")
    assert stale.state == "stale"

    pool.fetchrow.return_value = {**row, "policy_state": "quarantined"}
    denied = await resolve_control_plane_target(pool, expected, required_capability="trigger")
    assert denied.state == "denied" and denied.reason == "policy_denied"
    pool.execute.assert_not_awaited()

    # A daemon-authored registry capability cannot exceed the Git roster.
    config = SimpleNamespace(
        name="health",
        port=41103,
        modules={"measurements": {}},
        runtime_seed=SimpleNamespace(route_contract_min=1, route_contract_max=1),
    )
    with patch("butlers.config.list_butlers", return_value=[config]):
        assert expected_route_target("health", required_capability="unconfigured") is None
        admitted = expected_route_target("health", required_capability="trigger")
        assert admitted is not None and admitted.name == "health" and admitted.port == 41103


async def test_internal_route_preflight_coalesces_and_invalidates_cached_policy():
    from butlers.tools.switchboard.routing.preflight import RoutePreflight

    instance_id = uuid.UUID(generate_uuid7_string())
    row = _ready_control_plane_row(instance_id, now=_NOW)
    pool = AsyncMock()
    pool.fetch.return_value = [{"name": "health", "policy_state": "active"}]
    pool.fetchrow.return_value = row
    requests = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        await asyncio.sleep(0.01)
        return httpx.Response(
            200,
            json={
                "schema_version": "butler.control.v1",
                "butler_name": "health",
                "boot_instance_id": str(instance_id),
                "boot_epoch": 7,
                "route_contract": {"min": 1, "max": 1},
                "accepting_routes": True,
            },
        )

    preflight = RoutePreflight(
        pool,
        [SimpleNamespace(name="health", port=41103, type=SimpleNamespace(value="butler"))],
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        min_interval_s=10,
    )
    first, second = await asyncio.gather(preflight.check(), preflight.check())
    assert first.ready and second.ready
    assert len(requests) == 1
    assert requests[0].url.path == "/internal/control-plane/identity"

    pool.fetchrow.return_value = {**row, "boot_epoch": 8}
    successor = await preflight.check()
    assert not successor.ready
    assert len(requests) == 1

    pool.fetchrow.return_value = row
    pool.fetch.return_value = [{"name": "health", "policy_state": "quarantined"}]
    held = await preflight.check()
    assert not held.ready and held.failure_category == "policy_denied"
    assert len(requests) == 1
    pool.execute.assert_not_awaited()
    pool.fetchval.assert_not_awaited()  # no reserve/record operation


async def test_internal_preflight_fixed_target_malformed_and_timeout_fail_closed():
    from butlers.tools.switchboard.routing.preflight import RoutePreflight

    instance_id = uuid.UUID(generate_uuid7_string())
    row = _ready_control_plane_row(instance_id, now=_NOW)
    configs = [
        SimpleNamespace(name="health", port=41103, type=SimpleNamespace(value="butler")),
        SimpleNamespace(name="finance", port=41107, type=SimpleNamespace(value="butler")),
    ]
    pool = AsyncMock()
    pool.fetch.return_value = [
        {"name": "finance", "policy_state": "paused"},
        {"name": "health", "policy_state": "active"},
    ]
    pool.fetchrow.return_value = row
    requests: list[httpx.Request] = []

    def malformed(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"butler_name": "health"})

    preflight = RoutePreflight(
        pool,
        configs,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(malformed)),
        min_interval_s=0,
    )
    result = await preflight.check()
    assert not result.ready and result.failure_category == "invalid_identity"
    assert requests[0].url.port == 41103  # Git roster, never caller/registry endpoint

    pool.fetch.return_value = [
        {"name": "finance", "policy_state": "active"},
        {"name": "health", "policy_state": "active"},
    ]
    pool.fetchrow.return_value = {**row, "name": "finance", "observed_state": "stale"}
    result = await preflight.check()
    assert not result.ready and result.failure_category == "stale_observation"
    assert len(requests) == 1  # first lexical active target fails; no healthier fallback

    pool.fetch.return_value = [{"name": "health", "policy_state": "active"}]
    pool.fetchrow.return_value = row

    async def too_slow(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.02)
        return httpx.Response(200, json=_identity_payload())

    timed = RoutePreflight(
        pool,
        configs[:1],
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(too_slow)),
        deadline_s=0.001,
    )
    result = await timed.check()
    assert not result.ready and result.failure_category == "timeout"
    pool.execute.assert_not_awaited()
    pool.fetchval.assert_not_awaited()


async def test_internal_route_preflight_is_mounted_only_on_switchboard_backend():
    from fastmcp import FastMCP

    from butlers.daemon import ButlerDaemon
    from butlers.tools.switchboard.routing.preflight import RoutePreflight

    instance_id = uuid.UUID(generate_uuid7_string())
    pool = AsyncMock()
    pool.fetch.return_value = [{"name": "health", "policy_state": "active"}]
    pool.fetchrow.return_value = _ready_control_plane_row(instance_id, now=_NOW)
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "schema_version": "butler.control.v1",
                "butler_name": "health",
                "boot_instance_id": str(instance_id),
                "boot_epoch": 7,
                "route_contract": {"min": 1, "max": 1},
                "accepting_routes": True,
            },
        )

    preflight = RoutePreflight(
        pool,
        [SimpleNamespace(name="health", port=41103, type=SimpleNamespace(value="butler"))],
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    switchboard = ButlerDaemon._build_mcp_http_app(
        FastMCP("switchboard-test"),
        butler_name="switchboard",
        route_preflight=preflight,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=switchboard), base_url="http://test"
    ) as client:
        ready = await client.get("/internal/control-plane/route-preflight")
        assert ready.status_code == 200
        assert ready.json() == {"ready": True, "failure_category": "ready"}
        rejected = await client.get("/internal/control-plane/route-preflight?target=health")
        assert rejected.status_code == 400
        assert rejected.json() == {"ready": False, "failure_category": "invalid_request"}
        assert (await client.get("/ready")).status_code == 404
    assert len(requests) == 1

    domain = ButlerDaemon._build_mcp_http_app(
        FastMCP("domain-test"), butler_name="health", route_preflight=preflight
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=domain), base_url="http://test"
    ) as client:
        assert (await client.get("/internal/control-plane/route-preflight")).status_code == 404

    from butlers.api.app import create_app

    dashboard = create_app(api_key="synthetic-owner-key")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=dashboard), base_url="http://test"
    ) as client:
        assert (await client.get("/internal/control-plane/route-preflight")).status_code in {
            401,
            404,
        }
    assert len(requests) == 1
    pool.execute.assert_not_awaited()
    pool.fetchval.assert_not_awaited()


async def test_failed_identity_records_attempt_without_healthy_renewal():
    expected = ExpectedDaemon("health", 41103, "butlers-up")
    payload = _identity_payload()
    pool = AsyncMock()
    pool.fetchrow.return_value = {"boot_epoch": 7, "probe_sequence": 4}
    pool.fetchval.side_effect = [uuid.UUID(payload["boot_instance_id"]), True]

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={**payload, "boot_epoch": 6})
        )
    ) as client:
        outcome = await probe_once(pool, expected, client=client)

    assert outcome.category == "invalid_identity" and outcome.recorded
    assert pool.fetchval.await_args.args[1:] == (
        "health",
        7,
        4,
        False,
        None,
        None,
        "invalid_identity",
    )


async def test_receiver_probe_refuses_bounded_network_failures():
    expected = ExpectedDaemon("health", 41103, "butlers-up")
    for mode, category in [
        ("timeout", "timeout"),
        ("oversized", "invalid_identity"),
        ("duplicate", "invalid_identity"),
    ]:
        payload = _identity_payload()
        pool = AsyncMock()
        pool.fetchrow.return_value = {"boot_epoch": 7, "probe_sequence": 5}
        pool.fetchval.side_effect = [uuid.UUID(payload["boot_instance_id"]), True]

        async def respond(_request: httpx.Request) -> httpx.Response:
            if mode == "timeout":
                await asyncio.sleep(0.02)
                return httpx.Response(200, json=payload)
            if mode == "duplicate":
                body = json.dumps(payload).replace(
                    '"boot_epoch": 7', '"boot_epoch": 7, "boot_epoch": 7'
                )
                return httpx.Response(200, content=body)
            return httpx.Response(200, content=b"x" * 2049)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            outcome = await probe_once(
                pool, expected, client=client, deadline_s=0.001 if mode == "timeout" else 1
            )

        assert outcome.category == category and outcome.recorded
        assert pool.fetchval.await_args.args[4] is False


async def test_partial_shadow_cycle_cannot_claim_complete_fleet():
    pool = AsyncMock()
    pool.fetch.return_value = []
    pool.fetchval.return_value = datetime.now(UTC)
    prober = SimpleNamespace(probe=AsyncMock(return_value=ProbeOutcome("observer_error", False)))

    cycle = await run_shadow_cycle(pool, (ExpectedDaemon("health", 41103, "butlers-up"),), prober)

    assert cycle.complete is False
    assert cycle.expected_count == 1 and cycle.recorded_count == 0
    assert cycle.mismatch_count == 1

    # A later operator TTL reduction wins over the Git seed; the observer
    # must shorten its next interval rather than keep the original 300s.
    pool.fetch.return_value = [{"name": "health", "liveness_ttl_seconds": 30}]
    assert (
        await _effective_observer_interval_s(pool, (ExpectedDaemon("health", 41103, "butlers-up"),))
        == 15
    )
