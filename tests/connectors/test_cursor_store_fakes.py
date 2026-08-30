"""Contract tests for cursor-store test support."""

from __future__ import annotations

from inspect import signature

import pytest

from butlers.connectors.cursor_store import save_cursor
from tests.connectors.cursor_store_fakes import RecordingSaveCursor


async def test_recording_save_cursor_accepts_current_keyword_only_parent_identity() -> None:
    pool = object()
    fake = RecordingSaveCursor()

    assert signature(fake) == signature(save_cursor)

    await fake(
        pool,
        "synthetic_connector",
        "synthetic:endpoint",
        "cursor-value",
        parent_endpoint_identity="synthetic:parent",
    )

    assert fake.calls == [
        {
            "pool": pool,
            "connector_type": "synthetic_connector",
            "endpoint_identity": "synthetic:endpoint",
            "cursor_value": "cursor-value",
            "parent_endpoint_identity": "synthetic:parent",
        }
    ]


async def test_recording_save_cursor_names_signature_mismatch() -> None:
    fake = RecordingSaveCursor()

    with pytest.raises(
        TypeError,
        match=(
            "^save_cursor fake signature mismatch: got an unexpected keyword argument "
            "'renamed_parent_endpoint_identity'$"
        ),
    ):
        await fake(
            object(),
            "synthetic_connector",
            "synthetic:endpoint",
            "cursor-value",
            parent_endpoint_identity="synthetic:parent",
            renamed_parent_endpoint_identity="synthetic:parent",
        )

    assert fake.calls == []
