"""Argument handling and matching of the live proposal e2e runner (never dials anything)."""

from __future__ import annotations

import argparse
import importlib.util
import json
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


def test_approve_body_echoes_the_typed_review_displayed_call() -> None:
    """REQ-dashboard-approvals-904: the displayed call lives in the typed review."""
    shown = {"tool_name": "relationship_assert_fact", "args_digest": "d"}
    detail = {"call_preview": None, "external_proposal_review": {"displayed_call": shown}}
    assert runner.approve_body(detail) == {"displayed_call": shown}
    # A generic preview is never what an external proposal approves from.
    assert runner.approve_body({"call_preview": {"displayed_call": shown}}) is None
    # Approval unavailable (say, a merged entity): nothing to echo, never an empty body.
    unavailable = {"external_proposal_review": {"displayed_call": None}}
    assert runner.approve_body(unavailable) is None
    assert runner.approve_body({}) is None
    unavailable["external_proposal_review"]["approve_unavailable_reason"] = "merged"
    assert runner.approve_unavailable_reason(unavailable) == "merged"


def test_decide_posts_displayed_call_and_steps_are_independent(monkeypatch, tmp_path) -> None:
    shown = {"tool_name": "relationship_assert_fact", "args_digest": "d"}
    listing = [
        {
            "id": "1",
            "tool_name": "relationship_assert_fact",
            "status": "pending",
            "tool_args": {"subject": A, "object": B},
        },
        {
            "id": "2",
            "tool_name": "relationship_assert_fact",
            "status": "pending",
            "tool_args": {"subject": B, "object": C},
        },
    ]
    sent = []

    def fake(url, token_file, body=None):
        sent.append((url, body))
        if "/actions?" in url:
            return {"data": listing}
        if url.endswith("/api/approvals/1"):
            return {"data": {"external_proposal_review": {"displayed_call": shown}}}
        return {}

    monkeypatch.setattr(runner, "_request", fake)
    state = tmp_path / "s.json"
    state.write_text(
        json.dumps(
            {
                "proposals": {
                    "applied": {"id": "p1", "subject": A, "object": B},
                    "rejected": {"id": "p2", "subject": B, "object": C},
                    "expired": {"id": "p3", "subject": C, "object": A},
                }
            }
        )
    )
    args = argparse.Namespace(dashboard_url="https://d", token_file=tmp_path, state=state)
    assert runner.run_decide(args) == 0
    posts = {url: body for url, body in sent if url.endswith(("/approve", "/reject"))}
    assert posts["https://d/api/approvals/actions/1/approve"] == {"displayed_call": shown}
    assert posts["https://d/api/approvals/actions/2/reject"] == {}

    # An approve with no displayed call is BLOCKED, and the reject still runs.
    sent.clear()
    monkeypatch.setattr(
        runner,
        "_request",
        lambda url, token_file, body=None: (
            {"data": listing}
            if "/actions?" in url
            else {"data": {"external_proposal_review": {"displayed_call": None}}}
            if url.endswith("/api/approvals/1")
            else sent.append((url, body)) or {}
        ),
    )
    assert runner.run_decide(args) == 1
    assert [u for u, _ in sent] == ["https://d/api/approvals/actions/2/reject"]


def test_request_refuses_non_https_before_reading_the_token(tmp_path) -> None:
    missing = tmp_path / "never-read"
    for url in ("http://x/api", "ftp://x", "//x"):
        with pytest.raises(ValueError, match="non-https"):
            runner._request(url, missing)
