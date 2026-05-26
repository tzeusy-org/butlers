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

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.connectors.google_health import (
    GOOGLE_HEALTH_SCOPES,
    RESOURCE_BUNDLES,
    GoogleHealthConnector,
    GoogleHealthConnectorConfig,
    OwnerContext,
    ResourceState,
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
from butlers.google_account_registry import HealthScopedAccount

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


def _make_connector_with_account(
    email: str = _OWNER_EMAIL,
    account_id: uuid.UUID | None = None,
    entity_id: uuid.UUID | None = None,
) -> tuple[GoogleHealthConnector, OwnerContext]:
    """Create a connector with a single pre-loaded account for per-account tests."""
    connector = _make_connector()
    acct_id = account_id or uuid.uuid4()
    ent_id = entity_id or uuid.uuid4()
    ctx = OwnerContext(
        account_id=acct_id,
        email=email,
        entity_id=ent_id,
        refresh_token_present=True,
        endpoint_identity=_endpoint_identity_for_user(email),
    )
    connector._accounts[acct_id] = ctx
    for bundle in RESOURCE_BUNDLES:
        connector._resources[(acct_id, bundle.resource)] = ResourceState(bundle=bundle)
    connector._google_user_id = email
    connector._endpoint_identity = ctx.endpoint_identity
    connector._account_missing = False
    connector._scope_missing = False
    return connector, ctx


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
    connector, ctx = _make_connector_with_account()
    state = connector._resources[(ctx.account_id, "sleep")]
    assert state.backfill_done is False
    since, until = connector._compute_window(state)
    # since should be ~backfill_days ago (30 days default).
    delta = (until - since).days
    assert delta >= connector._config.backfill_days - 1


def test_compute_window_steady_state_uses_tight_trailing_window() -> None:
    connector, ctx = _make_connector_with_account()
    state = connector._resources[(ctx.account_id, "sleep")]
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
    connector, ctx = _make_connector_with_account()

    # Pre-seed cursor so the next poll sees the same record.
    state = connector._resources[(ctx.account_id, "sleep")]
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
    monkeypatch.setattr(connector, "_make_account_api_client", lambda _acct_id: fake_api)

    submit_mock = AsyncMock()
    monkeypatch.setattr(connector, "_submit_envelope", submit_mock)

    await connector._poll_resource(ctx.account_id, state)
    assert submit_mock.await_count == 0


@pytest.mark.asyncio
async def test_poll_resource_emits_envelope_for_new_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connector, ctx = _make_connector_with_account()
    state = connector._resources[(ctx.account_id, "sleep")]
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
    monkeypatch.setattr(connector, "_make_account_api_client", lambda _acct_id: fake_api)
    monkeypatch.setattr(connector, "_save_cursor", AsyncMock())

    submit_mock = AsyncMock()
    monkeypatch.setattr(connector, "_submit_envelope", submit_mock)

    await connector._poll_resource(ctx.account_id, state)
    assert submit_mock.await_count == 1
    envelope = submit_mock.await_args.args[0]
    assert envelope["event"]["external_event_id"] == "google_health:sleep_session:sess-new"
    assert envelope["source"]["channel"] == "wellness"
    assert envelope["source"]["provider"] == "google_health"


@pytest.mark.asyncio
async def test_poll_activity_resource_uses_daily_rollup_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connector, ctx = _make_connector_with_account()
    state = connector._resources[(ctx.account_id, "activity")]
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
    monkeypatch.setattr(connector, "_make_account_api_client", lambda _acct_id: fake_api)
    monkeypatch.setattr(connector, "_save_cursor", AsyncMock())

    submit_mock = AsyncMock()
    monkeypatch.setattr(connector, "_submit_envelope", submit_mock)

    await connector._poll_resource(ctx.account_id, state)

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


# ---------------------------------------------------------------------------
# Multi-account acceptance tests [bu-91zdb.1]
# ---------------------------------------------------------------------------


_HEALTH_SCOPE_LIST = list(GOOGLE_HEALTH_SCOPES)
_NON_HEALTH_SCOPE = "https://www.googleapis.com/auth/calendar"
_UUID_A = uuid.uuid4()
_UUID_B = uuid.uuid4()
_UUID_C = uuid.uuid4()
_ENTITY_A = uuid.uuid4()
_ENTITY_B = uuid.uuid4()
_ENTITY_C = uuid.uuid4()


def _make_fake_row(
    account_id: uuid.UUID,
    entity_id: uuid.UUID,
    email: str,
    granted_scopes: list[str],
    status: str = "active",
    refresh_token_present: bool = True,
) -> dict[str, Any]:
    """Build a fake asyncpg-style row dict for list_health_scoped_accounts tests."""
    return {
        "id": account_id,
        "entity_id": entity_id,
        "email": email,
        "granted_scopes": granted_scopes,
        "status": status,
        "refresh_token_present": refresh_token_present,
    }


@pytest.mark.asyncio
async def test_list_health_scoped_accounts_filters_by_status_and_scope_superset() -> None:
    """list_health_scoped_accounts returns only status='active' rows with all three health scopes.

    The SQL WHERE clause handles the status='active' filter; the Python post-filter
    handles the scope-superset check.  The test stubs only the active rows that the
    SQL would return, then asserts that the scope-superset filter excludes the
    partial-scope row.

    Acceptance test [bu-91zdb.1] AC-1.
    """
    from butlers.google_account_registry import list_health_scoped_accounts

    # Row A: active, all three health scopes — should be included.
    row_a = _make_fake_row(_UUID_A, _ENTITY_A, "a@example.com", _HEALTH_SCOPE_LIST)
    # Row B: active, only two health scopes (missing one) — excluded by Python scope filter.
    row_b = _make_fake_row(
        _UUID_B,
        _ENTITY_B,
        "b@example.com",
        _HEALTH_SCOPE_LIST[:2],
    )
    # Simulate the SQL WHERE status='active': the mock returns only active rows.
    # Row C (revoked) would not appear in the DB result set because the SQL filters it.
    active_rows = [row_a, row_b]

    fake_conn = MagicMock()
    fake_conn.fetch = AsyncMock(return_value=active_rows)
    fake_conn.__aenter__ = AsyncMock(return_value=fake_conn)
    fake_conn.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=fake_conn)

    results = await list_health_scoped_accounts(pool, health_scopes=GOOGLE_HEALTH_SCOPES)

    # Only row A qualifies (B is excluded by Python scope-superset filter).
    assert len(results) == 1
    assert results[0].id == _UUID_A
    assert results[0].email == "a@example.com"
    assert results[0].entity_id == _ENTITY_A
    assert results[0].refresh_token_present is True

    # Confirm the SQL WHERE clause filters on status='active'.
    assert fake_conn.fetch.await_count == 1
    sql_called = fake_conn.fetch.await_args.args[0]
    assert "status = 'active'" in sql_called


@pytest.mark.asyncio
async def test_resolve_owner_and_scopes_diffs_account_set_across_cycles() -> None:
    """_resolve_owner_and_scopes detects adds and removals across back-to-back calls.

    Cycle 1: two accounts present → both added.
    Cycle 2: one account removed (scope revoked) → removal recorded, teardown called.
    Cycle 3: first account still present → no adds or removals; context refreshed.
    Acceptance test [bu-91zdb.1] AC-2.
    """
    connector = _make_connector()

    # Build two fake HealthScopedAccount rows.
    acct_x = HealthScopedAccount(
        id=_UUID_A,
        email="x@example.com",
        entity_id=_ENTITY_A,
        refresh_token_present=True,
    )
    acct_y = HealthScopedAccount(
        id=_UUID_B,
        email="y@example.com",
        entity_id=_ENTITY_B,
        refresh_token_present=True,
    )

    teardown_calls: list[str] = []

    async def _fake_teardown(ctx: OwnerContext) -> None:
        teardown_calls.append(ctx.email)

    connector._teardown_account = _fake_teardown  # type: ignore[method-assign]

    # ------------------------------------------------------------------
    # Cycle 1: shared pool returns both accounts.
    # ------------------------------------------------------------------
    shared_pool = MagicMock()
    connector._shared_pool = shared_pool

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "butlers.connectors.google_health.list_health_scoped_accounts",
            AsyncMock(return_value=[acct_x, acct_y]),
        )
        await connector._resolve_owner_and_scopes(initial=True)

    assert len(connector._accounts) == 2
    assert _UUID_A in connector._accounts
    assert _UUID_B in connector._accounts
    assert set(connector._accounts_added) == {_UUID_A, _UUID_B}
    assert connector._accounts_removed == []
    assert connector._scope_missing is False
    assert connector._account_missing is False
    assert len(teardown_calls) == 0

    # ------------------------------------------------------------------
    # Cycle 2: account Y removed (scope revoked / deleted).
    # ------------------------------------------------------------------
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "butlers.connectors.google_health.list_health_scoped_accounts",
            AsyncMock(return_value=[acct_x]),
        )
        await connector._resolve_owner_and_scopes()

    assert len(connector._accounts) == 1
    assert _UUID_A in connector._accounts
    assert _UUID_B not in connector._accounts
    assert connector._accounts_added == []
    assert connector._accounts_removed == [_UUID_B]
    # Teardown must have been called once for the removed account.
    assert teardown_calls == ["y@example.com"]

    # ------------------------------------------------------------------
    # Cycle 3: only account X still present — no diff.
    # ------------------------------------------------------------------
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "butlers.connectors.google_health.list_health_scoped_accounts",
            AsyncMock(return_value=[acct_x]),
        )
        await connector._resolve_owner_and_scopes()

    assert len(connector._accounts) == 1
    assert connector._accounts_added == []
    assert connector._accounts_removed == []
    # teardown not called again.
    assert teardown_calls == ["y@example.com"]


# ---------------------------------------------------------------------------
# Per-account poll sets + token cache + heartbeat [bu-91zdb.2]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_accounts_produce_two_heartbeat_rows() -> None:
    """Two configured accounts each get a distinct ConnectorHeartbeat instance.

    After _resolve_owner_and_scopes discovers two accounts, _heartbeats should
    contain two entries keyed by distinct account UUIDs with distinct endpoint
    identities (google_health:user:<email>).

    Acceptance test [bu-91zdb.2] AC-3.
    """
    connector = _make_connector()

    acct_a = HealthScopedAccount(
        id=_UUID_A,
        email="alice@example.com",
        entity_id=_ENTITY_A,
        refresh_token_present=True,
    )
    acct_b = HealthScopedAccount(
        id=_UUID_B,
        email="bob@example.com",
        entity_id=_ENTITY_B,
        refresh_token_present=True,
    )

    # Stub _teardown_account so no MCP call is needed.
    connector._teardown_account = AsyncMock()  # type: ignore[method-assign]
    connector._shared_pool = MagicMock()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "butlers.connectors.google_health.list_health_scoped_accounts",
            AsyncMock(return_value=[acct_a, acct_b]),
        )
        await connector._resolve_owner_and_scopes(initial=True)

    # Two accounts registered.
    assert len(connector._accounts) == 2

    # Two per-(account, resource) state entries per resource bundle.
    assert len(connector._resources) == len(RESOURCE_BUNDLES) * 2
    for bundle in RESOURCE_BUNDLES:
        assert (_UUID_A, bundle.resource) in connector._resources
        assert (_UUID_B, bundle.resource) in connector._resources

    # Two heartbeat tasks, one per account.
    assert len(connector._heartbeats) == 2
    assert _UUID_A in connector._heartbeats
    assert _UUID_B in connector._heartbeats

    hb_a = connector._heartbeats[_UUID_A]
    hb_b = connector._heartbeats[_UUID_B]
    assert hb_a._config.endpoint_identity == "google_health:user:alice@example.com"
    assert hb_b._config.endpoint_identity == "google_health:user:bob@example.com"
    assert hb_a._config.endpoint_identity != hb_b._config.endpoint_identity


@pytest.mark.asyncio
async def test_per_account_token_mint_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    """A mint failure for account A leaves account B's polls and heartbeat untouched.

    When _mint_access_token raises for account A (e.g. no refresh token),
    account B can still be polled successfully using its own token.

    Acceptance test [bu-91zdb.2] AC-4.
    """
    connector, ctx_a = _make_connector_with_account(
        email="a@example.com",
        account_id=_UUID_A,
        entity_id=_ENTITY_A,
    )
    ctx_a.refresh_token_present = False  # A has no refresh token

    # Add a second account with a valid token.
    ctx_b = OwnerContext(
        account_id=_UUID_B,
        email="b@example.com",
        entity_id=_ENTITY_B,
        refresh_token_present=True,
        endpoint_identity=_endpoint_identity_for_user("b@example.com"),
    )
    connector._accounts[_UUID_B] = ctx_b
    for bundle in RESOURCE_BUNDLES:
        connector._resources[(_UUID_B, bundle.resource)] = ResourceState(bundle=bundle)

    mint_calls: list[uuid.UUID] = []

    async def _fake_mint(account_uuid: uuid.UUID) -> str:
        mint_calls.append(account_uuid)
        if account_uuid == _UUID_A:
            raise GoogleHealthCredentialError("no refresh token for A")
        # Mimic what the real _mint_access_token does: cache in OwnerContext.
        from datetime import UTC, datetime, timedelta

        token = f"token-for-{account_uuid}"
        ctx = connector._accounts[account_uuid]
        ctx.cached_access_token = token
        ctx.token_expires_at = datetime.now(UTC) + timedelta(hours=1)
        return token

    monkeypatch.setattr(connector, "_mint_access_token", _fake_mint)

    # Attempt to get a token for A → raises.
    with pytest.raises(GoogleHealthCredentialError, match="no refresh token for A"):
        await connector._get_access_token(_UUID_A)

    # Attempt to get a token for B → succeeds (A's failure has no side-effect on B).
    token_b = await connector._get_access_token(_UUID_B)
    assert token_b == f"token-for-{_UUID_B}"

    # Only B's token is cached; A has nothing.
    assert ctx_a.cached_access_token is None
    assert ctx_b.cached_access_token == f"token-for-{_UUID_B}"


def test_prometheus_labels_distinct_per_account() -> None:
    """Two accounts produce two distinct Prometheus label sets keyed by endpoint_identity.

    The google_health_polls_total counter accepts an ``endpoint_identity`` label.
    When two accounts fire polls, the resulting label combinations are disjoint.

    Acceptance test [bu-91zdb.2] AC-5.
    """
    from prometheus_client import REGISTRY

    endpoint_a = "google_health:user:alice@example.com"
    endpoint_b = "google_health:user:bob@example.com"
    resource = "sleep"

    # Increment the counter for each account.
    from butlers.connectors.google_health import google_health_polls_total

    google_health_polls_total.labels(
        endpoint_identity=endpoint_a, resource=resource, outcome="success"
    ).inc()
    google_health_polls_total.labels(
        endpoint_identity=endpoint_b, resource=resource, outcome="success"
    ).inc()

    # Collect all label sets from the counter.
    label_sets: list[dict[str, str]] = []
    for metric in REGISTRY.collect():
        if metric.name == "connector_google_health_polls":
            for sample in metric.samples:
                if sample.labels.get("resource") == resource:
                    label_sets.append(dict(sample.labels))

    endpoint_identities = {ls["endpoint_identity"] for ls in label_sets}
    assert endpoint_a in endpoint_identities
    assert endpoint_b in endpoint_identities
    # The two accounts are represented by different label sets — they are disjoint.
    assert endpoint_a != endpoint_b
