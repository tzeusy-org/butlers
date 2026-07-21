"""Condensed OwnTracks connector tests — ingest.v1 contract only.

Consolidates: test_owntracks_connector.py, test_owntracks_integration.py,
test_owntracks_checkpoint.py, test_owntracks_retention.py, test_owntracks_auth.py

Verifies:
- ingest.v1 envelope production for location, transition, and waypoints events
- metadata vs full tier: raw field null in metadata tier
- Idempotency key determinism
- Normalized text: coordinates present, SSID excluded
- retention purge degradation through the existing connector health callback

[bu-35fm7]
"""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock

import pytest

from butlers.connectors.owntracks import (
    OwnTracksConnector,
    OwnTracksConnectorConfig,
    OwnTracksRetention,
    OwnTracksRetentionConfig,
    _verify_webhook_auth,
    build_location_envelope,
    build_location_normalized_text,
    build_transition_envelope,
    build_waypoints_envelope,
    persist_location_point,
)

_ENDPOINT = "owntracks:device:phone1"
_OBSERVED = "2026-03-26T10:00:00+00:00"

_LOCATION_PAYLOAD = {
    "_type": "location",
    "tst": 1711447200,
    "tid": "ph",
    "lat": 37.7749,
    "lon": -122.4194,
    "acc": 10,
    "alt": 50,
    "vel": 0,
}

_TRANSITION_PAYLOAD = {
    "_type": "transition",
    "tst": 1711447300,
    "tid": "ph",
    "event": "enter",
    "desc": "Home",
    "lat": 37.7749,
    "lon": -122.4194,
}


class _PurgeConnection:
    """Small asyncpg boundary fake that returns or raises queued purge results."""

    def __init__(self, outcomes: list[str | Exception]) -> None:
        self._outcomes = outcomes

    async def execute(self, *_args: object) -> str:
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _PurgeAcquire:
    def __init__(self, connection: _PurgeConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> _PurgeConnection:
        return self._connection

    async def __aexit__(self, *_args: object) -> None:
        return None


class _PurgePool:
    def __init__(self, outcomes: list[str | Exception]) -> None:
        self._connection = _PurgeConnection(outcomes)

    def acquire(self) -> _PurgeAcquire:
        return _PurgeAcquire(self._connection)


class _ChangingFailureStreakRetention(OwnTracksRetention):
    """Retention fixture that simulates a concurrent streak update between reads."""

    def __init__(self) -> None:
        self._failure_streak_values = iter((1, 2, 3))
        self._failure_streak_reads = 0

    def __getattribute__(self, name: str) -> object:
        if name == "_consecutive_failures":
            reads = object.__getattribute__(self, "_failure_streak_reads")
            object.__setattr__(self, "_failure_streak_reads", reads + 1)
            return next(object.__getattribute__(self, "_failure_streak_values"))
        return object.__getattribute__(self, name)

    @property
    def failure_streak_reads(self) -> int:
        return self._failure_streak_reads


def _make_retention(*outcomes: str | Exception) -> OwnTracksRetention:
    return OwnTracksRetention(
        OwnTracksRetentionConfig(retention_days=30),
        _PurgePool(list(outcomes)),
    )


def _make_connector(retention: OwnTracksRetention) -> OwnTracksConnector:
    connector = OwnTracksConnector(
        OwnTracksConnectorConfig(switchboard_mcp_url="http://switchboard.test"),
        webhook_token="test-token",
    )
    connector._retention = retention
    return connector


def test_location_envelope_schema_version() -> None:
    env = build_location_envelope(_LOCATION_PAYLOAD, _ENDPOINT, _OBSERVED, "metadata")
    assert env["schema_version"] == "ingest.v1"
    assert env["source"]["channel"] == "owntracks"
    assert env["source"]["provider"] == "owntracks"


def test_location_envelope_metadata_tier_raw_is_null() -> None:
    """Metadata-tier Switchboard envelopes must omit the raw GPS payload."""
    env = build_location_envelope(_LOCATION_PAYLOAD, _ENDPOINT, _OBSERVED, "metadata")
    assert env["payload"]["raw"] is None
    assert env["control"]["ingestion_tier"] == "metadata"


def test_location_envelope_full_tier_has_raw() -> None:
    """Full-tier Switchboard envelopes must carry the complete raw payload."""
    env = build_location_envelope(_LOCATION_PAYLOAD, _ENDPOINT, _OBSERVED, "full")
    assert env["payload"]["raw"] is not None
    assert env["control"]["ingestion_tier"] == "full"


async def test_persist_location_point_keeps_ssid_and_inregions_untouched() -> None:
    """The connector's durable JSONB evidence keeps app-provided context verbatim."""
    pool = AsyncMock()
    pool.fetchval.return_value = "point-id"
    raw_payload = {
        **_LOCATION_PAYLOAD,
        "SSID": "Office WiFi",
        "inregions": ["Office", "Downtown"],
    }

    inserted = await persist_location_point(
        pool,
        endpoint_identity=_ENDPOINT,
        tst=_LOCATION_PAYLOAD["tst"],
        lat=_LOCATION_PAYLOAD["lat"],
        lon=_LOCATION_PAYLOAD["lon"],
        accuracy=_LOCATION_PAYLOAD["acc"],
        trigger="p",
        raw_payload=raw_payload,
    )

    assert inserted is True
    sql, *args = pool.fetchval.await_args.args
    assert "connectors.owntracks_points" in sql
    assert "raw_payload" in sql
    persisted_payload = args[7]
    assert persisted_payload is raw_payload
    assert persisted_payload["SSID"] == "Office WiFi"
    assert persisted_payload["inregions"] == ["Office", "Downtown"]


async def test_retention_purge_failures_stay_retryable_and_degrade_connector_health() -> None:
    retention = _make_retention(RuntimeError("first failure"), RuntimeError("second failure"))
    connector = _make_connector(retention)

    await retention._run_purge()

    assert connector._get_health_state() == (
        "degraded",
        "OwnTracks retention purge has failed 1 consecutive time",
    )

    await retention._run_purge()

    assert connector._get_health_state() == (
        "degraded",
        "OwnTracks retention purge has failed 2 consecutive times",
    )


def test_retention_health_diagnostic_uses_one_failure_streak_snapshot() -> None:
    retention = _ChangingFailureStreakRetention()

    assert (
        retention.health_degradation_message
        == "OwnTracks retention purge has failed 1 consecutive time"
    )
    assert retention.failure_streak_reads == 1


async def test_successful_retention_purge_resets_degraded_connector_health() -> None:
    retention = _make_retention(RuntimeError("temporary failure"), "DELETE 3")
    connector = _make_connector(retention)

    await retention._run_purge()
    await retention._run_purge()

    assert connector._get_health_state() == ("healthy", None)


async def test_connector_error_outranks_retention_degradation() -> None:
    retention = _make_retention(RuntimeError("retention failure"))
    connector = _make_connector(retention)

    await retention._run_purge()
    connector._health_error = "Switchboard ingest unavailable"

    assert connector._get_health_state() == ("error", "Switchboard ingest unavailable")


async def test_retention_health_diagnostic_does_not_leak_exception_details() -> None:
    retention = _make_retention(RuntimeError("database password=swordfish traceback details"))
    connector = _make_connector(retention)

    await retention._run_purge()

    state, diagnostic = connector._get_health_state()

    assert state == "degraded"
    assert diagnostic == "OwnTracks retention purge has failed 1 consecutive time"
    assert "swordfish" not in diagnostic
    assert "traceback" not in diagnostic


def test_location_envelope_event_id_format() -> None:
    env = build_location_envelope(_LOCATION_PAYLOAD, _ENDPOINT, _OBSERVED, "metadata")
    assert env["event"]["external_event_id"] == "1711447200:location"


def test_location_idempotency_key_deterministic() -> None:
    e1 = build_location_envelope(_LOCATION_PAYLOAD, _ENDPOINT, _OBSERVED, "metadata")
    e2 = build_location_envelope(_LOCATION_PAYLOAD, _ENDPOINT, _OBSERVED, "metadata")
    assert e1["control"]["idempotency_key"] == e2["control"]["idempotency_key"]


def test_transition_envelope_event_id_includes_event_type() -> None:
    env = build_transition_envelope(_TRANSITION_PAYLOAD, _ENDPOINT, _OBSERVED, "metadata")
    assert env["schema_version"] == "ingest.v1"
    assert "enter" in env["event"]["external_event_id"]


def test_envelopes_pass_parse_ingest_envelope() -> None:
    """OwnTracks location and waypoints envelopes validate against parse_ingest_envelope."""
    from pydantic import ValidationError

    from butlers.tools.switchboard.routing.contracts import parse_ingest_envelope

    waypoints = {"_type": "waypoints", "tst": 1711447400, "tid": "ph", "waypoints": []}
    envelopes = [
        build_location_envelope(_LOCATION_PAYLOAD, _ENDPOINT, _OBSERVED, "metadata"),
        build_waypoints_envelope(waypoints, _ENDPOINT, _OBSERVED, "metadata"),
    ]
    for env in envelopes:
        assert env["schema_version"] == "ingest.v1"
        try:
            parse_ingest_envelope(env)
        except ValidationError as exc:
            pytest.fail(f"parse_ingest_envelope raised ValidationError: {exc}")


def test_location_normalized_text_includes_coordinates() -> None:
    """Normalized text includes GPS coordinates (both metadata and full tiers)."""
    text = build_location_normalized_text(_LOCATION_PAYLOAD, "metadata")
    assert "37.7749" in text
    assert "122.4194" in text


def test_location_normalized_text_excludes_ssid() -> None:
    """SSID must not appear in normalized text (privacy constraint)."""
    payload_with_ssid = {**_LOCATION_PAYLOAD, "SSID": "HomeNetwork"}
    text = build_location_normalized_text(payload_with_ssid, "metadata")
    assert "HomeNetwork" not in text


_TOKEN = "s3cret-token"


def _basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


@pytest.mark.parametrize(
    "header",
    [
        f"Bearer {_TOKEN}",
        _basic("zoos", _TOKEN),
        _basic("", _TOKEN),  # OwnTracks clients may send empty username
    ],
)
def test_verify_webhook_auth_accepts_valid_credentials(header: str) -> None:
    assert _verify_webhook_auth(header, _TOKEN) is True


@pytest.mark.parametrize(
    "header",
    [
        "",
        "Bearer wrong",
        "Bearer",
        "bogus scheme",
        _basic("zoos", "wrong"),
        "Basic not-base64!!",
        "Basic " + base64.b64encode(b"no-colon-here").decode(),
    ],
)
def test_verify_webhook_auth_rejects_invalid_credentials(header: str) -> None:
    assert _verify_webhook_auth(header, _TOKEN) is False


def test_verify_webhook_auth_rejects_empty_expected_token() -> None:
    assert _verify_webhook_auth(f"Bearer {_TOKEN}", "") is False
