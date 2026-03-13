"""Tests for live-listener checkpoint persistence.

Covers task 6.4, 6.5 from the connector-live-listener openspec:
- Checkpoint JSON serialization/deserialization
- save_voice_checkpoint calls cursor_store with correct args
- load_voice_checkpoint reads cursor_store and populates VoiceCheckpoint
- Fail-open: DB errors return empty checkpoint, not an exception
- Checkpoint keyed by (provider="live-listener", endpoint_identity=...)
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.live_listener.checkpoint import (
    VoiceCheckpoint,
    load_voice_checkpoint,
    save_voice_checkpoint,
)
from butlers.connectors.live_listener.envelope import endpoint_identity

pytestmark = pytest.mark.unit

_DEVICE = "kitchen"
_EP_ID = endpoint_identity(_DEVICE)
_CONNECTOR_TYPE = "live-listener"

# Patch target: the module-level imports in checkpoint.py
_PATCH_LOAD = "butlers.connectors.live_listener.checkpoint.load_cursor"
_PATCH_SAVE = "butlers.connectors.live_listener.checkpoint.save_cursor"


# ---------------------------------------------------------------------------
# VoiceCheckpoint serialisation
# ---------------------------------------------------------------------------


def test_checkpoint_to_json_all_fields() -> None:
    ckpt = VoiceCheckpoint(
        last_utterance_ts=1_700_000_000_000,
        session_id="voice:kitchen:1700000000000",
        session_last_ts=1_700_000_000_000,
    )
    data = json.loads(ckpt.to_json())
    assert data["last_utterance_ts"] == 1_700_000_000_000
    assert data["session_id"] == "voice:kitchen:1700000000000"
    assert data["session_last_ts"] == 1_700_000_000_000


def test_checkpoint_to_json_none_fields() -> None:
    ckpt = VoiceCheckpoint(last_utterance_ts=None, session_id=None, session_last_ts=None)
    data = json.loads(ckpt.to_json())
    assert data["last_utterance_ts"] is None
    assert data["session_id"] is None
    assert data["session_last_ts"] is None


def test_checkpoint_roundtrip() -> None:
    ckpt = VoiceCheckpoint(
        last_utterance_ts=42000,
        session_id="voice:kitchen:42000",
        session_last_ts=42000,
    )
    restored = VoiceCheckpoint.from_json(ckpt.to_json())
    assert restored.last_utterance_ts == ckpt.last_utterance_ts
    assert restored.session_id == ckpt.session_id
    assert restored.session_last_ts == ckpt.session_last_ts


def test_checkpoint_empty() -> None:
    ckpt = VoiceCheckpoint.empty()
    assert ckpt.last_utterance_ts is None
    assert ckpt.session_id is None
    assert ckpt.session_last_ts is None


def test_checkpoint_from_json_partial_fields() -> None:
    """from_json should tolerate missing optional fields (default to None)."""
    raw = json.dumps({"last_utterance_ts": 100})
    ckpt = VoiceCheckpoint.from_json(raw)
    assert ckpt.last_utterance_ts == 100
    assert ckpt.session_id is None
    assert ckpt.session_last_ts is None


# ---------------------------------------------------------------------------
# load_voice_checkpoint — success path
# ---------------------------------------------------------------------------


async def test_load_checkpoint_returns_saved_state() -> None:
    """load_voice_checkpoint should parse the cursor value returned by load_cursor."""
    persisted = VoiceCheckpoint(
        last_utterance_ts=5000,
        session_id="voice:kitchen:5000",
        session_last_ts=5000,
    )
    mock_pool = MagicMock()

    with patch(_PATCH_LOAD, new=AsyncMock(return_value=persisted.to_json())) as mock_load:
        result = await load_voice_checkpoint(mock_pool, _DEVICE)

    mock_load.assert_awaited_once_with(mock_pool, _CONNECTOR_TYPE, _EP_ID)
    assert result.last_utterance_ts == 5000
    assert result.session_id == "voice:kitchen:5000"
    assert result.session_last_ts == 5000


async def test_load_checkpoint_returns_empty_when_no_row() -> None:
    """When no checkpoint row exists, load_voice_checkpoint returns empty."""
    mock_pool = MagicMock()

    with patch(_PATCH_LOAD, new=AsyncMock(return_value=None)):
        result = await load_voice_checkpoint(mock_pool, _DEVICE)

    assert result.last_utterance_ts is None
    assert result.session_id is None
    assert result.session_last_ts is None


async def test_load_checkpoint_fail_open_on_db_error() -> None:
    """DB errors during checkpoint load should return empty checkpoint (fail-open)."""
    mock_pool = MagicMock()

    with patch(_PATCH_LOAD, side_effect=Exception("connection refused")):
        result = await load_voice_checkpoint(mock_pool, _DEVICE)

    # Should not raise — returns empty checkpoint
    assert result.last_utterance_ts is None
    assert result.session_id is None


# ---------------------------------------------------------------------------
# save_voice_checkpoint — success path
# ---------------------------------------------------------------------------


async def test_save_checkpoint_calls_save_cursor_with_correct_args() -> None:
    """save_voice_checkpoint should call save_cursor with correct connector_type and endpoint."""
    mock_pool = MagicMock()
    ts = 1_700_000_000_000
    sid = f"voice:{_DEVICE}:{ts}"

    with patch(_PATCH_SAVE, new=AsyncMock()) as mock_save:
        await save_voice_checkpoint(
            pool=mock_pool,
            device_name=_DEVICE,
            last_utterance_ts=ts,
            session_id=sid,
            session_last_ts=ts,
        )

    mock_save.assert_awaited_once()
    call_args = mock_save.call_args
    # Positional: pool, connector_type, endpoint_identity, json_value
    assert call_args.args[0] is mock_pool
    assert call_args.args[1] == _CONNECTOR_TYPE
    assert call_args.args[2] == _EP_ID

    # Verify the JSON payload
    saved_json = call_args.args[3]
    data = json.loads(saved_json)
    assert data["last_utterance_ts"] == ts
    assert data["session_id"] == sid
    assert data["session_last_ts"] == ts


async def test_save_checkpoint_with_none_session() -> None:
    """save_voice_checkpoint should handle None session_id and session_last_ts."""
    mock_pool = MagicMock()

    with patch(_PATCH_SAVE, new=AsyncMock()) as mock_save:
        await save_voice_checkpoint(
            pool=mock_pool,
            device_name=_DEVICE,
            last_utterance_ts=1000,
            session_id=None,
            session_last_ts=None,
        )

    saved_json = mock_save.call_args.args[3]
    data = json.loads(saved_json)
    assert data["session_id"] is None
    assert data["session_last_ts"] is None


async def test_save_checkpoint_swallows_db_errors() -> None:
    """DB errors during checkpoint save should be logged and not re-raised."""
    mock_pool = MagicMock()

    with patch(_PATCH_SAVE, side_effect=Exception("db error")):
        # Should not raise
        await save_voice_checkpoint(
            pool=mock_pool,
            device_name=_DEVICE,
            last_utterance_ts=1000,
            session_id=None,
            session_last_ts=None,
        )


# ---------------------------------------------------------------------------
# Endpoint identity used as checkpoint key
# ---------------------------------------------------------------------------


async def test_checkpoint_keyed_by_endpoint_identity() -> None:
    """The checkpoint key must be the live-listener mic endpoint identity."""
    expected_ep_id = f"live-listener:mic:{_DEVICE}"
    mock_pool = MagicMock()

    with patch(_PATCH_SAVE, new=AsyncMock()) as mock_save:
        await save_voice_checkpoint(
            pool=mock_pool,
            device_name=_DEVICE,
            last_utterance_ts=1000,
            session_id=None,
            session_last_ts=None,
        )

    assert mock_save.call_args.args[2] == expected_ep_id


async def test_checkpoint_different_mics_use_different_keys() -> None:
    """Each microphone must produce a distinct endpoint identity for its checkpoint."""
    mock_pool = MagicMock()

    captured_keys: list[str] = []

    async def capture_save(pool, connector_type, endpoint, value):  # noqa: ANN001
        captured_keys.append(endpoint)

    with patch(_PATCH_SAVE, side_effect=capture_save):
        await save_voice_checkpoint(mock_pool, "kitchen", 1000, None, None)
        await save_voice_checkpoint(mock_pool, "bedroom", 1000, None, None)

    assert len(captured_keys) == 2
    assert captured_keys[0] != captured_keys[1]
    assert "kitchen" in captured_keys[0]
    assert "bedroom" in captured_keys[1]
