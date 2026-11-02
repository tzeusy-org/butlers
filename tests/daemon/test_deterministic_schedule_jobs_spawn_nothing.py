"""Converted deterministic schedules spawn no session (bu-idc5mx.4, AC1).

``upcoming-dates-check``, ``relationship-maintenance`` and ``chronicler_gap_interview`` are
job-mode rows in their butler.toml. Each is dispatched through
``background.dispatch_scheduled_task`` exactly as the scheduler does, with its real job body
running down to the data reads and the owner send, which are stubbed. The spawner is a sentinel
that records every attribute touched: the dispatcher may only probe ``codex_auth_authority``
(answered with AttributeError), and nothing may reach ``trigger`` or any other runtime surface.
"""

from __future__ import annotations

import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.background import dispatch_scheduled_task

pytestmark = pytest.mark.unit

_ROSTER = Path(__file__).resolve().parents[2] / "roster"


class _SpawnerSentinel:
    """A spawner that refuses every attribute, recording which ones were asked for."""

    def __init__(self) -> None:
        object.__setattr__(self, "touched", [])

    def __getattr__(self, name: str) -> Any:
        self.touched.append(name)
        raise AttributeError(f"spawner.{name} touched on a deterministic job path")


def _schedule_row(butler: str, name: str) -> dict[str, Any]:
    config = tomllib.loads((_ROSTER / butler / "butler.toml").read_text())
    rows = {row["name"]: row for row in config["butler"]["schedule"]}
    return rows[name]


def _stub_upcoming_dates(monkeypatch) -> AsyncMock:
    from butlers.tools.relationship import reminders

    monkeypatch.setattr(
        reminders,
        "upcoming_dates",
        AsyncMock(
            return_value=[
                {
                    "contact_name": "Alice",
                    "label": "Birthday",
                    "upcoming_date": datetime.now(UTC).date().isoformat(),
                }
            ]
        ),
    )
    send = AsyncMock(return_value="delivered")
    monkeypatch.setattr(reminders, "send_owner_telegram", send)
    return send


def _stub_maintenance(monkeypatch) -> AsyncMock:
    from butlers.tools.relationship import reminders

    monkeypatch.setattr(
        reminders,
        "contacts_overdue_evaluation",
        AsyncMock(
            return_value={"contacts": [], "cadence_available": True, "unmeasurable_count": 0}
        ),
    )
    send = AsyncMock(return_value="delivered")
    monkeypatch.setattr(reminders, "send_owner_telegram", send)
    return send


def _stub_gap_interview(monkeypatch) -> AsyncMock:
    from butlers.chronicler import gap_interview_job

    monkeypatch.setattr(
        "butlers.core.general_settings.resolve_general_timezone",
        AsyncMock(return_value="UTC"),
    )
    ask = AsyncMock(return_value={"status": "no_gap"})
    monkeypatch.setattr(gap_interview_job, "ask_gap_interview", ask)
    return ask


@pytest.mark.parametrize(
    ("butler", "schedule", "stub"),
    [
        ("relationship", "upcoming-dates-check", _stub_upcoming_dates),
        ("relationship", "relationship-maintenance", _stub_maintenance),
        ("chronicler", "chronicler_gap_interview", _stub_gap_interview),
    ],
    ids=["upcoming-dates-check", "relationship-maintenance", "chronicler_gap_interview"],
)
async def test_converted_schedule_runs_its_job_without_touching_the_spawner(
    monkeypatch, butler, schedule, stub
):
    row = _schedule_row(butler, schedule)
    assert row.get("dispatch_mode") == "job"
    assert "prompt" not in row
    effect = stub(monkeypatch)
    spawner = _SpawnerSentinel()

    await dispatch_scheduled_task(
        butler_name=butler,
        pool=MagicMock(),
        spawner=spawner,
        trigger_source=f"schedule:{schedule}",
        prompt=row.get("prompt"),
        job_name=row["job_name"],
        job_args=row.get("job_args"),
        switchboard_client=MagicMock(),
    )

    effect.assert_awaited_once()
    assert set(spawner.touched) <= {"codex_auth_authority"}


@pytest.mark.parametrize("outcome", ["return", "error", "cancel"])
async def test_daemon_gap_job_borrows_an_isolated_store_until_dispatch_finishes(
    monkeypatch, outcome
):
    import asyncio
    from types import SimpleNamespace

    from butlers.core.tool_call_capture import (
        get_current_credential_store,
        reset_current_credential_store,
        set_current_credential_store,
    )
    from butlers.credential_store import CredentialStore
    from butlers.daemon import ButlerDaemon

    stores = []
    daemons = []
    for index in range(2):
        local = MagicMock()
        local_conn = AsyncMock()
        local_conn.fetchrow.return_value = None
        local.acquire.return_value.__aenter__ = AsyncMock(return_value=local_conn)
        shared = MagicMock()
        shared_conn = AsyncMock()
        shared_conn.fetchrow.return_value = {"secret_value": f"synthetic-token-{index}"}
        shared.acquire.return_value.__aenter__ = AsyncMock(return_value=shared_conn)
        store = CredentialStore(local, fallback_pools=[shared])
        assert store.has_system_global_authority is False
        stores.append(store)
        daemon = ButlerDaemon(butler_name="chronicler", db=SimpleNamespace(pool=local))
        daemon.config = SimpleNamespace(name="chronicler")
        daemon.spawner = _SpawnerSentinel()
        daemon._credential_store = store
        daemons.append(daemon)

    arrived = [asyncio.Event(), asyncio.Event()]
    release = asyncio.Event()

    async def episodes(*args, **kwargs):
        current = get_current_credential_store()
        index = next(i for i, store in enumerate(stores) if store is current)
        arrived[index].set()
        await release.wait()
        assert get_current_credential_store() is stores[index]
        if index == 0 and outcome == "error":
            raise RuntimeError("synthetic-data-read-failure")
        return []

    monkeypatch.setattr("butlers.chronicler.storage.list_episodes", episodes)
    monkeypatch.setattr("butlers.core.state.state_get", AsyncMock(return_value=None))
    monkeypatch.setattr("butlers.core.state.state_set", AsyncMock())
    monkeypatch.setattr(
        "butlers.core.general_settings.resolve_general_timezone", AsyncMock(return_value="UTC")
    )
    monkeypatch.setattr(
        "butlers.core.temporal.delivery_db.get_delivery_preferences", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "butlers.chronicler.gap_interview_telegram.resolve_owner_chat_id",
        AsyncMock(return_value="synthetic-owner-chat"),
    )
    http = MagicMock()
    response = MagicMock(status_code=200)
    response.json.return_value = {"result": {"message_id": 9}}
    http.post = AsyncMock(return_value=response)
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=http)
    client.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr("httpx.AsyncClient", MagicMock(return_value=client))
    parent_store = object()
    token = set_current_credential_store(parent_store)
    restored = []

    async def invoke(daemon):
        try:
            return await daemon._dispatch_scheduled_task(
                trigger_source="schedule:chronicler_gap_interview",
                job_name="chronicler_gap_interview",
            )
        finally:
            restored.append(get_current_credential_store() is parent_store)

    tasks = [asyncio.create_task(invoke(daemon)) for daemon in daemons]
    try:
        async with asyncio.timeout(5):
            await asyncio.gather(*(event.wait() for event in arrived))
            if outcome == "cancel":
                tasks[0].cancel()
            release.set()
            results = await asyncio.gather(*tasks, return_exceptions=True)
        assert results[1]["status"] == "asked"
        if outcome == "return":
            assert results[0]["status"] == "asked"
        elif outcome == "error":
            assert isinstance(results[0], RuntimeError)
        else:
            assert isinstance(results[0], asyncio.CancelledError)
        assert restored == [True, True]
        assert get_current_credential_store() is parent_store
        assert http.post.await_count == (2 if outcome == "return" else 1)
        observed_urls = {call.args[0] for call in http.post.await_args_list}
        assert any(url.endswith("synthetic-token-1/sendMessage") for url in observed_urls)
        assert all("synthetic-token-" in url for url in observed_urls)
        assert all(set(daemon.spawner.touched) <= {"codex_auth_authority"} for daemon in daemons)
        assert all(
            not store.pool.close.called and not store.shared_pool.close.called for store in stores
        )
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        reset_current_credential_store(token)
