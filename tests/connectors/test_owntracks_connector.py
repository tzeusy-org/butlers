"""Condensed OwnTracks connector tests — ingest.v1 contract only.

Consolidates: test_owntracks_connector.py, test_owntracks_integration.py,
test_owntracks_checkpoint.py, test_owntracks_retention.py, test_owntracks_auth.py

Verifies:
- ingest.v1 envelope production for location, transition, and waypoints events
- metadata vs full tier: raw field null in metadata tier
- Idempotency key determinism
- Normalized text: coordinates present, SSID excluded

[bu-35fm7]
"""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock

import pytest

from butlers.connectors.owntracks import (
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
