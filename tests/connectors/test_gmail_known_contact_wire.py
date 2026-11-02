"""bu-q7vx1q.88.3 review corrections: the known-contact classification rides the wire.

Only a *loaded* snapshot may classify a sender. A stale or failed snapshot that still holds
contacts must read as unknown (``None``), distinguishable from a loaded "not a contact" (``False``),
on both the live path and the backfill signal. The envelope carries ``control.known_contact`` only
when a classification exists, so the Switchboard's global rule pass can honor the same option.
"""

from __future__ import annotations

import base64
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.gmail import GmailConnectorConfig, GmailConnectorRuntime

_KNOWN = "alice@known.example"
_STRANGER = "bob@stranger.example"


@pytest.fixture
def runtime() -> GmailConnectorRuntime:
    config = GmailConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        connector_provider="gmail",
        connector_channel="email",
        connector_endpoint_identity="gmail:user:test@example.com",
        connector_max_inflight=4,
        gmail_client_id="test-client-id",
        gmail_client_secret="test-client-secret",
        gmail_refresh_token="test-refresh-token",
        gmail_watch_renew_interval_s=3600,
        gmail_poll_interval_s=5,
    )
    return GmailConnectorRuntime(config, cursor_pool=MagicMock())


def _message(from_addr: str) -> dict[str, Any]:
    return {
        "id": "msg1",
        "threadId": "thread1",
        "internalDate": "1708000000000",
        "labelIds": ["INBOX"],
        "payload": {
            "headers": [
                {"name": "From", "value": from_addr},
                {"name": "Subject", "value": "Hello"},
                {"name": "Message-ID", "value": "<m1@example.com>"},
            ],
            "mimeType": "text/plain",
            "body": {"data": base64.urlsafe_b64encode(b"body").decode()},
        },
    }


async def _load(runtime: GmailConnectorRuntime, state: str) -> None:
    """Put the connector's snapshot in ``state`` while it still holds ``_KNOWN``."""
    evaluator = runtime._gmail_policy_evaluator
    evaluator._db_pool = AsyncMock()
    evaluator._db_pool.fetch.return_value = [{"value": _KNOWN}]
    await evaluator.get_snapshot()
    if state == "stale":
        # The refresh fails after the cache aged out: contacts are retained, state is not loaded.
        evaluator._db_pool.fetch.side_effect = RuntimeError("db down")
        evaluator._cache_loaded_at = time.monotonic() - 10_000
        await evaluator.get_snapshot()
    snapshot = evaluator.peek_snapshot()
    assert (snapshot.state == "loaded") == (state == "loaded")
    assert _KNOWN in snapshot.contacts


@pytest.mark.parametrize(
    ("state", "sender", "expected"),
    [
        ("loaded", _KNOWN, True),
        ("loaded", _STRANGER, False),
        ("stale", _KNOWN, None),
        ("stale", _STRANGER, None),
    ],
)
async def test_the_signal_is_unknown_unless_the_snapshot_is_loaded(
    runtime, state: str, sender: str, expected: bool | None
) -> None:
    await _load(runtime, state)

    assert runtime._known_contact_signal(sender) is expected


async def test_an_unloaded_snapshot_is_unknown(runtime) -> None:
    assert runtime._known_contact_signal(_KNOWN) is None


async def _submitted_control(runtime: GmailConnectorRuntime, sender: str) -> dict[str, Any]:
    submit = AsyncMock()
    with (
        patch.object(
            runtime, "_fetch_message", new_callable=AsyncMock, return_value=_message(sender)
        ),
        patch.object(runtime, "_submit_to_ingest_api", submit),
    ):
        await runtime._ingest_single_message("msg1")
    assert submit.await_count == 1
    return submit.await_args.args[0]["control"]


@pytest.mark.parametrize(("sender", "expected"), [(_KNOWN, True), (_STRANGER, False)])
async def test_a_loaded_classification_is_carried_on_the_wire(
    runtime, sender: str, expected: bool
) -> None:
    await _load(runtime, "loaded")

    assert (await _submitted_control(runtime, sender))["known_contact"] is expected


async def test_a_stale_snapshot_sends_no_classification(runtime) -> None:
    await _load(runtime, "stale")

    assert "known_contact" not in await _submitted_control(runtime, _KNOWN)
