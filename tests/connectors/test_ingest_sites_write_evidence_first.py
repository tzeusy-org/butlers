"""Every function that calls the ingest tool writes its evidence first (bu-uhbhzq.4).

Spotify has its own stricter guard (test_spotify_single_submit_path). These connectors keep
a live path and a replay path; each must commit evidence before it submits, so a new replay
hook cannot send an envelope nobody recorded.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from butlers.connectors import google_health, owntracks
from butlers.connectors.evidence_journal import EvidenceNotCommitted
from butlers.testing.source_guard import parse_file

_EVIDENCE_CALLS = {"commit_envelope", "persist_location_point", "require_committed"}


def _ingest_sites(module) -> list[tuple[str, int, list[int]]]:
    tree = parse_file(Path(inspect.getsourcefile(module) or ""))
    found = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        ingest, evidence = [], []
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
            if name == "submit_ingest" or (
                name == "call_tool" and node.args and getattr(node.args[0], "value", "") == "ingest"
            ):
                ingest.append(node.lineno)
            if name in _EVIDENCE_CALLS:
                evidence.append(node.lineno)
        if ingest:
            found.append((fn.name, min(ingest), evidence))
    return found


@pytest.mark.parametrize(
    ("module", "expected_sites"),
    [
        (owntracks, {"_process_webhook_event", "_submit_envelope"}),
        (google_health, {"_submit_envelope", "_submit_to_ingest_direct"}),
    ],
    ids=["owntracks", "google_health"],
)
def test_each_ingest_site_commits_evidence_before_it_submits(
    module, expected_sites: set[str]
) -> None:
    sites = _ingest_sites(module)
    assert expected_sites <= {name for name, _, _ in sites}
    for name, first_ingest, evidence in sites:
        assert evidence and min(evidence) < first_ingest, name


def test_owntracks_replay_hook_is_among_the_guarded_sites() -> None:
    assert "_submit_envelope" in {name for name, _, _ in _ingest_sites(owntracks)}


async def test_a_health_replay_verifies_the_journal_row_and_writes_nothing() -> None:
    from unittest.mock import AsyncMock

    from tests.connectors.test_google_health_connector import _make_connector, _privacy_envelope

    connector = _make_connector()
    connector._shared_pool = AsyncMock()
    connector._mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})
    envelope = _privacy_envelope()
    envelope["control"]["idempotency_key"] = "synthetic-key"

    connector._shared_pool.fetchval.return_value = 1
    await connector._submit_to_ingest_direct(envelope)
    query = connector._shared_pool.fetchval.await_args.args[0]
    assert "SELECT" in query and "INSERT" not in query
    connector._mcp_client.call_tool.assert_awaited_once()

    for refused in (None, "no-key"):
        connector._mcp_client.call_tool.reset_mock()
        connector._shared_pool.fetchval.return_value = None
        if refused == "no-key":
            envelope["control"].pop("idempotency_key")
        with pytest.raises(EvidenceNotCommitted):
            await connector._submit_to_ingest_direct(envelope)
        connector._mcp_client.call_tool.assert_not_awaited()
