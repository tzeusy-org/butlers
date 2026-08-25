#!/usr/bin/env python3
"""Decide whether main is healthy enough for the next merge in a batch.

bu-vul8u: the batch merge driver checks each PR's own CI before merging it and
never reads main's state between merges. On 2026-08-24 two PRs each numbered
their migration ``core_204``. Both were green, because a pull request's CI can
only ever see its own branch. The repository's post-merge detector -- the
"Migration Chain Integrity (main)" workflow -- fired correctly on the merged
tree and went red, and the driver merged several more PRs onto that known-red
main because nothing consumed the detector.

Detection was never the gap. This module is the consumer.

It answers one question about one SHA on main: proceed, wait, or halt. The
answer comes from two halves, and the split is deliberate.

The hosted half polls the push-triggered workflow runs for that exact SHA. Four
different situations look like the same absence on the wire, and conflating any
two of them is how a red main reads green:

* the workflow's ``paths:`` filter legitimately excluded the merge (proceed),
* the push webhook has not created the run yet (wait),
* the run exists but has not finished, which ``gh --json conclusion`` reports as
  the empty string rather than null (wait),
* the run was cancelled, which is UNKNOWN and never a pass.

Only workflows that can actually earn a per-SHA verdict are polled. A workflow
whose concurrency group is keyed on the branch ref with ``cancel-in-progress``
is structurally incapable of settling mid-batch: every push cancels the previous
run. Polling it would produce a permanent UNKNOWN, so it is excluded here and
covered by the local half instead.

The local half runs the repository's own repo-wide guards against the merged
tree directly. That is where a green verdict actually comes from during a batch.
The guards are enumerated from the tree under test rather than from a list
frozen here: a new repo-wide guard is absent from every branch cut before it,
and an absent check is invisible to both a fail-scan and a required-name list.
That is exactly how a bad PR landed once already.

Exit codes:
  0  proceed -- main is healthy for the next merge
  1  halt -- something is definitively red
  2  unresolved -- no trustworthy verdict yet; wait and re-run
  3  usage or transport error
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

DEFAULT_REPO = "Tzeusy/butlers"
WORKFLOWS_SUBDIR = Path(".github") / "workflows"

#: Failure name recorded when a guard leaves the merged tree dirty. The frontend
#: copy inventory guard reports staleness by rewriting a committed file, not
#: through its exit code; requiring a clean tree after the sweep catches every
#: generator-shaped guard without naming any of them.
DIRTY_TREE_FAILURE = "worktree-dirty-after-guards"

#: Options a discovered single-file pytest guard may carry. Anything else makes
#: the command too specific to be a repo-wide guard invocation.
PYTEST_FLAGS = frozenset({"-q", "-x", "--tb=short", "--tb=line", "-p", "no:cacheprovider"})

#: GitHub's commit endpoint returns at most this many files. A capped list
#: cannot prove that a path filter excluded the merge.
_COMMIT_FILES_CAP = 300

_FAILING_CONCLUSIONS = frozenset({"failure", "timed_out", "startup_failure", "action_required"})


class RunState(StrEnum):
    """What one hosted workflow run's status/conclusion pair actually says."""

    SUCCESS = "success"
    FAILURE = "failure"
    IN_PROGRESS = "in-progress"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    INDETERMINATE = "indeterminate"


class WorkflowVerdict(StrEnum):
    """What one workflow says about one SHA on main."""

    GREEN = "green"
    RED = "red"
    EXCLUDED_BY_PATH_FILTER = "excluded-by-path-filter"
    NOT_CREATED_YET = "not-created-yet"
    IN_PROGRESS = "in-progress"
    NO_TRUSTWORTHY_VERDICT = "no-trustworthy-verdict"
    CHANGED_PATHS_UNKNOWN = "changed-paths-unknown"


#: Verdicts that are neither a pass nor a proven failure. None of them may be
#: read as green, and none of them may halt a batch on their own either.
UNSETTLED_VERDICTS = frozenset(
    {
        WorkflowVerdict.NOT_CREATED_YET,
        WorkflowVerdict.IN_PROGRESS,
        WorkflowVerdict.NO_TRUSTWORTHY_VERDICT,
        WorkflowVerdict.CHANGED_PATHS_UNKNOWN,
    }
)


class Decision(StrEnum):
    PROCEED = "proceed"
    WAIT = "wait"
    HALT = "halt"


@dataclass(frozen=True)
class WorkflowSpec:
    """A push-on-main workflow and whether it can earn a per-SHA verdict."""

    filename: str
    name: str
    paths: tuple[str, ...] | None
    paths_ignore: tuple[str, ...] | None
    verdict_capable: bool
    uncertainty_reason: str | None = None


@dataclass(frozen=True)
class Guard:
    """One repo-wide guard invocation discovered from the tree under test."""

    name: str
    argv: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class HealthReport:
    sha: str
    decision: Decision
    workflows: Mapping[str, WorkflowVerdict]
    guard_failures: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    guards_run: tuple[str, ...] = field(default=())

    @property
    def exit_code(self) -> int:
        """0 proceed, 1 definitively red, 2 unresolved.

        An exhausted wait budget resolves to HALT, but it halts for want of
        evidence rather than because something failed. Collapsing the two would
        report a missing run as a failing one, and the whole point of this gate
        is that those are different answers.
        """
        if self.decision is Decision.PROCEED:
            return 0
        if self.guard_failures or any(
            verdict is WorkflowVerdict.RED for verdict in self.workflows.values()
        ):
            return 1
        return 2

    def to_dict(self) -> dict[str, Any]:
        return {
            "sha": self.sha,
            "decision": self.decision.value,
            "workflows": {name: verdict.value for name, verdict in self.workflows.items()},
            "guard_failures": list(self.guard_failures),
            "guards_run": list(self.guards_run),
            "reasons": list(self.reasons),
            "exit_code": self.exit_code,
        }


# ---------------------------------------------------------------------------
# hosted half
# ---------------------------------------------------------------------------


def classify_run(status: str | None, conclusion: str | None) -> RunState:
    """Classify one workflow run without mistaking an absence for a verdict.

    ``gh --json conclusion`` returns the empty string, not null, while a run is
    still in flight, so a naive ``conclusion != null`` test treats an unfinished
    run as settled.
    """
    settled = (conclusion or "").strip()
    if not settled:
        return RunState.IN_PROGRESS if (status or "") != "completed" else RunState.INDETERMINATE
    if settled == "success":
        return RunState.SUCCESS
    if settled in _FAILING_CONCLUSIONS:
        return RunState.FAILURE
    if settled == "cancelled":
        return RunState.CANCELLED
    if settled == "skipped":
        return RunState.SKIPPED
    return RunState.INDETERMINATE


def _translate_github_glob(pattern: str) -> re.Pattern[str]:
    out: list[str] = []
    index = 0
    length = len(pattern)
    while index < length:
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("/**", index):
            out.append("(?:/.*)?")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(pattern[index]))
            index += 1
    return re.compile("^" + "".join(out) + "$")


def github_path_matches(pattern: str, path: str) -> bool:
    """Match one changed path against one GitHub workflow filter pattern."""
    return _translate_github_glob(pattern).match(path) is not None


def _any_path_matches(patterns: Sequence[str], paths: Iterable[str]) -> bool:
    return any(github_path_matches(pattern, path) for pattern in patterns for path in paths)


def workflow_covers_paths(spec: WorkflowSpec, changed_paths: Sequence[str]) -> bool:
    """Would GitHub have created a run for this workflow on this change?"""
    if spec.paths is not None and not _any_path_matches(spec.paths, changed_paths):
        return False
    if spec.paths_ignore is not None:
        unignored = [p for p in changed_paths if not _any_path_matches(spec.paths_ignore, [p])]
        if not unignored:
            return False
    return True


def _latest_run(runs: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    return max(runs, key=lambda run: (run.get("run_number") or 0, run.get("id") or 0))


def classify_workflow(
    spec: WorkflowSpec,
    runs: Sequence[Mapping[str, Any]],
    changed_paths: Sequence[str] | None,
) -> WorkflowVerdict:
    """Say what one workflow reports about one SHA, absences included."""
    if runs:
        state = classify_run(_latest_run(runs).get("status"), _latest_run(runs).get("conclusion"))
        if state is RunState.SUCCESS:
            return WorkflowVerdict.GREEN
        if state is RunState.FAILURE:
            return WorkflowVerdict.RED
        if state is RunState.IN_PROGRESS:
            return WorkflowVerdict.IN_PROGRESS
        return WorkflowVerdict.NO_TRUSTWORTHY_VERDICT

    # No run. That is three different situations wearing one costume.
    if changed_paths is None:
        return WorkflowVerdict.CHANGED_PATHS_UNKNOWN
    if not workflow_covers_paths(spec, changed_paths):
        return WorkflowVerdict.EXCLUDED_BY_PATH_FILTER
    return WorkflowVerdict.NOT_CREATED_YET


def _as_tuple(value: Any) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return None


def _push_trigger_on_main(workflow: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Return the push trigger if a push to main fires this workflow.

    A tag-only push trigger (``push: tags:``) never fires on a branch push, so a
    release workflow is not a main detector and must not be polled for a verdict
    it can never produce. Reading it as one turns every batch into a permanent
    "no run yet".
    """
    # PyYAML parses the unquoted key `on:` as the boolean True.
    triggers = workflow.get("on", workflow.get(True))
    if not isinstance(triggers, Mapping):
        return None
    push = triggers.get("push")
    if not isinstance(push, Mapping):
        return None
    branches = _as_tuple(push.get("branches"))
    branches_ignore = _as_tuple(push.get("branches-ignore"))
    if branches is None and branches_ignore is None:
        tag_only = push.get("tags") is not None or push.get("tags-ignore") is not None
        return None if tag_only else push
    if branches is not None and not any(
        github_path_matches(pattern, "main") for pattern in branches
    ):
        return None
    if branches_ignore is not None and any(
        github_path_matches(pattern, "main") for pattern in branches_ignore
    ):
        return None
    return push


def _cancellation_reason(workflow: Mapping[str, Any]) -> str | None:
    """Explain why a workflow cannot settle on main, if it cannot.

    A concurrency group keyed on the branch ref with ``cancel-in-progress`` means
    each push to main cancels the previous run. Cancelled is UNKNOWN, and an
    UNKNOWN reads exactly like a green baseline, so such a workflow is never
    asked for a verdict.
    """
    concurrency = workflow.get("concurrency")
    if isinstance(concurrency, str):
        concurrency = {"group": concurrency}
    if not isinstance(concurrency, Mapping):
        return None
    if not concurrency.get("cancel-in-progress"):
        return None
    group = str(concurrency.get("group") or "")
    if "github.ref" in group or "github.sha" not in group:
        return f"branch-keyed cancel-in-progress concurrency group: {group or '(unset)'}"
    return None


def parse_workflow(path: Path) -> WorkflowSpec | None:
    """Return the spec for a push-on-main workflow, or None if it is not one."""
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(workflow, Mapping):
        return None
    push = _push_trigger_on_main(workflow)
    if push is None:
        return None
    reason = _cancellation_reason(workflow)
    return WorkflowSpec(
        filename=path.name,
        name=str(workflow.get("name") or path.stem),
        paths=_as_tuple(push.get("paths")),
        paths_ignore=_as_tuple(push.get("paths-ignore")),
        verdict_capable=reason is None,
        uncertainty_reason=reason,
    )


def load_push_on_main_workflows(tree: Path) -> tuple[WorkflowSpec, ...]:
    workflows_dir = tree / WORKFLOWS_SUBDIR
    specs = [
        spec
        for path in sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml"))
        if (spec := parse_workflow(path)) is not None
    ]
    return tuple(specs)


def verdict_capable_workflows(tree: Path) -> tuple[WorkflowSpec, ...]:
    """The workflows whose per-SHA conclusion on main can be trusted."""
    return tuple(spec for spec in load_push_on_main_workflows(tree) if spec.verdict_capable)


def run_gh_json(args: list[str]) -> Any:
    """Run ``gh`` and return JSON, surfacing any transport or API error."""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if proc.returncode:
        raise RuntimeError(
            f"gh {' '.join(args[:3])} failed ({proc.returncode}): {proc.stderr.strip()}"
        )
    return json.loads(proc.stdout)


def fetch_workflow_runs(repo: str, workflow_filename: str, sha: str) -> list[dict[str, Any]]:
    """List push runs of one workflow for one SHA.

    Both this endpoint and ``gh run list --workflow`` key on the workflow's
    FILENAME, never on its display name.
    """
    payload = run_gh_json(
        [
            "api",
            f"repos/{repo}/actions/workflows/{workflow_filename}/runs"
            f"?head_sha={sha}&event=push&per_page=50",
        ]
    )
    runs = payload.get("workflow_runs") if isinstance(payload, Mapping) else None
    return list(runs) if isinstance(runs, list) else []


def fetch_changed_paths(repo: str, sha: str) -> tuple[str, ...] | None:
    """Return the commit's changed paths, or None when they cannot be trusted.

    None is not "nothing changed". It is "this cannot prove a path filter
    excluded the merge", which must never be read as an exclusion.
    """
    payload = run_gh_json(["api", f"repos/{repo}/commits/{sha}"])
    files = payload.get("files") if isinstance(payload, Mapping) else None
    if not isinstance(files, list) or len(files) >= _COMMIT_FILES_CAP:
        return None
    names = [entry.get("filename") for entry in files if isinstance(entry, Mapping)]
    if any(not isinstance(name, str) or not name for name in names):
        return None
    return tuple(str(name) for name in names)


# ---------------------------------------------------------------------------
# local half
# ---------------------------------------------------------------------------


def _makefile_recipe(tree: Path, target: str) -> str | None:
    makefile = tree / "Makefile"
    if not makefile.is_file():
        return None
    recipe: list[str] = []
    collecting = False
    for line in makefile.read_text(encoding="utf-8").splitlines():
        if collecting:
            if line.startswith("\t"):
                recipe.append(line[1:].strip())
                continue
            if not line.strip():
                continue
            break
        if line.startswith(f"{target}:"):
            collecting = True
    return recipe[0] if len(recipe) == 1 else None


def parse_guard_command(command: str, tree: Path, *, source: str = "") -> Guard | None:
    """Recognise a repo-wide guard invocation by its shape, not by its name.

    Two shapes qualify, and both describe a command that inspects the tree
    rather than one that configures a specific run:

    * a repository script invoked with no options, only existing paths, and
    * pytest pointed at exactly one test file.

    Anything with a flag beyond the pytest basics (a marker expression, a
    ``--maxfail``, a ``--commit-range``) is a tuned invocation, not a guard.
    """
    if "\n" in command.strip():
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens:
        return None
    if tokens[:2] == ["uv", "run"]:
        tokens = tokens[2:]
    if tokens[:1] == ["make"] and len(tokens) == 2:
        recipe = _makefile_recipe(tree, tokens[1])
        return None if recipe is None else parse_guard_command(recipe, tree, source=source)
    if tokens and tokens[0] in {"python", "python3"} and tokens[1:2] == ["-m"]:
        tokens = tokens[2:]

    if tokens[0] in {"python", "python3"} and len(tokens) >= 2:
        script = tokens[1]
        args = tokens[2:]
        if not script.startswith("scripts/") or not script.endswith(".py"):
            return None
        if not (tree / script).is_file():
            return None
        if any(arg.startswith("-") or not (tree / arg).exists() for arg in args):
            return None
        return Guard(name=Path(script).stem, argv=(script, *args), source=source)

    if tokens[0] == "pytest":
        rest = tokens[1:]
        positionals = [token for token in rest if not token.startswith("-")]
        options = [token for token in rest if token.startswith("-")]
        if len(positionals) != 1 or any(option not in PYTEST_FLAGS for option in options):
            return None
        target = positionals[0]
        if not target.endswith(".py") or not (tree / target).is_file():
            return None
        return Guard(name=Path(target).stem, argv=(target, *options), source=source)

    return None


def _workflow_run_commands(tree: Path) -> list[tuple[str, str]]:
    commands: list[tuple[str, str]] = []
    workflows_dir = tree / WORKFLOWS_SUBDIR
    if not workflows_dir.is_dir():
        return commands
    for path in sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(workflow, Mapping):
            continue
        for job_name, job in (workflow.get("jobs") or {}).items():
            if not isinstance(job, Mapping):
                continue
            for step in job.get("steps") or []:
                run = step.get("run") if isinstance(step, Mapping) else None
                if isinstance(run, str):
                    commands.append((run, f"{path.name}:{job_name}"))
    return commands


def discover_guards(tree: Path) -> tuple[Guard, ...]:
    """Enumerate repo-wide guards from the tree's own CI definition.

    Enumerating rather than hardcoding is the point: a new guard is absent from
    every branch cut before it, and a frozen list would keep gating the merged
    tree with yesterday's checks while the newest one silently never runs.
    """
    guards: dict[str, Guard] = {}
    for command, source in _workflow_run_commands(tree):
        guard = parse_guard_command(command, tree, source=source)
        if guard is not None and guard.name not in guards:
            guards[guard.name] = guard
    return tuple(guards.values())


def guard_scripts_referenced_by_ci(tree: Path) -> set[str]:
    """Every ``scripts/check*.py`` the CI definition mentions, however it runs.

    Discovery that quietly stops seeing a guard is the same defect one level up,
    so this deliberately reads the raw text rather than the parsed commands.
    """
    referenced: set[str] = set()
    pattern = re.compile(r"scripts/check[\w.-]*\.py")
    sources = list((tree / WORKFLOWS_SUBDIR).glob("*.y*ml"))
    makefile = tree / "Makefile"
    if makefile.is_file():
        sources.append(makefile)
    make_targets = re.compile(r"make ([\w-]+)")
    for path in sources:
        text = path.read_text(encoding="utf-8")
        referenced.update(pattern.findall(text))
        if path.name != "Makefile":
            for target in make_targets.findall(text):
                recipe = _makefile_recipe(tree, target)
                if recipe:
                    referenced.update(pattern.findall(recipe))
    return {name for name in referenced if (tree / name).is_file()}


def _tree_is_dirty(tree: Path) -> bool:
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tree,
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(proc.stdout.strip())


def run_guards(
    tree: Path,
    guards: Sequence[Guard],
    *,
    python: str | None = None,
    timeout_s: int = 600,
) -> tuple[str, ...]:
    """Run every guard against the merged tree and name the ones that failed.

    ``PYTHONPATH`` is load-bearing. The interpreter is usually the caller's venv,
    whose editable install resolves ``butlers.migrations`` -- and therefore the
    Alembic directory -- to the caller's own tree. Without this the migration
    chain guard would check that tree against itself and pass every time.
    """
    interpreter = python or sys.executable
    env_src = str((tree / "src").resolve())
    failures: list[str] = []
    for guard in guards:
        argv = (
            [interpreter, "-m", "pytest", *guard.argv, "-n0"]
            if guard.argv[0].endswith(".py") and guard.argv[0].startswith("tests/")
            else [interpreter, *guard.argv]
        )
        try:
            proc = subprocess.run(
                argv,
                cwd=tree,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_s,
                env={**_guard_env(), "PYTHONPATH": env_src},
            )
        except subprocess.TimeoutExpired:
            failures.append(guard.name)
            continue
        if proc.returncode:
            failures.append(guard.name)
    if _tree_is_dirty(tree):
        failures.append(DIRTY_TREE_FAILURE)
    return tuple(failures)


def _guard_env() -> dict[str, str]:
    import os

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return env


# ---------------------------------------------------------------------------
# decision
# ---------------------------------------------------------------------------


def decide(
    workflow_verdicts: Mapping[str, WorkflowVerdict],
    *,
    guard_failures: Sequence[str],
    wait_budget_exhausted: bool,
) -> Decision:
    """Fold every verdict into one instruction for the batch driver.

    An exhausted wait resolves to HALT, never PROCEED: an absence of evidence
    that never arrived is still an absence of evidence.
    """
    if guard_failures:
        return Decision.HALT
    if any(verdict is WorkflowVerdict.RED for verdict in workflow_verdicts.values()):
        return Decision.HALT
    if any(verdict in UNSETTLED_VERDICTS for verdict in workflow_verdicts.values()):
        return Decision.HALT if wait_budget_exhausted else Decision.WAIT
    return Decision.PROCEED


def evaluate(
    repo: str,
    sha: str,
    *,
    tree: Path,
    run_local_guards: bool = True,
    wait_budget_exhausted: bool = False,
    guard_timeout_s: int = 600,
    python: str | None = None,
) -> HealthReport:
    """Evaluate one SHA on main once, without sleeping."""
    specs = verdict_capable_workflows(tree)
    changed_paths = fetch_changed_paths(repo, sha) if specs else ()
    verdicts: dict[str, WorkflowVerdict] = {}
    reasons: list[str] = []
    for spec in specs:
        verdict = classify_workflow(
            spec, fetch_workflow_runs(repo, spec.filename, sha), changed_paths
        )
        verdicts[spec.filename] = verdict
        if verdict is WorkflowVerdict.RED:
            reasons.append(f"{spec.filename} concluded red for {sha}")
        elif verdict in UNSETTLED_VERDICTS:
            reasons.append(f"{spec.filename} has no trustworthy verdict yet ({verdict.value})")

    guards = discover_guards(tree) if run_local_guards else ()
    guard_failures = (
        run_guards(tree, guards, python=python, timeout_s=guard_timeout_s) if guards else ()
    )
    reasons.extend(f"local guard failed: {name}" for name in guard_failures)

    return HealthReport(
        sha=sha,
        decision=decide(
            verdicts,
            guard_failures=guard_failures,
            wait_budget_exhausted=wait_budget_exhausted,
        ),
        workflows=verdicts,
        guard_failures=guard_failures,
        reasons=tuple(reasons),
        guards_run=tuple(guard.name for guard in guards),
    )


def evaluate_with_wait(
    repo: str,
    sha: str,
    *,
    tree: Path,
    run_local_guards: bool = True,
    wait_seconds: int = 0,
    poll_interval_s: int = 15,
    guard_timeout_s: int = 600,
    python: str | None = None,
    sleep: Any = time.sleep,
    monotonic: Any = time.monotonic,
) -> HealthReport:
    """Poll until the hosted verdicts settle or the wait budget runs out."""
    deadline = monotonic() + max(wait_seconds, 0)
    while True:
        exhausted = monotonic() >= deadline
        report = evaluate(
            repo,
            sha,
            tree=tree,
            run_local_guards=run_local_guards,
            wait_budget_exhausted=exhausted,
            guard_timeout_s=guard_timeout_s,
            python=python,
        )
        if report.decision is not Decision.WAIT or exhausted:
            return report
        sleep(min(poll_interval_s, max(deadline - monotonic(), 0) + 0.01))


def sync_tree(tree: Path, ref: str, *, remote: str = "origin") -> str:
    """Hard-reset a scratch worktree onto a ref and return the resolved SHA.

    Refuses anything that is not a linked worktree, so a mistyped path cannot
    reset the repository root.
    """
    if not (tree / ".git").is_file():
        raise ValueError(f"{tree} is not a linked git worktree; refusing to reset it")
    subprocess.run(["git", "-C", str(tree), "fetch", "--quiet", remote], check=True)
    subprocess.run(["git", "-C", str(tree), "merge", "--abort"], check=False, capture_output=True)
    subprocess.run(["git", "-C", str(tree), "reset", "--quiet", "--hard", ref], check=True)
    subprocess.run(["git", "-C", str(tree), "clean", "--quiet", "-fd"], check=True)
    proc = subprocess.run(
        ["git", "-C", str(tree), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Decide whether main is healthy enough for the next merge in a batch."
    )
    parser.add_argument("--repo", default=DEFAULT_REPO, help="GitHub repository as owner/repo")
    parser.add_argument("--sha", help="main SHA to evaluate (default: the tree's HEAD)")
    parser.add_argument(
        "--tree",
        type=Path,
        default=Path.cwd(),
        help="checkout of the merged tree whose guards should run",
    )
    parser.add_argument(
        "--sync-tree-to",
        help="hard-reset the scratch worktree to this ref first (linked worktrees only)",
    )
    parser.add_argument(
        "--no-local-guards",
        action="store_true",
        help="poll hosted workflow verdicts only",
    )
    parser.add_argument("--wait-seconds", type=int, default=0, help="budget for unsettled verdicts")
    parser.add_argument("--poll-interval", type=int, default=15, help="seconds between polls")
    parser.add_argument("--guard-timeout", type=int, default=600, help="per-guard timeout")
    parser.add_argument("--python", help="interpreter used to run local guards")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    tree = args.tree.resolve()
    try:
        if args.sync_tree_to:
            sha = sync_tree(tree, args.sync_tree_to)
        elif args.sha:
            sha = args.sha
        else:
            sha = subprocess.run(
                ["git", "-C", str(tree), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        report = evaluate_with_wait(
            args.repo,
            sha,
            tree=tree,
            run_local_guards=not args.no_local_guards,
            wait_seconds=args.wait_seconds,
            poll_interval_s=args.poll_interval,
            guard_timeout_s=args.guard_timeout,
            python=args.python,
        )
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        json.dump({"decision": "error", "error": str(exc)}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 3

    json.dump(report.to_dict(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
