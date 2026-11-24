"""Google Health weight and body-fat bundles (bu-q7vx1q.35, bu-u5euwb.10).

One envelope per sample, keyed on the sample's physical time; weight is converted from grams to
kilograms at the connector. Polls use the ``:reconcile`` operation, consume every page, and only
advance the checkpoint after every required submission succeeds. The sleep and workout session
bundles share that paging contract (bu-u5euwb.29).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.connectors import google_health as gh
from tests.connectors.filtered_event_fakes import FilteredEventsPool

pytestmark = pytest.mark.unit

_SAMPLE = "2026-10-18T01:30:00Z"


def _bundle(resource: str) -> gh.ResourceBundle:
    return next(b for b in gh.RESOURCE_BUNDLES if b.resource == resource)


def _weight_point(grams=70500, sample=_SAMPLE) -> dict:
    return {
        "name": "users/me/dataTypes/weight/dataPoints/abc123",
        "weight": {"sampleTime": {"physicalTime": sample}, "weightGrams": grams},
    }


def _fat_point(pct=18.2, sample=_SAMPLE) -> dict:
    return {
        "name": "users/me/dataTypes/body-fat/dataPoints/def456",
        "bodyFat": {"sampleTime": {"physicalTime": sample}, "percentage": pct},
    }


def test_bundles_are_measurement_category_with_hourly_default() -> None:
    for resource in ("weight", "body_fat"):
        bundle = _bundle(resource)
        assert bundle.category == "measurement"
        assert bundle.default_interval_s == 3600
        assert gh._DEFAULT_POLL_INTERVALS[resource] == 3600


def test_weight_grams_become_kilograms() -> None:
    record = gh._normalize_google_health_record(_bundle("weight"), _weight_point(70500))
    assert record["value"] == 70.5 and record["unit"] == "kg"
    assert record["sampleTime"] == _SAMPLE
    assert gh._normalize_google_health_record(_bundle("weight"), _weight_point(81234))[
        "value"
    ] == pytest.approx(81.234)


def test_body_fat_is_a_percentage() -> None:
    record = gh._normalize_google_health_record(_bundle("body_fat"), _fat_point(18.2))
    assert record["value"] == 18.2 and record["unit"] == "%"


@pytest.mark.parametrize(
    "point",
    [
        {"weight": {"weightGrams": 70500}},
        {"weight": {"sampleTime": {"physicalTime": _SAMPLE}}},
        {"weight": {"sampleTime": {"physicalTime": _SAMPLE}, "weightGrams": "heavy"}},
        {"weight": {"sampleTime": {"physicalTime": _SAMPLE}, "weightGrams": True}},
        {"other": {}},
    ],
)
def test_unusable_points_have_no_identity_and_are_skipped(point: dict) -> None:
    record = gh._normalize_google_health_record(_bundle("weight"), point)
    assert gh._record_identity(_bundle("weight"), record) is None


def test_identity_is_the_sample_time_not_the_date() -> None:
    record = gh._normalize_google_health_record(_bundle("weight"), _weight_point())
    assert gh._record_identity(_bundle("weight"), record) == _SAMPLE


def test_envelope_keys_are_per_sample_and_idempotent() -> None:
    bundle = _bundle("weight")
    record = gh._normalize_google_health_record(bundle, _weight_point())

    def build():
        return gh.build_daily_summary_envelope(
            endpoint_identity="google_health:user:me@example.com",
            google_user_id="me@example.com",
            resource=bundle.resource,
            record_date=gh._record_identity(bundle, record),
            record=record,
            normalized_summary_template=bundle.normalized_summary,
            observed_at=datetime(2026, 10, 18, tzinfo=UTC).isoformat(),
        )

    envelope = build()
    expected = f"google_health:me@example.com:weight:{_SAMPLE}"
    assert envelope["event"]["external_event_id"] == expected
    assert envelope["control"]["idempotency_key"] == expected
    assert envelope["payload"]["normalized_text"] == "Weight: 70.5 kg"
    assert build()["control"]["idempotency_key"] == expected


def test_two_samples_on_one_day_have_distinct_keys() -> None:
    bundle = _bundle("weight")
    ids = {
        gh._record_identity(
            bundle, gh._normalize_google_health_record(bundle, _weight_point(sample=s))
        )
        for s in ("2026-10-18T01:30:00Z", "2026-10-18T13:30:00Z")
    }
    assert len(ids) == 2


def test_query_window_uses_timestamps_not_dates() -> None:
    connector = gh.GoogleHealthConnector.__new__(gh.GoogleHealthConnector)
    since = datetime(2026, 10, 1, 3, 0, tzinfo=UTC)
    until = datetime(2026, 10, 18, 3, 0, tzinfo=UTC)
    params = connector._build_params(_bundle("weight"), None, since, until)
    assert since.isoformat() in params["filter"] and until.isoformat() in params["filter"]
    assert params["filter"].startswith('weight.sample_time.physical_time >= "')


# ---------------------------------------------------------------------------
# Poll: pages, empty responses, and checkpoint retention on failure
# ---------------------------------------------------------------------------

_EMAIL = "me@example.com"
_LATER = "2026-10-18T13:30:00Z"


def _poll_harness(responses: list[Any], resource: str = "weight"):
    """A connector with one account whose API client returns ``responses`` in order."""
    connector = gh.GoogleHealthConnector(
        config=gh.GoogleHealthConnectorConfig(switchboard_mcp_url="http://localhost:41999/mcp"),
        shared_pool=None,
        cursor_pool=None,
    )
    acct = uuid.uuid4()
    connector._accounts[acct] = gh.OwnerContext(
        account_id=acct,
        email=_EMAIL,
        entity_id=uuid.uuid4(),
        refresh_token_present=True,
        endpoint_identity=gh._endpoint_identity_for_user(_EMAIL),
    )
    state = gh.ResourceState(bundle=_bundle(resource))
    api: Any = type(
        "FakeAPI",
        (),
        {"get_json": AsyncMock(side_effect=responses), "last_rate_limit_headers": {}},
    )()
    connector._make_account_api_client = lambda _acct: api  # type: ignore[method-assign]
    connector._save_cursor = AsyncMock()  # type: ignore[method-assign]
    return connector, acct, state, api


def _keys(submit: AsyncMock) -> list[str]:
    return [c.args[0]["control"]["idempotency_key"] for c in submit.await_args_list]


def _point(resource: str, record_id: str) -> dict:
    """A minimal provider point whose record identity is ``record_id``."""
    return _weight_point(sample=record_id) if resource == "weight" else {"session_id": record_id}


@pytest.mark.parametrize("resource", ["weight", "sleep", "workout"])
async def test_poll_consumes_every_page_before_committing(resource) -> None:
    page1 = {"dataPoints": [_point(resource, _SAMPLE)], "nextPageToken": "t1"}
    page2 = {"dataPoints": [_point(resource, _LATER)]}
    connector, acct, state, api = _poll_harness([page1, page2], resource)
    connector._submit_envelope = AsyncMock(return_value=True)  # type: ignore[method-assign]

    await connector._poll_resource(acct, state)

    first, second = (c.kwargs["params"] for c in api.get_json.await_args_list)
    assert api.get_json.await_args_list[0].args[0] == _bundle(resource).endpoint_path
    assert "pageToken" not in first
    assert second == {**first, "pageToken": "t1"}
    first_key, second_key = _keys(connector._submit_envelope)
    assert first_key.endswith(f":{_SAMPLE}") and second_key.endswith(f":{_LATER}")
    connector._save_cursor.assert_awaited_once_with(acct, state, _LATER)
    assert state.backfill_done and state.last_poll_at is not None


@pytest.mark.parametrize("resource", ["weight", "body_fat"])
@pytest.mark.parametrize("response", [{}, {"dataPoints": []}])
async def test_empty_response_emits_nothing_and_keeps_the_cursor(resource, response) -> None:
    connector, acct, state, _api = _poll_harness([response], resource)
    state.last_cursor = _SAMPLE
    connector._submit_envelope = AsyncMock(return_value=True)  # type: ignore[method-assign]

    await connector._poll_resource(acct, state)

    connector._submit_envelope.assert_not_awaited()
    connector._save_cursor.assert_not_awaited()
    assert state.last_cursor == _SAMPLE


async def test_failed_submission_keeps_checkpoint_and_replays_same_keys() -> None:
    body = {"dataPoints": [_weight_point(sample=_SAMPLE), _weight_point(sample=_LATER)]}
    connector, acct, state, _api = _poll_harness([body, body])
    connector._submit_envelope = AsyncMock(  # type: ignore[method-assign]
        side_effect=[True, False, True, True]
    )

    with pytest.raises(gh.GoogleHealthPollIncompleteError):
        await connector._poll_resource(acct, state)
    connector._save_cursor.assert_not_awaited()
    assert state.last_poll_at is None and not state.backfill_done

    await connector._poll_resource(acct, state)
    keys = _keys(connector._submit_envelope)
    assert keys[0] == keys[2] and keys[1] == keys[3]
    connector._save_cursor.assert_awaited_once_with(acct, state, _LATER)


@pytest.mark.parametrize("resource", ["weight", "sleep", "workout"])
@pytest.mark.parametrize(
    ("pages", "error"),
    [
        pytest.param(
            [{"dataPoints": [_weight_point()], "nextPageToken": "t1"}, RuntimeError("503")],
            "RuntimeError",
            id="page-fetch-fails",
        ),
        pytest.param(
            [{"dataPoints": [_weight_point()], "nextPageToken": "t"}] * gh._MAX_WINDOW_PAGES,
            "GoogleHealthPollIncompleteError",
            id="page-bound-exceeded",
        ),
    ],
)
async def test_incomplete_window_submits_nothing_and_keeps_checkpoint(
    resource, pages, error
) -> None:
    connector, acct, state, _api = _poll_harness(pages, resource)
    connector._submit_envelope = AsyncMock(return_value=True)  # type: ignore[method-assign]

    # Resolve the class at run time: another test reloads ``google_health``, so a class
    # captured at collection time would not match the one the reloaded module raises.
    expected = RuntimeError if error == "RuntimeError" else getattr(gh, error)
    with pytest.raises(expected):
        await connector._poll_resource(acct, state)

    connector._submit_envelope.assert_not_awaited()
    connector._save_cursor.assert_not_awaited()
    assert state.last_poll_at is None


def _page_cap_hits(resource: str) -> float:
    from prometheus_client import REGISTRY

    value = REGISTRY.get_sample_value(
        "connector_google_health_window_page_cap_exceeded_total",
        {"endpoint_identity": gh._endpoint_identity_for_user(_EMAIL), "resource": resource},
    )
    return value or 0.0


async def test_page_cap_overrun_is_counted_for_alerting() -> None:
    pages = [{"dataPoints": [_weight_point()], "nextPageToken": "t"}] * gh._MAX_WINDOW_PAGES
    connector, acct, state, _api = _poll_harness(pages)
    before = _page_cap_hits("weight")

    with pytest.raises(gh.GoogleHealthPollIncompleteError):
        await connector._poll_resource(acct, state)

    assert _page_cap_hits("weight") == before + 1


# ---------------------------------------------------------------------------
# Poison envelopes: bounded holds, quarantine with a receipt
# ---------------------------------------------------------------------------


def _outcomes(*names: str) -> list[Any]:
    # Resolved at run time: another test reloads ``google_health`` (see above).
    return [getattr(gh.SubmissionOutcome, name) for name in names]


def _harness_with_buffer(polls: int):
    from butlers.connectors.filtered_event_buffer import FilteredEventBuffer

    body = {"dataPoints": [_weight_point(sample=_SAMPLE), _weight_point(sample=_LATER)]}
    connector, acct, state, _api = _poll_harness([body] * polls)
    connector._filtered_event_buffer = FilteredEventBuffer("google_health", "ep")
    connector._shared_pool = FilteredEventsPool()  # a quarantine receipt is committed to it
    return connector, acct, state


async def test_repeated_rejection_quarantines_the_envelope_with_a_receipt() -> None:
    limit = gh._POISON_REJECTION_LIMIT
    connector, acct, state = _harness_with_buffer(limit)
    # The first sample is rejected on every attempt; the later one is accepted.
    connector._submit_envelope = AsyncMock(  # type: ignore[method-assign]
        side_effect=_outcomes(*["REJECTED"] * limit, "HANDLED")
    )

    for _ in range(limit - 1):
        with pytest.raises(gh.GoogleHealthPollIncompleteError):
            await connector._poll_resource(acct, state)
    connector._save_cursor.assert_not_awaited()
    assert len(connector._filtered_event_buffer) == 0

    await connector._poll_resource(acct, state)

    keys = _keys(connector._submit_envelope)
    assert len(set(keys[:limit])) == 1 and keys[limit].endswith(f":{_LATER}")
    connector._save_cursor.assert_awaited_once_with(acct, state, _LATER)
    (receipt,) = connector._shared_pool.written
    assert receipt[3] == keys[0]  # external_message_id
    assert receipt[7] == "poison_quarantine" and receipt[8] == "error"
    assert receipt[9]["control"]["idempotency_key"] == keys[0]  # replayable envelope
    assert state.rejected_key is None and state.rejected_count == 0


async def test_unwritten_quarantine_receipt_holds_the_checkpoint_and_retries() -> None:
    """The receipt is the only copy once the sample is consumed (bu-dhloi1.274)."""
    limit = gh._POISON_REJECTION_LIMIT
    connector, acct, state = _harness_with_buffer(limit + 1)
    pool = connector._shared_pool
    pool.fail_writes = True
    # The first sample is rejected on every attempt; the later one is accepted.
    connector._submit_envelope = AsyncMock(  # type: ignore[method-assign]
        side_effect=_outcomes(*["REJECTED"] * (limit + 1), "HANDLED")
    )

    for _ in range(limit):
        with pytest.raises(gh.GoogleHealthPollIncompleteError):
            await connector._poll_resource(acct, state)
    # Rejected `limit` times, but the receipt was not written: the sample is not consumed.
    connector._save_cursor.assert_not_awaited()
    assert pool.written == [] and state.rejected_count == limit - 1

    pool.fail_writes = False
    await connector._poll_resource(acct, state)
    connector._save_cursor.assert_awaited_once()
    (receipt,) = pool.written
    assert receipt[7] == "poison_quarantine" and receipt[8] == "error"


@pytest.mark.parametrize("case", ["transport-failures-never-count", "handled-attempt-resets"])
async def test_only_consecutive_rejections_reach_quarantine(case) -> None:
    """Transport failures never count toward quarantine; a handled attempt resets the streak."""
    below = ["REJECTED"] * (gh._POISON_REJECTION_LIMIT - 1)
    if case == "transport-failures-never-count":
        sequence = [*below[:-1], "FAILED", "FAILED", below[-1], "FAILED"]
    else:
        sequence = [*below, "HANDLED", *below]
    connector, acct, state = _harness_with_buffer(len(sequence))
    # A HANDLED first sample lets the poll move on to the second, which is then accepted.
    outcomes: list[Any] = []
    for name in sequence:
        outcomes += _outcomes(name, "HANDLED") if name == "HANDLED" else _outcomes(name)
    connector._submit_envelope = AsyncMock(side_effect=outcomes)  # type: ignore[method-assign]

    for name in sequence:
        if name == "HANDLED":
            await connector._poll_resource(acct, state)
        else:
            with pytest.raises(gh.GoogleHealthPollIncompleteError):
                await connector._poll_resource(acct, state)

    assert len(connector._filtered_event_buffer) == 0
