"""The QA publication hold is checked explicitly at the cooldown gates (bu-g7p1qv).

``get_recent_attempt`` reports only the real attempt. A lineage held by an unresolved
publication outlives the cooldown window, so triage, the healing dispatcher, the QA
dispatcher and ``get_healing_status`` each ask ``get_publication_hold`` beside it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.healing.dispatch import dispatch_healing
from butlers.core.healing.tracking import get_recent_attempt
from butlers.core.qa.dispatch import QaDispatchConfig, dispatch_qa_investigation
from butlers.core.qa.triage import triage_findings
from butlers.modules.self_healing import SelfHealingModule
from tests.core.healing.test_dispatch import _make_config, _make_fp, _make_pool_all_pass
from tests.core.qa.test_dispatch import _make_finding, _make_pool, _make_triaged

pytestmark = pytest.mark.unit

_FP = "f" * 64


def _attempt_row() -> dict:
    now = datetime.now(UTC)
    return {"id": uuid.uuid4(), "fingerprint": _FP, "status": "failed", "closed_at": now}


# -- get_recent_attempt reports the real attempt --------------------------------------------


async def test_get_recent_attempt_returns_the_real_attempt_and_never_asks_about_the_hold():
    pool = MagicMock()
    row = _attempt_row()
    pool.fetchrow = AsyncMock(return_value=row)
    pool.fetchval = AsyncMock(return_value=True)  # a hold exists

    recent = await get_recent_attempt(pool, _FP, 60)

    assert recent is not None and recent["id"] == row["id"]
    assert "publication_held" not in recent
    pool.fetchval.assert_not_awaited()


async def test_get_recent_attempt_is_none_when_no_attempt_closed_in_the_window():
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=True)

    assert await get_recent_attempt(pool, _FP, 0) is None


# -- triage ---------------------------------------------------------------------------------


async def _triage(*, held: bool, recent: dict | None):
    pool = MagicMock()
    get_recent = AsyncMock(return_value=recent)
    with (
        patch(
            "butlers.core.qa.triage.insert_finding",
            new_callable=AsyncMock,
            return_value=uuid.uuid4(),
        ),
        patch(
            "butlers.core.qa.triage.get_active_attempt", new_callable=AsyncMock, return_value=None
        ),
        patch("butlers.core.qa.triage.is_dismissed", new_callable=AsyncMock, return_value=False),
        patch(
            "butlers.core.qa.triage.get_publication_hold",
            new_callable=AsyncMock,
            return_value=held,
        ),
        patch("butlers.core.qa.triage.get_recent_attempt", get_recent),
    ):
        result = await triage_findings(pool, uuid.uuid4(), [_make_finding_for_triage()])
    return result, get_recent


def _make_finding_for_triage():
    return _make_finding(severity=1)


async def test_triage_holds_a_held_lineage_even_with_no_recent_attempt():
    result, get_recent = await _triage(held=True, recent=None)

    assert result.all_findings[0].dedup_reason == "publication_held"
    assert result.novel_findings == []
    get_recent.assert_not_awaited()


async def test_triage_reports_a_held_lineage_as_held_not_cooldown_with_a_real_attempt():
    result, _ = await _triage(held=True, recent=_attempt_row())

    assert result.all_findings[0].dedup_reason == "publication_held"


async def test_triage_cooldown_is_still_a_cooldown_without_a_hold():
    result, _ = await _triage(held=False, recent=_attempt_row())

    assert result.all_findings[0].dedup_reason == "cooldown"


# -- dispatch gates -------------------------------------------------------------------------


async def test_qa_dispatch_refuses_a_held_lineage_at_the_cooldown_gate():
    attempt_id = uuid.uuid4()
    get_recent = AsyncMock(return_value=None)
    with (
        patch(
            "butlers.core.qa.dispatch.create_or_join_attempt",
            new_callable=AsyncMock,
            return_value=(attempt_id, True),
        ),
        patch("butlers.core.qa.dispatch.update_finding_attempt", new_callable=AsyncMock),
        patch(
            "butlers.core.qa.dispatch.get_publication_hold",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("butlers.core.qa.dispatch.get_recent_attempt", get_recent),
        patch("butlers.core.qa.dispatch.delete_orphaned_attempt", new_callable=AsyncMock) as drop,
    ):
        result = await dispatch_qa_investigation(
            pool=_make_pool(),
            triaged_finding=_make_triaged(_make_finding(severity=1)),
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            gh_token=None,
        )

    assert result.accepted is False and result.reason == "cooldown"
    drop.assert_awaited_once()
    get_recent.assert_not_awaited()


async def test_healing_dispatch_refuses_a_held_lineage_at_the_cooldown_gate(tmp_path: Path):
    attempt_id = uuid.uuid4()
    get_recent = AsyncMock(return_value=None)
    with (
        patch(
            "butlers.core.healing.dispatch.session_set_healing_fingerprint", new_callable=AsyncMock
        ),
        patch(
            "butlers.core.healing.dispatch.create_or_join_attempt",
            new_callable=AsyncMock,
            return_value=(attempt_id, True),
        ),
        patch(
            "butlers.core.healing.dispatch.get_publication_hold",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("butlers.core.healing.dispatch.get_recent_attempt", get_recent),
        patch(
            "butlers.core.healing.dispatch.delete_orphaned_attempt",
            new_callable=AsyncMock,
            return_value=True,
        ) as drop,
        patch(
            "butlers.core.healing.dispatch.create_dispatch_event", new_callable=AsyncMock
        ) as event,
    ):
        result = await dispatch_healing(
            pool=_make_pool_all_pass(),
            butler_name="email",
            session_id=uuid.uuid4(),
            fingerprint_input=_make_fp(),
            config=_make_config(),
            repo_root=tmp_path,
            spawner=MagicMock(),
        )

    assert result.accepted is False and result.reason == "cooldown"
    assert drop.call_args[0][1] == attempt_id
    assert event.call_args.kwargs.get("decision") == "cooldown"
    get_recent.assert_not_awaited()


# -- get_healing_status ---------------------------------------------------------------------


def _module(pool) -> SelfHealingModule:
    module = SelfHealingModule()
    module._pool = pool
    module._butler_name = "email"
    return module


async def test_healing_status_shows_the_real_attempt_and_the_hold_separately():
    row = _attempt_row()
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=row)
    pool.fetchval = AsyncMock(return_value=True)

    status = await _module(pool)._handle_get_healing_status(_FP)

    assert status["publication_held"] is True
    (attempt,) = status["attempts"]
    assert attempt["id"] == str(row["id"])
    assert attempt["status"] == "failed"
    assert "publication_held" not in attempt


async def test_healing_status_reports_a_hold_with_no_attempt_and_no_hold_when_clear():
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=True)
    held = await _module(pool)._handle_get_healing_status(_FP)
    pool.fetchval = AsyncMock(return_value=False)
    clear = await _module(pool)._handle_get_healing_status(_FP)

    assert held["attempts"] == [] and held["publication_held"] is True
    assert clear["attempts"] == [] and clear["publication_held"] is False
