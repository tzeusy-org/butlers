"""Tests for the conversation-anchor wiring in route.execute (bu-bkthr).

Follow-up to bu-ep4ks.8 (PR #3582): every inbound thread that already
normalizes a ``source_thread_identity`` at ingest (Telegram, email, ...)
should get a durable ``dashboard_conversations`` anchor row on the TARGET
butler, via ``conversation_get_or_create_by_thread``, and the resulting
conversation id should be forwarded to ``spawner.trigger(conversation_id=...)``
so the spawner can attach a provider resume handle to it.

Each test boots a real daemon (``booted_daemon``) and calls route.execute over MCP;
the route inbox, lease, anchor upsert and spawner are the production ones, and
anchors are read back from ``public.dashboard_conversations`` on the clone. The
spawner's ``trigger`` is wrapped by a pass-through recorder, so the forwarded
``conversation_id`` is observed at the boundary it crosses. Two failures the
database will not produce on its own (an anchor lookup raising, and a recovery
takeover landing mid-anchor) wrap the anchor helper; nothing in the lifecycle is
patched.
"""

from __future__ import annotations

import asyncio
import itertools
import uuid
from pathlib import Path
from typing import Any

import pytest

from butlers.api import conversations
from butlers.testing.daemon import write_butler_config

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

_REQUEST_IDS = (f"018f6f4e-5b3b-7b2d-9c2f-{n:012x}" for n in itertools.count(0xC0))


def _route_request_context(
    *,
    source_channel: str = "telegram_bot",
    source_thread_identity: str | None = "12345:678",
    external_conversation_id: str | None = "telegram:12345",
) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "request_id": next(_REQUEST_IDS),
        "received_at": "2026-02-18T10:00:00Z",
        "source_channel": source_channel,
        "source_endpoint_identity": "switchboard",
        "source_sender_identity": "health",
    }
    if source_thread_identity is not None:
        ctx["source_thread_identity"] = source_thread_identity
    if external_conversation_id is not None:
        ctx["external_conversation_id"] = external_conversation_id
    return ctx


async def _boot(booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Boot a registered butler whose spawner records each trigger's kwargs."""
    booted = await booted_daemon(write_butler_config(tmp_path, "general"), registered=True)
    spawner = booted.daemon.spawner
    real_trigger = spawner.trigger
    booted.triggers = []

    async def recording_trigger(**kwargs: Any) -> Any:
        booted.triggers.append(kwargs)
        return await real_trigger(**kwargs)

    monkeypatch.setattr(spawner, "trigger", recording_trigger)
    return booted


async def _route(booted, prompt: str, **request: Any) -> dict[str, Any]:
    return await booted.call_tool(
        "route.execute",
        {
            "schema_version": "route.v1",
            "request_context": _route_request_context(**request),
            "input": {"prompt": prompt},
        },
    )


async def _drain(booted) -> None:
    """Wait for every background route task the daemon started."""
    await asyncio.gather(*tuple(booted.daemon._route_inbox_tasks))


async def _anchors(booted) -> list[dict[str, Any]]:
    rows = await booted.daemon.db.pool.fetch(
        """
        SELECT id, butler_name, title, source_channel, external_conversation_id,
               provider_session_id
        FROM public.dashboard_conversations
        ORDER BY created_at
        """
    )
    return [dict(row) for row in rows]


async def test_creates_conversation_anchor_and_forwards_conversation_id(
    booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    booted = await _boot(booted_daemon, tmp_path, monkeypatch)

    result = await _route(booted, "Hello from Telegram.")
    await _drain(booted)

    assert result["status"] == "accepted"
    (anchor,) = await _anchors(booted)
    assert anchor["butler_name"] == "general"
    assert anchor["source_channel"] == "telegram_bot"
    assert anchor["external_conversation_id"] == "telegram:12345"
    # Titled from the raw prompt, not the <routed_message>-wrapped runtime text.
    assert anchor["title"] == "Hello from Telegram."
    (trigger,) = booted.triggers
    assert trigger["conversation_id"] == anchor["id"]


async def test_dashboard_channel_resolves_existing_conversation_without_forking(
    booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """bu-0ynlk.5: a dashboard turn must resolve the existing conversation by
    id (its source_thread_identity IS that conversation's id, see
    build_dashboard_envelope) rather than upsert it as a novel thread key --
    the latter forks an invisible second "ghost" anchor every turn.
    """
    booted = await _boot(booted_daemon, tmp_path, monkeypatch)
    # Classification may route a dashboard turn to any butler, so the existing
    # conversation need not belong to the one that runs it.
    existing = await conversations.conversation_create(
        booted.daemon.db.pool, butler_name="switchboard", first_message="Groceries"
    )

    await _route(
        booted,
        "What did I spend on groceries?",
        source_channel="dashboard",
        source_thread_identity=str(existing["id"]),
    )
    await _drain(booted)

    assert [anchor["id"] for anchor in await _anchors(booted)] == [existing["id"]]
    (trigger,) = booted.triggers
    assert trigger["conversation_id"] == existing["id"]


async def test_two_routed_telegram_ingests_reuse_anchor_and_resume_provider_session(
    booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    booted = await _boot(booted_daemon, tmp_path, monkeypatch)
    booted.runtime.supports_resume = True

    for message_id, prompt in (("1", "first turn"), ("2", "second turn")):
        await _route(booted, prompt, source_thread_identity=f"12345:{message_id}")
        await _drain(booted)

    (anchor,) = await _anchors(booted)
    assert [trigger["conversation_id"] for trigger in booted.triggers] == [anchor["id"]] * 2
    # Turn 1 cold-starts and mints a session; turn 2 resumes it and mints the next.
    assert [call["resume_session_id"] for call in booted.runtime.invocations] == [
        None,
        "fake-session-1",
    ]
    assert anchor["provider_session_id"] == "fake-session-2"


async def test_skips_conversation_anchor_when_no_thread_identity(
    booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    booted = await _boot(booted_daemon, tmp_path, monkeypatch)

    await _route(
        booted,
        "Run health check.",
        source_channel="api",
        source_thread_identity=None,
        external_conversation_id=None,
    )
    await _drain(booted)

    assert await _anchors(booted) == []
    (trigger,) = booted.triggers
    assert trigger["conversation_id"] is None
    assert len(booted.runtime.invocations) == 1


async def test_conversation_anchor_lookup_failure_does_not_block_routing(
    booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    booted = await _boot(booted_daemon, tmp_path, monkeypatch)
    lookups: list[dict[str, Any]] = []

    async def failing_anchor(_pool: Any, **kwargs: Any) -> Any:
        lookups.append(kwargs)
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(conversations, "conversation_get_or_create_by_thread", failing_anchor)

    result = await _route(booted, "Hello from Telegram.")
    await _drain(booted)

    assert result["status"] == "accepted"
    assert len(lookups) == 1
    (trigger,) = booted.triggers
    assert trigger["conversation_id"] is None
    assert len(booted.runtime.invocations) == 1
    (state,) = await booted.daemon.db.pool.fetch("SELECT lifecycle_state FROM route_inbox")
    assert state["lifecycle_state"] == "processed"


async def test_reclaimed_lease_during_anchor_never_invokes_original_worker(
    booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recovery takeover while anchor I/O waits fences the original route worker."""
    booted = await _boot(booted_daemon, tmp_path, monkeypatch)
    real_anchor = conversations.conversation_get_or_create_by_thread
    takeover_claim = uuid.uuid4()

    async def reclaim_during_anchor(pool: Any, **kwargs: Any) -> Any:
        # A recovery worker replaces the claim while the original worker is still
        # waiting on optional anchor I/O inside its lease heartbeat.
        await pool.execute(
            "UPDATE route_inbox SET processing_claim_id = $1, processing_claimed_at = now()",
            takeover_claim,
        )
        return await real_anchor(pool, **kwargs)

    monkeypatch.setattr(
        conversations, "conversation_get_or_create_by_thread", reclaim_during_anchor
    )

    result = await _route(booted, "Do not spawn after the reclaimed anchor.")
    await _drain(booted)

    assert result["status"] == "accepted"
    assert len(await _anchors(booted)) == 1
    assert booted.triggers == []
    assert booted.runtime.invocations == []
    # The displaced worker wrote no terminal state: the row is the new owner's.
    (row,) = await booted.daemon.db.pool.fetch(
        "SELECT lifecycle_state, processing_claim_id FROM route_inbox"
    )
    assert (row["lifecycle_state"], row["processing_claim_id"]) == ("processing", takeover_claim)
