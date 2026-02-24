"""Tests for Telegram module update extraction helpers.

The pipeline integration (process_update, set_pipeline) has been removed —
ingestion is now owned by TelegramBotConnector. This file retains the
helper-function tests for _extract_text and _extract_chat_id.
"""

from __future__ import annotations

import pytest

from butlers.modules.telegram import _extract_chat_id, _extract_text

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _extract_text helper
# ---------------------------------------------------------------------------


class TestExtractText:
    """Test the _extract_text helper for various Telegram update formats."""

    def test_regular_message(self):
        update = {"update_id": 1, "message": {"text": "hello", "chat": {"id": 123}}}
        assert _extract_text(update) == "hello"

    def test_edited_message(self):
        update = {"update_id": 2, "edited_message": {"text": "edited", "chat": {"id": 123}}}
        assert _extract_text(update) == "edited"

    def test_channel_post(self):
        update = {"update_id": 3, "channel_post": {"text": "channel msg", "chat": {"id": -100}}}
        assert _extract_text(update) == "channel msg"

    def test_no_text(self):
        update = {"update_id": 4, "message": {"photo": [{}], "chat": {"id": 123}}}
        assert _extract_text(update) is None

    def test_empty_update(self):
        update = {"update_id": 5}
        assert _extract_text(update) is None

    def test_priority_message_over_edited(self):
        """Regular message takes priority if both are present (unlikely but safe)."""
        update = {
            "update_id": 6,
            "message": {"text": "original", "chat": {"id": 1}},
            "edited_message": {"text": "edited", "chat": {"id": 1}},
        }
        assert _extract_text(update) == "original"


# ---------------------------------------------------------------------------
# _extract_chat_id helper
# ---------------------------------------------------------------------------


class TestExtractChatId:
    """Test the _extract_chat_id helper."""

    def test_regular_message(self):
        update = {"update_id": 1, "message": {"text": "hi", "chat": {"id": 12345}}}
        assert _extract_chat_id(update) == "12345"

    def test_no_chat(self):
        update = {"update_id": 2, "message": {"text": "hi"}}
        assert _extract_chat_id(update) is None

    def test_empty_update(self):
        update = {"update_id": 3}
        assert _extract_chat_id(update) is None
