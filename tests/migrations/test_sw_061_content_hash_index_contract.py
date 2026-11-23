"""Docker-free pin: the ingest content-hash lookup has a matching index migration.

bu-dhloi1.178. The lookup ran unindexed (14s on dev); the behavioural proof against a real
planner is test_sw_061_message_inbox_content_hash_index_migration.py.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from butlers.roster_imports import roster_module

_MIGRATIONS = Path(__file__).resolve().parents[2] / "roster/switchboard/migrations"
_EXPR = "(request_context ->> 'content_hash_key')"


def test_content_hash_lookup_expression_has_an_index_migration() -> None:
    ingest = roster_module("switchboard", "tools/ingestion/ingest")
    lookup = inspect.getsource(ingest._find_request_by_content_hash)
    assert "request_context ->> 'content_hash_key' = $1" in lookup

    source = (_MIGRATIONS / "061_message_inbox_content_hash_index.py").read_text()
    assert 'down_revision = "sw_060"' in source
    assert 'revision = "sw_061"' in source
    # Same expression, leading column, so the lookup plus ORDER BY received_at DESC is served.
    assert re.search(
        r"CREATE INDEX IF NOT EXISTS ix_message_inbox_content_hash_received_at\s+"
        r"ON message_inbox \(" + re.escape(_EXPR) + r", received_at DESC\)",
        source,
    )
    assert "DROP INDEX IF EXISTS ix_message_inbox_content_hash_received_at" in source
