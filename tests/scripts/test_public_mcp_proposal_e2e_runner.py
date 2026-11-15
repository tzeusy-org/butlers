"""Argument handling and matching of the live proposal e2e runner (never dials anything)."""

from __future__ import annotations

import argparse
import importlib.util
import uuid
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "k8s" / "public_mcp_proposal_e2e.py"
_spec = importlib.util.spec_from_file_location("public_mcp_proposal_e2e", _PATH)
assert _spec and _spec.loader
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)

A, B, C = (str(uuid.uuid4()) for _ in range(3))


def test_pair_parses_two_distinct_uuids() -> None:
    assert runner.parse_pair(f"{A}:{B}") == (A, B)
    for bad in (A, f"{A}:{A}", f"{A}:{B}:{C}", f"{A}:not-a-uuid"):
        with pytest.raises(argparse.ArgumentTypeError):
            runner.parse_pair(bad)


def test_propose_requires_all_three_paths(tmp_path: Path) -> None:
    base = ["propose", "--token-file", "t", "--state", "s", "--mcp-url", "https://x"]
    with pytest.raises(SystemExit):
        runner.build_parser().parse_args([*base, "--applied", f"{A}:{B}"])
    args = runner.build_parser().parse_args(
        [*base, "--applied", f"{A}:{B}", "--rejected", f"{B}:{C}", "--expired", f"{C}:{A}"]
    )
    assert args.applied == (A, B) and args.expired == (C, A)


def test_expected_states_cover_the_three_terminal_states() -> None:
    state = {"proposals": {p: {"id": f"id-{p}"} for p in runner.PATHS}}
    assert runner.expected_states(state) == {
        "id-applied": "applied",
        "id-rejected": "owner_rejected",
        "id-expired": "expired",
    }


def test_match_action_needs_exactly_one_pending_match() -> None:
    def action(i, subject, obj, status="pending", tool="relationship_assert_fact"):
        return {
            "id": i,
            "tool_name": tool,
            "status": status,
            "tool_args": {"subject": subject, "object": obj},
        }

    actions = [action("1", A, B), action("2", B, C), action("3", A, B, status="executed")]
    assert runner.match_action(actions, A, B) == "1"
    assert runner.match_action(actions, C, A) is None
    assert runner.match_action([action("1", A, B), action("4", A, B)], A, B) is None
    assert runner.match_action([action("5", A, B, tool="other")], A, B) is None


def test_propose_call_is_the_documented_tool_call() -> None:
    call = runner.propose_call(A, B, "k")
    assert call["params"]["name"] == "propose_fact"
    assert call["params"]["arguments"]["predicate"] == "works-at"
