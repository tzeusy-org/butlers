"""Real-Postgres tests for the curriculum-request receipt spine (bu-6jv4m.10).

``POST /api/education/curriculum-requests`` used to persist only a KV lock and
fire a detached trigger, so a trigger failure or an API restart left the owner
with a success toast, no terminal status, and a stranded 409 guard. Migration
``education_004`` installs ``education.curriculum_requests``: an immutable
receipt per accepted request, a partial unique index as the single pending
guard, and CHECK constraints that stop a row from claiming an outcome it has no
evidence for.

These tests exercise the migration and the router's receipt helpers against a
migrated Postgres (core + education chains via testcontainers/Docker), because
the guarantees under test — partial unique index, CHECK constraints, idempotent
first-writer-wins settle — are backend behaviour that a mocked pool cannot
falsify.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

# tests/config/test_*.py -> parents[2] == repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ROUTER_PATH = _REPO_ROOT / "roster" / "education" / "api" / "router.py"


def _load_education_router():
    """Load roster/education/api/router.py as an importable module.

    The router is discovered dynamically at app startup rather than imported by
    path, so a direct unit-level import needs the same importlib dance.
    """
    if "education_receipt_router_under_test" in sys.modules:
        return sys.modules["education_receipt_router_under_test"]
    spec = importlib.util.spec_from_file_location(
        "education_receipt_router_under_test", _ROUTER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["education_receipt_router_under_test"] = module
    spec.loader.exec_module(module)
    return module


edu = _load_education_router()


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision core (public.entities) + education (education schema) chains."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "education"],
        schemas={"education": "education"},
    )


@pytest.fixture
async def pool(postgres_container, migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    await p.execute("TRUNCATE TABLE education.curriculum_requests CASCADE")
    await p.execute("TRUNCATE TABLE education.mind_maps CASCADE")
    yield p
    await p.close()


# ---------------------------------------------------------------------------
# Migration shape
# ---------------------------------------------------------------------------


async def test_receipt_table_exists_after_migration(pool: asyncpg.Pool) -> None:
    """The chain must create the receipt table with its evidence columns.

    ``education_005`` adds the notice-evidence pair (bu-358jk). Asserting the
    exact set, not a subset, so a column that quietly appears has to be named
    and justified here rather than shipping unreviewed.
    """
    cols = {
        r["column_name"]
        for r in await pool.fetch(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = 'education'
               AND table_name = 'curriculum_requests'
            """
        )
    }
    assert cols == {
        "id",
        "topic",
        "goal",
        "status",
        "session_id",
        "mind_map_id",
        "calibration_ready_at",
        "calibration_notice_outcome",
        "calibration_notice_accepted_at",
        "failure_reason",
        "requested_at",
        "triggered_at",
        "settled_at",
        "updated_at",
    }


async def test_terminal_status_requires_settled_at(pool: asyncpg.Pool) -> None:
    """A receipt must not read 'completed' while claiming it never settled."""
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            """
            INSERT INTO education.curriculum_requests (topic, status, settled_at)
            VALUES ('Python', 'completed', NULL)
            """
        )


async def test_failed_status_requires_failure_reason(pool: asyncpg.Pool) -> None:
    """A failure the owner cannot read is not a receipt."""
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            """
            INSERT INTO education.curriculum_requests (topic, status, settled_at)
            VALUES ('Python', 'failed', now())
            """
        )


async def test_open_receipt_must_not_be_settled(pool: asyncpg.Pool) -> None:
    """A live receipt carrying a settled_at would fake a terminal outcome."""
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            """
            INSERT INTO education.curriculum_requests (topic, status, settled_at)
            VALUES ('Python', 'running', now())
            """
        )


async def test_accepted_notice_requires_an_acceptance_timestamp(pool: asyncpg.Pool) -> None:
    """ "Delivered" with no moment is a claim with no evidence behind it."""
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            """
            INSERT INTO education.curriculum_requests
                (topic, status, calibration_notice_outcome, calibration_notice_accepted_at)
            VALUES ('Python', 'accepted', 'delivered', NULL)
            """
        )


async def test_unaccepted_notice_must_not_carry_an_acceptance_timestamp(
    pool: asyncpg.Pool,
) -> None:
    """The failure direction, which is the one that would overclaim.

    A timestamp beside a non-delivered outcome is exactly the row a UI would
    render as "the butler messaged you" while the notice never left.
    """
    outcomes = ("failed", "suppressed", "deferred", "coalesced", "no_record", "unproven")
    assert outcomes, "no outcomes to check; the loop below would be vacuous"
    for outcome in outcomes:
        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(
                """
                INSERT INTO education.curriculum_requests
                    (topic, status, calibration_notice_outcome, calibration_notice_accepted_at)
                VALUES ('Python', 'accepted', $1, now())
                """,
                outcome,
            )


async def test_null_notice_outcome_must_not_carry_an_acceptance_timestamp(
    pool: asyncpg.Pool,
) -> None:
    """ "Never asked" cannot come with an answer attached."""
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            """
            INSERT INTO education.curriculum_requests
                (topic, status, calibration_notice_outcome, calibration_notice_accepted_at)
            VALUES ('Python', 'accepted', NULL, now())
            """
        )


async def test_unknown_notice_outcome_is_rejected(pool: asyncpg.Pool) -> None:
    """The vocabulary is closed, so an invented word cannot reach the UI."""
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            """
            INSERT INTO education.curriculum_requests
                (topic, status, calibration_notice_outcome)
            VALUES ('Python', 'accepted', 'probably_sent')
            """
        )


async def test_every_notice_outcome_the_router_can_write_is_accepted(
    pool: asyncpg.Pool,
) -> None:
    """The CHECK and the router must agree on the vocabulary.

    Covers the ledger's own outcomes plus the router's two sentinels, so a
    constraint that silently drops one cannot 500 a settle in production.
    """
    outcomes = [
        "delivered",
        "coalesced",
        "deferred",
        "suppressed",
        "failed",
        edu._NOTICE_NO_RECORD,
        edu._NOTICE_UNPROVEN,
    ]
    assert outcomes, "no outcomes to check; the loop below would be vacuous"
    for outcome in outcomes:
        accepted_at = datetime.now(UTC) if outcome == "delivered" else None
        request_id = await pool.fetchval(
            """
            INSERT INTO education.curriculum_requests
                (topic, status, settled_at,
                 calibration_notice_outcome, calibration_notice_accepted_at)
            VALUES ('Python', 'completed', now(), $1, $2)
            RETURNING id
            """,
            outcome,
            accepted_at,
        )
        assert request_id is not None, f"{outcome} was rejected by the notice CHECK"
        await pool.execute("DELETE FROM education.curriculum_requests WHERE id = $1", request_id)


# ---------------------------------------------------------------------------
# The single pending guard
# ---------------------------------------------------------------------------


async def test_second_open_receipt_is_rejected(pool: asyncpg.Pool) -> None:
    """The partial unique index is the one-pending-at-a-time guard."""
    first = await edu._create_receipt(pool, "Python", "web dev")
    assert first is not None

    second = await edu._create_receipt(pool, "Rust", None)
    assert second is None, "a second open receipt must be refused (409 path)"


async def test_new_receipt_allowed_once_previous_is_terminal(pool: asyncpg.Pool) -> None:
    """Settling releases the guard — the terminal state IS the release."""
    first = await edu._create_receipt(pool, "Python", None)
    assert first is not None

    settled = await edu._settle_receipt(
        pool,
        str(first["id"]),
        status="failed",
        failure_reason=edu._FAILURE_SESSION_ERROR,
    )
    assert settled is True

    second = await edu._create_receipt(pool, "Rust", None)
    assert second is not None, "a terminal receipt must not keep holding the guard"


async def test_two_terminal_receipts_coexist(pool: asyncpg.Pool) -> None:
    """The guard covers only open rows — history is unbounded."""
    for topic in ("Python", "Rust", "Go"):
        row = await edu._create_receipt(pool, topic, None)
        assert row is not None
        await edu._settle_receipt(
            pool, str(row["id"]), status="failed", failure_reason=edu._FAILURE_TIMED_OUT
        )

    total = await pool.fetchval("SELECT count(*) FROM education.curriculum_requests")
    assert total == 3


# ---------------------------------------------------------------------------
# Lifecycle settlement
# ---------------------------------------------------------------------------


async def test_receipt_starts_accepted_with_no_evidence(pool: asyncpg.Pool) -> None:
    """The row exists before any detached work — accepted, and honest about it."""
    row = await edu._create_receipt(pool, "Python", "web dev")
    assert row is not None
    assert row["status"] == "accepted"
    assert row["topic"] == "Python"
    assert row["goal"] == "web dev"
    assert row["session_id"] is None
    assert row["mind_map_id"] is None
    assert row["calibration_ready_at"] is None
    assert row["failure_reason"] is None
    assert row["triggered_at"] is None
    assert row["settled_at"] is None


async def test_mark_running_stamps_triggered_at(pool: asyncpg.Pool) -> None:
    """Handing the request to the session is itself recorded evidence."""
    row = await edu._create_receipt(pool, "Python", None)
    triggered_at = datetime.now(UTC)
    await edu._mark_receipt_running(pool, str(row["id"]), triggered_at)

    after = await edu._get_receipt(pool, str(row["id"]))
    assert after["status"] == "running"
    assert after["triggered_at"] is not None


async def test_completed_settle_records_full_evidence(pool: asyncpg.Pool) -> None:
    """Trigger/session, curriculum, calibration and timestamps land on the receipt."""
    map_id = await pool.fetchval(
        "INSERT INTO education.mind_maps (title) VALUES ('Python') RETURNING id"
    )
    row = await edu._create_receipt(pool, "Python", None)
    await edu._mark_receipt_running(pool, str(row["id"]), datetime.now(UTC))

    settled = await edu._settle_receipt(
        pool,
        str(row["id"]),
        status="completed",
        session_id="sess-1",
        mind_map_id=str(map_id),
        calibration_ready=True,
    )
    assert settled is True

    after = await edu._get_receipt(pool, str(row["id"]))
    assert after["status"] == "completed"
    assert after["session_id"] == "sess-1"
    assert after["mind_map_id"] == map_id
    assert after["calibration_ready_at"] is not None
    assert after["failure_reason"] is None
    assert after["settled_at"] is not None


async def test_settle_is_idempotent_first_writer_wins(pool: asyncpg.Pool) -> None:
    """A second settle must not rewrite a terminal outcome."""
    row = await edu._create_receipt(pool, "Python", None)
    request_id = str(row["id"])

    assert await edu._settle_receipt(
        pool, request_id, status="failed", failure_reason=edu._FAILURE_TRIGGER_UNREACHABLE
    )
    assert not await edu._settle_receipt(
        pool, request_id, status="completed", session_id="sess-late"
    )

    after = await edu._get_receipt(pool, request_id)
    assert after["status"] == "failed"
    assert after["failure_reason"] == edu._FAILURE_TRIGGER_UNREACHABLE
    assert after["session_id"] is None


async def test_failed_settle_keeps_session_evidence(pool: asyncpg.Pool) -> None:
    """A failure still names the session that failed, so the owner can open it."""
    row = await edu._create_receipt(pool, "Python", None)
    await edu._settle_receipt(
        pool,
        str(row["id"]),
        status="failed",
        session_id="sess-boom",
        failure_reason=edu._FAILURE_SESSION_ERROR,
    )

    after = await edu._get_receipt(pool, str(row["id"]))
    assert after["status"] == "failed"
    assert after["session_id"] == "sess-boom"
    assert after["failure_reason"] == edu._FAILURE_SESSION_ERROR


async def test_settle_records_notice_evidence_from_the_notification_path(
    pool: asyncpg.Pool,
) -> None:
    """Delivery evidence round-trips as the pair the CHECK requires."""
    row = await edu._create_receipt(pool, "Python", None)
    accepted_at = datetime.now(UTC) - timedelta(seconds=5)

    assert await edu._settle_receipt(
        pool,
        str(row["id"]),
        status="completed",
        session_id="sess-ok",
        mind_map_id=None,
        calibration_ready=True,
        notice_outcome="delivered",
        notice_accepted_at=accepted_at,
    )

    after = await edu._get_receipt(pool, str(row["id"]))
    assert after["calibration_notice_outcome"] == "delivered"
    assert after["calibration_notice_accepted_at"] == accepted_at


async def test_settle_records_a_failed_notice_without_claiming_delivery(
    pool: asyncpg.Pool,
) -> None:
    """The central case: calibration live, notice never delivered.

    ``calibration_ready_at`` is set and the notice outcome is ``failed``, so the
    receipt says both true things at once and neither one implies the other.
    """
    row = await edu._create_receipt(pool, "Python", None)

    assert await edu._settle_receipt(
        pool,
        str(row["id"]),
        status="completed",
        session_id="sess-boom",
        calibration_ready=True,
        notice_outcome="failed",
        notice_accepted_at=None,
    )

    after = await edu._get_receipt(pool, str(row["id"]))
    assert after["calibration_ready_at"] is not None, "flow reached diagnosing"
    assert after["calibration_notice_outcome"] == "failed"
    assert after["calibration_notice_accepted_at"] is None


async def test_settle_without_notice_evidence_leaves_the_columns_untouched(
    pool: asyncpg.Pool,
) -> None:
    """A settle that has nothing to say about the notice must say nothing."""
    row = await edu._create_receipt(pool, "Python", None)

    assert await edu._settle_receipt(
        pool,
        str(row["id"]),
        status="failed",
        failure_reason=edu._FAILURE_TRIGGER_UNREACHABLE,
    )

    after = await edu._get_receipt(pool, str(row["id"]))
    assert after["calibration_notice_outcome"] is None
    assert after["calibration_notice_accepted_at"] is None


# ---------------------------------------------------------------------------
# Restart safety — the guard must never strand
# ---------------------------------------------------------------------------


async def test_sweep_settles_abandoned_receipt_and_releases_guard(pool: asyncpg.Pool) -> None:
    """An API restart kills the owning task; the sweep must free the owner."""
    row = await edu._create_receipt(pool, "Python", None)
    # Simulate a receipt whose owning task died long ago.
    await pool.execute(
        "UPDATE education.curriculum_requests SET status = 'running',"
        " triggered_at = $2, requested_at = $2 WHERE id = $1",
        row["id"],
        datetime.now(UTC) - edu._RECEIPT_TIMEOUT - timedelta(minutes=1),
    )

    assert await edu._create_receipt(pool, "Rust", None) is None, "guard held before sweep"

    swept = await edu._sweep_abandoned_receipts(pool)
    assert swept == 1

    after = await edu._get_receipt(pool, str(row["id"]))
    assert after["status"] == "failed"
    assert after["failure_reason"] == edu._FAILURE_TIMED_OUT
    assert after["settled_at"] is not None

    assert await edu._create_receipt(pool, "Rust", None) is not None


async def test_sweep_leaves_fresh_receipts_alone(pool: asyncpg.Pool) -> None:
    """A request still in flight must not be declared failed."""
    row = await edu._create_receipt(pool, "Python", None)
    assert await edu._sweep_abandoned_receipts(pool) == 0

    after = await edu._get_receipt(pool, str(row["id"]))
    assert after["status"] == "accepted"


async def test_sweep_is_idempotent(pool: asyncpg.Pool) -> None:
    """Running the sweep twice must not re-settle or double-count."""
    row = await edu._create_receipt(pool, "Python", None)
    await pool.execute(
        "UPDATE education.curriculum_requests SET requested_at = $2 WHERE id = $1",
        row["id"],
        datetime.now(UTC) - edu._RECEIPT_TIMEOUT - timedelta(minutes=1),
    )

    assert await edu._sweep_abandoned_receipts(pool) == 1
    assert await edu._sweep_abandoned_receipts(pool) == 0


# ---------------------------------------------------------------------------
# Curriculum correlation
# ---------------------------------------------------------------------------


async def test_correlate_finds_map_created_after_trigger(pool: asyncpg.Pool) -> None:
    """The curriculum door is correlated by the trigger's creation window."""
    triggered_at = datetime.now(UTC)
    map_id = await pool.fetchval(
        "INSERT INTO education.mind_maps (title) VALUES ('Python') RETURNING id"
    )

    found, calibration_ready = await edu._correlate_curriculum(pool, triggered_at)
    assert found == str(map_id)
    # No teaching flow written -> calibration is not evidenced.
    assert calibration_ready is False


async def test_correlate_ignores_maps_predating_the_trigger(pool: asyncpg.Pool) -> None:
    """A curriculum that already existed is not evidence this request worked."""
    await pool.fetchval("INSERT INTO education.mind_maps (title) VALUES ('Older') RETURNING id")
    triggered_at = datetime.now(UTC) + timedelta(seconds=1)

    found, calibration_ready = await edu._correlate_curriculum(pool, triggered_at)
    assert found is None
    assert calibration_ready is False


async def test_correlate_reports_calibration_ready_from_flow_state(pool: asyncpg.Pool) -> None:
    """Calibration counts as ready only once the teaching flow is diagnosing."""
    from butlers.core.state import state_set

    triggered_at = datetime.now(UTC)
    map_id = await pool.fetchval(
        "INSERT INTO education.mind_maps (title) VALUES ('Python') RETURNING id"
    )
    await state_set(pool, f"flow:{map_id}", {"status": "diagnosing", "mind_map_id": str(map_id)})

    found, calibration_ready = await edu._correlate_curriculum(pool, triggered_at)
    assert found == str(map_id)
    assert calibration_ready is True


async def test_correlate_pending_flow_is_not_calibration_ready(pool: asyncpg.Pool) -> None:
    """A flow that never advanced past PENDING has delivered no calibration."""
    from butlers.core.state import state_set

    triggered_at = datetime.now(UTC)
    map_id = await pool.fetchval(
        "INSERT INTO education.mind_maps (title) VALUES ('Python') RETURNING id"
    )
    await state_set(pool, f"flow:{map_id}", {"status": "pending", "mind_map_id": str(map_id)})

    _, calibration_ready = await edu._correlate_curriculum(pool, triggered_at)
    assert calibration_ready is False


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def test_latest_receipt_returns_newest(pool: asyncpg.Pool) -> None:
    """The status read follows the most recent request."""
    first = await edu._create_receipt(pool, "Python", None)
    await edu._settle_receipt(
        pool, str(first["id"]), status="failed", failure_reason=edu._FAILURE_TIMED_OUT
    )
    second = await edu._create_receipt(pool, "Rust", None)

    latest = await edu._latest_receipt(pool)
    assert str(latest["id"]) == str(second["id"])


async def test_latest_receipt_is_none_when_no_requests(pool: asyncpg.Pool) -> None:
    """No request ever made is a real, distinguishable state."""
    assert await edu._latest_receipt(pool) is None


async def test_get_receipt_unknown_id_returns_none(pool: asyncpg.Pool) -> None:
    """An unknown request ID must read as absent, not as a fabricated receipt."""
    assert await edu._get_receipt(pool, str(uuid.uuid4())) is None
