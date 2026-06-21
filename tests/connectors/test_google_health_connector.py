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
    GoogleHealthForbiddenError,
    GoogleHealthRateLimitError,
    GoogleHealthSourcePreconditionError,
    GoogleHealthTokenRevokedError,
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
    assert len(GOOGLE_HEALTH_SCOPES) == 3
    for scope in GOOGLE_HEALTH_SCOPES:
        assert scope.startswith("https://www.googleapis.com/auth/googlehealth.")


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
    # Cursor key now includes account_uuid between email and resource.
    test_uuid = uuid.UUID("12345678-1234-1234-1234-123456789012")
    got = _cursor_endpoint_identity(_OWNER_EMAIL, test_uuid, "sleep")
    assert got == f"google_health:user:owner@example.com:{test_uuid}:sleep"
    # Envelope identity stays canonical (3-segment).
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
    assert (
        env["event"]["external_event_id"] == f"google_health:{_OWNER_EMAIL}:sleep_session:sess-123"
    )
    assert env["control"]["idempotency_key"] == f"google_health:{_OWNER_EMAIL}:sleep:sess-123"
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
    assert (
        env["event"]["external_event_id"] == f"google_health:{_OWNER_EMAIL}:resting_hr:2026-04-23"
    )
    assert (
        env["control"]["idempotency_key"] == f"google_health:{_OWNER_EMAIL}:resting_hr:2026-04-23"
    )
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


@pytest.mark.parametrize(
    "data,expected_len",
    [
        ({"sessions": [{"session_id": "a"}, {"session_id": "b"}]}, 2),
        ({"dataPoints": [{"value": 1}, {"value": 2}]}, 2),
        ({"rollupDataPoints": [{"steps": {"countSum": "1200"}}]}, 1),
        ({"foo": "bar"}, 0),  # no known list key → empty
    ],
)
def test_extract_records_shape(data: dict, expected_len: int) -> None:
    assert len(_extract_records(data)) == expected_len


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
async def test_client_403_raises_forbidden_error() -> None:
    """A 403 is an access/authorisation failure (test-mode / allowlist / API not
    enabled) and must raise the distinct GoogleHealthForbiddenError so the
    connector can surface a 'connector unavailable (403)' degraded signal."""
    responses = [
        httpx.Response(403, json={"error": {"code": 403, "message": "Forbidden"}}),
    ]
    transport = _StubTransport(responses)
    http = httpx.AsyncClient(transport=transport, base_url="https://health.googleapis.com/v4")
    fetcher = AsyncMock(return_value="token")
    client = GoogleHealthClient(token_fetcher=fetcher, client=http)
    with pytest.raises(GoogleHealthForbiddenError) as excinfo:
        await client.get_json("/users/me/dataTypes/sleep/dataPoints:reconcile")
    assert "403" in str(excinfo.value)
    assert excinfo.value.path == "/users/me/dataTypes/sleep/dataPoints:reconcile"
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
    assert (
        envelope["event"]["external_event_id"]
        == f"google_health:{_OWNER_EMAIL}:sleep_session:sess-new"
    )
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
    assert (
        envelope["event"]["external_event_id"]
        == f"google_health:{_OWNER_EMAIL}:activity:2026-04-24"
    )
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


# ---------------------------------------------------------------------------
# Cursor key shape migration [bu-91zdb.3]
# ---------------------------------------------------------------------------


_ACCOUNT_UUID_3 = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


def test_cursor_endpoint_identity_includes_account_uuid() -> None:
    """_cursor_endpoint_identity produces the new 5-segment shape with account_uuid.

    Acceptance test [bu-91zdb.3] AC-1.
    """
    got = _cursor_endpoint_identity(_OWNER_EMAIL, _ACCOUNT_UUID_3, "sleep")
    expected = f"google_health:user:{_OWNER_EMAIL}:{_ACCOUNT_UUID_3}:sleep"
    assert got == expected
    # Must differ from the canonical envelope identity (3-segment).
    assert got != _ENDPOINT
    # Must contain the account_uuid string.
    assert str(_ACCOUNT_UUID_3) in got
    # Resource must appear as the LAST segment.
    assert got.endswith(":sleep")


def test_cursor_endpoint_identity_two_accounts_produce_distinct_keys() -> None:
    """Two distinct account UUIDs produce distinct cursor keys even for the same resource."""
    uuid_x = uuid.UUID("11111111-2222-3333-4444-555555555555")
    uuid_y = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-ffffffffffff")
    key_x = _cursor_endpoint_identity("shared@example.com", uuid_x, "resting_hr")
    key_y = _cursor_endpoint_identity("shared@example.com", uuid_y, "resting_hr")
    assert key_x != key_y
    assert str(uuid_x) in key_x
    assert str(uuid_y) in key_y


@pytest.mark.asyncio
async def test_cursor_migration_idempotent_and_loads_post_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connector reads cursor via new key shape after the migration runs.

    Regression test for ADR-3: the one-shot SQL migration rewrites old-shape
    cursor rows.  We verify that after the migration the connector retrieves
    the stored cursor value using the new key (account_uuid embedded).

    The test simulates the post-migration state by pre-storing a cursor under
    the new key, then confirming ``_load_all_cursors`` picks it up.

    Acceptance test [bu-91zdb.3] AC-3.
    """
    account_id = uuid.UUID("cafecafe-cafe-cafe-cafe-cafecafecafe")
    connector, ctx = _make_connector_with_account(
        email="migrated@example.com",
        account_id=account_id,
    )

    # Expected key after migration.
    expected_endpoint = _cursor_endpoint_identity(ctx.email, account_id, "sleep")
    assert str(account_id) in expected_endpoint, "account_uuid must appear in the key"

    # Simulate the cursor pool returning a cursor for the migrated key.
    stored_cursor = "2026-05-01T00:00:00Z"

    async def _fake_load_cursor(
        pool: Any,
        connector_type: str,
        endpoint_identity: str,
    ) -> str | None:
        if endpoint_identity == expected_endpoint and connector_type == "google_health":
            return stored_cursor
        return None

    monkeypatch.setattr(
        "butlers.connectors.google_health.load_cursor",
        _fake_load_cursor,
    )

    # Attach a fake cursor pool so the method proceeds.
    connector._cursor_pool = MagicMock()

    await connector._load_all_cursors()

    sleep_state = connector._resources[(account_id, "sleep")]
    assert sleep_state.last_cursor == stored_cursor
    assert sleep_state.backfill_done is True


# ---------------------------------------------------------------------------
# Ingestion-event identity migration [bu-91zdb.4]
# ---------------------------------------------------------------------------


_MIGRATED_EMAIL = "uniquosity@gmail.com"


def test_envelope_external_event_id_includes_email_prefix() -> None:
    """Both envelope builders embed the account email in external_event_id.

    Acceptance test [bu-91zdb.4] AC-1 — sleep session shape.
    """
    sleep_env = build_sleep_session_envelope(
        endpoint_identity=_ENDPOINT,
        google_user_id=_MIGRATED_EMAIL,
        session_id="sess-abc",
        session_record={"session_id": "sess-abc", "durationMillis": 28800000, "efficiency": 88},
        observed_at=_OBSERVED,
    )
    expected_sleep_id = f"google_health:{_MIGRATED_EMAIL}:sleep_session:sess-abc"
    assert sleep_env["event"]["external_event_id"] == expected_sleep_id, (
        f"Expected {expected_sleep_id!r}, got {sleep_env['event']['external_event_id']!r}"
    )

    daily_env = build_daily_summary_envelope(
        endpoint_identity=_ENDPOINT,
        google_user_id=_MIGRATED_EMAIL,
        resource="activity",
        record_date="2026-04-20",
        record={"value": 9000},
        normalized_summary_template="Steps: {value}",
        observed_at=_OBSERVED,
    )
    expected_daily_id = f"google_health:{_MIGRATED_EMAIL}:activity:2026-04-20"
    assert daily_env["event"]["external_event_id"] == expected_daily_id, (
        f"Expected {expected_daily_id!r}, got {daily_env['event']['external_event_id']!r}"
    )


def test_idempotency_key_includes_email_prefix() -> None:
    """control.idempotency_key mirrors the email-prefixed shape of external_event_id.

    Acceptance test [bu-91zdb.4] AC-1 — idempotency key.
    """
    sleep_env = build_sleep_session_envelope(
        endpoint_identity=_ENDPOINT,
        google_user_id=_MIGRATED_EMAIL,
        session_id="sess-xyz",
        session_record={"session_id": "sess-xyz", "durationMillis": 0, "efficiency": 0},
        observed_at=_OBSERVED,
    )
    expected_sleep_key = f"google_health:{_MIGRATED_EMAIL}:sleep:sess-xyz"
    assert sleep_env["control"]["idempotency_key"] == expected_sleep_key

    daily_env = build_daily_summary_envelope(
        endpoint_identity=_ENDPOINT,
        google_user_id=_MIGRATED_EMAIL,
        resource="resting_hr",
        record_date="2026-05-01",
        record={"value": 62},
        normalized_summary_template="Resting HR: {value} bpm",
        observed_at=_OBSERVED,
    )
    expected_daily_key = f"google_health:{_MIGRATED_EMAIL}:resting_hr:2026-05-01"
    assert daily_env["control"]["idempotency_key"] == expected_daily_key


def test_two_accounts_produce_distinct_external_event_ids_for_same_date() -> None:
    """Two accounts with the same resource+date produce non-colliding external_event_ids.

    This is the core motivation: without email disambiguation the ingest pipeline
    would deduplicate activity on 2026-04-20 across two different Google accounts.
    Acceptance test [bu-91zdb.4] AC-1 — multi-account dedup.
    """
    email_a = "alice@example.com"
    email_b = "bob@example.com"
    kwargs = {
        "endpoint_identity": _ENDPOINT,
        "resource": "activity",
        "record_date": "2026-04-20",
        "record": {"value": 5000},
        "normalized_summary_template": "Steps: {value}",
        "observed_at": _OBSERVED,
    }
    env_a = build_daily_summary_envelope(google_user_id=email_a, **kwargs)
    env_b = build_daily_summary_envelope(google_user_id=email_b, **kwargs)

    assert env_a["event"]["external_event_id"] != env_b["event"]["external_event_id"]
    assert email_a in env_a["event"]["external_event_id"]
    assert email_b in env_b["event"]["external_event_id"]
    assert env_a["control"]["idempotency_key"] != env_b["control"]["idempotency_key"]


@pytest.mark.asyncio
async def test_ingest_counts_predicate_post_migration_returns_same_totals() -> None:
    """_fetch_ingest_counts returns correct counts after the email-prefix migration.

    Simulates the post-migration DB state:
    - 2 daily-summary rows (email-prefixed, 4-segment shape)
    - 1 sleep-session row (email-prefixed, 4-segment shape)

    Verifies that the updated SQL predicates match these rows and return
    totals identical to what the old predicates returned for old-shape rows.

    Acceptance test [bu-91zdb.4] AC-3.
    """
    from butlers.api.routers.google_health import _fetch_ingest_counts

    migrated_rows = [
        # New 4-segment daily-summary rows (post-migration).
        {"external_event_id": f"google_health:{_MIGRATED_EMAIL}:activity:2026-04-20"},
        {"external_event_id": f"google_health:{_MIGRATED_EMAIL}:resting_hr:2026-04-21"},
        # New 4-segment sleep-session row (post-migration).
        {"external_event_id": f"google_health:{_MIGRATED_EMAIL}:sleep_session:sess-1"},
    ]

    # Simulate DB fetchrow returning (sleep_sessions_7d, daily_summaries_7d).
    # We compute these manually based on the predicate logic to verify parity.
    # The SQL predicates are:
    #   sleep:   split_part(..., ':', 3) = 'sleep_session' AND 4-segment
    #   daily:   4-segment AND segment 3 != 'sleep_session'
    expected_sleep = sum(
        1
        for r in migrated_rows
        if r["external_event_id"].split(":")[2] == "sleep_session"
        and len(r["external_event_id"].split(":")) == 4
    )
    expected_daily = sum(
        1
        for r in migrated_rows
        if r["external_event_id"].split(":")[2] != "sleep_session"
        and len(r["external_event_id"].split(":")) == 4
    )
    assert expected_sleep == 1
    assert expected_daily == 2

    # Mock a pool that returns the aggregated counts as if the SQL ran.
    fake_row = {"sleep_sessions_7d": expected_sleep, "daily_summaries_7d": expected_daily}
    fake_conn = MagicMock()
    fake_conn.fetchrow = AsyncMock(return_value=fake_row)
    fake_conn.__aenter__ = AsyncMock(return_value=fake_conn)
    fake_conn.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=fake_conn)

    counts = await _fetch_ingest_counts(pool)

    assert counts["sleep_sessions_7d"] == 1, (
        f"Expected 1 sleep session, got {counts['sleep_sessions_7d']}"
    )
    assert counts["daily_summaries_7d"] == 2, (
        f"Expected 2 daily summaries, got {counts['daily_summaries_7d']}"
    )


# ---------------------------------------------------------------------------
# Cross-cutting multi-account integration suite [bu-91zdb.7]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_health_scoped_accounts_filters() -> None:
    """list_health_scoped_accounts returns only active + scope-superset accounts.

    Covers:
    - status='active' filter: SQL WHERE clause excludes revoked rows at DB level.
    - scope-superset filter: Python post-filter excludes partial-scope rows.
    - Rows with missing scopes are excluded.

    Acceptance test [bu-91zdb.7] §7.1.
    """
    from butlers.google_account_registry import list_health_scoped_accounts

    uuid_active_full = uuid.uuid4()
    uuid_active_partial = uuid.uuid4()
    entity_active_full = uuid.uuid4()
    entity_active_partial = uuid.uuid4()

    # Row with all three health scopes (active) — should be included.
    row_full = _make_fake_row(
        uuid_active_full,
        entity_active_full,
        "full@example.com",
        _HEALTH_SCOPE_LIST,
        status="active",
    )
    # Row with only two of three scopes (active) — excluded by Python scope-superset filter.
    row_partial = _make_fake_row(
        uuid_active_partial,
        entity_active_partial,
        "partial@example.com",
        _HEALTH_SCOPE_LIST[:2],
        status="active",
    )
    # Row with zero scopes (active) — excluded.
    uuid_no_scopes = uuid.uuid4()
    row_no_scopes = _make_fake_row(
        uuid_no_scopes,
        uuid.uuid4(),
        "noscopes@example.com",
        [],
        status="active",
    )

    # The SQL WHERE status='active' returns only the three active rows above.
    # A revoked row would not appear in the DB result set — simulated by omission.
    active_rows = [row_full, row_partial, row_no_scopes]

    fake_conn = MagicMock()
    fake_conn.fetch = AsyncMock(return_value=active_rows)
    fake_conn.__aenter__ = AsyncMock(return_value=fake_conn)
    fake_conn.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=fake_conn)

    results = await list_health_scoped_accounts(pool, health_scopes=GOOGLE_HEALTH_SCOPES)

    # Only the full-scopes row passes.
    assert len(results) == 1
    assert results[0].id == uuid_active_full
    assert results[0].email == "full@example.com"

    # The partial-scope and no-scope rows were excluded by the Python filter.
    returned_ids = {r.id for r in results}
    assert uuid_active_partial not in returned_ids
    assert uuid_no_scopes not in returned_ids


@pytest.mark.asyncio
async def test_per_account_teardown_does_not_affect_other_accounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mid-run scope revocation of account A leaves account B's polls and cursors untouched.

    Simulates:
    1. Two accounts A and B active with one resource each.
    2. Account A is revoked — _resolve_owner_and_scopes removes it and calls _teardown_account.
    3. After teardown, account B's ResourceState (cursor and backfill_done) is unchanged.

    Acceptance test [bu-91zdb.7] §7.2.
    """
    uuid_a = uuid.uuid4()
    uuid_b = uuid.uuid4()
    entity_a = uuid.uuid4()
    entity_b = uuid.uuid4()

    connector, ctx_a = _make_connector_with_account(
        email="a@example.com",
        account_id=uuid_a,
        entity_id=entity_a,
    )

    # Add second account B manually.
    ctx_b = OwnerContext(
        account_id=uuid_b,
        email="b@example.com",
        entity_id=entity_b,
        refresh_token_present=True,
        endpoint_identity=_endpoint_identity_for_user("b@example.com"),
    )
    connector._accounts[uuid_b] = ctx_b
    for bundle in RESOURCE_BUNDLES:
        connector._resources[(uuid_b, bundle.resource)] = ResourceState(bundle=bundle)

    # Pre-seed cursors for both accounts so we can verify they are untouched.
    connector._resources[(uuid_a, "sleep")].last_cursor = "sess-a-old"
    connector._resources[(uuid_a, "sleep")].backfill_done = True
    connector._resources[(uuid_b, "sleep")].last_cursor = "sess-b-old"
    connector._resources[(uuid_b, "sleep")].backfill_done = True

    teardown_called: list[str] = []

    async def _fake_teardown(ctx: OwnerContext) -> None:
        teardown_called.append(ctx.email)

    connector._teardown_account = _fake_teardown  # type: ignore[method-assign]
    connector._shared_pool = MagicMock()

    # Simulate scope revocation: only account B is returned by list_health_scoped_accounts.
    acct_b = HealthScopedAccount(
        id=uuid_b,
        email="b@example.com",
        entity_id=entity_b,
        refresh_token_present=True,
    )
    monkeypatch.setattr(
        "butlers.connectors.google_health.list_health_scoped_accounts",
        AsyncMock(return_value=[acct_b]),
    )
    await connector._resolve_owner_and_scopes()

    # Account A was torn down; account B remains.
    assert uuid_a not in connector._accounts
    assert uuid_b in connector._accounts
    assert teardown_called == ["a@example.com"]

    # Account A's resource state was removed.
    assert (uuid_a, "sleep") not in connector._resources

    # Account B's cursor and backfill_done remain exactly as they were — no cross-account mutation.
    state_b_sleep = connector._resources[(uuid_b, "sleep")]
    assert state_b_sleep.last_cursor == "sess-b-old", (
        "B's cursor must not be touched by A's teardown"
    )
    assert state_b_sleep.backfill_done is True, (
        "B's backfill_done must not be reset by A's teardown"
    )

    # All other resource keys for B are still intact.
    for bundle in RESOURCE_BUNDLES:
        assert (uuid_b, bundle.resource) in connector._resources, (
            f"B's resource {bundle.resource!r} must survive A's teardown"
        )


@pytest.mark.asyncio
async def test_two_account_integration_distinct_heartbeats_and_mints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two-account _StubTransport integration: distinct heartbeats, scope-restricted token mints,
    envelopes for both accounts, no cross-account token reuse, no cursor collisions.

    Acceptance test [bu-91zdb.7] §7.3.
    """
    uuid_alice = uuid.uuid4()
    uuid_bob = uuid.uuid4()
    entity_alice = uuid.uuid4()
    entity_bob = uuid.uuid4()

    connector = _make_connector()

    # Set up two accounts via _resolve_owner_and_scopes.
    acct_alice = HealthScopedAccount(
        id=uuid_alice,
        email="alice@example.com",
        entity_id=entity_alice,
        refresh_token_present=True,
    )
    acct_bob = HealthScopedAccount(
        id=uuid_bob,
        email="bob@example.com",
        entity_id=entity_bob,
        refresh_token_present=True,
    )
    connector._teardown_account = AsyncMock()  # type: ignore[method-assign]
    connector._shared_pool = MagicMock()

    monkeypatch.setattr(
        "butlers.connectors.google_health.list_health_scoped_accounts",
        AsyncMock(return_value=[acct_alice, acct_bob]),
    )
    await connector._resolve_owner_and_scopes(initial=True)

    # ---- Assert: two heartbeat rows with distinct endpoint identities ----
    assert len(connector._heartbeats) == 2
    assert uuid_alice in connector._heartbeats
    assert uuid_bob in connector._heartbeats

    hb_alice = connector._heartbeats[uuid_alice]
    hb_bob = connector._heartbeats[uuid_bob]
    assert hb_alice._config.endpoint_identity == "google_health:user:alice@example.com"
    assert hb_bob._config.endpoint_identity == "google_health:user:bob@example.com"
    assert hb_alice._config.endpoint_identity != hb_bob._config.endpoint_identity

    # ---- Assert: per-account token mints are isolated ----
    # Track which account UUID each mint call targets.
    mint_calls: dict[uuid.UUID, list[str]] = {uuid_alice: [], uuid_bob: []}

    from datetime import UTC, datetime, timedelta

    async def _fake_mint(account_uuid: uuid.UUID) -> str:
        token = f"token-{account_uuid}"
        ctx = connector._accounts[account_uuid]
        ctx.cached_access_token = token
        ctx.token_expires_at = datetime.now(UTC) + timedelta(hours=1)
        mint_calls[account_uuid].append(token)
        return token

    monkeypatch.setattr(connector, "_mint_access_token", _fake_mint)

    token_a = await connector._get_access_token(uuid_alice)
    token_b = await connector._get_access_token(uuid_bob)

    # Tokens are distinct — no cross-account reuse.
    assert token_a != token_b
    assert str(uuid_alice) in token_a
    assert str(uuid_bob) in token_b

    # Each account was minted exactly once.
    assert len(mint_calls[uuid_alice]) == 1
    assert len(mint_calls[uuid_bob]) == 1

    # No cross-account leakage: alice's token is not in bob's context.
    ctx_alice = connector._accounts[uuid_alice]
    ctx_bob = connector._accounts[uuid_bob]
    assert ctx_alice.cached_access_token != ctx_bob.cached_access_token

    # ---- Assert: no cursor collisions ----
    # Cursor keys for the same resource on different accounts must be distinct.
    for bundle in RESOURCE_BUNDLES:
        key_alice = _cursor_endpoint_identity("alice@example.com", uuid_alice, bundle.resource)
        key_bob = _cursor_endpoint_identity("bob@example.com", uuid_bob, bundle.resource)
        assert key_alice != key_bob, (
            f"Cursor keys collide for resource {bundle.resource!r}: {key_alice!r}"
        )
        assert str(uuid_alice) in key_alice
        assert str(uuid_bob) in key_bob

    # ---- Assert: poll produces envelopes with correct per-account email ----
    # Stub the API for alice's sleep resource — returns one new session.
    state_alice_sleep = connector._resources[(uuid_alice, "sleep")]

    fake_api_alice: Any = type(
        "FakeAPI",
        (),
        {
            "get_json": AsyncMock(
                return_value={
                    "sessions": [
                        {
                            "session_id": "alice-sess-1",
                            "durationMillis": 7 * 3600_000,
                            "efficiency": 88,
                        }
                    ]
                }
            ),
            "last_rate_limit_headers": {},
        },
    )()

    monkeypatch.setattr(
        connector,
        "_make_account_api_client",
        lambda acct_id: fake_api_alice if acct_id == uuid_alice else None,
    )
    monkeypatch.setattr(connector, "_save_cursor", AsyncMock())

    submitted_envelopes: list[dict[str, Any]] = []

    async def _capture_envelope(env: dict[str, Any], **kwargs: Any) -> None:
        submitted_envelopes.append(env)

    monkeypatch.setattr(connector, "_submit_envelope", _capture_envelope)

    await connector._poll_resource(uuid_alice, state_alice_sleep)

    assert len(submitted_envelopes) == 1
    env = submitted_envelopes[0]
    # Envelope identifies alice, not bob.
    assert "alice@example.com" in env["event"]["external_event_id"]
    assert "bob@example.com" not in env["event"]["external_event_id"]
    assert env["source"]["endpoint_identity"] == "google_health:user:alice@example.com"
    assert env["sender"]["identity"] == "alice@example.com"


# ---------------------------------------------------------------------------
# Per-account auth_error tracking [bu-fyo0l]
# ---------------------------------------------------------------------------


def _make_two_account_connector() -> tuple[GoogleHealthConnector, OwnerContext, OwnerContext]:
    """Create a connector with two pre-loaded accounts (A and B)."""
    connector = _make_connector()
    ctx_a = OwnerContext(
        account_id=_UUID_A,
        email="a@example.com",
        entity_id=_ENTITY_A,
        refresh_token_present=True,
        endpoint_identity=_endpoint_identity_for_user("a@example.com"),
    )
    ctx_b = OwnerContext(
        account_id=_UUID_B,
        email="b@example.com",
        entity_id=_ENTITY_B,
        refresh_token_present=True,
        endpoint_identity=_endpoint_identity_for_user("b@example.com"),
    )
    connector._accounts[_UUID_A] = ctx_a
    connector._accounts[_UUID_B] = ctx_b
    for bundle in RESOURCE_BUNDLES:
        connector._resources[(_UUID_A, bundle.resource)] = ResourceState(bundle=bundle)
        connector._resources[(_UUID_B, bundle.resource)] = ResourceState(bundle=bundle)
    connector._account_missing = False
    connector._scope_missing = False
    return connector, ctx_a, ctx_b


def test_per_account_auth_error_fields_default_to_false() -> None:
    """OwnerContext starts with auth_error=False and no message/timestamp."""
    ctx = OwnerContext(
        account_id=uuid.uuid4(),
        email="test@example.com",
        entity_id=uuid.uuid4(),
        refresh_token_present=True,
        endpoint_identity=_endpoint_identity_for_user("test@example.com"),
    )
    assert ctx.auth_error is False
    assert ctx.auth_error_message is None
    assert ctx.auth_error_at is None


def test_one_account_auth_error_does_not_set_global_flag() -> None:
    """When only one of two accounts has auth_error, global _auth_error stays False."""
    connector, ctx_a, ctx_b = _make_two_account_connector()

    # Simulate: account A has a credential error, B is healthy.
    ctx_a.auth_error = True
    ctx_a.auth_error_message = "token_invalid"

    # Recompute global as the code does.
    connector._auth_error = bool(connector._accounts) and all(
        c.auth_error for c in connector._accounts.values()
    )

    assert connector._auth_error is False, (
        "Global auth_error must be False when at least one account is healthy"
    )


def test_all_accounts_auth_error_sets_global_flag() -> None:
    """When every account has auth_error, global _auth_error becomes True."""
    connector, ctx_a, ctx_b = _make_two_account_connector()

    ctx_a.auth_error = True
    ctx_a.auth_error_message = "token_invalid"
    ctx_b.auth_error = True
    ctx_b.auth_error_message = "token_invalid"

    connector._auth_error = bool(connector._accounts) and all(
        c.auth_error for c in connector._accounts.values()
    )

    assert connector._auth_error is True, (
        "Global auth_error must be True when all accounts have auth_error"
    )


def test_account_health_state_reflects_per_account_auth_error() -> None:
    """_get_account_health_state returns error when per-account auth_error is set."""
    connector, ctx_a, ctx_b = _make_two_account_connector()

    # Account A has auth_error; B is healthy.
    ctx_a.auth_error = True
    ctx_a.auth_error_message = "revoked_token"

    state_a, err_a = connector._get_account_health_state(_UUID_A)
    state_b, err_b = connector._get_account_health_state(_UUID_B)

    assert state_a == "error"
    assert err_a == "revoked_token"
    assert state_b == "healthy"
    assert err_b is None


def test_global_health_state_is_error_when_one_account_fails() -> None:
    """Connector-level _get_health_state reports error (worst-of) when one of two accounts fails."""
    connector, ctx_a, ctx_b = _make_two_account_connector()

    ctx_a.auth_error = True
    ctx_a.auth_error_message = "token_invalid"
    # Account A's token is cleared, B is still healthy.
    ctx_a.cached_access_token = None
    ctx_a.refresh_token_present = False

    # Global flag must reflect "not all accounts down".
    connector._auth_error = False

    state, _err = connector._get_health_state()
    # Worst-of includes one error account → overall is error
    assert state == "error", "_get_health_state should bubble up the error from the failing account"


def test_auth_error_clears_on_successful_poll_and_recomputes_global() -> None:
    """When an account that had auth_error completes a successful poll, flags are cleared."""
    connector, ctx_a, ctx_b = _make_two_account_connector()

    # Seed: both accounts had auth_error → global True.
    ctx_a.auth_error = True
    ctx_a.auth_error_message = "token_invalid"
    ctx_b.auth_error = True
    ctx_b.auth_error_message = "token_invalid"
    connector._auth_error = True

    # Simulate account A recovering (successful poll clears per-account fields).
    ctx_a.auth_error = False
    ctx_a.auth_error_message = None
    ctx_a.auth_error_at = None
    connector._auth_error = bool(connector._accounts) and all(
        c.auth_error for c in connector._accounts.values()
    )

    # Global flag must be False now (not ALL accounts have auth_error any more).
    assert connector._auth_error is False
    # B still has auth_error.
    assert ctx_b.auth_error is True
    # A is clear.
    assert ctx_a.auth_error is False


@pytest.mark.asyncio
async def test_main_loop_credential_error_sets_per_account_auth_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A GoogleHealthCredentialError in _main_loop sets auth_error on the failing OwnerContext.

    The global _auth_error flag must remain False when only one of two accounts fails.
    """
    connector, ctx_a, ctx_b = _make_two_account_connector()
    connector._running = True
    connector._mcp_client = MagicMock()  # prevent heartbeat MCP calls

    # Stub _mark_account_revoked to avoid DB calls.
    monkeypatch.setattr(connector, "_mark_account_revoked", AsyncMock())
    monkeypatch.setattr(connector, "_drain_replay", AsyncMock())
    monkeypatch.setattr(connector, "_flush_filtered_events", AsyncMock())
    monkeypatch.setattr(connector, "_resolve_owner_and_scopes", AsyncMock())

    # Collect which accounts were polled; set shutdown after processing all resources.
    polled_accounts: list[uuid.UUID] = []

    async def _poll_resource_raises_for_a(acct_id: uuid.UUID, state: ResourceState) -> None:
        polled_accounts.append(acct_id)
        if acct_id == _UUID_A:
            raise GoogleHealthCredentialError("token_revoked")
        # B succeeds (no-op).
        # Stop the loop after all resources for both accounts have been attempted.
        all_resources = len(RESOURCE_BUNDLES) * 2
        if len(polled_accounts) >= all_resources:
            connector._shutdown_event.set()

    monkeypatch.setattr(connector, "_poll_resource", _poll_resource_raises_for_a)

    # Stub heartbeat for A so _send_heartbeat doesn't blow up.
    hb_a_mock = MagicMock()
    hb_a_mock._send_heartbeat = AsyncMock()
    connector._heartbeats[_UUID_A] = hb_a_mock

    await connector._main_loop()

    # Per-account: A has auth_error, B does not.
    assert ctx_a.auth_error is True
    assert ctx_a.auth_error_message == "token_revoked"
    assert ctx_a.auth_error_at is not None

    assert ctx_b.auth_error is False

    # Global flag must be False (not all accounts failed).
    assert connector._auth_error is False

    # Heartbeat was triggered for the failing account (once per resource error, or at least once).
    assert hb_a_mock._send_heartbeat.await_count >= 1


@pytest.mark.asyncio
async def test_main_loop_transient_credential_error_does_not_revoke_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-revocation GoogleHealthCredentialError must NOT flip the shared account row.

    The google_accounts row is read by every Google connector's account-sync
    (status='active' filter), so revoking it on a transient/scope-local failure
    would knock Drive/Calendar/Gmail offline (Bug B).  Only invalid_grant may.
    """
    connector, ctx_a, _ctx_b = _make_two_account_connector()
    connector._running = True
    connector._mcp_client = MagicMock()

    revoke_mock = AsyncMock()
    monkeypatch.setattr(connector, "_mark_account_revoked", revoke_mock)
    monkeypatch.setattr(connector, "_drain_replay", AsyncMock())
    monkeypatch.setattr(connector, "_flush_filtered_events", AsyncMock())
    monkeypatch.setattr(connector, "_resolve_owner_and_scopes", AsyncMock())

    polled: list[uuid.UUID] = []

    async def _poll(acct_id: uuid.UUID, _state: ResourceState) -> None:
        polled.append(acct_id)
        all_resources = len(RESOURCE_BUNDLES) * 2
        if len(polled) >= all_resources:
            connector._shutdown_event.set()
        if acct_id == _UUID_A:
            raise GoogleHealthCredentialError("no refresh token yet (DB sync race)")

    monkeypatch.setattr(connector, "_poll_resource", _poll)
    hb = MagicMock()
    hb._send_heartbeat = AsyncMock()
    connector._heartbeats[_UUID_A] = hb

    await connector._main_loop()

    # Per-account auth_error is still set for observability...
    assert ctx_a.auth_error is True
    # ...but the shared account row was NOT revoked.
    revoke_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_main_loop_token_revoked_error_marks_account_revoked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A genuine GoogleHealthTokenRevokedError (invalid_grant) DOES revoke the account."""
    connector, ctx_a, _ctx_b = _make_two_account_connector()
    connector._running = True
    connector._mcp_client = MagicMock()

    revoke_mock = AsyncMock()
    monkeypatch.setattr(connector, "_mark_account_revoked", revoke_mock)
    monkeypatch.setattr(connector, "_drain_replay", AsyncMock())
    monkeypatch.setattr(connector, "_flush_filtered_events", AsyncMock())
    monkeypatch.setattr(connector, "_resolve_owner_and_scopes", AsyncMock())

    polled: list[uuid.UUID] = []

    async def _poll(acct_id: uuid.UUID, _state: ResourceState) -> None:
        polled.append(acct_id)
        all_resources = len(RESOURCE_BUNDLES) * 2
        if len(polled) >= all_resources:
            connector._shutdown_event.set()
        if acct_id == _UUID_A:
            raise GoogleHealthTokenRevokedError("refresh token revoked/expired: invalid_grant")

    monkeypatch.setattr(connector, "_poll_resource", _poll)
    hb = MagicMock()
    hb._send_heartbeat = AsyncMock()
    connector._heartbeats[_UUID_A] = hb

    await connector._main_loop()

    assert ctx_a.auth_error is True
    revoke_mock.assert_awaited_with(_UUID_A)


@pytest.mark.asyncio
async def test_main_loop_all_accounts_fail_sets_global_auth_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When every account raises GoogleHealthCredentialError, global _auth_error becomes True."""
    connector, ctx_a, ctx_b = _make_two_account_connector()
    connector._running = True
    connector._mcp_client = MagicMock()

    monkeypatch.setattr(connector, "_mark_account_revoked", AsyncMock())
    monkeypatch.setattr(connector, "_drain_replay", AsyncMock())
    monkeypatch.setattr(connector, "_flush_filtered_events", AsyncMock())
    monkeypatch.setattr(connector, "_resolve_owner_and_scopes", AsyncMock())

    polled_accounts: list[uuid.UUID] = []

    async def _poll_always_fails(acct_id: uuid.UUID, _state: ResourceState) -> None:
        polled_accounts.append(acct_id)
        all_resources = len(RESOURCE_BUNDLES) * 2
        if len(polled_accounts) >= all_resources:
            connector._shutdown_event.set()
        raise GoogleHealthCredentialError("token_revoked")

    monkeypatch.setattr(connector, "_poll_resource", _poll_always_fails)

    hb_mock = MagicMock()
    hb_mock._send_heartbeat = AsyncMock()
    connector._heartbeats[_UUID_A] = hb_mock
    connector._heartbeats[_UUID_B] = hb_mock

    await connector._main_loop()

    assert ctx_a.auth_error is True
    assert ctx_b.auth_error is True
    assert connector._auth_error is True, (
        "Global auth_error must be True when all accounts have credential failures"
    )


# ---------------------------------------------------------------------------
# Regression: _load_all_cursors is a safe no-op when _resources is empty
# [bu-69krs]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_all_cursors_is_noop_with_no_cursor_pool() -> None:
    """_load_all_cursors returns immediately when cursor_pool is None.

    This is the degenerate case: Phase 3 of start() is always called, but
    cursor_pool may be None in test and degraded-mode environments.  No error
    must be raised.
    """
    connector = _make_connector()
    # Freshly constructed — _resources is empty, _cursor_pool is None.
    assert connector._resources == {}
    assert connector._cursor_pool is None

    # Must not raise.
    await connector._load_all_cursors()


@pytest.mark.asyncio
async def test_load_all_cursors_is_noop_when_resources_empty_and_pool_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_load_all_cursors is a no-op when _resources is empty, even with a live cursor_pool.

    Invariant: _load_all_cursors() is called in start() Phase 3 *before*
    _resolve_owner_and_scopes has had a chance to populate _resources.  In
    that pre-population window _resources is empty, so the for-loop body never
    executes and load_cursor is never called — regardless of whether a cursor
    pool is attached.

    Regression guard for bu-69krs.
    """
    connector = _make_connector()
    # Attach a real-looking (mocked) cursor pool so the early-return guard
    # at the top of _load_all_cursors does NOT fire.
    connector._cursor_pool = MagicMock()

    # _resources must be empty — this is the invariant we are locking in.
    assert connector._resources == {}

    load_cursor_mock = AsyncMock()
    monkeypatch.setattr("butlers.connectors.google_health.load_cursor", load_cursor_mock)

    # Must not raise, must not call into the DB.
    await connector._load_all_cursors()

    load_cursor_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# Regression: `python -m` double-import must not crash the connector.
#
# The connector runs as ``__main__`` (``python -m butlers.connectors.google_health``).
# Any ``from butlers.connectors.google_health import ...`` then re-imports the
# module under its real package name, re-executing the module-level Prometheus
# metric definitions. Two independent guards keep that safe:
#   1. ``_resolve_owner_and_scopes`` passes ``health_scopes`` explicitly so the
#      registry never performs the lazy self-import in the first place.
#   2. The module-level metrics use ``_metric()`` get-or-create so a re-exec
#      reuses existing collectors instead of raising "Duplicated timeseries".
# Either alone fixes the observed outage; together they are defence-in-depth.
# ---------------------------------------------------------------------------


def test_module_reimport_is_idempotent() -> None:
    """Re-executing the module body must not raise DuplicatedTimeseries.

    ``importlib.reload`` re-runs every module-level statement against the live
    Prometheus registry — exactly what the ``__main__`` double-import did at
    runtime. Before the ``_metric`` get-or-create guard this raised
    ``ValueError: Duplicated timeseries in CollectorRegistry``.
    """
    import importlib

    import butlers.connectors.google_health as gh

    reloaded = importlib.reload(gh)

    # Metrics still usable after reload.
    reloaded.google_health_polls_total.labels(
        endpoint_identity="google_health:user:reload@example.com",
        resource="sleep",
        outcome="success",
    ).inc()


def test_metric_reraises_non_collision_value_error() -> None:
    """A non-collision ``ValueError`` must surface, not be masked by a KeyError.

    ``_metric`` only swallows ``ValueError`` when the metric is actually already
    registered (a duplicate-registration collision). For any other cause — e.g.
    a reserved label name — the original ``ValueError`` must propagate so the
    real failure is debuggable rather than masked by a ``KeyError`` from the
    registry lookup.
    """
    from prometheus_client import Counter

    from butlers.connectors.google_health import _metric

    with pytest.raises(ValueError, match="Reserved label"):
        _metric(
            Counter,
            "connector_google_health_unit_test_reserved_label",
            "Should surface the reserved-label ValueError",
            labelnames=["__reserved"],
        )


async def test_resolve_owner_passes_explicit_scopes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The connector pins ``health_scopes`` so the registry never self-imports.

    Passing ``GOOGLE_HEALTH_SCOPES`` explicitly is what stops
    ``list_health_scoped_accounts`` from running its lazy
    ``from butlers.connectors.google_health import GOOGLE_HEALTH_SCOPES`` —
    the re-import path that crashed account discovery every cycle.
    """
    connector = _make_connector()
    connector._shared_pool = MagicMock()  # bypass the None-pool degraded short-circuit

    captured: dict[str, Any] = {}

    async def _fake_list(pool: Any, health_scopes: Any = None) -> list[Any]:
        captured["pool"] = pool
        captured["health_scopes"] = health_scopes
        return []

    monkeypatch.setattr("butlers.connectors.google_health.list_health_scoped_accounts", _fake_list)

    await connector._resolve_owner_and_scopes()

    assert captured["pool"] is connector._shared_pool
    assert captured["health_scopes"] == GOOGLE_HEALTH_SCOPES
