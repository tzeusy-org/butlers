"""Tests for scripts/main_health_gate.py.

Regression guard for bu-vul8u. The batch merge driver checked each PR's own CI
before merging it and never read main's state between merges. Two PRs each
numbered a migration ``core_204``; each was green because a PR's CI can only see
its own branch. The post-merge "Migration Chain Integrity (main)" workflow fired
correctly on the merged tree and went red, and the driver merged several more
PRs onto that known-red main because nothing consumed the detector.

Detection was never the gap. So the tests that matter here are not "does the
guard notice a failure" but "does the guard refuse to call an absence of
evidence a pass". Four absences look identical to a naive reader and mean
different things:

* the workflow's ``paths:`` filter legitimately excluded this merge (proceed),
* the push webhook has not created the run yet (wait),
* the run exists but is still going (``conclusion`` is the empty string, not
  ``None``) (wait),
* the run was cancelled by a concurrency group (never a pass).

Every test below exists because conflating two of those is how a red main reads
green.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import main_health_gate as gate  # noqa: E402

pytestmark = pytest.mark.unit

REPO = "Tzeusy/butlers"
MERGED_SHA = "e567a497d0a3b1c2d3e4f5061728394a5b6c7d8e"

MIGRATION_WORKFLOW = textwrap.dedent(
    """
    name: Migration Chain Integrity (main)
    on:
      push:
        branches:
          - main
        paths:
          - "alembic/versions/**"
          - "src/butlers/modules/*/migrations/**"
          - "roster/*/migrations/**"
      workflow_dispatch:
    jobs:
      migration-chain-head:
        runs-on: ubuntu-latest
        steps:
          - run: uv run pytest tests/config/test_migration_chain_head.py -q
    """
)

CANCELLABLE_WORKFLOW = textwrap.dedent(
    """
    name: CI
    on:
      push:
        branches:
          - main
      pull_request:
    concurrency:
      group: ${{ github.workflow }}-${{ github.head_ref || github.ref }}
      cancel-in-progress: true
    jobs:
      check:
        runs-on: ubuntu-latest
        steps:
          - run: python3 scripts/check_countable_tasks.py
    """
)

TAG_ONLY_WORKFLOW = textwrap.dedent(
    """
    name: Release
    on:
      push:
        tags:
          - "v*"
    jobs:
      release:
        runs-on: ubuntu-latest
        steps:
          - run: echo release
    """
)

PULL_REQUEST_ONLY_WORKFLOW = textwrap.dedent(
    """
    name: PR only
    on:
      pull_request:
    jobs:
      lint:
        runs-on: ubuntu-latest
        steps:
          - run: python3 scripts/check_countable_tasks.py
    """
)


def _spec(
    *,
    filename: str = "migration-chain-main.yml",
    name: str = "Migration Chain Integrity (main)",
    paths: tuple[str, ...] | None = ("alembic/versions/**",),
    paths_ignore: tuple[str, ...] | None = None,
    verdict_capable: bool = True,
) -> gate.WorkflowSpec:
    return gate.WorkflowSpec(
        filename=filename,
        name=name,
        paths=paths,
        paths_ignore=paths_ignore,
        verdict_capable=verdict_capable,
        uncertainty_reason=None if verdict_capable else "branch-keyed cancel-in-progress",
    )


def _run(status: str, conclusion: str | None, *, run_number: int = 1) -> dict[str, object]:
    return {"status": status, "conclusion": conclusion, "run_number": run_number}


# --------------------------------------------------------------------------
# classify_run: what a single hosted run's status/conclusion pair actually says
# --------------------------------------------------------------------------


def test_in_flight_run_reports_empty_string_conclusion_not_null() -> None:
    """``gh --json conclusion`` yields "" for an unfinished run, never null.

    A naive ``select(.conclusion != null)`` treats that empty string as a
    settled verdict.
    """
    assert gate.classify_run("in_progress", "") is gate.RunState.IN_PROGRESS
    assert gate.classify_run("queued", "") is gate.RunState.IN_PROGRESS
    assert gate.classify_run("waiting", None) is gate.RunState.IN_PROGRESS


def test_completed_conclusions_map_to_settled_states() -> None:
    assert gate.classify_run("completed", "success") is gate.RunState.SUCCESS
    for failing in ("failure", "timed_out", "startup_failure", "action_required"):
        assert gate.classify_run("completed", failing) is gate.RunState.FAILURE


def test_cancelled_run_is_never_a_pass() -> None:
    """Every push to main cancels the previous CI run mid-batch.

    Cancelled is UNKNOWN, and UNKNOWN reads exactly like a green baseline unless
    it is given its own state.
    """
    state = gate.classify_run("completed", "cancelled")
    assert state is gate.RunState.CANCELLED
    assert state is not gate.RunState.SUCCESS


def test_completed_run_without_a_conclusion_is_indeterminate() -> None:
    assert gate.classify_run("completed", "") is gate.RunState.INDETERMINATE
    assert gate.classify_run("completed", "neutral") is gate.RunState.INDETERMINATE
    assert gate.classify_run("completed", "skipped") is gate.RunState.SKIPPED


# --------------------------------------------------------------------------
# path filters: "excluded" and "not created yet" are the same absence on the wire
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("alembic/versions/**", "alembic/versions/0001_init.py", True),
        ("alembic/versions/**", "alembic/versions/nested/0001.py", True),
        ("alembic/versions/**", "alembic/env.py", False),
        ("alembic/versions/**", "alembic/versionsfoo.py", False),
        ("src/butlers/modules/*/migrations/**", "src/butlers/modules/email/migrations/1.py", True),
        ("src/butlers/modules/*/migrations/**", "src/butlers/modules/email/tools.py", False),
        ("src/butlers/modules/*/migrations/**", "src/butlers/modules/a/b/migrations/1.py", False),
        ("**/conftest.py", "tests/scripts/conftest.py", True),
        ("**/conftest.py", "conftest.py", True),
        ("docs/*.md", "docs/index.md", True),
        ("docs/*.md", "docs/api/index.md", False),
        ("scripts/check_?.py", "scripts/check_a.py", True),
        ("scripts/check_?.py", "scripts/check_ab.py", False),
    ],
)
def test_github_path_matching_follows_glob_semantics(
    pattern: str, path: str, expected: bool
) -> None:
    assert gate.github_path_matches(pattern, path) is expected


def test_path_filter_exclusion_is_distinguished_from_a_missing_run() -> None:
    """The two absences that must never be conflated.

    Same workflow, same empty run list. The only difference is whether the
    merged commit touched a path the filter covers.
    """
    spec = _spec()

    excluded = gate.classify_workflow(spec, runs=[], changed_paths=("docs/index.md",))
    not_yet = gate.classify_workflow(spec, runs=[], changed_paths=("alembic/versions/0002.py",))

    assert excluded is gate.WorkflowVerdict.EXCLUDED_BY_PATH_FILTER
    assert not_yet is gate.WorkflowVerdict.NOT_CREATED_YET
    assert excluded is not not_yet


def test_unfiltered_workflow_with_no_run_is_never_excluded() -> None:
    spec = _spec(paths=None)
    verdict = gate.classify_workflow(spec, runs=[], changed_paths=("docs/index.md",))
    assert verdict is gate.WorkflowVerdict.NOT_CREATED_YET


def test_unknown_changed_paths_cannot_claim_path_filter_exclusion() -> None:
    """A truncated or unavailable file list must not be read as "not covered"."""
    spec = _spec()
    verdict = gate.classify_workflow(spec, runs=[], changed_paths=None)
    assert verdict is gate.WorkflowVerdict.CHANGED_PATHS_UNKNOWN


def test_paths_ignore_excludes_only_when_every_changed_path_is_ignored() -> None:
    spec = _spec(paths=None, paths_ignore=("docs/**",))
    assert (
        gate.classify_workflow(spec, runs=[], changed_paths=("docs/a.md", "docs/b.md"))
        is gate.WorkflowVerdict.EXCLUDED_BY_PATH_FILTER
    )
    assert (
        gate.classify_workflow(spec, runs=[], changed_paths=("docs/a.md", "src/x.py"))
        is gate.WorkflowVerdict.NOT_CREATED_YET
    )


def test_existing_runs_outrank_the_path_filter() -> None:
    """If GitHub created a run, its verdict is the answer regardless of paths."""
    spec = _spec()
    verdict = gate.classify_workflow(
        spec,
        runs=[_run("completed", "failure")],
        changed_paths=("docs/index.md",),
    )
    assert verdict is gate.WorkflowVerdict.RED


def test_latest_attempt_wins_over_an_earlier_run() -> None:
    spec = _spec()
    verdict = gate.classify_workflow(
        spec,
        runs=[
            _run("completed", "failure", run_number=7),
            _run("completed", "success", run_number=9),
        ],
        changed_paths=("alembic/versions/0002.py",),
    )
    assert verdict is gate.WorkflowVerdict.GREEN


def test_cancelled_run_yields_no_trustworthy_verdict() -> None:
    spec = _spec()
    verdict = gate.classify_workflow(
        spec,
        runs=[_run("completed", "cancelled")],
        changed_paths=("alembic/versions/0002.py",),
    )
    assert verdict is gate.WorkflowVerdict.NO_TRUSTWORTHY_VERDICT


# --------------------------------------------------------------------------
# workflow parsing: which workflows can even earn a per-SHA verdict on main
# --------------------------------------------------------------------------


def test_push_on_main_workflow_parses_its_path_filter(tmp_path: Path) -> None:
    path = tmp_path / "migration-chain-main.yml"
    path.write_text(MIGRATION_WORKFLOW, encoding="utf-8")

    spec = gate.parse_workflow(path)

    assert spec is not None
    assert spec.filename == "migration-chain-main.yml"
    assert spec.name == "Migration Chain Integrity (main)"
    assert spec.paths == (
        "alembic/versions/**",
        "src/butlers/modules/*/migrations/**",
        "roster/*/migrations/**",
    )
    assert spec.verdict_capable is True


def test_branch_keyed_cancel_in_progress_workflow_cannot_earn_a_verdict(tmp_path: Path) -> None:
    """Main structurally cannot go green mid-batch under a cancelling group.

    Every push cancels the previous run, and cancelled is UNKNOWN. Such a
    workflow is excluded from the hosted half entirely rather than left to
    produce a permanent UNKNOWN that would stall every batch.
    """
    path = tmp_path / "ci.yml"
    path.write_text(CANCELLABLE_WORKFLOW, encoding="utf-8")

    spec = gate.parse_workflow(path)

    assert spec is not None
    assert spec.verdict_capable is False
    assert spec.uncertainty_reason is not None


def test_workflow_not_triggered_by_push_to_main_is_not_a_main_detector(tmp_path: Path) -> None:
    path = tmp_path / "pr-only.yml"
    path.write_text(PULL_REQUEST_ONLY_WORKFLOW, encoding="utf-8")
    assert gate.parse_workflow(path) is None


def test_tag_only_push_workflow_is_not_a_main_detector(tmp_path: Path) -> None:
    """A release workflow never fires on a branch push.

    Polling it would report a permanent "not created yet" and stall every batch
    on evidence that can never arrive.
    """
    path = tmp_path / "release.yml"
    path.write_text(TAG_ONLY_WORKFLOW, encoding="utf-8")
    assert gate.parse_workflow(path) is None


def test_branches_ignore_excluding_main_is_not_a_main_detector(tmp_path: Path) -> None:
    path = tmp_path / "not-main.yml"
    path.write_text(
        textwrap.dedent(
            """
            name: Not main
            on:
              push:
                branches-ignore:
                  - main
            jobs:
              j:
                runs-on: ubuntu-latest
                steps:
                  - run: echo hi
            """
        ),
        encoding="utf-8",
    )
    assert gate.parse_workflow(path) is None


def test_real_repository_workflows_classify_as_expected() -> None:
    specs = {s.filename: s for s in gate.load_push_on_main_workflows(REPO_ROOT)}

    assert "migration-chain-main.yml" in specs, "the post-merge migration detector must be found"
    assert specs["migration-chain-main.yml"].verdict_capable is True
    assert specs["migration-chain-main.yml"].paths is not None

    assert "ci.yml" in specs
    assert specs["ci.yml"].verdict_capable is False

    assert "release.yml" not in specs, "a tag-only push workflow is not a main detector"


def test_verdict_capable_workflows_are_the_only_polled_ones() -> None:
    polled = {s.filename for s in gate.verdict_capable_workflows(REPO_ROOT)}
    assert "migration-chain-main.yml" in polled
    assert "ci.yml" not in polled


# --------------------------------------------------------------------------
# the halt decision
# --------------------------------------------------------------------------


def test_a_red_workflow_halts_the_batch() -> None:
    decision = gate.decide(
        {"migration-chain-main.yml": gate.WorkflowVerdict.RED},
        guard_failures=(),
        wait_budget_exhausted=False,
    )
    assert decision is gate.Decision.HALT


def test_a_failing_local_guard_halts_even_when_hosted_checks_are_green() -> None:
    decision = gate.decide(
        {"migration-chain-main.yml": gate.WorkflowVerdict.GREEN},
        guard_failures=("check_duplicate_toplevel_names",),
        wait_budget_exhausted=False,
    )
    assert decision is gate.Decision.HALT


def test_a_missing_run_waits_and_then_halts_but_never_proceeds() -> None:
    """ "No run" is UNKNOWN, not pass, and an exhausted wait stays UNKNOWN."""
    verdicts = {"migration-chain-main.yml": gate.WorkflowVerdict.NOT_CREATED_YET}
    assert (
        gate.decide(verdicts, guard_failures=(), wait_budget_exhausted=False) is gate.Decision.WAIT
    )
    assert (
        gate.decide(verdicts, guard_failures=(), wait_budget_exhausted=True) is gate.Decision.HALT
    )


@pytest.mark.parametrize(
    "verdict",
    [
        gate.WorkflowVerdict.IN_PROGRESS,
        gate.WorkflowVerdict.NO_TRUSTWORTHY_VERDICT,
        gate.WorkflowVerdict.CHANGED_PATHS_UNKNOWN,
    ],
)
def test_every_unsettled_verdict_waits_rather_than_proceeding(
    verdict: gate.WorkflowVerdict,
) -> None:
    assert (
        gate.decide({"w.yml": verdict}, guard_failures=(), wait_budget_exhausted=False)
        is gate.Decision.WAIT
    )


def test_path_filter_exclusion_plus_green_proceeds() -> None:
    decision = gate.decide(
        {
            "migration-chain-main.yml": gate.WorkflowVerdict.EXCLUDED_BY_PATH_FILTER,
            "other.yml": gate.WorkflowVerdict.GREEN,
        },
        guard_failures=(),
        wait_budget_exhausted=False,
    )
    assert decision is gate.Decision.PROCEED


def test_report_exit_codes_separate_red_from_unknown() -> None:
    proceed = gate.HealthReport(
        sha=MERGED_SHA,
        decision=gate.Decision.PROCEED,
        workflows={"migration-chain-main.yml": gate.WorkflowVerdict.EXCLUDED_BY_PATH_FILTER},
        guard_failures=(),
        reasons=(),
    )
    halted = gate.HealthReport(
        sha=MERGED_SHA,
        decision=gate.Decision.HALT,
        workflows={"migration-chain-main.yml": gate.WorkflowVerdict.RED},
        guard_failures=(),
        reasons=("migration-chain-main.yml is red",),
    )
    unknown = gate.HealthReport(
        sha=MERGED_SHA,
        decision=gate.Decision.WAIT,
        workflows={"migration-chain-main.yml": gate.WorkflowVerdict.NOT_CREATED_YET},
        guard_failures=(),
        reasons=("no run yet",),
    )

    assert proceed.exit_code == 0
    assert halted.exit_code == 1
    assert unknown.exit_code == 2
    assert proceed.to_dict()["decision"] == "proceed"


def test_halting_for_want_of_evidence_is_not_reported_as_a_failure() -> None:
    """An exhausted wait halts, but it is still an unknown, not a red."""
    report = gate.HealthReport(
        sha=MERGED_SHA,
        decision=gate.Decision.HALT,
        workflows={"migration-chain-main.yml": gate.WorkflowVerdict.NOT_CREATED_YET},
        guard_failures=(),
        reasons=("wait budget exhausted",),
    )
    assert report.exit_code == 2


# --------------------------------------------------------------------------
# hosted lookups: the workflow FILENAME is the key, not the display name
# --------------------------------------------------------------------------


def test_runs_are_fetched_by_workflow_filename(monkeypatch: pytest.MonkeyPatch) -> None:
    """``gh run list --workflow`` needs the filename; the API path does too."""
    seen: list[list[str]] = []

    def fake_run_gh_json(args: list[str]) -> dict[str, object]:
        seen.append(args)
        return {"workflow_runs": [_run("completed", "success")]}

    monkeypatch.setattr(gate, "run_gh_json", fake_run_gh_json)

    runs = gate.fetch_workflow_runs(REPO, "migration-chain-main.yml", MERGED_SHA)

    assert runs == [_run("completed", "success")]
    endpoint = seen[0][-1]
    assert "workflows/migration-chain-main.yml/runs" in endpoint
    assert f"head_sha={MERGED_SHA}" in endpoint
    assert "Migration Chain Integrity" not in endpoint


def test_truncated_commit_file_list_reports_unknown_changed_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GitHub caps the commit files array; a capped list cannot prove exclusion."""
    monkeypatch.setattr(
        gate,
        "run_gh_json",
        lambda _args: {"files": [{"filename": f"src/f{i}.py"} for i in range(300)]},
    )
    assert gate.fetch_changed_paths(REPO, MERGED_SHA) is None


def test_commit_file_list_is_returned_when_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gate,
        "run_gh_json",
        lambda _args: {"files": [{"filename": "alembic/versions/0002.py"}]},
    )
    assert gate.fetch_changed_paths(REPO, MERGED_SHA) == ("alembic/versions/0002.py",)


def test_missing_files_key_reports_unknown_rather_than_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gate, "run_gh_json", lambda _args: {"sha": MERGED_SHA})
    assert gate.fetch_changed_paths(REPO, MERGED_SHA) is None


# --------------------------------------------------------------------------
# local guards: enumerated from the tree under test, not from a frozen list
# --------------------------------------------------------------------------


def test_guards_are_discovered_from_the_tree_under_test() -> None:
    names = {g.name for g in gate.discover_guards(REPO_ROOT)}
    for expected in (
        "check_spec_overwrites",
        "check_archived_requirements_landed",
        "check_countable_tasks",
        "check_cited_requirements_resolve",
        "check-no-em-dashes",
        "check_duplicate_toplevel_names",
        "test_migration_chain_head",
    ):
        assert expected in names, f"{expected} must be discovered from the repo's own CI definition"


def test_full_suite_and_option_taking_commands_are_not_treated_as_guards() -> None:
    names = {g.name for g in gate.discover_guards(REPO_ROOT)}
    assert "session_link_guard" not in names, "takes options and is pull-request only"
    assert "combine_review_comment_sources" not in names, "takes options"
    for guard in gate.discover_guards(REPO_ROOT):
        assert not any(
            arg.startswith("-") for arg in guard.argv[1:] if arg not in gate.PYTEST_FLAGS
        )


def test_a_new_repo_wide_guard_cannot_stay_invisible() -> None:
    """A guard added to CI but invisible to discovery is the #3848 shape.

    An absent check is invisible to both a fail-scan and a required-name list.
    This asserts discovery sees every guard script the repository's own CI
    definition references, so adding one in an unparseable shape fails here
    instead of silently dropping out of the batch gate.
    """
    referenced = gate.guard_scripts_referenced_by_ci(REPO_ROOT)
    discovered = {g.argv[0] for g in gate.discover_guards(REPO_ROOT)}
    assert referenced, "the scan must not go quiet"
    missing = referenced - discovered
    assert not missing, f"CI references guards discovery cannot see: {sorted(missing)}"


def test_make_target_recipes_are_resolved_to_their_command(tmp_path: Path) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "check_thing.py").write_text("", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "Makefile").write_text(
        "check-thing:\n\tpython3 scripts/check_thing.py src/\n",
        encoding="utf-8",
    )

    guard = gate.parse_guard_command("make check-thing", tmp_path)

    assert guard is not None
    assert guard.argv == ("scripts/check_thing.py", "src/")


@pytest.mark.parametrize(
    "command",
    [
        "uv run pytest tests/ roster/ -q --maxfail=1 --tb=short --ignore=tests/e2e",
        "python3 scripts/session_link_guard.py --commit-range origin/main..HEAD",
        "uv lock --check",
        "npm run lint",
        "make lint",
    ],
)
def test_non_guard_commands_are_rejected(command: str) -> None:
    assert gate.parse_guard_command(command, REPO_ROOT) is None


def test_single_test_file_pytest_step_is_a_guard() -> None:
    guard = gate.parse_guard_command(
        "uv run pytest tests/config/test_migration_chain_head.py -q", REPO_ROOT
    )
    assert guard is not None
    assert guard.name == "test_migration_chain_head"
    assert guard.argv[0] == "tests/config/test_migration_chain_head.py"


def test_guard_execution_reports_failures_by_name(tmp_path: Path) -> None:
    _git_init(tmp_path)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "check_good.py").write_text("", encoding="utf-8")
    (tmp_path / "scripts" / "check_bad.py").write_text("raise SystemExit(1)\n", encoding="utf-8")
    _git_commit_all(tmp_path)
    guards = (
        gate.Guard(name="check_good", argv=("scripts/check_good.py",), source="fixture"),
        gate.Guard(name="check_bad", argv=("scripts/check_bad.py",), source="fixture"),
    )

    failures = gate.run_guards(tmp_path, guards)

    assert failures == ("check_bad",)


def test_a_guard_that_dirties_the_tree_is_a_failure(tmp_path: Path) -> None:
    """The frontend copy inventory's signal is a dirty file, never stdout.

    Handling that by name would hardcode one generator; requiring a clean tree
    after the sweep catches every generator-shaped guard the same way.
    """
    _git_init(tmp_path)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "inventory.md").write_text("stale\n", encoding="utf-8")
    (tmp_path / "scripts" / "regen.py").write_text(
        "from pathlib import Path\nPath('inventory.md').write_text('fresh\\n')\n",
        encoding="utf-8",
    )
    _git_commit_all(tmp_path)
    guards = (gate.Guard(name="regen", argv=("scripts/regen.py",), source="fixture"),)

    failures = gate.run_guards(tmp_path, guards)

    assert failures == (gate.DIRTY_TREE_FAILURE,)


def test_guard_timeout_is_a_failure_not_a_pass(tmp_path: Path) -> None:
    _git_init(tmp_path)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "slow.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    _git_commit_all(tmp_path)
    guards = (gate.Guard(name="slow", argv=("scripts/slow.py",), source="fixture"),)

    failures = gate.run_guards(tmp_path, guards, timeout_s=1)

    assert failures == ("slow",)


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)


def _git_commit_all(path: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=path, check=True)


# --------------------------------------------------------------------------
# end to end through the module entry point
# --------------------------------------------------------------------------


def test_evaluate_halts_on_the_red_post_merge_detector(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate, "verdict_capable_workflows", lambda _tree: (_spec(),))
    monkeypatch.setattr(
        gate, "fetch_changed_paths", lambda *_a: ("alembic/versions/0002_core_204.py",)
    )
    monkeypatch.setattr(gate, "fetch_workflow_runs", lambda *_a: [_run("completed", "failure")])

    report = gate.evaluate(REPO, MERGED_SHA, tree=REPO_ROOT, run_local_guards=False)

    assert report.decision is gate.Decision.HALT
    assert report.exit_code == 1
    assert report.workflows["migration-chain-main.yml"] is gate.WorkflowVerdict.RED


def test_evaluate_proceeds_when_the_merge_touched_no_filtered_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gate, "verdict_capable_workflows", lambda _tree: (_spec(),))
    monkeypatch.setattr(gate, "fetch_changed_paths", lambda *_a: ("docs/index.md",))
    monkeypatch.setattr(gate, "fetch_workflow_runs", lambda *_a: [])

    report = gate.evaluate(REPO, MERGED_SHA, tree=REPO_ROOT, run_local_guards=False)

    assert report.decision is gate.Decision.PROCEED
    assert report.exit_code == 0


def test_evaluate_waits_when_the_run_has_not_appeared_yet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gate, "verdict_capable_workflows", lambda _tree: (_spec(),))
    monkeypatch.setattr(gate, "fetch_changed_paths", lambda *_a: ("alembic/versions/0002.py",))
    monkeypatch.setattr(gate, "fetch_workflow_runs", lambda *_a: [])

    report = gate.evaluate(REPO, MERGED_SHA, tree=REPO_ROOT, run_local_guards=False)

    assert report.decision is gate.Decision.WAIT
    assert report.exit_code == 2
