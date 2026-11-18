"""Real-Postgres: the bound device's last fix becomes custody-case evidence (S5f).

bu-s11n0s.1 S5f, REQ-endpoint-custody-holds-009. Runs the real job and its SQL on a migrated
Chronicler schema (the OwnTracks projection owner) against the shared fleet-case tables, with a
planted correct device, a wrong device, a stale fix, a tombstoned fix and an outlier. Docker;
CI-only.
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from butlers.chronicler import custody_device_fix as job

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
DEVICE = "owntracks:owner:phone"
OTHER = "owntracks:owner:tablet"


@pytest.fixture
async def pool(db):
    pool = await db(
        "core",
        "chronicler",
        schemas={"core": "chronicler", "chronicler": "chronicler"},
        schema="chronicler",
    )
    for source in ("owntracks.points", "owntracks.place_cluster"):
        await pool.execute(
            "INSERT INTO source_adapter_state (source_name, chronicler_compatibility) "
            "VALUES ($1, 'supported') ON CONFLICT DO NOTHING",
            source,
        )
    return pool


async def _case(pool, endpoint: str | None = DEVICE) -> str:
    case_id = await pool.fetchval(
        "INSERT INTO public.fleet_cases (correlation_key) VALUES ($1) RETURNING id::text",
        f"endpoint_custody:{uuid.uuid4()}",
    )
    if endpoint:
        await pool.execute(
            "INSERT INTO public.fleet_case_evidence (case_id, contributor, kind, ref, payload) "
            "VALUES ($1::uuid, 'custody_owner', 'device_binding', $2, '{}')",
            case_id,
            endpoint,
        )
    return case_id


async def _fix(pool, endpoint: str, at: datetime, **extra) -> None:
    payload = {"endpoint_identity": endpoint, "lat": 1.3, "lon": 103.8} | extra.pop("payload", {})
    await pool.execute(
        "INSERT INTO point_events (source_name, source_ref, event_type, occurred_at, payload, "
        "tombstone_at) VALUES ('owntracks.points', $1, 'location', $2, $3::jsonb, $4)",
        str(uuid.uuid4()),
        at,
        json.dumps(payload),
        extra.get("tombstone_at"),
    )


async def _place(pool, endpoint: str, start: datetime, end: datetime, label: str) -> None:
    await pool.execute(
        "INSERT INTO episodes (source_name, source_ref, episode_type, start_at, end_at, payload) "
        "VALUES ('owntracks.place_cluster', $1, 'place_episode', $2, $3, $4::jsonb)",
        str(uuid.uuid4()),
        start,
        end,
        json.dumps(
            {"endpoint_identity": endpoint, "label": label, "label_source": "owner_reference"}
        ),
    )


async def _evidence(pool, case_id: str):
    return await pool.fetch(
        "SELECT ref, payload FROM public.fleet_case_evidence "
        "WHERE case_id = $1::uuid AND contributor = 'chronicler' AND kind = 'device_last_fix'",
        case_id,
    )


async def test_the_bound_device_reports_its_own_fix_age_and_named_place(pool) -> None:
    case_id = await _case(pool)
    fix = NOW - timedelta(minutes=20)
    await _fix(pool, OTHER, NOW - timedelta(minutes=1))  # a wrong device never answers
    await _fix(pool, DEVICE, fix - timedelta(hours=1))
    await _fix(pool, DEVICE, fix)
    await _place(pool, DEVICE, fix - timedelta(hours=2), fix + timedelta(minutes=5), "Home")
    result = await job.run_custody_device_fix_evidence(pool, now=NOW)
    assert result == {"bindings": 1, "contributed": 1, "errors": 0}
    (row,) = await _evidence(pool, case_id)
    payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
    assert row["ref"] == job.evidence_ref(DEVICE, fix)
    assert (payload["status"], payload["place"]) == ("known", "Home")
    assert payload["age_seconds_at_observation"] == 1200
    assert not {"lat", "lon"} & set(payload)
    again = await job.run_custody_device_fix_evidence(pool, now=NOW + timedelta(minutes=1))
    assert again["contributed"] == 0


async def test_stale_tombstoned_outlier_and_missing_fixes_read_unknown(pool) -> None:
    stale_case = await _case(pool)
    await _fix(pool, DEVICE, NOW - job.FRESH_FOR - timedelta(hours=1))
    await job.run_custody_device_fix_evidence(pool, now=NOW)
    (stale,) = await _evidence(pool, stale_case)
    assert "stale" in str(stale["payload"]) and "Home" not in str(stale["payload"])

    pool2_case = await _case(pool, endpoint=OTHER)
    await _fix(pool, OTHER, NOW - timedelta(minutes=1), tombstone_at=NOW)
    await _fix(
        pool, OTHER, NOW - timedelta(minutes=2), payload={"trail_outlier": {"reason": "jump"}}
    )
    await job.run_custody_device_fix_evidence(pool, now=NOW)
    (gone,) = await _evidence(pool, pool2_case)
    assert "no_fix" in str(gone["payload"])


async def test_an_unbound_episode_gets_nothing(pool) -> None:
    case_id = await _case(pool, endpoint=None)
    await _fix(pool, DEVICE, NOW - timedelta(minutes=1))
    result = await job.run_custody_device_fix_evidence(pool, now=NOW)
    assert result["bindings"] == 0 and await _evidence(pool, case_id) == []
