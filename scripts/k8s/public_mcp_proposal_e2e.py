#!/usr/bin/env python3
"""Live dev run of the three public MCP proposal paths (task 5.7, live half).

NOT run in CI and not run until the public MCP server is deployed (bu-y66o00.6) and a dashboard
with the typed review (bu-y66o00.9) provides the displayed call. The live leg is tracked in
bu-y66o00.48. The hermetic twin is tests/integration/test_public_mcp_proposal_e2e.py. The only
writes are synthetic proposals and the owner decisions on them; use synthetic entities, never
personal data.
Secrets are read from files named on the command line and never printed or put in a state file.

Three steps, each re-runnable, sharing a state file of proposal ids:

  propose  --mcp-url U --token-file F --state S --applied A:B --rejected C:D --expired E:F
           one propose_fact per path through the public endpoint (SUBJECT:OBJECT entity uuids)
  decide   --dashboard-url U --token-file F --state S
           after an importer pass has parked them (every 5 minutes): approve the `applied` path
           (echoing the dashboard's external_proposal_review.displayed_call, which the approve
           route requires), reject the `rejected` path, leave `expired` alone. The two are
           independent. A review without a displayed call (an entity merged or deleted since
           parking, say) reports BLOCKED with the review's stated reason
  verify   --mcp-url U --token-file F --state S
           fetch("proposal:<id>") per path and compare with the expected state

The `expired` path ends when the approval window (14 days) passes and an importer pass runs; on a
dev cluster the owner may instead set that one synthetic action's expires_at into the past and
wait for the next pass. `verify` reports a path still `parked` as PENDING, never as a pass.
Exit 0 only when every path is at its expected state; 2 for bad arguments; 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

PATHS = ("applied", "rejected", "expired")
EXPECTED = {"applied": "applied", "rejected": "owner_rejected", "expired": "expired"}
TIMEOUT = 30


def parse_pair(value: str) -> tuple[str, str]:
    """``SUBJECT:OBJECT`` as two distinct canonical UUIDs."""
    parts = value.split(":")
    try:
        subject, obj = (str(uuid.UUID(part)) for part in parts)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{value!r}: expected SUBJECT:OBJECT entity uuids"
        ) from None
    if len(parts) != 2 or subject == obj:
        raise argparse.ArgumentTypeError(f"{value!r}: expected two distinct uuids")
    return subject, obj


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="step", required=True)
    for name in ("propose", "decide", "verify"):
        step = sub.add_parser(name)
        step.add_argument("--token-file", type=Path, required=True)
        step.add_argument("--state", type=Path, required=True)
        step.add_argument("--mcp-url" if name != "decide" else "--dashboard-url", required=True)
    propose = sub.choices["propose"]
    for path in PATHS:
        propose.add_argument(f"--{path}", type=parse_pair, required=True, metavar="SUBJ:OBJ")
    return parser


def expected_states(state: dict[str, Any]) -> dict[str, str]:
    """The state each recorded path must reach, keyed by proposal id."""
    return {state["proposals"][path]["id"]: EXPECTED[path] for path in PATHS}


def propose_call(subject: str, obj: str, key: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "propose_fact",
            "arguments": {
                "subject_entity_id": subject,
                "predicate": "works-at",
                "object_entity_id": obj,
                "idempotency_key": key,
            },
        },
    }


def match_action(actions: Sequence[dict[str, Any]], subject: str, obj: str) -> str | None:
    """The one pending relationship_assert_fact action for exactly this pair, else None."""
    found = [
        a["id"]
        for a in actions
        if a.get("tool_name") == "relationship_assert_fact"
        and a.get("status") == "pending"
        and str((a.get("tool_args") or {}).get("subject")) == subject
        and str((a.get("tool_args") or {}).get("object")) == obj
    ]
    return found[0] if len(found) == 1 else None


def _request(url: str, token_file: Path, body: dict[str, Any] | None = None) -> dict[str, Any]:
    if urllib.parse.urlsplit(url).scheme != "https":
        raise ValueError("refusing to send a bearer over a non-https URL")
    token = token_file.read_text(encoding="utf-8").strip()
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(  # noqa: S310 -- operator-supplied https URL
        url,
        data=data,
        method="GET" if body is None else "POST",
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
        },
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
        return json.loads(response.read())


def _tool(url: str, token_file: Path, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    return _request(url, token_file, body)["result"]


def run_propose(args: argparse.Namespace) -> int:
    run = uuid.uuid4().hex[:12]
    recorded = {}
    for path in PATHS:
        subject, obj = getattr(args, path)
        result = _tool(
            args.mcp_url,
            args.token_file,
            "propose_fact",
            propose_call(subject, obj, f"e2e-{run}-{path}")["params"]["arguments"],
        )
        if result.get("isError"):
            print(f"FAIL propose {path}: {result['structuredContent'].get('error')}")
            return 1
        recorded[path] = {
            "id": result["structuredContent"]["proposal_id"],
            "subject": subject,
            "object": obj,
        }
        print(f"ok   propose {path}: {recorded[path]['id']}")
    args.state.write_text(json.dumps({"proposals": recorded}, indent=2), encoding="utf-8")
    print("next: wait for an importer pass (every 5 minutes), then `decide`")
    return 0


def approve_body(detail: dict[str, Any]) -> dict[str, Any] | None:
    """The approve body for one action: the displayed call the dashboard showed, echoed back.

    An external proposal's displayed call is in its typed review
    (REQ-dashboard-approvals-904); its generic ``call_preview`` is always null. The approve
    route refuses a body without the call (409), and a review that cannot be approved (an
    entity merged or deleted since parking) has none to echo.
    """
    review = detail.get("external_proposal_review")
    if not isinstance(review, dict) or not isinstance(review.get("displayed_call"), dict):
        return None
    return {"displayed_call": review["displayed_call"]}


def approve_unavailable_reason(detail: dict[str, Any]) -> str:
    """Why the dashboard offers no approval, as the typed review states it."""
    review = detail.get("external_proposal_review")
    if not isinstance(review, dict):
        return "no external proposal review (is the dashboard at bu-y66o00.9 or later?)"
    reason = review.get("approve_unavailable_reason")
    return reason if isinstance(reason, str) and reason else "no displayed call"


def decide_one(base: str, token_file: Path, path: str, verb: str, item: dict[str, Any], listing):
    """One path's decision; returns a verdict line and whether it passed."""
    action_id = match_action(listing, item["subject"], item["object"])
    if action_id is None:
        return f"PENDING decide {path}: no single pending action yet (importer not run?)", False
    body: dict[str, Any] = {}
    if verb == "approve":
        detail = _request(f"{base}/api/approvals/{action_id}", token_file)["data"]
        body_or_none = approve_body(detail)
        if body_or_none is None:
            return (
                f"BLOCKED approve {path}: action {action_id} offers no approval: "
                f"{approve_unavailable_reason(detail)}",
                False,
            )
        body = body_or_none
    _request(f"{base}/api/approvals/actions/{action_id}/{verb}", token_file, body)
    return f"ok   {verb} {path}: action {action_id}", True


def run_decide(args: argparse.Namespace) -> int:
    state = json.loads(args.state.read_text(encoding="utf-8"))
    base = args.dashboard_url.rstrip("/")
    listing = _request(
        f"{base}/api/approvals/actions?status=pending&tool_name=relationship_assert_fact"
        "&butler=relationship&limit=500",
        args.token_file,
    )["data"]
    failed = 0
    for path, verb in (("applied", "approve"), ("rejected", "reject")):
        # Independent: one path's refusal never skips the other.
        try:
            line, passed = decide_one(
                base, args.token_file, path, verb, state["proposals"][path], listing
            )
        except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
            line, passed = f"FAIL {verb} {path}: {type(exc).__name__}", False
        print(line)
        failed += not passed
    print("expired path: left undecided on purpose")
    return 1 if failed else 0


def run_verify(args: argparse.Namespace) -> int:
    state = json.loads(args.state.read_text(encoding="utf-8"))
    failed = 0
    for proposal_id, want in expected_states(state).items():
        result = _tool(args.mcp_url, args.token_file, "fetch", {"id": f"proposal:{proposal_id}"})
        if result.get("isError"):
            print(f"FAIL {proposal_id}: {result['structuredContent'].get('error')}")
            failed += 1
            continue
        meta = result["structuredContent"]["metadata"]
        if meta["state"] == want and (want == "parked" or meta["decided_at"]):
            print(f"ok   {proposal_id}: {want} decided_at={meta['decided_at']}")
        elif meta["state"] == "parked":
            print(f"PENDING {proposal_id}: still parked, expected {want}")
            failed += 1
        else:
            print(f"FAIL {proposal_id}: {meta['state']}, expected {want}")
            failed += 1
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return {"propose": run_propose, "decide": run_decide, "verify": run_verify}[args.step](args)
    except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
        print(f"FAIL {args.step}: {type(exc).__name__}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
