"""One-field queue trial and complete queue provenance; no live mutations."""

from __future__ import annotations

import copy
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import ci_queue_admission as admission  # noqa: E402

pytestmark = pytest.mark.unit
NOW = datetime(2026, 10, 15, tzinfo=UTC)


def ruleset():
    return {
        "id": 22281319,
        "name": "main-merge-queue",
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
        "bypass_actors": [{"actor_type": "Team", "actor_id": 123, "bypass_mode": "always"}],
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {
                "type": "merge_queue",
                "parameters": {
                    "max_entries_to_build": 5,
                    "max_entries_to_merge": 5,
                    "grouping_strategy": "ALLGREEN",
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "required_status_checks": [
                        {"context": c} for c in ("check", "guards", "frontend")
                    ]
                },
            },
        ],
        "updated_at": "2026-10-14T00:00:00Z",
    }


class Fake:
    def __init__(self):
        self.live = ruleset()
        self.admin = True
        self.merged = True
        self.calls = []

    def __call__(self, endpoint, *, method="GET", payload=None):
        self.calls.append((endpoint, method, copy.deepcopy(payload)))
        if endpoint.endswith("/rulesets/22281319"):
            if method == "PUT":
                self.live.update(copy.deepcopy(payload))
                self.live["updated_at"] = "2026-10-15T00:00:01Z"
            return copy.deepcopy(self.live)
        if endpoint.endswith("/pulls/4530"):
            return {"merged": self.merged, "merge_commit_sha": "a" * 40 if self.merged else None}
        if endpoint == "repos/tzeusy-org/butlers":
            return {"permissions": {"admin": self.admin}}
        raise AssertionError(endpoint)


def test_plan_retains_partial_inspectable_state_without_authorizing_apply(tmp_path):
    api = Fake()
    api.admin = False
    del api.live["bypass_actors"]
    plan = admission.plan(api, now=NOW)
    assert plan["status"] == "NOT_APPLYABLE"
    assert plan["original"] == api.live
    assert plan["candidate"] is None
    path = tmp_path / "plan.json"
    admission.save(path, plan)
    with pytest.raises(FileExistsError):
        admission.save(path, plan)
    assert all(method == "GET" for _, method, _ in api.calls)


def test_exact_one_field_apply_and_expired_rollback_preserve_every_other_field():
    api = Fake()
    plan = admission.plan(api, now=NOW)
    original = copy.deepcopy(plan["rollback"])
    expected = copy.deepcopy(original)
    expected["rules"][2]["parameters"]["max_entries_to_build"] = 1
    assert plan["candidate"] == expected
    admission.change(plan, admission.digest(plan), api, now=NOW)
    assert admission.payload(api.live) == expected
    admission.change(plan, admission.digest(plan), api, rollback=True, now=NOW + timedelta(days=2))
    assert admission.payload(api.live) == original
    assert [m for _, m, _ in api.calls].count("PUT") == 2


@pytest.mark.parametrize("case", ["digest", "admin", "bypass", "drift", "expired", "pipeline"])
def test_apply_refuses_without_mutation(case):
    api = Fake()
    plan = admission.plan(api, now=NOW)
    reviewed = admission.digest(plan)
    now = NOW
    if case == "digest":
        reviewed = "0" * 64
    elif case == "admin":
        api.admin = False
    elif case == "bypass":
        del api.live["bypass_actors"]
    elif case == "drift":
        api.live["rules"][2]["parameters"]["max_entries_to_merge"] = 2
    elif case == "expired":
        now += timedelta(days=2)
    else:
        api.merged = False
    with pytest.raises(ValueError):
        admission.change(plan, reviewed, api, now=now)
    assert all(method != "PUT" for _, method, _ in api.calls)


def test_rollback_refuses_foreign_drift_and_never_requires_pipeline_or_unexpired_plan():
    api = Fake()
    plan = admission.plan(api, now=NOW)
    admission.change(plan, admission.digest(plan), api, now=NOW)
    api.live["bypass_actors"].append({"actor_type": "Team", "actor_id": 456})
    api.merged = False
    with pytest.raises(ValueError):
        admission.change(plan, admission.digest(plan), api, rollback=True, now=NOW)
    assert [m for _, m, _ in api.calls].count("PUT") == 1


def test_capture_retains_complete_enqueue_events_source_and_every_attempt():
    events = [
        {"id": f"event-{i}", "__typename": kind, "createdAt": f"2026-10-15T00:0{i}:00Z"}
        for i, kind in enumerate(
            [
                "AddedToMergeQueueEvent",
                "RemovedFromMergeQueueEvent",
                "AddedToMergeQueueEvent",
                "MergedEvent",
            ]
        )
    ]
    events[1]["reason"] = "checks_timed_out"
    events[3]["commit"] = {"oid": "b" * 40}
    row = {
        "id": 12,
        "run_attempt": 2,
        "head_sha": "b" * 40,
        "event": "merge_group",
        "head_branch": "gh-readonly-queue/main/pr-42-abcd",
        "pull_requests": [],
        "created_at": NOW.isoformat(),
        "run_started_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        "status": "completed",
        "conclusion": "success",
    }
    calls = []

    def api(endpoint, *, method="GET", payload=None):
        calls.append(endpoint)
        if endpoint == "graphql":
            if "mergeQueue" in payload["query"] and "pullRequest(number" not in payload["query"]:
                return {
                    "data": {
                        "repository": {
                            "mergeQueue": {
                                "entries": {
                                    "pageInfo": {"hasNextPage": False},
                                    "nodes": [],
                                }
                            }
                        }
                    }
                }
            return {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "number": 42,
                            "headRefOid": "c" * 40,
                            "mergedAt": events[-1]["createdAt"],
                            "mergeCommit": {"oid": "b" * 40},
                            "timelineItems": {
                                "pageInfo": {"hasNextPage": False},
                                "nodes": events,
                            },
                        }
                    }
                }
            }
        if endpoint.startswith("search/issues"):
            return {"total_count": 1, "items": [{"id": 99, "number": 42}]}
        if "/attempts/" in endpoint:
            attempt = int(endpoint.split("/attempts/")[1].split("/")[0])
            if "/jobs?" in endpoint:
                return {
                    "total_count": 1,
                    "jobs": [
                        {
                            "id": 100 + attempt,
                            "run_id": 12,
                            "run_attempt": attempt,
                            "head_sha": "b" * 40,
                            "name": "check",
                            "status": "completed",
                            "conclusion": "failure" if attempt == 1 else "success",
                            "created_at": NOW.isoformat(),
                            "started_at": NOW.isoformat(),
                            "completed_at": (NOW + timedelta(seconds=5)).isoformat(),
                        }
                    ],
                }
            return dict(row, run_attempt=attempt)
        return {"total_count": 1, "workflow_runs": [row]}

    result = admission.capture(api, since=NOW.isoformat(), now=NOW + timedelta(hours=1))
    assert result["pull_requests"][0]["timelineItems"]["nodes"] == events
    assert result["pull_requests"][0]["headRefOid"] == "c" * 40
    assert [x["run_attempt"] for x in result["attempts"]] == [1, 2]
    assert {x["head_sha"] for x in result["attempts"]} == {"b" * 40}
    assert [x["jobs"][0]["conclusion"] for x in result["attempts"]] == ["failure", "success"]
    assert any("/attempts/1" in x for x in calls)
    for case in ("queue", "timeline", "runs", "search", "attempt", "jobs"):

        def broken(endpoint, *, method="GET", payload=None, case=case):
            value = copy.deepcopy(api(endpoint, method=method, payload=payload))
            if endpoint == "graphql":
                repository = value["data"]["repository"]
                if case == "queue" and "mergeQueue" in repository:
                    repository["mergeQueue"]["entries"]["pageInfo"]["hasNextPage"] = True
                if case == "timeline" and "pullRequest" in repository:
                    repository["pullRequest"]["timelineItems"]["pageInfo"]["hasNextPage"] = True
            elif case == "jobs" and "jobs" in value:
                value["jobs"][0]["run_attempt"] = 3
            elif case == "attempt" and "/attempts/" in endpoint and "jobs" not in value:
                value["head_sha"] = "d" * 40
            elif case == "search" and endpoint.startswith("search/issues"):
                value["incomplete_results"] = True
            elif case == "runs" and "workflow_runs" in value:
                value["total_count"] = 101
            return value

        with pytest.raises(ValueError):
            admission.capture(broken, since=NOW.isoformat(), now=NOW + timedelta(hours=1))


def test_postwrite_verification_failure_does_not_claim_success():
    api = Fake()
    plan = admission.plan(api, now=NOW)

    def corrupt(endpoint, *, method="GET", payload=None):
        value = api(endpoint, method=method, payload=payload)
        if method == "PUT":
            api.live["rules"][2]["parameters"]["max_entries_to_merge"] = 4
        return value

    with pytest.raises(ValueError, match="post-write mismatch"):
        admission.change(plan, admission.digest(plan), corrupt, now=NOW)
    assert plan["rollback"] == admission.payload(ruleset())
