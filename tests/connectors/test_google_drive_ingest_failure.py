"""Drive holds its page token at a failed ingest, then parks it (bu-dhloi1.224).

Before this:
- the non-batched path retried a failed page forever, and the retry re-read
  the page against a metadata cache that already held the new state, so a
  rename or move came back as ``modified`` and was dropped, and every attempt
  minted a new ``external_event_id``;
- the batched path saved the page token before the digest was sent, and a
  failed flush escaped the poll loop's ``finally`` and killed the account task.

Now the envelopes a page produced are kept, in order, and resent exactly
(same key, same event id) until delivered or, once their attempts are spent,
parked as replayable error rows. Only then are the page token and the
metadata cache committed. A removed file's key falls back to the change's own
time, which a re-read reproduces, rather than the poll's wall clock.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors.google_drive import (
    GDriveAccountConfig,
    GDriveAccountLoop,
    GDriveCursor,
    _make_idempotency_key,
)
from tests.connectors.filtered_event_fakes import FilteredEventsPool

pytestmark = pytest.mark.unit

_EMAIL = "user@example.com"


def _change(file_id: str, name: str) -> dict[str, Any]:
    return {
        "fileId": file_id,
        "time": "2026-11-09T08:00:00.000Z",
        "file": {
            "id": file_id,
            "name": name,
            "mimeType": "text/plain",
            "parents": ["p1"],
            "owners": [{"emailAddress": _EMAIL}],
            "modifiedTime": "2026-11-09T07:59:00.000Z",
        },
    }


def _account(
    *, batch_window_s: int = 0, fail: list[bool] | None = None
) -> tuple[GDriveAccountLoop, list[dict[str, Any]], AsyncMock]:
    config = GDriveAccountConfig(
        email=_EMAIL,
        client_id="client-id",
        client_secret="client-secret",
        refresh_token="refresh-token",
        switchboard_mcp_url="http://switchboard.test/mcp",
        batch_window_s=batch_window_s,
    )
    account = GDriveAccountLoop(_EMAIL, config)
    account._http_client = MagicMock()
    account._refresh_known_contacts = AsyncMock()  # type: ignore[method-assign]
    stored = [GDriveCursor(page_token="T1", last_updated_at=_now())]
    account._load_cursor = AsyncMock(side_effect=lambda: stored[-1])  # type: ignore[method-assign]
    account._save_cursor = AsyncMock(side_effect=stored.append)  # type: ignore[method-assign]
    account._save_metadata_cache_to_store = AsyncMock()  # type: ignore[method-assign]
    account._ingestion_policy = MagicMock(evaluate=MagicMock(return_value=None))
    account._metadata_cache = {"cached": MagicMock()}  # skip the cold-start load
    page = {"changes": [_change("f1", "a.txt"), _change("f2", "b.txt")], "newStartPageToken": "T2"}
    quiet = {"changes": [], "newStartPageToken": "T2"}
    account._fetch_changes_page = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda token: page if token == "T1" else quiet
    )
    sent: list[dict[str, Any]] = []
    outcomes = list(fail or [])

    async def call_tool(tool: str, envelope: dict[str, Any]) -> dict[str, Any]:
        sent.append(envelope)
        if outcomes and outcomes.pop(0):
            raise ConnectionError("switchboard unreachable")
        return {"status": "accepted"}

    account._mcp_client = MagicMock()
    account._mcp_client.call_tool = AsyncMock(side_effect=call_tool)
    account._mcp_client.aclose = AsyncMock()
    pool: Any = FilteredEventsPool()
    account._db_pool = pool
    return account, sent, account._fetch_changes_page


def _pool(account: GDriveAccountLoop) -> FilteredEventsPool:
    pool: Any = account._db_pool
    return pool


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC)


def _saved_tokens(account: GDriveAccountLoop) -> list[str]:
    return [c.args[0].page_token for c in account._save_cursor.await_args_list]  # type: ignore[attr-defined]


async def test_a_failed_change_holds_the_token_and_is_resent_exactly() -> None:
    account, sent, fetch = _account(fail=[False, True, False])

    await account._poll_once()

    assert [e["event"]["external_event_id"] for e in sent] == [
        sent[0]["event"]["external_event_id"],
        sent[1]["event"]["external_event_id"],
    ]
    assert _saved_tokens(account) == []  # held at T1
    account._save_metadata_cache_to_store.assert_not_awaited()  # type: ignore[attr-defined]

    await account._poll_once()

    assert sent[2] == sent[1]  # same key, same event id
    assert _saved_tokens(account) == ["T2"]
    account._save_metadata_cache_to_store.assert_awaited_once()  # type: ignore[attr-defined]
    # The page is never re-read against the already-updated cache: the next
    # read starts at the committed token.
    assert [c.args[0] for c in fetch.await_args_list] == ["T1", "T2"]


async def test_a_change_out_of_attempts_is_parked_and_the_token_moves_on() -> None:
    account, sent, _ = _account(fail=[False] + [True] * 10)
    attempts = account._ingest_retry.max_attempts

    for _ in range(attempts):
        await account._poll_once()

    failed = sent[1]
    assert sent[1:] == [failed] * attempts
    (row,) = _pool(account).written  # committed before the token moved
    assert row[8] == "error"
    assert row[9]["control"]["idempotency_key"] == failed["control"]["idempotency_key"]
    assert "schema_version" not in row[9]
    assert row[10] == "ConnectionError"  # the class, never the transport text
    assert _saved_tokens(account) == ["T2"]


async def test_a_park_that_is_not_written_keeps_the_token_held() -> None:
    account, sent, _ = _account(fail=[False] + [True] * 20)
    attempts = account._ingest_retry.max_attempts
    _pool(account).fail_writes = True

    for _ in range(attempts):
        await account._poll_once()

    assert _pool(account).written == []
    assert _saved_tokens(account) == []  # no durable copy, so the token stays at T1
    account._save_metadata_cache_to_store.assert_not_awaited()  # type: ignore[attr-defined]

    # Once the database is back, the change is parked and the token moves on.
    _pool(account).fail_writes = False
    for _ in range(attempts):
        await account._poll_once()

    assert len(_pool(account).written) == 1
    assert _saved_tokens(account) == ["T2"]


async def test_stop_parks_a_spent_digest_durably_then_commits_the_token() -> None:
    account, sent, _ = _account(batch_window_s=1, fail=[True] * 10)
    attempts = account._ingest_retry.max_attempts

    await account._poll_once()
    account._batch_window_opened_at = 0.0
    await account._maybe_flush_batch()  # attempt 1
    for _ in range(attempts - 2):
        await account._poll_once()  # resends before reading on
    assert _saved_tokens(account) == [] and _pool(account).written == []

    await account.stop()  # last attempt: parked durably, then committed

    digest = sent[0]
    assert sent == [digest] * attempts
    (row,) = _pool(account).written
    assert row[9]["control"]["idempotency_key"] == digest["control"]["idempotency_key"]
    assert _saved_tokens(account) == ["T2"]


async def test_stop_flushes_filtered_rows() -> None:
    account, _, _ = _account()
    account._filtered_event_buffer.record(
        external_message_id="f9",
        source_channel="google_drive",
        sender_identity=_EMAIL,
        subject_or_preview=None,
        filter_reason="policy",
        full_payload={},
    )

    await account.stop()

    assert len(_pool(account).written) == 1
    assert len(account._filtered_event_buffer) == 0


async def test_a_failed_batch_digest_is_kept_and_the_loop_survives() -> None:
    account, sent, _ = _account(batch_window_s=1, fail=[True, False])

    await account._poll_once()
    assert sent == [] and _saved_tokens(account) == []  # buffered, token uncommitted

    account._batch_window_opened_at = 0.0  # window elapsed
    await account._maybe_flush_batch()  # fails; must not raise out of the loop

    assert len(sent) == 1 and _saved_tokens(account) == []
    digest = sent[0]

    await account._poll_once()  # resends the same digest before reading on

    assert sent[1] == digest
    assert _saved_tokens(account) == ["T2"]


def test_a_removed_files_key_uses_the_change_time_not_the_poll_clock() -> None:
    account, _, _ = _account()
    account._metadata_cache = {}
    removed = {"fileId": "gone", "removed": True, "time": "2026-11-09T08:00:00.000Z"}

    first = account.process_change(removed, observed_at="2026-11-09T09:00:00+00:00")
    again = account.process_change(dict(removed), observed_at="2026-11-09T09:05:00+00:00")

    assert first is not None and again is not None
    expected = _make_idempotency_key(account.endpoint_identity, "gone", removed["time"])
    assert first["control"]["idempotency_key"] == again["control"]["idempotency_key"] == expected
