"""Decision intent applier: at most one tracker close per intent, honest states.

bu-ckkpz.3, REQ-owner-decision-desk-002. Real PostgreSQL (migrated through
``sw_042``) for the claim and state transitions; an in-memory tracker stands in
for ``bd`` and counts every close.
"""

from __future__ import annotations

import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

from tests.decision_desk_helpers import migrated_switchboard_db, switchboard_pool

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import beads_decision_applier as applier_mod  # noqa: E402

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


class FakeTracker:
    def __init__(self) -> None:
        self.issues: dict[str, dict[str, Any]] = {}
        self.closes: list[tuple[str, str]] = []
        self.down = False
        self.unreadable: set[str] = set()
        self.fail_close = False
        self.close_lands_despite_failure = False

    def add(self, bead_id: str = "bu-test1", **overrides: Any) -> dict[str, Any]:
        issue = {
            "id": bead_id,
            "status": "open",
            "issue_type": "task",
            "labels": ["decision"],
            "metadata": {"decision": {"options": ["Ship it", "Hold"], "default": "Hold"}},
            **overrides,
        }
        self.issues[bead_id] = issue
        return issue

    def show(self, bead_id: str) -> dict[str, Any] | None:
        if self.down:
            raise applier_mod.TrackerUnavailable
        if bead_id in self.unreadable:
            raise applier_mod.BeadUnreadable
        issue = self.issues.get(bead_id)
        return dict(issue) if issue is not None else None

    def close(self, bead_id: str, reason: str) -> None:
        if self.down:
            raise applier_mod.CloseFailed
        if self.fail_close and not self.close_lands_despite_failure:
            raise applier_mod.CloseFailed
        self.closes.append((bead_id, reason))
        self.issues[bead_id].update(status="closed", close_reason=reason)
        if self.fail_close:
            raise applier_mod.CloseFailed


@pytest.fixture(scope="module")
def desk_db_url(postgres_container) -> str:
    return migrated_switchboard_db(postgres_container)


@pytest.fixture
async def pool(desk_db_url):
    pool = await switchboard_pool(desk_db_url)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
def tracker() -> FakeTracker:
    return FakeTracker()


async def _intent(pool, bead_id="bu-test1", option="Hold", status="pending", **cols) -> uuid.UUID:
    fields = {"bead_id": bead_id, "option": option, "source": "telegram", "actor": "owner@telegram"}
    fields.update(status=status, **cols)
    names = ", ".join(fields)
    params = ", ".join(f"${i}" for i in range(1, len(fields) + 1))
    return await pool.fetchval(
        f"INSERT INTO switchboard.decision_intents ({names}) VALUES ({params}) RETURNING id",
        *fields.values(),
    )


async def _state(pool, intent_id) -> dict[str, Any]:
    row = await pool.fetchrow(
        "SELECT status, attempts, failure_reason, last_error, finished_at IS NOT NULL AS done "
        "FROM switchboard.decision_intents WHERE id = $1",
        intent_id,
    )
    return dict(row)


async def _run(pool, tracker, batch=20) -> applier_mod.Applier:
    async with pool.acquire() as conn:
        applier = applier_mod.Applier(conn, tracker)
        await applier.run(batch)
    return applier


async def test_pending_intent_closes_its_bead_once(pool, tracker) -> None:
    tracker.add()
    intent_id = await _intent(pool)

    await _run(pool, tracker)
    second = await _run(pool, tracker)

    assert tracker.closes == [
        ("bu-test1", f"Decision: Hold (decision-intent {intent_id}, via telegram)")
    ]
    assert await _state(pool, intent_id) == {
        "status": "applied",
        "attempts": 1,
        "failure_reason": None,
        "last_error": None,
        "done": True,
    }
    assert sum(second.outcomes.values()) == 0


async def test_crash_after_close_reconciles_to_applied_without_reclosing(pool, tracker) -> None:
    intent_id = await _intent(pool, status="applying")
    tracker.add(
        status="closed", close_reason=f"Decision: Hold (decision-intent {intent_id}, via x)"
    )

    await _run(pool, tracker)

    assert tracker.closes == []
    assert (await _state(pool, intent_id))["status"] == "applied"


async def test_crash_before_close_returns_to_pending_and_applies(pool, tracker) -> None:
    tracker.add()
    intent_id = await _intent(pool, status="applying")

    applier = await _run(pool, tracker)

    assert applier.outcomes["pending:reconciled"] == 1
    assert len(tracker.closes) == 1
    assert (await _state(pool, intent_id))["status"] == "applied"


async def test_stranded_intent_on_bead_closed_elsewhere_fails(pool, tracker) -> None:
    tracker.add(status="closed", close_reason="Resolved in chat")
    intent_id = await _intent(pool, status="applying")

    await _run(pool, tracker)

    assert tracker.closes == []
    state = await _state(pool, intent_id)
    assert (state["status"], state["failure_reason"]) == ("failed", "bead_closed_elsewhere")


@pytest.mark.parametrize(
    ("issue", "reason"),
    [
        (None, "bead_not_found"),
        ({"status": "closed", "close_reason": "elsewhere"}, "bead_not_open"),
        ({"labels": ["test"]}, "not_a_decision"),
        ({"issue_type": "epic"}, "not_a_decision"),
        (
            {"metadata": {"decision": {"options": ["Ship it"], "default": "Ship it"}}},
            "option_not_offered",
        ),
        ({"metadata": {}}, "option_not_offered"),
    ],
)
async def test_drifted_bead_fails_without_modification(pool, tracker, issue, reason) -> None:
    if issue is not None:
        tracker.add(**issue)
    intent_id = await _intent(pool)

    await _run(pool, tracker)

    assert tracker.closes == []
    state = await _state(pool, intent_id)
    assert (state["status"], state["failure_reason"], state["done"]) == ("failed", reason, True)


async def test_tracker_outage_releases_the_batch_and_stops(pool, tracker) -> None:
    tracker.add()
    tracker.add("bu-test2")
    first = await _intent(pool)
    second = await _intent(pool, "bu-test2")
    tracker.down = True

    applier = await _run(pool, tracker)

    assert applier.tracker_unavailable is True
    for intent_id in (first, second):
        state = await _state(pool, intent_id)
        assert (state["status"], state["last_error"], state["attempts"]) == (
            "pending",
            "tracker_unavailable",
            0,
        )

    tracker.down = False
    await _run(pool, tracker)
    assert [bead for bead, _ in tracker.closes] == ["bu-test1", "bu-test2"]


async def test_outage_during_reconcile_leaves_stranded_rows_applying(pool, tracker) -> None:
    stranded = await _intent(pool, status="applying")
    waiting = await _intent(pool, "bu-test2")
    tracker.down = True

    applier = await _run(pool, tracker)

    assert applier.tracker_unavailable is True
    assert (await _state(pool, stranded))["status"] == "applying"
    assert (await _state(pool, waiting))["status"] == "pending"


async def test_close_failure_retries_then_becomes_terminal(pool, tracker) -> None:
    tracker.add()
    tracker.fail_close = True
    intent_id = await _intent(pool)

    for expected_attempts in (1, 2):
        await _run(pool, tracker)
        state = await _state(pool, intent_id)
        assert (state["status"], state["last_error"], state["attempts"]) == (
            "pending",
            "bd_close_failed",
            expected_attempts,
        )
    await _run(pool, tracker)

    state = await _state(pool, intent_id)
    assert (state["status"], state["failure_reason"], state["attempts"]) == (
        "failed",
        "bd_close_failed",
        3,
    )


async def test_close_that_landed_despite_an_error_is_applied(pool, tracker) -> None:
    tracker.add()
    tracker.fail_close = True
    tracker.close_lands_despite_failure = True
    intent_id = await _intent(pool)

    await _run(pool, tracker)

    assert len(tracker.closes) == 1
    assert (await _state(pool, intent_id))["status"] == "applied"


async def test_batch_is_bounded_and_oldest_first(pool, tracker) -> None:
    ids = []
    for n in range(3):
        tracker.add(f"bu-{n}")
        ids.append(await _intent(pool, f"bu-{n}"))

    await _run(pool, tracker, batch=2)

    assert [bead for bead, _ in tracker.closes] == ["bu-0", "bu-1"]
    assert (await _state(pool, ids[2]))["status"] == "pending"


async def test_main_exits_nonzero_without_a_database(monkeypatch) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setenv("POSTGRES_PORT", "1")
    assert await applier_mod.main() == 1


async def test_unreadable_bead_fails_alone_and_does_not_stall_the_queue(pool, tracker) -> None:
    tracker.add("bu-ok")
    stuck = await _intent(pool, "bu-weird")
    fine = await _intent(pool, "bu-ok")
    tracker.unreadable.add("bu-weird")

    applier = await _run(pool, tracker)

    assert applier.tracker_unavailable is False
    state = await _state(pool, stuck)
    assert (state["status"], state["failure_reason"]) == ("failed", "bead_unreadable")
    assert (await _state(pool, fine))["status"] == "applied"


async def test_unreadable_stranded_intent_stays_applying_without_blocking(pool, tracker) -> None:
    stranded = await _intent(pool, "bu-weird", status="applying")
    tracker.add("bu-ok")
    fine = await _intent(pool, "bu-ok")
    tracker.unreadable.add("bu-weird")

    await _run(pool, tracker)

    assert (await _state(pool, stranded))["status"] == "applying"
    assert (await _state(pool, fine))["status"] == "applied"
