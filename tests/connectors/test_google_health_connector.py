"""Google Health connector tests — ingest.v1 envelope contract, scope gate, API client.

Covers:

- ``GoogleHealthClient`` 401 → token refresh → retry once → raise.
- ``GoogleHealthClient`` 429 Retry-After parsing + rate-limit header capture.
- Exponential backoff helper produces delays within expected bounds.
- ``build_sleep_session_envelope`` / ``build_daily_summary_envelope`` produce
  contract-compliant ``ingest.v1`` payloads (source.channel="wellness",
  source.provider="google_health", 3-segment endpoint_identity,
  deterministic idempotency keys).
- ``GoogleHealthConnector._get_health_state()`` maps internal flags to the
  allowed heartbeat states (``healthy | degraded | error``) without ever
  returning ``broken``.
- ``_extract_records`` / ``_record_identity`` unpacking of canonical and
  fallback response shapes.
- ``_cursor_endpoint_identity`` builds the per-resource cursor key while
  ``_endpoint_identity_for_user`` keeps the envelope identity canonical.

[bu-k5l35.2.1]
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from butlers.connectors.google_health import (
    GOOGLE_HEALTH_SCOPES,
    RESOURCE_BUNDLES,
    GoogleHealthConnector,
    GoogleHealthConnectorConfig,
    _build_activity_records,
    _cursor_endpoint_identity,
    _endpoint_identity_for_user,
    _extract_records,
    _format_sleep_duration_label,
    _normalize_google_health_record,
    _record_identity,
    build_daily_summary_envelope,
    build_sleep_session_envelope,
)
from butlers.connectors.google_health_client import (
    GoogleHealthClient,
    GoogleHealthCredentialError,
    GoogleHealthRateLimitError,
    GoogleHealthSourcePreconditionError,
    exponential_backoff_delay,
)

_OWNER_EMAIL = "owner@example.com"
_ENDPOINT = _endpoint_identity_for_user(_OWNER_EMAIL)
_OBSERVED = "2026-04-24T10:00:00+00:00"


# ---------------------------------------------------------------------------
# Scope set contract
# ---------------------------------------------------------------------------


def test_google_health_scopes_are_full_urls() -> None:
    for scope in GOOGLE_HEALTH_SCOPES:
        assert scope.startswith("https://www.googleapis.com/auth/googlehealth.")


def test_google_health_scope_count_is_three() -> None:
    assert len(GOOGLE_HEALTH_SCOPES) == 3


def test_resource_bundles_include_required_types() -> None:
    resources = {b.resource for b in RESOURCE_BUNDLES}
    required = {"sleep", "activity", "resting_hr", "hrv", "spo2", "breathing_rate", "vo2_max"}
    assert required.issubset(resources)


def test_resource_bundle_paths_match_google_health_v4_discovery() -> None:
    paths = {b.resource: b.endpoint_path for b in RESOURCE_BUNDLES}
    assert paths == {
        "sleep": "/users/me/dataTypes/sleep/dataPoints:reconcile",
        "activity": "/users/me/dataTypes/steps/dataPoints:dailyRollUp",
        "resting_hr": "/users/me/dataTypes/daily-resting-heart-rate/dataPoints:reconcile",
        "hrv": "/users/me/dataTypes/daily-heart-rate-variability/dataPoints:reconcile",
        "spo2": "/users/me/dataTypes/daily-oxygen-saturation/dataPoints:reconcile",
        "breathing_rate": "/users/me/dataTypes/daily-respiratory-rate/dataPoints:reconcile",
        "vo2_max": "/users/me/dataTypes/daily-vo2-max/dataPoints:reconcile",
    }


# ---------------------------------------------------------------------------
# Endpoint identity shape
# ---------------------------------------------------------------------------


def test_endpoint_identity_uses_three_segment_canonical_form() -> None:
    assert _ENDPOINT == "google_health:user:owner@example.com"


def test_cursor_endpoint_identity_appends_resource_suffix() -> None:
    got = _cursor_endpoint_identity(_OWNER_EMAIL, "sleep")
    assert got == "google_health:user:owner@example.com:sleep"
    # Envelope identity stays canonical.
    assert _ENDPOINT != got


# ---------------------------------------------------------------------------
# Envelope builders
# ---------------------------------------------------------------------------


def test_sleep_session_envelope_shape() -> None:
    env = build_sleep_session_envelope(
        endpoint_identity=_ENDPOINT,
        google_user_id=_OWNER_EMAIL,
        session_id="sess-123",
        session_record={
            "session_id": "sess-123",
            "durationMillis": 7 * 3600 * 1000 + 23 * 60 * 1000,
            "efficiency": 91,
            "stages": {"deep": 60, "light": 120, "rem": 80, "wake": 15},
        },
        observed_at=_OBSERVED,
    )
    assert env["schema_version"] == "ingest.v1"
    assert env["source"]["channel"] == "wellness"
    assert env["source"]["provider"] == "google_health"
    assert env["source"]["endpoint_identity"] == _ENDPOINT
    assert env["sender"]["identity"] == _OWNER_EMAIL
    assert env["event"]["external_event_id"] == "google_health:sleep_session:sess-123"
    assert env["control"]["idempotency_key"] == "google_health:sleep:sess-123"
    assert env["control"]["policy_tier"] == "default"
    assert env["control"]["ingestion_tier"] == "full"
    assert "Slept 7h 23m" in env["payload"]["normalized_text"]
    # Raw payload retains the full record for downstream translators.
    assert env["payload"]["raw"]["stages"]["deep"] == 60


def test_daily_summary_envelope_shape() -> None:
    env = build_daily_summary_envelope(
        endpoint_identity=_ENDPOINT,
        google_user_id=_OWNER_EMAIL,
        resource="resting_hr",
        record_date="2026-04-23",
        record={"value": 58, "date": "2026-04-23"},
        normalized_summary_template="Resting HR: {value} bpm",
        observed_at=_OBSERVED,
    )
    assert env["event"]["external_event_id"] == "google_health:resting_hr:2026-04-23"
    assert env["control"]["idempotency_key"] == "google_health:resting_hr:2026-04-23"
    assert env["payload"]["normalized_text"] == "Resting HR: 58 bpm"


def test_envelope_idempotency_keys_are_deterministic() -> None:
    e1 = build_daily_summary_envelope(
        endpoint_identity=_ENDPOINT,
        google_user_id=_OWNER_EMAIL,
        resource="activity",
        record_date="2026-04-20",
        record={"value": 9341},
        normalized_summary_template="Steps: {value}",
        observed_at=_OBSERVED,
    )
    e2 = build_daily_summary_envelope(
        endpoint_identity=_ENDPOINT,
        google_user_id=_OWNER_EMAIL,
        resource="activity",
        record_date="2026-04-20",
        record={"value": 9341},
        normalized_summary_template="Steps: {value}",
        observed_at="2026-04-24T11:00:00+00:00",  # different observed_at
    )
    assert e1["control"]["idempotency_key"] == e2["control"]["idempotency_key"]


# ---------------------------------------------------------------------------
# Sleep duration formatting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ms,expected",
    [
        (0, "0m"),
        (60_000, "1m"),
        (3_600_000, "1h 0m"),
        (3_600_000 + 30 * 60_000, "1h 30m"),
        (7 * 3_600_000 + 23 * 60_000, "7h 23m"),
    ],
)
def test_format_sleep_duration_label(ms: int, expected: str) -> None:
    assert _format_sleep_duration_label(ms) == expected


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def test_extract_records_from_sessions_shape() -> None:
    data = {"sessions": [{"session_id": "a"}, {"session_id": "b"}]}
    assert len(_extract_records(data)) == 2


def test_extract_records_from_data_points_shape() -> None:
    data = {"dataPoints": [{"value": 1}, {"value": 2}]}
    assert len(_extract_records(data)) == 2


def test_extract_records_from_rollup_data_points_shape() -> None:
    data = {"rollupDataPoints": [{"steps": {"countSum": "1200"}}]}
    assert len(_extract_records(data)) == 1


def test_extract_records_returns_empty_when_no_known_list() -> None:
    assert _extract_records({"foo": "bar"}) == []


def test_normalize_sleep_data_point_shape() -> None:
    bundle = next(b for b in RESOURCE_BUNDLES if b.resource == "sleep")
    record = _normalize_google_health_record(
        bundle,
        {
            "name": "users/u/dataTypes/sleep/dataPoints/sleep-1",
            "sleep": {
                "interval": {
                    "startTime": "2026-04-24T22:00:00Z",
                    "endTime": "2026-04-25T06:30:00Z",
                },
                "summary": {
                    "minutesAsleep": "450",
                    "minutesInSleepPeriod": "510",
                    "stagesSummary": [
                        {"stage": "DEEP", "totalDuration": "5400s"},
                        {"stage": "REM", "totalDuration": "7200s"},
                    ],
                },
            },
        },
    )

    assert record["session_id"] == "sleep-1"
    assert record["durationMillis"] == 510 * 60_000
    assert record["efficiency"] == 88
    assert record["startTime"] == "2026-04-24T22:00:00Z"
    assert record["stages"]["deep"] == 90
    assert record["stages"]["rem"] == 120


@pytest.mark.parametrize(
    "resource,union_key,value_key,value",
    [
        ("resting_hr", "dailyRestingHeartRate", "beatsPerMinute", "58"),
        ("hrv", "dailyHeartRateVariability", "averageHeartRateVariabilityMilliseconds", 42.5),
        ("spo2", "dailyOxygenSaturation", "averagePercentage", 96.3),
        ("breathing_rate", "dailyRespiratoryRate", "breathsPerMinute", 14.2),
        ("vo2_max", "dailyVo2Max", "vo2Max", 41.7),
    ],
)
def test_normalize_daily_data_point_shape(
    resource: str,
    union_key: str,
    value_key: str,
    value: object,
) -> None:
    bundle = next(b for b in RESOURCE_BUNDLES if b.resource == resource)
    record = _normalize_google_health_record(
        bundle,
        {
            "name": f"users/u/dataTypes/{bundle.data_type}/dataPoints/p",
            union_key: {"date": {"year": 2026, "month": 4, "day": 24}, value_key: value},
        },
    )

    assert record["date"] == "2026-04-24"
    assert record["value"] == value
    assert record[value_key] == value


def test_build_activity_records_merges_step_and_active_minute_rollups() -> None:
    records = _build_activity_records(
        {
            "rollupDataPoints": [
                {
                    "civilStartTime": {"date": {"year": 2026, "month": 4, "day": 24}},
                    "steps": {"countSum": "9341"},
                }
            ]
        },
        {
            "rollupDataPoints": [
                {
                    "civilStartTime": {"date": {"year": 2026, "month": 4, "day": 24}},
                    "activeMinutes": {
                        "activeMinutesRollupByActivityLevel": [
                            {"activityLevel": "LIGHT", "activeMinutes": "20"},
                            {"activityLevel": "MODERATE", "activeMinutes": "35"},
                        ]
                    },
                }
            ]
        },
    )

    assert records == [
        {
            "date": "2026-04-24",
            "steps": 9341,
            "value": 9341,
            "activeMinutes": 55,
            "active_minutes": 55,
        }
    ]


def test_record_identity_for_sleep_uses_session_id() -> None:
    bundle = next(b for b in RESOURCE_BUNDLES if b.resource == "sleep")
    assert _record_identity(bundle, {"session_id": "abc"}) == "abc"


def test_record_identity_for_daily_normalizes_date() -> None:
    bundle = next(b for b in RESOURCE_BUNDLES if b.resource == "activity")
    assert _record_identity(bundle, {"date": "2026-04-23"}) == "2026-04-23"
    assert _record_identity(bundle, {"startTime": "2026-04-23T00:00:00Z"}) == "2026-04-23"


def test_record_identity_returns_none_when_unavailable() -> None:
    bundle = next(b for b in RESOURCE_BUNDLES if b.resource == "spo2")
    assert _record_identity(bundle, {"value": 98}) is None


# ---------------------------------------------------------------------------
# Client — 401 retry semantics
# ---------------------------------------------------------------------------


class _StubTransport(httpx.AsyncBaseTransport):
    """httpx transport that returns pre-scripted responses."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:  # type: ignore[override]
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("No more stubbed responses")
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_client_401_triggers_token_refresh_and_retries_once() -> None:
    responses = [
        httpx.Response(401, json={"error": "expired"}),
        httpx.Response(200, json={"sessions": [{"session_id": "a"}]}),
    ]
    transport = _StubTransport(responses)
    http = httpx.AsyncClient(transport=transport, base_url="https://health.googleapis.com/v4")

    fetcher = AsyncMock(side_effect=["token-old", "token-new"])

    client = GoogleHealthClient(token_fetcher=fetcher, client=http)
    data = await client.get_json("/users/me/dataTypes/sleep/sessions")
    assert data == {"sessions": [{"session_id": "a"}]}
    # Two requests made, with the second bearing the refreshed token.
    assert len(transport.requests) == 2
    assert transport.requests[0].headers["Authorization"] == "Bearer token-old"
    assert transport.requests[1].headers["Authorization"] == "Bearer token-new"
    assert fetcher.await_count == 2
    await http.aclose()


@pytest.mark.asyncio
async def test_client_401_twice_raises_credential_error() -> None:
    responses = [
        httpx.Response(401, json={"error": "expired"}),
        httpx.Response(401, json={"error": "still-expired"}),
    ]
    transport = _StubTransport(responses)
    http = httpx.AsyncClient(transport=transport, base_url="https://health.googleapis.com/v4")
    fetcher = AsyncMock(side_effect=["t1", "t2"])
    client = GoogleHealthClient(token_fetcher=fetcher, client=http)
    with pytest.raises(GoogleHealthCredentialError):
        await client.get_json("/anything")
    await http.aclose()


@pytest.mark.asyncio
async def test_client_429_raises_rate_limit_error_with_retry_after() -> None:
    responses = [httpx.Response(429, headers={"Retry-After": "42"}, json={})]
    transport = _StubTransport(responses)
    http = httpx.AsyncClient(transport=transport, base_url="https://health.googleapis.com/v4")
    fetcher = AsyncMock(return_value="token")
    client = GoogleHealthClient(token_fetcher=fetcher, client=http)
    with pytest.raises(GoogleHealthRateLimitError) as excinfo:
        await client.get_json("/anything")
    assert excinfo.value.retry_after == 42.0
    await http.aclose()


@pytest.mark.asyncio
async def test_client_429_without_retry_after_signals_backoff() -> None:
    responses = [httpx.Response(429, json={})]
    transport = _StubTransport(responses)
    http = httpx.AsyncClient(transport=transport, base_url="https://health.googleapis.com/v4")
    fetcher = AsyncMock(return_value="token")
    client = GoogleHealthClient(token_fetcher=fetcher, client=http)
    with pytest.raises(GoogleHealthRateLimitError) as excinfo:
        await client.get_json("/anything")
    assert excinfo.value.retry_after is None
    await http.aclose()


@pytest.mark.asyncio
async def test_client_account_not_linked_raises_source_precondition() -> None:
    responses = [
        httpx.Response(
            400,
            json={
                "error": {
                    "code": 400,
                    "message": "The account is not linked to Google Health.",
                    "status": "FAILED_PRECONDITION",
                    "details": [
                        {
                            "reason": "ACCOUNT_NOT_LINKED",
                            "metadata": {"redirect_uri": "https://fitbit.google.com/auth/signup"},
                        }
                    ],
                }
            },
        )
    ]
    transport = _StubTransport(responses)
    http = httpx.AsyncClient(transport=transport, base_url="https://health.googleapis.com/v4")
    fetcher = AsyncMock(return_value="token")
    client = GoogleHealthClient(token_fetcher=fetcher, client=http)
    with pytest.raises(GoogleHealthSourcePreconditionError) as excinfo:
        await client.get_json("/users/me/dataTypes/sleep/dataPoints:reconcile")
    assert excinfo.value.reason == "ACCOUNT_NOT_LINKED"
    assert excinfo.value.redirect_uri == "https://fitbit.google.com/auth/signup"
    await http.aclose()


@pytest.mark.asyncio
async def test_client_captures_rate_limit_headers_from_response() -> None:
    responses = [
        httpx.Response(
            200,
            json={"items": []},
            headers={"X-RateLimit-Remaining": "99", "X-RateLimit-Reset": "60"},
        )
    ]
    transport = _StubTransport(responses)
    http = httpx.AsyncClient(transport=transport, base_url="https://health.googleapis.com/v4")
    fetcher = AsyncMock(return_value="token")
    client = GoogleHealthClient(token_fetcher=fetcher, client=http)
    await client.get_json("/anything")
    headers = client.last_rate_limit_headers
    assert headers.get("X-RateLimit-Remaining") == "99"
    assert headers.get("X-RateLimit-Reset") == "60"
    await http.aclose()


def test_exponential_backoff_delay_within_bounds() -> None:
    # attempt=1 should be around 30s; attempt=3 around 120s; bounded by max 600.
    for attempt in range(1, 6):
        delay = exponential_backoff_delay(attempt)
        assert 0 <= delay <= 600


# ---------------------------------------------------------------------------
# Connector health-state mapping
# ---------------------------------------------------------------------------


def _make_connector() -> GoogleHealthConnector:
    config = GoogleHealthConnectorConfig(
        switchboard_mcp_url="http://localhost:41999/mcp",
        poll_intervals={b.resource: b.default_interval_s for b in RESOURCE_BUNDLES},
    )
    return GoogleHealthConnector(config=config, shared_pool=None, cursor_pool=None)


def test_health_state_reports_degraded_when_account_missing() -> None:
    connector = _make_connector()
    connector._account_missing = True
    state, err = connector._get_health_state()
    assert state == "degraded"
    assert err == "no_primary_account"


def test_health_state_reports_degraded_when_scope_missing() -> None:
    connector = _make_connector()
    connector._account_missing = False
    connector._scope_missing = True
    state, err = connector._get_health_state()
    assert state == "degraded"
    assert err == "scope_missing"


def test_health_state_reports_error_on_auth_error() -> None:
    connector = _make_connector()
    connector._account_missing = False
    connector._scope_missing = False
    connector._auth_error = True
    connector._auth_error_message = "token_invalid"
    state, err = connector._get_health_state()
    assert state == "error"
    assert err == "token_invalid"


def test_health_state_reports_healthy_when_all_green() -> None:
    connector = _make_connector()
    connector._account_missing = False
    connector._scope_missing = False
    connector._auth_error = False
    connector._last_source_api_ok = True
    state, err = connector._get_health_state()
    assert state == "healthy"
    assert err is None


def test_health_state_reports_degraded_with_source_api_reason() -> None:
    connector = _make_connector()
    connector._account_missing = False
    connector._scope_missing = False
    connector._auth_error = False
    connector._last_source_api_ok = False
    connector._source_api_error_message = "account_not_linked"
    state, err = connector._get_health_state()
    assert state == "degraded"
    assert err == "account_not_linked"


def test_health_state_never_emits_broken_string() -> None:
    connector = _make_connector()
    for account_missing, scope_missing, auth_error, api_ok in [
        (True, True, False, None),
        (False, True, False, False),
        (False, False, True, None),
        (False, False, False, True),
        (False, False, False, False),
    ]:
        connector._account_missing = account_missing
        connector._scope_missing = scope_missing
        connector._auth_error = auth_error
        connector._last_source_api_ok = api_ok
        state, _ = connector._get_health_state()
        assert state in {"healthy", "degraded", "error"}
        assert state != "broken"


# ---------------------------------------------------------------------------
# Config env-parsing
# ---------------------------------------------------------------------------


def test_config_from_env_requires_switchboard_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SWITCHBOARD_MCP_URL", raising=False)
    with pytest.raises(ValueError):
        GoogleHealthConnectorConfig.from_env()


def test_config_from_env_reads_backfill_days(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://x")
    monkeypatch.setenv("GOOGLE_HEALTH_BACKFILL_DAYS", "7")
    cfg = GoogleHealthConnectorConfig.from_env()
    assert cfg.backfill_days == 7


def test_config_from_env_falls_back_on_invalid_int(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://x")
    monkeypatch.setenv("GOOGLE_HEALTH_BACKFILL_DAYS", "not-a-number")
    cfg = GoogleHealthConnectorConfig.from_env()
    assert cfg.backfill_days == 30


# ---------------------------------------------------------------------------
# Contact info registration contract (idempotency helper signature)
# ---------------------------------------------------------------------------


def test_upsert_contact_info_is_exposed_from_connector_module() -> None:
    """The OAuth callback imports this symbol — keep it present."""
    from butlers.connectors.google_health import upsert_google_health_contact_info

    assert callable(upsert_google_health_contact_info)


# ---------------------------------------------------------------------------
# Window computation — first-run backfill vs steady-state
# ---------------------------------------------------------------------------


def test_compute_window_first_run_covers_backfill_days() -> None:
    connector = _make_connector()
    state = connector._resources["sleep"]
    assert state.backfill_done is False
    since, until = connector._compute_window(state)
    # since should be ~backfill_days ago (30 days default).
    delta = (until - since).days
    assert delta >= connector._config.backfill_days - 1


def test_compute_window_steady_state_uses_tight_trailing_window() -> None:
    connector = _make_connector()
    state = connector._resources["sleep"]
    state.backfill_done = True
    from datetime import UTC, datetime, timedelta

    state.last_poll_at = datetime.now(UTC) - timedelta(minutes=10)
    since, until = connector._compute_window(state)
    delta = (until - since).days
    # Steady-state window should be <= 7 days, not the 30-day backfill.
    assert delta <= 7


# ---------------------------------------------------------------------------
# Resource state cursor advance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_resource_skips_duplicate_records(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the API returns the same record as the current cursor, no envelope emits."""
    connector = _make_connector()
    connector._google_user_id = _OWNER_EMAIL
    connector._endpoint_identity = _ENDPOINT

    # Pre-seed cursor so the next poll sees the same record.
    state = connector._resources["sleep"]
    state.last_cursor = "sess-42"
    state.backfill_done = True

    fake_api: Any = type(
        "Fake",
        (),
        {
            "get_json": AsyncMock(return_value={"sessions": [{"session_id": "sess-42"}]}),
            "last_rate_limit_headers": {},
        },
    )()
    monkeypatch.setattr(connector, "_ensure_api_client", lambda: fake_api)

    submit_mock = AsyncMock()
    monkeypatch.setattr(connector, "_submit_envelope", submit_mock)

    await connector._poll_resource(state)
    assert submit_mock.await_count == 0


@pytest.mark.asyncio
async def test_poll_resource_emits_envelope_for_new_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connector = _make_connector()
    connector._google_user_id = _OWNER_EMAIL
    connector._endpoint_identity = _ENDPOINT
    state = connector._resources["sleep"]
    state.last_cursor = None
    state.backfill_done = False

    fake_api: Any = type(
        "Fake",
        (),
        {
            "get_json": AsyncMock(
                return_value={
                    "sessions": [
                        {
                            "session_id": "sess-new",
                            "durationMillis": 3600_000,
                            "efficiency": 85,
                        }
                    ]
                }
            ),
            "last_rate_limit_headers": {},
        },
    )()
    monkeypatch.setattr(connector, "_ensure_api_client", lambda: fake_api)
    monkeypatch.setattr(connector, "_save_cursor", AsyncMock())

    submit_mock = AsyncMock()
    monkeypatch.setattr(connector, "_submit_envelope", submit_mock)

    await connector._poll_resource(state)
    assert submit_mock.await_count == 1
    envelope = submit_mock.await_args.args[0]
    assert envelope["event"]["external_event_id"] == "google_health:sleep_session:sess-new"
    assert envelope["source"]["channel"] == "wellness"
    assert envelope["source"]["provider"] == "google_health"


@pytest.mark.asyncio
async def test_poll_activity_resource_uses_daily_rollup_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connector = _make_connector()
    connector._google_user_id = _OWNER_EMAIL
    connector._endpoint_identity = _ENDPOINT
    state = connector._resources["activity"]
    state.backfill_done = False

    fake_api: Any = type(
        "Fake",
        (),
        {
            "post_json": AsyncMock(
                side_effect=[
                    {
                        "rollupDataPoints": [
                            {
                                "civilStartTime": {"date": {"year": 2026, "month": 4, "day": 24}},
                                "steps": {"countSum": "1234"},
                            }
                        ]
                    },
                    {
                        "rollupDataPoints": [
                            {
                                "civilStartTime": {"date": {"year": 2026, "month": 4, "day": 24}},
                                "activeMinutes": {
                                    "activeMinutesRollupByActivityLevel": [
                                        {"activityLevel": "MODERATE", "activeMinutes": "12"}
                                    ]
                                },
                            }
                        ]
                    },
                ]
            ),
            "last_rate_limit_headers": {},
        },
    )()
    monkeypatch.setattr(connector, "_ensure_api_client", lambda: fake_api)
    monkeypatch.setattr(connector, "_save_cursor", AsyncMock())

    submit_mock = AsyncMock()
    monkeypatch.setattr(connector, "_submit_envelope", submit_mock)

    await connector._poll_resource(state)

    assert fake_api.post_json.await_count == 2
    assert fake_api.post_json.await_args_list[0].args[0] == (
        "/users/me/dataTypes/steps/dataPoints:dailyRollUp"
    )
    assert fake_api.post_json.await_args_list[1].args[0] == (
        "/users/me/dataTypes/active-minutes/dataPoints:dailyRollUp"
    )
    envelope = submit_mock.await_args.args[0]
    assert envelope["event"]["external_event_id"] == "google_health:activity:2026-04-24"
    assert envelope["payload"]["raw"]["steps"] == 1234
    assert envelope["payload"]["raw"]["activeMinutes"] == 12
