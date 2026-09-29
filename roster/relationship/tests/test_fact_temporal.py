"""Effective-time wire normalization (relationship-fact-effective-time, bu-h3b7t.1).

Pure: :func:`normalize_request` runs before any database access, so every
malformed packet it rejects is provably rejected before approval parking or
any fact/evidence/coverage write. The database CHECK constraints that back it
are exercised against real PostgreSQL in ``test_rel_035_migration.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from butlers.tools.relationship.fact_temporal import (
    INVALID,
    RequestMode,
    TemporalError,
    TemporalPacket,
    normalize_request,
    parked_request,
)

pytestmark = pytest.mark.unit

_P = uuid.UUID("5a1d1d1e-0000-4000-8000-000000000001")
_T = uuid.UUID("5a1d1d1e-0000-4000-8000-000000000002")


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


@pytest.mark.parametrize(
    ("kwargs", "mode", "packet"),
    [
        # Omission and explicit null are one request: ordinary, no packet.
        ({}, RequestMode.ordinary, TemporalPacket()),
        (
            dict.fromkeys(
                (
                    "effective_period_id",
                    "effective_from",
                    "effective_from_precision",
                    "effective_to",
                    "effective_to_precision",
                    "corrects_fact_id",
                )
            ),
            RequestMode.ordinary,
            TemporalPacket(),
        ),
        # Coarse bounds normalize into a half-open UTC interval.
        (
            dict(
                effective_from="2024-03",
                effective_from_precision="month",
                effective_to="2024-05",
                effective_to_precision="month",
            ),
            RequestMode.explicit,
            TemporalPacket(None, _utc(2024, 3, 1), "month", _utc(2024, 6, 1), "month"),
        ),
        (
            dict(effective_to="2023-12-31", effective_to_precision="day"),
            RequestMode.explicit,
            TemporalPacket(None, None, None, _utc(2024, 1, 1), "day"),
        ),
        (
            dict(effective_from="2019", effective_from_precision="year"),
            RequestMode.explicit,
            TemporalPacket(None, _utc(2019, 1, 1), "year"),
        ),
        # An instant keeps sub-second precision and converts to UTC.
        (
            dict(
                effective_from="2024-03-01T10:00:00.123456+02:00",
                effective_from_precision="instant",
                effective_to_precision="unbounded",
            ),
            RequestMode.explicit,
            TemporalPacket(None, _utc(2024, 3, 1, 8, 0, 0, 123456), "instant", None, "unbounded"),
        ),
        # A period id alone is explicit intent for a (still unknown) occurrence.
        (dict(effective_period_id=str(_P)), RequestMode.explicit, TemporalPacket(_P)),
        # A correction target alone means "replace with wholly unknown bounds".
        (dict(corrects_fact_id=_T), RequestMode.correction, TemporalPacket()),
    ],
    ids=[
        "omitted",
        "explicit-null",
        "month-range",
        "day-upper",
        "year-lower",
        "instant-open",
        "period-only",
        "correction-to-unknown",
    ],
)
def test_normalize_request(kwargs, mode, packet) -> None:
    request = normalize_request(**kwargs)
    assert request.mode is mode
    assert request.packet == packet
    if mode is RequestMode.correction:
        assert request.corrects_fact_id == _T
    # The parked canonical form decodes back to the same request.
    decoded, _ = parked_request(request.tool_args(request.packet), mode.value)
    assert decoded == request


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(effective_from="2024-03-01T10:00:00", effective_from_precision="instant"),
        dict(effective_from="2024-03-01T00:00:00Z", effective_from_precision="day"),
        dict(effective_from="2024-02-30", effective_from_precision="day"),
        dict(effective_from="2024-03", effective_from_precision="unknown"),
        dict(effective_from="2024-03"),
        dict(effective_from_precision="month"),
        dict(effective_to="9999", effective_to_precision="year"),
        dict(
            effective_from="2024-03",
            effective_from_precision="month",
            effective_to="2024-02",
            effective_to_precision="month",
        ),
        dict(
            effective_from="2024-03-01",
            effective_from_precision="day",
            effective_to="2024-02-29",
            effective_to_precision="day",
        ),
        dict(effective_period_id=str(uuid.UUID(int=0))),
        dict(effective_period_id="not-a-uuid"),
    ],
    ids=[
        "naive-instant",
        "coarse-with-time",
        "impossible-date",
        "unknown-token",
        "value-without-precision",
        "precision-without-value",
        "upper-overflow",
        "reversed",
        "empty-half-open",
        "zero-period",
        "malformed-period",
    ],
)
def test_invalid_packets_are_rejected_before_any_database_access(kwargs) -> None:
    with pytest.raises(TemporalError) as caught:
        normalize_request(**kwargs)
    assert caught.value.code == INVALID
