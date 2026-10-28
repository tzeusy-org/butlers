"""Google Health weight and body-fat bundles (bu-q7vx1q.35).

One envelope per sample, keyed on the sample's physical time; weight is converted from grams to
kilograms at the connector.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from butlers.connectors import google_health as gh

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
