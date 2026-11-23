"""An owner `never` through the dashboard route also pauses a watch notice's watch (dark)."""

from __future__ import annotations

from uuid import UUID

import pytest

from butlers.roster_imports import roster_module

router = roster_module("switchboard", "api/router")

pytestmark = pytest.mark.unit

INSIGHT = UUID("5b6f0d52-0000-4000-8000-000000000001")


@pytest.fixture
def recorded(monkeypatch):
    calls: list[tuple[str, dict]] = []

    async def feedback(pool, **kwargs):
        calls.append(("feedback", kwargs))
        return {
            "status": "recorded",
            "verdict": kwargs["verdict"],
            "insight_id": kwargs["insight_id"],
            "snooze_until": None,
        }

    async def pause(pool, insight_id, *, origin, **_):
        calls.append(("pause", {"insight_id": insight_id, "origin": origin}))
        return {"status": "paused"}

    monkeypatch.setattr(
        "butlers.tools.switchboard.insight.broker.record_insight_feedback", feedback
    )
    monkeypatch.setattr("butlers.watches.never.pause_on_never", pause)
    return calls


async def test_never_pauses_after_the_feedback_commits(recorded):
    response = await router._record_feedback(pool=object(), insight_id=INSIGHT, verdict="never")
    assert response.data.verdict == "never"
    assert [name for name, _ in recorded] == ["feedback", "pause"]
    assert recorded[1][1] == {"insight_id": str(INSIGHT), "origin": "owner"}


@pytest.mark.parametrize("verdict", ["useful", "not_now"])
async def test_other_verdicts_leave_the_watch_alone(recorded, verdict):
    kwargs = {"snooze_until": None}
    await router._record_feedback(pool=object(), insight_id=INSIGHT, verdict=verdict, **kwargs)
    assert [name for name, _ in recorded] == ["feedback"]


async def test_a_pause_failure_never_fails_the_reply(recorded, monkeypatch):
    async def boom(pool, insight_id, **_):
        raise ConnectionError("store down")

    monkeypatch.setattr("butlers.watches.never.pause_on_never", boom)
    response = await router._record_feedback(pool=object(), insight_id=INSIGHT, verdict="never")
    assert response.data.status == "recorded"


async def test_a_refused_feedback_pauses_nothing(monkeypatch):
    async def refused(pool, **kwargs):
        return {"status": "error", "reason": "insight not found"}

    paused = []

    async def pause(*args, **kwargs):
        paused.append(args)

    monkeypatch.setattr("butlers.tools.switchboard.insight.broker.record_insight_feedback", refused)
    monkeypatch.setattr("butlers.watches.never.pause_on_never", pause)
    with pytest.raises(router.HTTPException):
        await router._record_feedback(pool=object(), insight_id=INSIGHT, verdict="never")
    assert paused == []
