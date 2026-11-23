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

_EVIDENCE_CALLS = {"commit_envelope", "persist_location_point"}


def _ingest_sites(module) -> list[tuple[str, int, list[int]]]:
    tree = ast.parse(Path(inspect.getsourcefile(module) or "").read_text())
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
            if name == "call_tool" and node.args and getattr(node.args[0], "value", "") == "ingest":
                ingest.append(node.lineno)
            if name in _EVIDENCE_CALLS:
                evidence.append(node.lineno)
        if ingest:
            found.append((fn.name, min(ingest), evidence))
    return found


@pytest.mark.parametrize("module", [owntracks, google_health], ids=["owntracks", "google_health"])
def test_each_ingest_site_commits_evidence_before_it_submits(module) -> None:
    sites = _ingest_sites(module)
    assert sites
    for name, first_ingest, evidence in sites:
        assert evidence and min(evidence) < first_ingest, name


def test_owntracks_replay_hook_is_among_the_guarded_sites() -> None:
    assert "_submit_envelope" in {name for name, _, _ in _ingest_sites(owntracks)}
