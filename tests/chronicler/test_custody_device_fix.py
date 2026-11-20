"""Last OwnTracks fix of the owner-bound device as custody-case evidence (bu-s11n0s.1 S5f).

REQ-endpoint-custody-holds-009: association must be explicit, a missing or stale fix is honestly
unknown, only age and a named place are exposed (never coordinates), and the evidence is
idempotent per exact fix. Pools and the case store are faked; the SQL is covered on real
PostgreSQL in ``tests/integration/test_custody_device_fix_roundtrip.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from butlers.chronicler import custody_device_fix as job

pytestmark = pytest.mark.unit

FIX = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
NOW = FIX + timedelta(minutes=30)
ENDPOINT = "owntracks:owner:phone"


def test_a_fresh_fix_is_known_with_age_and_place_and_no_coordinates() -> None:
    payload = job.fix_evidence(fix_at=FIX, place="Home", observed_at=NOW)
    assert payload["status"] == "known" and payload["place"] == "Home"
    assert payload["age_seconds_at_observation"] == 1800
    assert payload["evidence_only"] is True
    assert not {"lat", "lon", "accuracy", "raw_payload"} & set(payload)


def test_a_known_fix_without_a_named_place_has_a_null_place() -> None:
    assert job.fix_evidence(fix_at=FIX, place=None, observed_at=NOW)["place"] is None


def test_a_stale_fix_reads_unknown_and_names_no_place() -> None:
    late = FIX + job.FRESH_FOR + timedelta(seconds=1)
    payload = job.fix_evidence(fix_at=FIX, place="Home", observed_at=late)
    assert (payload["status"], payload["reason"]) == ("unknown", "stale")
    assert payload["stale_fix_at"] == FIX.isoformat()
    assert "place" not in payload and "fix_at" not in payload


def test_no_fix_is_unknown() -> None:
    payload = job.fix_evidence(fix_at=None, place=None, observed_at=NOW)
    assert (payload["status"], payload["reason"]) == ("unknown", "no_fix")


def test_the_evidence_ref_names_the_endpoint_and_exact_fix() -> None:
    assert job.evidence_ref(ENDPOINT, FIX) == f"{ENDPOINT}:{int(FIX.timestamp())}"
    assert job.evidence_ref(ENDPOINT, None) == f"{ENDPOINT}:none"


class _Pool:
    def __init__(self, bindings, fix=FIX, place="Home", last=None) -> None:
        self.bindings, self.fix, self.place, self.last = bindings, fix, place, last
        self.sql: list[str] = []

    async def fetch(self, sql: str, *args: Any):
        self.sql.append(sql)
        return self.bindings

    async def fetchval(self, sql: str, *args: Any):
        self.sql.append(sql)
        if "fleet_case_evidence" in sql:
            return self.last
        return self.fix if "point_events" in sql else self.place


@pytest.fixture
def rows(monkeypatch):
    store: dict[tuple, dict] = {}

    async def contribute(pool, *, case_id, contributor, kind, ref, payload=None):
        key = (case_id, contributor, kind, ref)
        if key in store:
            return store[key], False
        store[key] = {"payload": payload}
        return store[key], True

    monkeypatch.setattr(job, "contribute_evidence", contribute)
    return store


async def test_a_bound_device_contributes_once_per_exact_fix(rows) -> None:
    binding = {"case_id": str(uuid.uuid4()), "endpoint_identity": ENDPOINT}
    first = await job.run_custody_device_fix_evidence(_Pool([binding]), now=NOW)
    again = await job.run_custody_device_fix_evidence(_Pool([binding]), now=NOW + job.FRESH_FOR)
    assert first == {"bindings": 1, "contributed": 1, "errors": 0}
    assert again["contributed"] == 0
    ((case_id, contributor, kind, ref), row) = next(iter(rows.items()))
    assert (contributor, kind, ref) == (
        "chronicler",
        "device_last_fix",
        job.evidence_ref(ENDPOINT, FIX),
    )
    assert row["payload"]["place"] == "Home"


async def test_the_unknown_place_label_is_never_reported(rows) -> None:
    binding = {"case_id": str(uuid.uuid4()), "endpoint_identity": ENDPOINT}
    await job.run_custody_device_fix_evidence(_Pool([binding], place="place_unknown"), now=NOW)
    assert next(iter(rows.values()))["payload"]["place"] is None


async def test_a_new_fix_appends_a_new_row(rows) -> None:
    binding = {"case_id": str(uuid.uuid4()), "endpoint_identity": ENDPOINT}
    await job.run_custody_device_fix_evidence(_Pool([binding]), now=NOW)
    await job.run_custody_device_fix_evidence(
        _Pool([binding], fix=FIX + timedelta(minutes=20)), now=NOW
    )
    assert len(rows) == 2


async def test_no_retained_fix_contributes_an_honest_unknown(rows) -> None:
    binding = {"case_id": str(uuid.uuid4()), "endpoint_identity": ENDPOINT}
    await job.run_custody_device_fix_evidence(_Pool([binding], fix=None), now=NOW)
    ((_k, row),) = rows.items()
    assert row["payload"]["reason"] == "no_fix"


async def test_no_binding_means_nothing_is_read_or_written(rows) -> None:
    pool = _Pool([])
    assert await job.run_custody_device_fix_evidence(pool, now=NOW) == {
        "bindings": 0,
        "contributed": 0,
        "errors": 0,
    }
    assert rows == {} and len(pool.sql) == 1  # only the binding lookup ran


def test_the_queries_read_only_retained_live_non_outlier_fixes() -> None:
    assert "v_point_events_corrected" in job._LAST_FIX_SQL  # owner corrections apply
    assert "v_episodes_corrected" in job._PLACE_SQL
    assert "tombstone_at IS NULL" in job._LAST_FIX_SQL
    assert "privacy <> 'restricted'" in job._LAST_FIX_SQL
    assert "trail_outlier" in job._LAST_FIX_SQL
    assert "lat" not in job._LAST_FIX_SQL.replace("place", "") and "lon" not in job._PLACE_SQL
    assert "c.state <> 'closed'" in job._BINDINGS_SQL


def _row(**kw):
    return job.fix_evidence(fix_at=FIX, place=kw.get("place", "Home"), observed_at=NOW)


async def test_a_new_fix_with_the_same_status_and_place_while_fresh_writes_nothing(rows) -> None:
    binding = {"case_id": str(uuid.uuid4()), "endpoint_identity": ENDPOINT}
    newer = FIX + timedelta(minutes=20)
    result = await job.run_custody_device_fix_evidence(
        _Pool([binding], fix=newer, last=_row()), now=NOW + timedelta(minutes=30)
    )
    assert result["contributed"] == 0 and rows == {}


async def test_a_changed_place_or_a_nearly_stale_row_is_written(rows) -> None:
    binding = {"case_id": str(uuid.uuid4()), "endpoint_identity": ENDPOINT}
    moved = _Pool([binding], fix=FIX + timedelta(minutes=20), place="Work", last=_row())
    assert (await job.run_custody_device_fix_evidence(moved, now=NOW))["contributed"] == 1
    near_stale = NOW + job.FRESH_FOR - job.REFRESH_MARGIN + timedelta(minutes=1)
    later = _Pool([binding], fix=near_stale - timedelta(minutes=5), last=_row())
    assert (await job.run_custody_device_fix_evidence(later, now=near_stale))["contributed"] == 1
