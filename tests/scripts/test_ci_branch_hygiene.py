"""Operator conformance: real local Git; synthetic provider/custody inputs, no live approval."""

from __future__ import annotations

import asyncio
import copy
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import ci_branch_hygiene as hygiene  # noqa: E402

pytestmark = pytest.mark.unit


def git_at(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=root, stderr=subprocess.DEVNULL, text=True
    ).strip()


def make_repository(path: Path) -> tuple[hygiene.Git, str, str]:
    path.mkdir()
    git_at(path, "init", "-q", "-b", "main")
    git_at(path, "config", "user.email", "fixture@example.invalid")
    git_at(path, "config", "user.name", "Git fixture")
    (path / "proof.txt").write_text("main survives")
    git_at(path, "add", ".")
    git_at(path, "commit", "-qm", "main fixture")
    git_at(path, "switch", "-qc", "agent/merged")
    (path / "proof.txt").write_text("candidate original bytes")
    git_at(path, "commit", "-qam", "candidate fixture")
    original = git_at(path, "rev-parse", "HEAD")
    git_at(path, "switch", "-q", "main")
    (path / "second.txt").write_text("replacement companion")
    git_at(path, "add", ".")
    git_at(path, "commit", "-qm", "later main")
    replacement = git_at(path, "rev-parse", "HEAD")
    remote = path.parent / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    git_at(path, "remote", "add", "origin", str(remote))
    git_at(path, "push", "-q", "origin", "main", "agent/merged")
    return hygiene.Git(path), original, replacement


def state_for(git: hygiene.Git, now: datetime) -> dict:
    heads = git.heads()
    sha = heads["agent/merged"]
    return {
        "repository": hygiene.REPOSITORY,
        "default_branch": "main",
        "complete": True,
        "custody_complete": True,
        "held_refs": [],
        "held_shas": [],
        "terminal_worktrees": [],
        "worktrees": git.worktrees(),
        "branches": [{"name": k, "sha": v, "protected": k == "main"} for k, v in heads.items()],
        "prs": [
            {
                "number": 7,
                "name": "agent/merged",
                "sha": sha,
                "head_repository": hygiene.REPOSITORY,
                "state": "closed",
                "merged_at": now.isoformat(),
                "closed_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "merge_sha": heads["main"],
            }
        ],
        "caches": [],
        "usage": {"active_caches_size_in_bytes": 0},
        "delete_branch_on_merge": False,
    }


class SettingsApi:
    def __init__(self, state):
        self.state = state
        self.mutations = []

    def repo(self):
        return {"delete_branch_on_merge": self.state["delete_branch_on_merge"]}

    def mutate(self, path, method, fields=None):
        self.mutations.append((path, method, fields))
        if fields:
            self.state["delete_branch_on_merge"] = fields["delete_branch_on_merge"]
        else:
            key = int(path.rsplit("/", 1)[1])
            self.state["caches"] = [x for x in self.state["caches"] if x["id"] != key]

    def caches(self):
        return self.state["caches"]


def test_bound_plan_real_git_lease_recovery_and_unknown_ack(tmp_path, monkeypatch):
    """REQ-testing-041 and REQ-testing-043: wrong heads and live workers survive real local operations."""
    git, original, replacement = make_repository(tmp_path / "checkout")
    now = datetime.now(UTC)
    state = state_for(git, now)
    row = next(x for x in state["branches"] if x["name"] == "agent/merged")
    assert hygiene.classify(row, state, now, git) == "merged_candidate"
    for field, value, expected in (
        ("custody_complete", False, "excluded_incomplete_custody"),
        ("held_refs", ["agent/merged"], "excluded_custody"),
        ("held_shas", [original], "excluded_custody"),
        (
            "worktrees",
            [
                {
                    "path": "owned elsewhere",
                    "branch": "agent/merged",
                    "sha": original,
                    "locked": False,
                }
            ],
            "excluded_worktree",
        ),
    ):
        wrong = copy.deepcopy(state)
        wrong[field] = value
        assert hygiene.classify(row, wrong, now, git) == expected
    wrong = copy.deepcopy(state)
    wrong["prs"][0]["state"] = "open"
    assert hygiene.classify(row, wrong, now, git) == "excluded_open_pr"
    wrong = copy.deepcopy(state)
    wrong["prs"][0]["sha"] = replacement
    assert hygiene.classify(row, wrong, now, git) == "excluded_unknown_history"
    wrong = copy.deepcopy(state)
    wrong["prs"][0]["merged_at"] = None
    assert hygiene.classify(row, wrong, now, git) == "excluded_unknown_ref_activity"
    with pytest.raises(hygiene.Refusal, match="independent"):
        git.recover(row["name"], original, git.root / "recovery")
    plan = hygiene.plan(state, git, tmp_path / "independent-store", now)
    assert len(plan["branches"]) == 1 and git.heads()["agent/merged"] == original
    recovery = plan["branches"][0]["recovery"]
    git.verify_recovery(recovery)
    restored = tmp_path / "isolated.git"
    subprocess.run(
        ["git", "clone", "--bare", recovery["path"], str(restored)], check=True, capture_output=True
    )
    assert git_at(restored, "show", "recovery:proof.txt") == "candidate original bytes"
    assert not (restored / "objects/info/alternates").exists()
    with pytest.raises(hygiene.Refusal, match="approval"):
        hygiene.authorize(plan, {}, "apply")
    assert git.heads()["agent/merged"] == original
    remote = git.root.parent / "origin.git"
    git_at(remote, "update-ref", "refs/heads/agent/merged", replacement)
    with pytest.raises(hygiene.Refusal):
        git.delete("agent/merged", original)
    assert git.heads()["agent/merged"] == replacement
    with pytest.raises(hygiene.Refusal, match="replacement"):
        git.restore("agent/merged", recovery)
    git_at(remote, "update-ref", "refs/heads/agent/merged", original, replacement)
    api = SettingsApi(state)

    def current(*args):
        result = copy.deepcopy(state)
        heads = git.heads()
        result["branches"] = [
            {"name": k, "sha": v, "protected": k == "main"} for k, v in heads.items()
        ]
        return result

    monkeypatch.setattr(hygiene, "snapshot", current)
    delete = git.delete

    def lost_ack(name, sha):
        delete(name, sha)
        raise hygiene.Refusal("synthetic_transport_ack_loss_after_real_delete")

    monkeypatch.setattr(git, "delete", lost_ack)
    approved = {
        "digest": plan["digest"],
        "action": "apply",
        "approval_reference": "synthetic inner operator control",
    }
    result = hygiene.apply(
        plan, approved, git, api, tmp_path / "custody.json", tmp_path / "receipts", now
    )
    assert result["branches"][0]["stage"] == "absent_after_unknown_ack"
    assert git.heads() == {"main": replacement}
    assert result["setting_enabled"] and not result["main_cache_survivor"]
    assert (
        json.loads((tmp_path / "receipts/branch-0-before.json").read_text())["stage"]
        == "before_mutation"
    )
    git.restore("agent/merged", recovery)
    assert git.heads()["agent/merged"] == original
    with pytest.raises(hygiene.Refusal, match="replacement"):
        git.restore("agent/merged", recovery)
    # Actual common-directory exclusion prevents a cooperating native creator.
    from butlers.core.healing.worktree import WorktreeCreationError, create_healing_worktree

    # A real cooperating command stands in for the coordinator's canonical
    # mutation. The marker tests exclusion, not actual Beads claim provenance.
    claim_marker = tmp_path / "claimed-marker"
    claim_command = [
        "flock",
        "-n",
        str(git.common() / "ci-branch-custody.lock"),
        sys.executable,
        "-c",
        "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('claimed')",
        str(claim_marker),
    ]
    with git.exclusion():
        claim = subprocess.run(claim_command, capture_output=True, timeout=5)
        assert claim.returncode != 0 and not claim_marker.exists()
        with pytest.raises(WorktreeCreationError, match="custody"):
            asyncio.run(create_healing_worktree(git.root, "qa", "a" * 64, prefix="qa"))
        with pytest.raises(hygiene.Refusal, match="custody"):
            with git.exclusion():
                raise AssertionError("competing operator acquired the exclusion")
    claim = subprocess.run(claim_command, capture_output=True, timeout=5)
    assert claim.returncode == 0 and claim_marker.read_text() == "claimed"
    created = asyncio.run(create_healing_worktree(git.root, "qa", "a" * 64, prefix="qa"))
    created_path, created_branch = created
    assert created_path.is_dir() and git_at(created_path, "rev-parse", "HEAD") == replacement
    git_at(git.root, "worktree", "remove", str(created_path))
    git_at(git.root, "branch", "-D", created_branch)
    # A genuinely owned terminal checkout is retained until clean removal,
    # and its refs are only retired after independent recovery.
    git_at(git.root, "fetch", "-q", "origin", "agent/merged")
    git_at(git.root, "branch", "agent/merged", "FETCH_HEAD")
    terminal = tmp_path / "terminal-worker"
    git_at(git.root, "worktree", "add", "-q", str(terminal), "agent/merged")
    terminal_state = state_for(git, now)
    terminal_state["terminal_worktrees"] = [str(terminal)]
    terminal_plan = hygiene.plan(terminal_state, git, tmp_path / "terminal-recovery", now)
    approved_terminal = {**approved, "digest": terminal_plan["digest"]}
    monkeypatch.setattr(hygiene, "snapshot", lambda *args: copy.deepcopy(terminal_state))
    (terminal / "unrelated-work.txt").write_text("dirty worker cannot be retired")
    with pytest.raises(hygiene.Refusal, match="dirty"):
        hygiene.apply(
            terminal_plan,
            approved_terminal,
            git,
            api,
            tmp_path / "custody",
            tmp_path / "dirty-refusal",
            now,
        )
    assert terminal.exists() and git.heads()["agent/merged"] == original
    (terminal / "unrelated-work.txt").unlink()

    def current_terminal(*args):
        result = copy.deepcopy(terminal_state)
        result["worktrees"] = git.worktrees()
        result["branches"] = [
            {"name": k, "sha": v, "protected": k == "main"} for k, v in git.heads().items()
        ]
        result["delete_branch_on_merge"] = state["delete_branch_on_merge"]
        return result

    monkeypatch.setattr(hygiene, "snapshot", current_terminal)
    result = hygiene.apply(
        terminal_plan,
        approved_terminal,
        git,
        api,
        tmp_path / "custody",
        tmp_path / "clean-retirement",
        now,
    )
    assert not terminal.exists() and "agent/merged" not in git.heads()
    assert "agent/merged" not in git_at(git.root, "branch", "--list")

    # Real Git QA refresh, with a dirty unrelated worker sentinel and stale local main.
    from butlers.core.qa.dispatch import MainRefreshError, _refresh_main_commit

    git_at(git.root, "update-ref", "refs/heads/main", original)
    (git.root / "dirty-sentinel.txt").write_text("unrelated worker survives")
    assert asyncio.run(_refresh_main_commit(git.root)) == replacement
    assert git_at(git.root, "rev-parse", "main") == original
    assert (git.root / "dirty-sentinel.txt").read_text() == "unrelated worker survives"
    git_at(git.root, "remote", "set-url", "origin", str(tmp_path / "absent.git"))
    with pytest.raises(MainRefreshError):
        asyncio.run(_refresh_main_commit(git.root))
    assert (git.root / "dirty-sentinel.txt").exists()


def test_cache_survivors_pagination_and_both_observation_windows(tmp_path, monkeypatch):
    """REQ-testing-042 and REQ-testing-045: synthetic provider conformance cannot credit live caches/windows."""
    git, _, _ = make_repository(tmp_path / "checkout")
    now = datetime.now(UTC)
    state = state_for(git, now)

    def cache(key, ref, id):
        return {
            "id": id,
            "key": key,
            "ref": ref,
            "version": "v1",
            "size_in_bytes": 123,
            "created_at": now.isoformat(),
            "last_accessed_at": now.isoformat(),
        }

    main = cache("node-cache-npm", "refs/heads/main", 1)
    removable = cache("node-cache-npm", "refs/heads/agent/retired", 2)
    browser = cache("playwright-Chromium", "refs/heads/agent/retired", 3)
    active = cache("node-cache-npm", "refs/heads/agent/merged", 4)
    state["caches"] = [main, removable, browser, active]
    state["held_refs"] = ["agent/merged"]
    planned = hygiene.plan(state, git, tmp_path / "independent", now)
    assert planned["caches"] == [removable] and planned["main_caches"] == [main]
    state["custody_complete"] = False
    assert hygiene.plan_cache_candidates(state) == []
    state["custody_complete"] = True
    api = SettingsApi(state)
    monkeypatch.setattr(hygiene, "snapshot", lambda *args: copy.deepcopy(state))
    # A cache ID reused with a different key refuses before any mutation.
    planned["branches"] = []
    planned["digest"] = hygiene.digest({k: v for k, v in planned.items() if k != "digest"})
    approval = {
        "digest": planned["digest"],
        "action": "apply",
        "approval_reference": "synthetic inner control",
    }
    state["caches"][1] = {**removable, "version": "v2"}
    with pytest.raises(hygiene.Refusal, match="cache_drift"):
        hygiene.apply(
            planned, approval, git, api, tmp_path / "custody", tmp_path / "wrong-receipts", now
        )
    assert not api.mutations and browser in state["caches"] and main in state["caches"]
    state["caches"][1] = removable
    positive = hygiene.apply(
        planned, approval, git, api, tmp_path / "custody", tmp_path / "positive-receipts", now
    )
    assert positive["main_cache_survivor"] and positive["cache_under10GB"]
    assert state["caches"] == [main, browser, active] and positive["cache_hit"] == "NOT_OBSERVED"

    class RunApi:
        truncate = False
        bad = False
        missing = False

        def get(self, path):
            query = parse_qs(urlsplit(path).query)
            a, b = map(hygiene.instant, query["created"][0].split(".."))
            if self.truncate and b - a > timedelta(days=1):
                return {"total_count": 1000, "workflow_runs": []}
            rows = (
                []
                if self.missing
                else [
                    {
                        "id": int(a.timestamp()),
                        "created_at": (a + timedelta(seconds=1)).isoformat(),
                        "head_branch": "qa/wrong" if self.bad else "main",
                        "head_sha": "a" * 40,
                        "event": "push",
                        "status": "completed",
                        "conclusion": "cancelled",
                    }
                ]
            )
            return {"total_count": len(rows), "workflow_runs": rows}

    runs = RunApi()
    start = now - timedelta(days=7)
    observed = hygiene.observe(runs, start, now)
    assert (
        observed["windows"]["7"]["verdict"] == "PASS"
        and observed["windows"]["14"]["verdict"] == "UNKNOWN"
    )
    runs.truncate = True
    full = hygiene.observe(runs, now - timedelta(days=14), now)
    assert full["windows"]["14"]["verdict"] == "PASS" and len(full["windows"]["14"]["runs"]) > 1
    runs.bad = True
    assert hygiene.observe(runs, start, now)["windows"]["7"]["verdict"] == "FAIL"
    runs.bad = False
    runs.missing = True
    assert hygiene.observe(runs, start, now)["windows"]["7"]["verdict"] == "UNKNOWN"

    class Incomplete(hygiene.Github):
        def get(self, *args, **kwargs):
            return {"total_count": 1, "actions_caches": []}

    with pytest.raises(hygiene.Refusal, match="pagination"):
        Incomplete().caches()

    # Complete page count and stable main identity are independent positives.
    class Complete(hygiene.Github):
        def get(self, *args, **kwargs):
            return {"total_count": 1, "actions_caches": [main]}

    assert Complete().caches() == [main]
