"""bu-q7vx1q.42: WhatsApp and Drive drop sites mark known-contact drops like Gmail.

Drives the real drop paths (``_flush_chat_buffer`` and ``process_change``) with real policy
evaluators and a real known-contact snapshot; only the network edges are patched.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.gmail_policy import _normalize_email
from butlers.connectors.google_drive import GDriveAccountConfig, GDriveAccountLoop
from butlers.connectors.known_contact_snapshot import (
    PriorityContactSnapshotSource,
    normalize_phone_digits,
    phone_is_known,
)
from butlers.connectors.whatsapp_user_client import (
    ChatBuffer,
    WhatsAppUserClientConnector,
    WhatsAppUserClientConnectorConfig,
)
from butlers.core.known_contact_state import classification_certain

_PHONE = "6598150802"
_KNOWN_JID = f"{_PHONE}@s.whatsapp.net"
_STRANGER_JID = "6591112222@s.whatsapp.net"
_GROUP_JID = "120363000000@g.us"
_KNOWN_EMAIL = "alice@known.example"
_OWNER = "user@example.com"


def _rule(rule_type: str, condition: dict[str, Any], action: str = "block") -> dict[str, Any]:
    return {
        "id": "rule-1",
        "rule_type": rule_type,
        "condition": condition,
        "action": action,
        "priority": 10,
        "name": None,
        "created_at": "2026-02-01T00:00:00Z",
        "created_by": "dashboard",
        "promoted_from_suggestion_id": None,
    }


async def _loaded_source(
    value: str, *, predicate: str, normalize: Any, loaded: bool = True
) -> PriorityContactSnapshotSource:
    source = PriorityContactSnapshotSource(predicate=predicate, normalize=normalize)  # type: ignore[arg-type]
    if loaded:
        source._db_pool = AsyncMock()
        source._db_pool.fetch.return_value = [{"value": value}]
        await source.get_snapshot()
    return source


# ---------------------------------------------------------------------------
# WhatsApp
# ---------------------------------------------------------------------------


@pytest.fixture
def wa() -> WhatsAppUserClientConnector:
    config = WhatsAppUserClientConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        provider="whatsapp",
        channel="whatsapp_user_client",
        endpoint_identity="whatsapp:+12025551234",
    )
    return WhatsAppUserClientConnector(config, cursor_pool=MagicMock())


async def _flush_wa(
    wa: WhatsAppUserClientConnector, chat_jid: str, rule: dict[str, Any], *, loaded: bool
) -> AsyncMock:
    wa._known_contacts = await _loaded_source(
        "+65 9815 0802", predicate="has-phone", normalize=normalize_phone_digits, loaded=loaded
    )
    wa._ingestion_policy._last_loaded_at = time.monotonic()
    wa._ingestion_policy._rules = [rule]
    buf = ChatBuffer(chat_jid=chat_jid)
    buf.messages = [
        {
            "message_id": "m1",
            "chat_jid": chat_jid,
            "sender_jid": chat_jid,
            "type": "text",
            "content": {"text": "hi"},
        }
    ]
    wa._chat_buffers[chat_jid] = buf
    submit = AsyncMock()
    with (
        patch.object(wa, "_refresh_lid_map", AsyncMock()),
        patch.object(wa, "_submit_to_ingest", submit),
        patch.object(wa, "_flush_and_drain", AsyncMock()),
        patch.object(wa, "_publish_known_contacts", AsyncMock()),
        patch.object(wa, "_save_checkpoint", AsyncMock()),
    ):
        await wa._flush_chat_buffer(chat_jid)
    return submit


def _only_row(wa: WhatsAppUserClientConnector) -> dict[str, Any]:
    (row,) = wa._filtered_event_buffer._rows
    return row[9]["drop_context"]


@pytest.mark.parametrize(
    ("chat_jid", "marked", "state"),
    [
        (_KNOWN_JID, True, "loaded"),
        (_STRANGER_JID, False, "loaded"),
        (_GROUP_JID, False, "not_applicable"),
    ],
)
async def test_whatsapp_block_drop_is_marked_by_chat_kind(
    wa: WhatsAppUserClientConnector, chat_jid: str, marked: bool, state: str
) -> None:
    rule = _rule("chat_id", {"chat_id": chat_jid})
    submit = await _flush_wa(wa, chat_jid, rule, loaded=True)

    assert submit.await_count == 0
    context = _only_row(wa)
    assert context.get("important_dropped", False) is marked
    assert context["classification"]["state"] == state
    assert classification_certain(context["classification"])


async def test_whatsapp_drop_with_unloaded_snapshot_is_unknown_not_certain(
    wa: WhatsAppUserClientConnector,
) -> None:
    await _flush_wa(wa, _KNOWN_JID, _rule("chat_id", {"chat_id": _KNOWN_JID}), loaded=False)

    context = _only_row(wa)
    assert context["classification"]["state"] == "unloaded"
    assert "important_dropped" not in context
    assert not classification_certain(context["classification"])


async def test_whatsapp_known_contact_passes_an_excepted_block_rule(
    wa: WhatsAppUserClientConnector,
) -> None:
    rule = _rule("chat_id", {"chat_id": _KNOWN_JID, "except_known_contacts": True})
    submit = await _flush_wa(wa, _KNOWN_JID, rule, loaded=True)

    assert submit.await_count == 1
    assert wa._filtered_event_buffer._rows == []


async def test_whatsapp_stranger_and_unloaded_stay_blocked_by_excepted_rule(
    wa: WhatsAppUserClientConnector,
) -> None:
    rule = _rule("chat_id", {"chat_id": _KNOWN_JID, "except_known_contacts": True})
    submit = await _flush_wa(wa, _KNOWN_JID, rule, loaded=False)

    assert submit.await_count == 0


def test_phone_matching_is_digit_based_with_bounded_suffix() -> None:
    stored = frozenset({"91153887", "6598150802"})
    assert phone_is_known("6598150802", stored)
    assert not phone_is_known("6591112222", stored)
    # A short stored local number matches no longer than the delta cap allows.
    assert phone_is_known("6591153887", frozenset({"91153887"}))
    assert not phone_is_known("60391153887", frozenset({"91153887"}))


# ---------------------------------------------------------------------------
# Drive
# ---------------------------------------------------------------------------


@pytest.fixture
def drive() -> GDriveAccountLoop:
    config = GDriveAccountConfig(
        email=_OWNER,
        client_id="id",
        client_secret="secret",
        refresh_token="token",
        switchboard_mcp_url="http://localhost:41100/sse",
    )
    return GDriveAccountLoop(_OWNER, config)


def _change(sharer: str | None, *, owners: list[str] | None = None) -> dict[str, Any]:
    file_data: dict[str, Any] = {
        "id": "f1",
        "name": "plan.pdf",
        "mimeType": "application/pdf",
        "owners": [{"emailAddress": e} for e in (owners or [_OWNER])],
        "modifiedTime": "2026-03-26T10:00:00Z",
    }
    if sharer:
        file_data["sharingUser"] = {"emailAddress": sharer}
    return {"fileId": "f1", "file": file_data}


async def _drive_change(
    drive: GDriveAccountLoop, change: dict[str, Any], rule: Any, *, loaded: bool
) -> dict[str, Any] | None:
    drive._known_contacts = await _loaded_source(
        _KNOWN_EMAIL, predicate="has-email", normalize=_normalize_email, loaded=loaded
    )
    drive._ingestion_policy._last_loaded_at = time.monotonic()
    drive._ingestion_policy._rules = rule if isinstance(rule, list) else [rule]
    return drive.process_change(change)


def _all_senders_rule() -> list[dict[str, Any]]:
    """Block rules covering the owner, the known sharer and a stranger sharer."""
    return [
        {**_rule("sender_domain", {"domain": d, "match": "exact"}), "id": d, "priority": i}
        for i, d in enumerate(["example.com", "known.example", "other.example"])
    ]


@pytest.mark.parametrize(
    ("sharer", "marked", "state"),
    [
        (_KNOWN_EMAIL, True, "loaded"),
        ("stranger@other.example", False, "loaded"),
        (None, False, "not_applicable"),
    ],
)
async def test_drive_block_drop_is_marked_by_sharer(
    drive: GDriveAccountLoop, sharer: str | None, marked: bool, state: str
) -> None:
    rule = _all_senders_rule()
    result = await _drive_change(drive, _change(sharer), rule, loaded=True)

    assert result is None
    (row,) = drive._filtered_event_buffer._rows
    context = row[9]["drop_context"]
    assert context.get("important_dropped", False) is marked
    assert context["classification"]["state"] == state


async def test_drive_drop_with_unloaded_snapshot_is_unknown(drive: GDriveAccountLoop) -> None:
    await _drive_change(drive, _change(_KNOWN_EMAIL), _all_senders_rule(), loaded=False)

    (row,) = drive._filtered_event_buffer._rows
    context = row[9]["drop_context"]
    assert context["classification"]["state"] == "unloaded"
    assert "important_dropped" not in context


async def test_drive_known_sharer_passes_an_excepted_block_rule(drive: GDriveAccountLoop) -> None:
    rule = _rule(
        "sender_domain",
        {"domain": "known.example", "match": "exact", "except_known_contacts": True},
    )
    result = await _drive_change(drive, _change(_KNOWN_EMAIL), rule, loaded=True)

    assert result is not None
    assert drive._filtered_event_buffer._rows == []


async def test_drive_sharer_falls_back_to_first_non_self_owner(drive: GDriveAccountLoop) -> None:
    assert drive._file_sharer(_change(None, owners=[_OWNER, _KNOWN_EMAIL])["file"]) == _KNOWN_EMAIL
    assert drive._file_sharer(_change(None)["file"]) is None


# ---------------------------------------------------------------------------
# Aggregate availability across marking connectors
# ---------------------------------------------------------------------------


def _registry_row(connector_type: str, endpoint: str, capabilities: Any = None) -> dict[str, Any]:
    return {
        "connector_type": connector_type,
        "endpoint_identity": endpoint,
        "operational_role": "runtime_instance",
        "archived_at": None,
        "deleted_at": None,
        "instance_id": "00000000-0000-0000-0000-000000000001",
        "last_heartbeat_at": None,
        "capabilities": capabilities,
    }


@pytest.mark.parametrize(
    ("rows", "available"),
    [
        # A WhatsApp runtime without classification evidence is never all clear.
        ([_registry_row("whatsapp_user_client", "whatsapp:+1")], False),
        # Drive's per-account rows carry no evidence of their own; the manager row does.
        ([_registry_row("google_drive", "google_drive:user:a@example.com")], True),
        ([_registry_row("google_drive", "google_drive:manager:process")], False),
        ([], True),
    ],
)
async def test_current_availability_requires_evidence_from_every_marking_runtime(
    rows: list[dict[str, Any]], available: bool
) -> None:
    from datetime import UTC, datetime

    from butlers.core.ingestion_events import ingestion_current_classification_available

    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=rows)

    assert (
        await ingestion_current_classification_available(pool, now=datetime.now(UTC)) is available
    )
