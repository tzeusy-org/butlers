"""Plan a reversible one-build queue trial; default is read-only, never a CI gate.

The administrator explicitly applies a reviewed plan and owns its timed rollback.
Capture is a bounded metadata snapshot, intended for repeated existing observation.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from ci_latency import identifier, population, strict_object, timestamp

REPO = "tzeusy-org/butlers"
RULESET = f"repos/{REPO}/rulesets/22281319"
FIELDS = {"name", "target", "enforcement", "conditions", "bypass_actors", "rules"}
SERVER_FIELDS = {
    "id",
    "node_id",
    "source",
    "source_type",
    "created_at",
    "updated_at",
    "_links",
    "current_user_can_bypass",
}


class Refused(ValueError):
    """A fixed operator-facing refusal, never a raw API diagnostic."""


class GitHub:
    def __init__(self):
        self.deadline = time.monotonic() + 240

    def __call__(self, endpoint, *, method="GET", payload=None):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise Refused("API deadline exceeded")
        command = ["gh", "api", "--method", method, endpoint]
        if payload is not None:
            command += ["--input", "-"]
        result = subprocess.run(
            command,
            input=json.dumps(payload).encode() if payload is not None else None,
            capture_output=True,
            timeout=min(20, remaining),
        )
        if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
            raise Refused("bounded API request unavailable")
        return json.loads(result.stdout, object_pairs_hook=strict_object)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def save(path, value):
    # Metadata can include otherwise hidden bypass actor IDs. Never overwrite a
    # reviewed plan or put this operational capture in the tracked checkout.
    path = path.resolve()
    checkout = Path(__file__).resolve().parents[1]
    if path.is_relative_to(checkout):
        raise Refused("capture must be outside the checkout")
    path.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def payload(state):
    if not isinstance(state, dict) or not FIELDS <= state.keys():
        raise Refused("complete administrator snapshot required, including bypass_actors")
    if state.keys() - FIELDS - SERVER_FIELDS:
        raise Refused("unknown ruleset fields require review")
    if not isinstance(state["bypass_actors"], list) or not isinstance(state["rules"], list):
        raise Refused("malformed ruleset population")
    return {key: copy.deepcopy(state[key]) for key in sorted(FIELDS)}


def queue_rule(state):
    rules = [r for r in state["rules"] if r["type"] == "merge_queue"]
    if len(rules) != 1:
        raise Refused("exactly one queue rule required")
    return rules[0]["parameters"]


def validate_original(state):
    result = payload(state)
    if (
        state.get("id") != 22281319
        or result["name"] != "main-merge-queue"
        or result["target"] != "branch"
        or result["enforcement"] != "active"
        or result["conditions"] != {"ref_name": {"include": ["refs/heads/main"], "exclude": []}}
        or queue_rule(result)["max_entries_to_build"] != 5
    ):
        raise Refused("unexpected baseline ruleset")
    checks = [r for r in result["rules"] if r["type"] == "required_status_checks"]
    if len(checks) != 1 or {
        c["context"] for c in checks[0]["parameters"]["required_status_checks"]
    } != {"check", "guards", "frontend"}:
        raise Refused("mandatory contexts changed")
    if not {"deletion", "non_fast_forward"} <= {r["type"] for r in result["rules"]}:
        raise Refused("mandatory protections changed")
    return result


def prerequisite(api):
    pr = api(f"repos/{REPO}/pulls/4530")
    if pr.get("merged") is not True or not re.fullmatch(
        "[0-9a-f]{40}", pr.get("merge_commit_sha") or ""
    ):
        raise Refused("pipeline PR4530 must be merged first")
    return pr["merge_commit_sha"]


def plan(api, *, now=None):
    now = now or datetime.now(UTC)
    state = api(RULESET)
    permissions = api(f"repos/{REPO}").get("permissions", {})
    result = {
        "schema": "ci-queue-admission.v1",
        "repository": REPO,
        "ruleset": RULESET,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=24)).isoformat(),
        "status": "NOT_APPLYABLE",
        "reason": None,
        "original": state,
        "rollback": None,
        "candidate": None,
        "pipeline_merge": None,
    }
    try:
        if permissions.get("admin") is not True:
            raise Refused("ruleset administration authority required")
        original = validate_original(state)
        result["pipeline_merge"] = prerequisite(api)
        candidate = copy.deepcopy(original)
        queue_rule(candidate)["max_entries_to_build"] = 1
        result.update(status="READY", rollback=original, candidate=candidate)
    except Refused as error:
        result["reason"] = str(error)  # Our fixed refusals only, never API exception text.
    return result


def change(reviewed, expected_digest, api, *, rollback=False, now=None):
    now = now or datetime.now(UTC)
    if digest(reviewed) != expected_digest or reviewed.get("schema") != "ci-queue-admission.v1":
        raise Refused("reviewed plan digest mismatch")
    if (
        reviewed.get("status") != "READY"
        or reviewed.get("repository") != REPO
        or reviewed.get("ruleset") != RULESET
    ):
        raise Refused("plan is not applyable")
    original = validate_original(reviewed["original"])
    candidate = copy.deepcopy(original)
    queue_rule(candidate)["max_entries_to_build"] = 1
    if original != reviewed["rollback"] or candidate != reviewed["candidate"]:
        raise Refused("plan is not an exact one-field change")
    if api(f"repos/{REPO}").get("permissions", {}).get("admin") is not True:
        raise Refused("ruleset administration authority required")
    if not rollback:
        if timestamp(reviewed["expires_at"]) <= now.timestamp():
            raise Refused("apply plan expired; rollback remains available")
        if prerequisite(api) != reviewed["pipeline_merge"]:
            raise Refused("pipeline prerequisite changed")
    live = api(RULESET)
    before = candidate if rollback else original
    after = original if rollback else candidate
    if payload(live) != before or (not rollback and digest(live) != digest(reviewed["original"])):
        raise Refused("concurrent ruleset drift; do not overwrite")
    # GitHub provides no documented conditional ruleset PUT. Serialize admin
    # changes operationally; this read/check/PUT is not atomic compare-and-swap.
    api(RULESET, method="PUT", payload=after)
    actual = api(RULESET)
    if payload(actual) != after:
        raise Refused("post-write mismatch; inspect live state before recovery")
    return {
        "operation": "rollback" if rollback else "apply",
        "at": now.isoformat(),
        "reviewed_digest": expected_digest,
        "verified_payload_digest": digest(after),
        "rollback_due_at": None if rollback else (now + timedelta(hours=24)).isoformat(),
    }


def nodes(connection):
    rows = connection["nodes"]
    if (
        connection["pageInfo"]["hasNextPage"] is not False
        or not isinstance(rows, list)
        or len(rows) > 100
        or any(
            not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]
            for row in rows
        )
    ):
        raise Refused("truncated or malformed GraphQL population")
    if len({row["id"] for row in rows}) != len(rows):
        raise Refused("duplicate GraphQL identity")
    return rows


def graphql(api, body, variables=None):
    response = api("graphql", method="POST", payload={"query": body, "variables": variables or {}})
    if response.get("errors"):
        raise Refused("GraphQL metadata unavailable")
    return response["data"]["repository"]


def capture(api, *, since, now=None):
    now = now or datetime.now(UTC)
    start = timestamp(since)
    if not 0 < now.timestamp() - start <= 48 * 3600:
        raise Refused("capture requires a past UTC since within48h")
    queue = graphql(
        api,
        """query {repository(owner:"tzeusy-org",name:"butlers") {
      mergeQueue(branch:"main") {entries(first:100) {pageInfo{hasNextPage} nodes {
        id enqueuedAt position state headCommit{oid} baseCommit{oid}
        pullRequest{number headRefOid}
      }}}
    }}""",
    )
    entries = nodes(queue["mergeQueue"]["entries"])
    for entry in entries:
        timestamp(entry["enqueuedAt"])
        identifier(entry["position"])
        for head in (
            entry["headCommit"]["oid"],
            entry["baseCommit"]["oid"],
            entry["pullRequest"]["headRefOid"],
        ):
            if not isinstance(head, str) or not re.fullmatch("[0-9a-f]{40}", head):
                raise Refused("queue source identity mismatch")
        if not isinstance(entry["state"], str) or not entry["state"]:
            raise Refused("queue state missing")
    numbers = {identifier(x["pullRequest"]["number"]) for x in entries}
    query = urlencode({"created": f"{since}..{now.isoformat()}", "per_page": 100})
    runs = population(
        api(f"repos/{REPO}/actions/workflows/ci.yml/runs?{query}"),
        "workflow_runs",
        limit=100,
        complete=True,
    )
    attempts = []
    fields = (
        "id",
        "run_attempt",
        "event",
        "head_sha",
        "head_branch",
        "status",
        "conclusion",
        "created_at",
        "run_started_at",
        "updated_at",
    )
    for run in runs:
        if not start <= timestamp(run["created_at"]) <= now.timestamp():
            raise Refused("run outside bounded cohort")
        count = identifier(run["run_attempt"])
        if count > 5 or not re.fullmatch("[0-9a-f]{40}", run["head_sha"]):
            raise Refused("invalid or excessive attempts")
        numbers.update(identifier(x["number"]) for x in run["pull_requests"])
        match = re.fullmatch(r"gh-readonly-queue/main/pr-([0-9]+)-.+", run["head_branch"])
        if match:
            numbers.add(int(match[1]))
        for attempt in range(1, count + 1):
            item = api(f"repos/{REPO}/actions/runs/{run['id']}/attempts/{attempt}")
            if (
                item["id"] != run["id"]
                or item["run_attempt"] != attempt
                or item["head_sha"] != run["head_sha"]
            ):
                raise Refused("attempt identity mismatch")
            timestamp(item["created_at"])
            if not isinstance(item["status"], str) or not item["status"]:
                raise Refused("attempt status missing")
            if item["status"] == "completed" and not isinstance(item["conclusion"], str):
                raise Refused("attempt conclusion missing")
            jobs = population(
                api(f"repos/{REPO}/actions/runs/{run['id']}/attempts/{attempt}/jobs?per_page=100"),
                "jobs",
                limit=100,
                complete=True,
            )
            retained = []
            for job in jobs:
                if (
                    job["run_id"] != run["id"]
                    or job["run_attempt"] != attempt
                    or job["head_sha"] != run["head_sha"]
                ):
                    raise Refused("job attempt identity mismatch")
                for key in ("created_at", "started_at", "completed_at"):
                    if job[key] is not None:
                        timestamp(job[key])
                retained.append(
                    {
                        key: job[key]
                        for key in (
                            "id",
                            "run_id",
                            "run_attempt",
                            "head_sha",
                            "name",
                            "status",
                            "conclusion",
                            "created_at",
                            "started_at",
                            "completed_at",
                        )
                    }
                )
            attempts.append({**{key: item[key] for key in fields}, "jobs": retained})
    query = urlencode(
        {"q": f"repo:{REPO} is:pr merged:{since}..{now.isoformat()}", "per_page": 100}
    )
    search = api(f"search/issues?{query}")
    if search.get("incomplete_results", False):
        raise Refused("incomplete merge search")
    numbers.update(
        identifier(x["number"]) for x in population(search, "items", limit=100, complete=True)
    )
    if len(numbers) > 100:
        raise Refused("too many PR timelines; choose shorter capture window")
    prs = []
    for number in sorted(numbers):
        value = graphql(
            api,
            """query($number:Int!) {repository(owner:"tzeusy-org",name:"butlers") {
          pullRequest(number:$number){number headRefOid mergedAt mergeCommit{oid}
            timelineItems(first:100,itemTypes:[ADDED_TO_MERGE_QUEUE_EVENT,
              REMOVED_FROM_MERGE_QUEUE_EVENT,MERGED_EVENT]) {
              pageInfo{hasNextPage} nodes {__typename
                ... on AddedToMergeQueueEvent{id createdAt}
                ... on RemovedFromMergeQueueEvent{id createdAt reason beforeCommit{oid}}
                ... on MergedEvent{id createdAt commit{oid}}
        }}}}}""",
            {"number": number},
        )["pullRequest"]
        for event in nodes(value["timelineItems"]):
            timestamp(event["createdAt"])
            kind = event["__typename"]
            if kind == "RemovedFromMergeQueueEvent":
                if not isinstance(event["reason"], str) or not event["reason"]:
                    raise Refused("queue removal reason missing")
            elif kind == "MergedEvent":
                if not re.fullmatch("[0-9a-f]{40}", event["commit"]["oid"]):
                    raise Refused("merge identity missing")
            elif kind != "AddedToMergeQueueEvent":
                raise Refused("unexpected queue timeline event")
        if value["number"] != number or not re.fullmatch("[0-9a-f]{40}", value["headRefOid"]):
            raise Refused("PR identity mismatch")
        prs.append(value)
    return {
        "schema": "ci-queue-capture.v1",
        "repository": REPO,
        "since": since,
        "observed_at": now.isoformat(),
        "queue_entries": entries,
        "attempts": attempts,
        "pull_requests": prs,
        "limits": "Bounded CI-created cohort and full queue timelines; event head is not "
        "asserted historically. Bind heads from successive live captures. Use ci_latency.py "
        "for run/job latency, never attempt updated_at as completion.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation", nargs="?", choices=("plan", "apply", "rollback", "capture"), default="plan"
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--digest")
    parser.add_argument("--since")
    args = parser.parse_args()
    try:
        api = GitHub()
        if args.operation in {"apply", "rollback"}:
            if not args.plan or not args.digest:
                raise Refused("explicit plan and reviewed digest required")
            reviewed = json.loads(args.plan.read_text(), object_pairs_hook=strict_object)
            result = change(reviewed, args.digest, api, rollback=args.operation == "rollback")
        else:
            if not args.output or (args.operation == "capture" and not args.since):
                raise Refused("output required; capture also requires since")
            result = plan(api) if args.operation == "plan" else capture(api, since=args.since)
            save(args.output, result)
        print(
            json.dumps(
                {
                    "operation": args.operation,
                    "status": result.get("status", "RECORDED"),
                    "digest": digest(result),
                    "reason": result.get("reason"),
                    "verified_payload_digest": result.get("verified_payload_digest"),
                    "rollback_due_at": result.get("rollback_due_at"),
                }
            )
        )
        return 2 if result.get("status") == "NOT_APPLYABLE" else 0
    except Refused as error:
        print(f"REFUSED: {error}")
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
        print("REFUSED: unavailable or malformed metadata; no completion claim")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
