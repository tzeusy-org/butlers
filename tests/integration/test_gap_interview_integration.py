"""Real-Postgres integration tests for the gap-interview answer path (bu-whhll.12).

The answer application is DB-heavy: it is the *first real tenant* of the
``chronicler.overrides`` table (0 rows had ever been written before this
feature) and it mutates ``chronicler.routines.confidence`` under the
``[0, 1]`` CHECK constraint added by ``chronicler_018``. Mocked-pool tests
cannot prove the JSONB-codec round-trip, the CHECK-constraint clamp, or that
the override row actually lands in the corrected view (see "Mocked-pool vs
integration test gap": PR #2598 class, ~8h main-red from SQL that passed
mocked-pool tests only). These run the real migration chain against a
migrated Postgres container.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from butlers.chronicler.adapters.occupation import (
    EPISODE_TYPE_OCCUPATION,
)
from butlers.chronicler.adapters.occupation import (
    SOURCE_NAME as OCCUPATION_SOURCE_NAME,
)
from butlers.chronicler.contracts import INITIAL_SOURCES, seed_source_registry
from butlers.chronicler.gap_interview import (
    GapInterviewAnswer,
    apply_gap_interview_answer,
)
from butlers.chronicler.models import Confidence, Episode, Layer, OverrideTarget, Precision
from butlers.chronicler.storage import (
    adjust_routine_confidence,
    get_episode,
    get_routine,
    list_overrides_for,
    upsert_episode,
    upsert_mined_routine,
)
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_TZ = ZoneInfo("Asia/Singapore")
_LOCAL_DATE = "2026-07-02"


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["chronicler"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url, min_size=1, max_size=5, init=register_jsonb_codec
    )
    await p.execute("TRUNCATE TABLE routines CASCADE")
    await p.execute("TRUNCATE TABLE episodes, point_events, overrides CASCADE")
    await seed_source_registry(p, sources=INITIAL_SOURCES)
    yield p
    await p.close()


async def _make_occupation_episode(pool, *, routine_id) -> Episode:
    return await upsert_episode(
        pool,
        Episode(
            source_name=OCCUPATION_SOURCE_NAME,
            source_ref=f"chronicler.routines:{routine_id}:{_LOCAL_DATE}",
            episode_type=EPISODE_TYPE_OCCUPATION,
            start_at=datetime(2026, 7, 2, 9, tzinfo=_TZ).astimezone(UTC),
            end_at=datetime(2026, 7, 2, 18, tzinfo=_TZ).astimezone(UTC),
            precision=Precision.HOUR,
            title="Occupation (Weekdays)",
            payload={"routine_id": str(routine_id), "local_date": _LOCAL_DATE},
            layer=Layer.ACTIVITY,
            confidence=Confidence.LOW,
        ),
    )


async def _make_routine(pool, *, confidence: float = 0.5, support: int = 10):
    return await upsert_mined_routine(
        pool,
        dow_mask=0b0011111,  # Mon-Fri
        window_start_local=time(9, 30),
        window_end_local=time(19, 30),
        label="Weekdays",
        support_count=support,
        confidence=confidence,
        evidence_summary={"slots": [1, 2, 3]},  # JSONB dict, never json.dumps
    )


# ── confirm ─────────────────────────────────────────────────────────────────


async def test_confirm_writes_override_note_and_reinforces_routine(pool) -> None:
    routine = await _make_routine(pool, confidence=0.5, support=10)
    episode = await _make_occupation_episode(pool, routine_id=routine.id)

    result = await apply_gap_interview_answer(
        pool,
        answer=GapInterviewAnswer.CONFIRM,
        local_date=_LOCAL_DATE,
        occupation_episode_id=episode.id,
        routine_id=routine.id,
    )
    assert result["status"] == "applied"
    assert result["override_id"] is not None
    assert result["routine_updated"] is True

    # First real override row lands, targets the occupation episode, carries a note.
    overrides = await list_overrides_for(
        pool, target_kind=OverrideTarget.EPISODE, target_id=episode.id
    )
    assert len(overrides) == 1
    assert overrides[0].note is not None
    assert "confirmed" in overrides[0].note
    assert overrides[0].submitted_by == "owner:gap_interview"
    # Confirm keeps the block (no tombstone).
    assert overrides[0].corrected_tombstone_at is None

    # Routine reinforced: confidence up, support incremented.
    refreshed = await get_routine(pool, routine.id)
    assert refreshed.confidence == pytest.approx(0.60)
    assert refreshed.support_count == 11

    # The episode is still live (not tombstoned) in the corrected view.
    ep = await get_episode(pool, episode.id)
    assert ep is not None


# ── correct ─────────────────────────────────────────────────────────────────


async def test_correct_tombstones_block_and_decays_routine(pool) -> None:
    routine = await _make_routine(pool, confidence=0.5, support=10)
    episode = await _make_occupation_episode(pool, routine_id=routine.id)
    now = datetime(2026, 7, 3, 1, 0, tzinfo=UTC)

    result = await apply_gap_interview_answer(
        pool,
        answer=GapInterviewAnswer.CORRECT,
        local_date=_LOCAL_DATE,
        occupation_episode_id=episode.id,
        routine_id=routine.id,
        now=now,
    )
    assert result["routine_updated"] is True

    overrides = await list_overrides_for(
        pool, target_kind=OverrideTarget.EPISODE, target_id=episode.id
    )
    assert len(overrides) == 1
    # Correcting a wrong inference tombstones the block via the override.
    assert overrides[0].corrected_tombstone_at == now

    refreshed = await get_routine(pool, routine.id)
    assert refreshed.confidence == pytest.approx(0.35)
    assert refreshed.support_count == 10  # decay does not touch support

    # Tombstoned block drops out of the corrected view.
    ep = await get_episode(pool, episode.id)
    assert ep is None


async def test_correct_defaults_now_and_still_tombstones(pool) -> None:
    """A ``correct`` with no explicit ``now`` must still tombstone the block.

    Guards the silent-correction-failure foot-gun: if ``now`` defaulted to
    ``None`` the override would be written without ``corrected_tombstone_at`` and
    the "correction" would report ``applied`` while changing nothing.
    """
    routine = await _make_routine(pool, confidence=0.5, support=10)
    episode = await _make_occupation_episode(pool, routine_id=routine.id)

    result = await apply_gap_interview_answer(
        pool,
        answer=GapInterviewAnswer.CORRECT,
        local_date=_LOCAL_DATE,
        occupation_episode_id=episode.id,
        routine_id=routine.id,
        # now intentionally omitted
    )
    assert result["status"] == "applied"

    overrides = await list_overrides_for(
        pool, target_kind=OverrideTarget.EPISODE, target_id=episode.id
    )
    assert len(overrides) == 1
    assert overrides[0].corrected_tombstone_at is not None
    # And the block really drops out of the corrected view.
    assert await get_episode(pool, episode.id) is None


# ── dismiss ─────────────────────────────────────────────────────────────────


async def test_dismiss_writes_note_only_leaves_routine(pool) -> None:
    routine = await _make_routine(pool, confidence=0.5, support=10)
    episode = await _make_occupation_episode(pool, routine_id=routine.id)

    result = await apply_gap_interview_answer(
        pool,
        answer=GapInterviewAnswer.DISMISS,
        local_date=_LOCAL_DATE,
        occupation_episode_id=episode.id,
        routine_id=routine.id,
    )
    assert result["routine_updated"] is False

    overrides = await list_overrides_for(
        pool, target_kind=OverrideTarget.EPISODE, target_id=episode.id
    )
    assert len(overrides) == 1
    assert overrides[0].corrected_tombstone_at is None

    refreshed = await get_routine(pool, routine.id)
    assert refreshed.confidence == pytest.approx(0.50)  # unchanged
    assert refreshed.support_count == 10


# ── unaccounted-only (no occupation block to target) ────────────────────────


async def test_confirm_without_occupation_reinforces_routine_no_override(pool) -> None:
    routine = await _make_routine(pool, confidence=0.5, support=10)
    result = await apply_gap_interview_answer(
        pool,
        answer=GapInterviewAnswer.CONFIRM,
        local_date=_LOCAL_DATE,
        occupation_episode_id=None,
        routine_id=routine.id,
    )
    assert result["override_id"] is None
    assert result["routine_updated"] is True
    refreshed = await get_routine(pool, routine.id)
    assert refreshed.confidence == pytest.approx(0.60)


# ── confidence clamp under the [0,1] CHECK constraint ───────────────────────


async def test_reinforce_confidence_clamped_at_one(pool) -> None:
    routine = await _make_routine(pool, confidence=0.95, support=10)
    updated = await adjust_routine_confidence(
        pool, routine.id, confidence_delta=0.10, support_delta=1
    )
    assert updated is not None
    assert updated.confidence == pytest.approx(1.0)  # clamped, no CHECK violation


async def test_decay_confidence_clamped_at_zero(pool) -> None:
    routine = await _make_routine(pool, confidence=0.05, support=10)
    updated = await adjust_routine_confidence(pool, routine.id, confidence_delta=-0.5)
    assert updated is not None
    assert updated.confidence == pytest.approx(0.0)  # clamped, no CHECK violation


async def test_adjust_missing_routine_returns_none(pool) -> None:
    from uuid import uuid4

    updated = await adjust_routine_confidence(pool, uuid4(), confidence_delta=0.1)
    assert updated is None


# ── MCP tool round-trip (state dedupe + resolve), needs the core `state` table ──


@pytest.fixture(scope="module")
def migrated_db_url_full(postgres_container) -> str:
    """core + chronicler chains, so the KV ``state`` table exists for dedupe."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name() + "_full",
        chains=["core", "chronicler"],
    )


@pytest.fixture
async def full_pool(migrated_db_url_full: str):
    p = await asyncpg.create_pool(
        migrated_db_url_full, min_size=1, max_size=5, init=register_jsonb_codec
    )
    await p.execute("TRUNCATE TABLE routines CASCADE")
    await p.execute("TRUNCATE TABLE episodes, point_events, overrides CASCADE")
    await p.execute("TRUNCATE TABLE state")
    await seed_source_registry(p, sources=INITIAL_SOURCES)
    yield p
    await p.close()


def _chronicler_tools(pool):
    """Register the chronicler module tools against a capturing MCP, return them."""
    import importlib
    from unittest.mock import MagicMock

    from butlers.modules.registry import default_registry

    default_registry()  # ensures roster modules are imported as synthetic modules
    mod = importlib.import_module("butlers.modules._roster_chronicler")

    registered: dict = {}
    mcp = MagicMock()
    mcp.tool.side_effect = lambda: lambda fn: registered.__setitem__(fn.__name__, fn) or fn

    class _DB:
        def __init__(self, p):
            self.pool = p

    module = mod.ChroniclerModule()
    return module, registered, mcp, _DB(pool)


async def _register(pool):
    module, registered, mcp, db = _chronicler_tools(pool)
    await module.register_tools(mcp=mcp, config=None, db=db, butler_name="chronicler")
    return registered


async def _seed_pending(pool, *, interview_id, occupation_episode_id, routine_id) -> None:
    from butlers.core.state import state_set

    await state_set(
        pool,
        f"gap_interview:pending:{interview_id}",
        {
            "interview_id": interview_id,
            "local_date": _LOCAL_DATE,
            "occupation_episode_id": str(occupation_episode_id) if occupation_episode_id else None,
            "routine_id": str(routine_id) if routine_id else None,
            "answered": False,
        },
    )


async def test_ask_tool_dedupe_already_asked(full_pool) -> None:
    from butlers.core.state import state_set

    # A day already asked is short-circuited before any transport/token work.
    await state_set(full_pool, f"gap_interview:asked:{_LOCAL_DATE}", {"interview_id": _LOCAL_DATE})
    gap = (await _register(full_pool))["chronicler_gap_interview"]
    result = await gap(date_label=_LOCAL_DATE, timezone="Asia/Singapore")
    assert result == {"status": "already_asked", "date": _LOCAL_DATE}


async def test_ask_tool_not_configured_without_owner_chat(full_pool) -> None:
    # No owner telegram chat id / bot token in the test DB → cannot deliver;
    # the day is NOT marked asked, so it can retry once configured.
    routine = await _make_routine(full_pool, confidence=0.5, support=10)
    await _make_occupation_episode(full_pool, routine_id=routine.id)
    gap = (await _register(full_pool))["chronicler_gap_interview"]
    result = await gap(date_label=_LOCAL_DATE, timezone="Asia/Singapore")
    assert result["status"] == "not_configured"

    from butlers.core.state import state_get

    assert await state_get(full_pool, f"gap_interview:asked:{_LOCAL_DATE}") is None


async def test_resolve_tool_roundtrip(full_pool) -> None:
    routine = await _make_routine(full_pool, confidence=0.5, support=10)
    episode = await _make_occupation_episode(full_pool, routine_id=routine.id)
    await _seed_pending(
        full_pool,
        interview_id=_LOCAL_DATE,
        occupation_episode_id=episode.id,
        routine_id=routine.id,
    )
    resolve = (await _register(full_pool))["chronicler_resolve_gap_interview"]

    applied = await resolve(interview_id=_LOCAL_DATE, answer="confirm")
    assert applied["status"] == "applied"
    assert applied["override_id"] is not None
    refreshed = await get_routine(full_pool, routine.id)
    assert refreshed.confidence == pytest.approx(0.60)

    # Idempotent: a re-tap (telegram retries callbacks) is a no-op.
    again = await resolve(interview_id=_LOCAL_DATE, answer="confirm")
    assert again["status"] == "already_answered"


async def test_concurrent_resolve_applies_once(full_pool) -> None:
    """Two concurrent taps for the same interview must apply exactly once.

    The resolve path reads pending -> applies the answer -> marks answered.
    Before bu-6nwa1 those steps ran on the pool with no transaction or row
    lock, so two overlapping callbacks (telegram retries, a double-tap) could
    both read ``answered=False`` and double-apply: two override rows and a
    doubled routine nudge. The fix wraps the steps in one connection plus a
    transaction and locks the pending row ``FOR UPDATE`` -- the loser blocks
    until the winner commits, then sees ``already_answered``.
    """
    import asyncio

    routine = await _make_routine(full_pool, confidence=0.5, support=10)
    episode = await _make_occupation_episode(full_pool, routine_id=routine.id)
    await _seed_pending(
        full_pool,
        interview_id=_LOCAL_DATE,
        occupation_episode_id=episode.id,
        routine_id=routine.id,
    )
    resolve = (await _register(full_pool))["chronicler_resolve_gap_interview"]

    first, second = await asyncio.gather(
        resolve(interview_id=_LOCAL_DATE, answer="confirm"),
        resolve(interview_id=_LOCAL_DATE, answer="confirm"),
    )

    # Exactly one tap wins; the other is a graceful no-op (not an exception).
    assert sorted([first["status"], second["status"]]) == ["already_answered", "applied"]

    # Applied exactly once: a single override row and a single reinforce
    # (0.5 -> 0.60, support 10 -> 11), never the doubled 0.70 / two rows.
    overrides = await list_overrides_for(
        full_pool, target_kind=OverrideTarget.EPISODE, target_id=episode.id
    )
    assert len(overrides) == 1
    refreshed = await get_routine(full_pool, routine.id)
    assert refreshed.confidence == pytest.approx(0.60)
    assert refreshed.support_count == 11


async def test_resolve_unknown_interview_errors(full_pool) -> None:
    resolve = (await _register(full_pool))["chronicler_resolve_gap_interview"]
    result = await resolve(interview_id="2026-01-01", answer="confirm")
    assert result["status"] == "error"


# ── Inbound one-tap round-trip via the dashboard API (connector's route) ─────


def _build_chronicler_api(pool):
    from unittest.mock import MagicMock

    from butlers.api.app import create_app
    from butlers.api.db import DatabaseManager

    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    app = create_app(api_key="")
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "chronicler" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: db
            break
    else:  # pragma: no cover — defensive
        raise AssertionError("chronicler router not registered on the app")
    import httpx

    return httpx.ASGITransport(app=app)


async def test_resolve_endpoint_applies_answer_end_to_end(full_pool) -> None:
    import httpx

    routine = await _make_routine(full_pool, confidence=0.5, support=10)
    episode = await _make_occupation_episode(full_pool, routine_id=routine.id)
    await _seed_pending(
        full_pool,
        interview_id=_LOCAL_DATE,
        occupation_episode_id=episode.id,
        routine_id=routine.id,
    )

    transport = _build_chronicler_api(full_pool)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/chronicler/gap-interview/resolve",
            json={"interview_id": _LOCAL_DATE, "answer": "confirm"},
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "applied"

    # The route actually wrote the override + reinforced the routine.
    overrides = await list_overrides_for(
        full_pool, target_kind=OverrideTarget.EPISODE, target_id=episode.id
    )
    assert len(overrides) == 1
    refreshed = await get_routine(full_pool, routine.id)
    assert refreshed.confidence == pytest.approx(0.60)


async def test_resolve_endpoint_unknown_interview_is_graceful(full_pool) -> None:
    import httpx

    transport = _build_chronicler_api(full_pool)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/chronicler/gap-interview/resolve",
            json={"interview_id": "2026-01-01", "answer": "confirm"},
        )
    # Always HTTP 200 with a status the connector can turn into a toast.
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"] == "unknown_or_expired_interview"
