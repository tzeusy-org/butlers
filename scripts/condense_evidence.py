#!/usr/bin/env python3
"""Produce branch/mutation evidence in owned copies, without editing live tests."""

from __future__ import annotations

import argparse
import ast
import copy
import fcntl
import hashlib
import io
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

from check_condensation_ledger import (
    PROTECTED,
    EvidenceError,
    canonical,
    fields,
    git,
    git_environment,
    input_record,
    public_path,
    same_owner_context_accounts,
    source_records,
    strict_json,
    test_path,
    tool_record,
    tracked_paths,
)
from pre_push import Refusal, await_owned_group_exit, recovery_signals


def publish(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def owned_run(
    command: list[str], *, cwd: Path, env: dict, timeout: float, process_record: Path | None = None
) -> tuple[int, bool]:
    """Use the landed R1 completion admission for this exact owned child group."""
    launched = [sys.executable, str(Path(__file__).resolve()), "_launch", *command]
    child = subprocess.Popen(
        launched,
        cwd=cwd,
        env=env,
        start_new_session=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if process_record is not None:
            identity = process_identity(child.pid)
            if identity is None or identity["group"] != child.pid:
                raise EvidenceError("owned-launch-identity-unknown")
            publish(process_record, {"identity": identity, "command_sha256": canonical(command)})
        child.stdin.write(b"1")
        child.stdin.close()
        try:
            return child.wait(timeout=timeout), True
        except subprocess.TimeoutExpired:
            raise EvidenceError("proof-timeout") from None
    finally:
        if child.stdin is not None and not child.stdin.closed:
            child.stdin.close()
        with recovery_signals():
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                await_owned_group_exit(child.pid)
                child.wait(timeout=5)
                if process_record is not None and process_record.is_file():
                    record = strict_json(process_record)
                    publish(process_record, {**record, "cleanup": True})
            except (Refusal, subprocess.TimeoutExpired):
                raise EvidenceError("owned-group-completion-unknown") from None


def process_identity(pid: int) -> dict | None:
    try:
        fields = (Path("/proc") / str(pid) / "stat").read_text().rsplit(") ", 1)[1].split()
        return {"pid": pid, "group": int(fields[2]), "start": fields[19], "state": fields[0]}
    except (FileNotFoundError, ProcessLookupError):
        return None


def _recover(output: Path) -> dict:
    """Restore only the journal's owned copy; recovery never upgrades UNKNOWN to PASS."""
    output = output.resolve()
    with (output / "run.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise EvidenceError("proof-owner-still-running") from None
        journal = strict_json(output / "journal.json")
        owned = Path(journal["owned_copy"])
        if (
            owned.parent != output
            or not owned.name.startswith("condense-owned-")
            or owned.is_symlink()
        ):
            raise EvidenceError("unowned-recovery-path")
        if not owned.is_dir():
            raise EvidenceError("owned-copy-already-absent-requires-completed-receipt")
        for process in sorted(output.glob("*.process.json")):
            record = strict_json(process)
            identity = record["identity"]
            current = process_identity(identity["pid"])
            if current and current["state"] not in {"Z", "X"}:
                # Recovery never signals a later numeric PID/group. The original owner
                # performs cleanup; an interrupted group must independently settle.
                raise EvidenceError("recovery-process-not-settled-no-signal")
            # Never kill a group with an absent/reused leader merely by its number.
            try:
                await_owned_group_exit(identity["group"])
            except Refusal:
                raise EvidenceError("recovery-owned-group-completion-unknown") from None
        for mutation in journal["mutations"]:
            name = public_path(mutation["path"])
            before = output / public_path(mutation["before_file"])
            if (
                before.is_symlink()
                or hashlib.sha256(before.read_bytes()).hexdigest() != mutation["before_sha256"]
            ):
                raise EvidenceError("recovery-journal-body-mismatch")
            for side in ("removed", "survivors"):
                target = owned / side / name
                if target.is_symlink() or owned not in target.resolve().parents:
                    raise EvidenceError("indirect-recovery-target")
                target.write_bytes(before.read_bytes())
                target.chmod(mutation["before_mode"])
                if (
                    target.read_bytes() != before.read_bytes()
                    or target.stat().st_mode & 0o777 != mutation["before_mode"]
                ):
                    raise EvidenceError("recovery-restore-unknown")
        journal["status"] = "RESTORED"
        publish(output / "journal.json", journal)
        receipt = {
            "status": "UNKNOWN",
            "restored": True,
            "cleanup": True,
            "category": "recovered-needs-fresh-proof",
        }
        publish(output / "recovery.json", receipt)
        return receipt


def recover(output: Path) -> dict:
    try:
        return _recover(output)
    except EvidenceError:
        raise
    except (OSError, KeyError, TypeError, ValueError):
        raise EvidenceError("recovery-input-unknown") from None


def worker(request: Path) -> int:
    """Raw pytest parameter identities remain only in this process's memory."""
    import coverage
    import pytest

    config = strict_json(request)
    root = Path(config["root"]).resolve()
    sys.path[:0] = [str(root / "src"), str(root)]
    output = Path(config["output"])
    selected = config["selection"]
    nonce = config["nonce"]
    cases = {}
    complete_collection = False
    deselected = 0
    cov = coverage.Coverage(
        branch=True,
        include=[str(root / public_path(p)) for p in config["scope"]],
        data_file=str(output.with_suffix(".coverage")),
        config_file=False,
    )

    class ProofPlugin:
        def pytest_deselected(self, items):
            nonlocal deselected
            deselected += len(items)

        def pytest_collection_finish(self, session):
            nonlocal complete_collection
            for item in session.items:
                key = hashlib.sha256((nonce + "\0" + item.nodeid).encode()).hexdigest()
                cases[item.nodeid] = {
                    "key": key,
                    "node": item.nodeid.split("[", 1)[0],
                    "phases": {},
                }
            complete_collection = bool(cases) and len(cases) == len(session.items)

        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_setup(self, item):
            cov.switch_context(cases[item.nodeid]["key"] + "|setup")
            yield

        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_call(self, item):
            cov.switch_context(cases[item.nodeid]["key"] + "|call")
            yield

        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_teardown(self, item):
            cov.switch_context(cases[item.nodeid]["key"] + "|teardown")
            yield
            cov.switch_context("")

        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_makereport(self, item, call):
            outcome = yield
            report = outcome.get_result()
            result = report.outcome
            if report.failed:
                result = (
                    "assertion-failed"
                    if (
                        call.when == "call"
                        and call.excinfo is not None
                        and issubclass(call.excinfo.type, AssertionError)
                    )
                    else "nonsemantic-failed"
                )
            cases[item.nodeid]["phases"][report.when] = result

    previous_core = os.environ.get("COVERAGE_CORE")
    try:
        os.environ["COVERAGE_CORE"] = "ctrace"
        cov.start()
        tracer = dict(cov.sys_info()).get("core", "UNKNOWN")
        args = [
            *selected,
            "-n",
            "0",
            "-p",
            "no:cov",
            "-p",
            "no:terminal",
            "-o",
            "addopts=",
            "--import-mode=importlib",
            "-m",
            "not nightly and not bench and not perf",
            "--ignore=tests/benchmarks",
        ]
        code = int(pytest.main(args, plugins=[ProofPlugin()]))
    finally:
        cov.stop()
        cov.save()
        if previous_core is None:
            os.environ.pop("COVERAGE_CORE", None)
        else:
            os.environ["COVERAGE_CORE"] = previous_core
    data = cov.get_data()
    arcs = {}
    for row in cases.values():
        case_arcs = set()
        for phase in ("setup", "call", "teardown"):
            data.set_query_context(row["key"] + "|" + phase)  # literal exact SQL query, never regex
            for filename in data.measured_files():
                path = Path(filename).resolve()
                if root in path.parents:
                    case_arcs.update(
                        (str(path.relative_to(root)), a, b) for a, b in (data.arcs(filename) or [])
                    )
        arcs[row["key"]] = sorted(case_arcs)
    public_cases = list(cases.values())
    complete = (
        complete_collection
        and deselected == 0
        and tracer == "CTracer"
        and all(
            set(r["phases"]) == {"setup", "call", "teardown"}
            and r["phases"]["setup"] == "passed"
            and r["phases"]["teardown"] == "passed"
            and r["phases"]["call"] in {"passed", "assertion-failed"}
            for r in public_cases
        )
    )
    publish(
        output,
        {
            "id": config["id"],
            "complete": complete,
            "cleanup": False,
            "selected": len(public_cases),
            "deselected": deselected,
            "cases": public_cases,
            "arcs": arcs,
            "core": "ctrace" if tracer == "CTracer" else "UNKNOWN",
            "tracer": tracer,
            "exit": code,
            "selection": selected,
            "command_flags": args[len(selected) :],
            "measurement_root": str(root),
            "scope": config["scope"],
            "scope_inputs": [input_record(root, p) for p in config["scope"]],
            "tools": tool_record(),
        },
    )
    return 0 if complete else 2


def mutants(root: Path, scope: list[str]) -> list[dict]:
    """Deterministic finite actual AST mutations; each changes one public source span."""
    result = []
    for path in sorted(scope):
        body = (root / public_path(path)).read_bytes()
        source = body.decode()
        lines = body.splitlines(keepends=True)
        for node in ast.walk(ast.parse(source)):
            replacement = None
            operator = None
            if isinstance(node, ast.If) and any(isinstance(n, ast.Return) for n in node.body):
                replacement, operator = "pass", "drop-guard-return"
            elif isinstance(node, ast.Compare) and len(node.ops) == 1:
                changes = {
                    ast.Eq: ast.NotEq,
                    ast.NotEq: ast.Eq,
                    ast.Lt: ast.GtE,
                    ast.Gt: ast.LtE,
                    ast.LtE: ast.Gt,
                    ast.GtE: ast.Lt,
                }
                changed = changes.get(type(node.ops[0]))
                if changed:
                    candidate = copy.deepcopy(node)
                    candidate.ops = [changed()]
                    replacement, operator = ast.unparse(candidate), "swap-comparison"
            elif isinstance(node, ast.If):
                target = node.test
                replacement, operator = "not (" + ast.unparse(target) + ")", "negate-condition"
                node = target
            elif (
                isinstance(node, ast.Return)
                and node.value is not None
                and not (isinstance(node.value, ast.Constant) and node.value.value is None)
            ):
                replacement, operator = "return None", "return-none"
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value
                in {"accepted", "duplicate", "error", "pending", "approved", "rejected", "ok"}
            ):
                replacement, operator = (
                    repr("error" if node.value != "error" else "accepted"),
                    "replace-status",
                )
            if replacement is None:
                continue
            start = sum(len(line) for line in lines[: node.lineno - 1]) + node.col_offset
            end = sum(len(line) for line in lines[: node.end_lineno - 1]) + node.end_col_offset
            changed_source = (body[:start] + replacement.encode() + body[end:]).decode()
            try:
                compile(changed_source, path, "exec")
            except SyntaxError:
                continue
            identity = canonical(
                {
                    "path": path,
                    "line": node.lineno,
                    "column": node.col_offset,
                    "operator": operator,
                    "before": hashlib.sha256(source.encode()).hexdigest(),
                    "after": hashlib.sha256(changed_source.encode()).hexdigest(),
                }
            )
            result.append(
                {
                    "id": identity,
                    "path": path,
                    "line": node.lineno,
                    "operator": operator,
                    "body": changed_source.encode(),
                }
            )
    return sorted(result, key=lambda r: r["id"])


def suite(
    copy_root: Path,
    selection: list[str],
    scope: list[str],
    nonce: str,
    output: Path,
    id_: str,
    timeout: float,
) -> dict:
    request = output.with_suffix(".request.json")
    publish(
        request,
        {
            "root": str(copy_root),
            "selection": selection,
            "scope": scope,
            "nonce": nonce,
            "output": str(output),
            "id": id_,
        },
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(copy_root / "src"), str(copy_root), str(Path(__file__).resolve().parent)]
    )
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["COVERAGE_CORE"] = "ctrace"
    command = [sys.executable, str(Path(__file__).resolve()), "_worker", str(request)]
    code, cleaned = owned_run(
        command,
        cwd=copy_root,
        env=env,
        timeout=timeout,
        process_record=output.with_suffix(".process.json"),
    )
    if code != 0 or not output.is_file():
        raise EvidenceError("worker-incomplete")
    result = strict_json(output)
    if result["complete"] is not True or result["core"] != "ctrace":
        raise EvidenceError("worker-phases-or-tracer-unknown")
    result["cleanup"] = cleaned
    publish(output, result)
    return result


def snapshot_base(root: Path, base: str, destination: Path) -> None:
    """Copy regular objects first, then confined Git links without dereferencing."""
    archive = subprocess.check_output(
        ["git", "-C", str(root), "archive", base], env=git_environment()
    )
    with tarfile.open(fileobj=io.BytesIO(archive)) as carrier:
        members = carrier.getmembers()
        links = {m.name for m in members if m.issym()}
        paths = []
        for member in members:
            name = public_path(member.name.rstrip("/"))
            target = destination / name
            if any(str(parent) in links for parent in name.parents):
                raise EvidenceError("indirect-base-source-parent")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                paths.append(str(name))
                target.parent.mkdir(parents=True, exist_ok=True)
                with carrier.extractfile(member) as stream:
                    target.write_bytes(stream.read())
                target.chmod(member.mode & 0o777)
            elif not member.issym():
                raise EvidenceError("indirect-base-source")
        for member in members:
            if member.issym():
                name = public_path(member.name)
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(member.linkname)
                paths.append(str(name))
        source_records(destination, sorted(paths))


def snapshot_current(root: Path, destination: Path, inputs: list[dict]) -> None:
    """Preserve link bodies/modes and complete target closure in an owned copy."""
    for row in inputs:
        if row["mode"] == "120000":
            continue
        target = destination / public_path(row["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / row["path"], target)
    for row in inputs:
        if row["mode"] == "120000":
            target = destination / public_path(row["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(row["link"])
    if source_records(destination, [r["path"] for r in inputs]) != inputs:
        raise EvidenceError("owned-copy-source-mismatch")


def artifact(path: Path, output: Path) -> dict:
    return {
        "path": str(path.relative_to(output)),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bytes": path.stat().st_size,
    }


def prove(root: Path, config: dict | Path, output: Path, *, timeout: float = 60) -> dict:
    if output.exists():
        raise EvidenceError("output-already-exists")
    output.mkdir(parents=True)
    receipt = {
        "status": "UNKNOWN",
        "restored": False,
        "cleanup": False,
        "category": "proof-started",
    }
    publish(output / "receipt.json", receipt)
    lock = (output / "run.lock").open("a+")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    deadline = None
    previous_handlers = {}
    journal = []
    owned = None
    nonce = secrets.token_hex(32)

    def interrupted(_signum, _frame):
        raise InterruptedError

    for signum in [signal.SIGTERM, signal.SIGINT]:
        previous_handlers[signum] = signal.signal(signum, interrupted)
    try:
        if type(timeout) not in {int, float} or not 0 < timeout <= 300:
            raise EvidenceError("invalid-proof-total-timeout")
        deadline = time.monotonic() + timeout
        if isinstance(config, Path):
            config = strict_json(config)
        required = {
            "base",
            "scope",
            "removed",
            "survivors",
            "cluster",
            "bead",
            "contract",
            "mapping",
        }
        fields(config, required)
        if any(
            not isinstance(config[k], str) or not config[k] for k in ("cluster", "bead", "base")
        ):
            raise EvidenceError("config-owner-or-base")
        contract = config["contract"]
        fields(contract, {"class", "cites"})
        if (
            contract["class"] not in PROTECTED
            or contract["class"] == "migration"
            or not isinstance(contract["cites"], list)
            or not contract["cites"]
            or any(not isinstance(c, str) or not c for c in contract["cites"])
            or len(set(contract["cites"])) != len(contract["cites"])
        ):
            raise EvidenceError("unsupported-or-malformed-protected-contract")
        initial_head = git(root, "rev-parse", "HEAD")
        initial_tree = git(root, "rev-parse", "HEAD^{tree}")
        paths = tracked_paths(root)
        inputs = source_records(root, paths)
        scope = config["scope"]
        if not isinstance(scope, list) or not scope or len(set(scope)) != len(scope):
            raise EvidenceError("empty-or-aliased-scope")
        for path in scope:
            public_path(path)
            if (
                path not in paths
                or not path.endswith(".py")
                or any(c in path for c in "*?[]")
                or test_path(path)
                or Path(path).name.startswith("test_")
                or Path(path).name == "conftest.py"
                or path in {"scripts/condense_evidence.py", "scripts/check_condensation_ledger.py"}
                or input_record(root, path)["mode"] == "120000"
            ):
                raise EvidenceError("scope-not-owned-production-python-source")
        selections = {kind: config[kind] for kind in ["removed", "survivors"]}
        if any(
            not isinstance(rows, list)
            or not rows
            or len(set(rows)) != len(rows)
            or any(not isinstance(n, str) or "[" in n for n in rows)
            for rows in selections.values()
        ):
            raise EvidenceError("selection-needs-static-whole-case-owners")
        overlap = set(selections["removed"]) & set(selections["survivors"])
        if set(config["mapping"]) != set(selections["removed"]):
            raise EvidenceError("unmapped-removed-owner")
        for node, row in config["mapping"].items():
            if (
                set(row) != {"survivors", "reason"}
                or not row["reason"]
                or not row["survivors"]
                or not set(row["survivors"]) <= set(selections["survivors"])
            ):
                raise EvidenceError("unmapped-survivor")
            if node in overlap and row["survivors"] != [node]:
                raise EvidenceError("same-owner-context-needs-exact-self-mapping")
        base = git(root, "rev-parse", config["base"])
        if overlap:
            same_owner_context_accounts(root, base, overlap)
        runs, baseline = [], {}
        killed = {"removed": [], "survivors": []}
        with tempfile.TemporaryDirectory(prefix="condense-owned-", dir=output) as directory:
            owned = Path(directory)
            copies = {kind: owned / kind for kind in selections}
            for copy_root in copies.values():
                copy_root.mkdir()
            snapshot_base(root, base, copies["removed"])
            snapshot_current(root, copies["survivors"], inputs)
            if any(
                input_record(copies["removed"], p) != input_record(copies["survivors"], p)
                for p in scope
            ):
                raise EvidenceError("changed-production-scope-needs-independent-lineage-proof")
            base_paths = tracked_paths(root, base)
            old_links = {
                r["path"]: r
                for r in source_records(copies["removed"], base_paths)
                if r["mode"] == "120000"
            }
            new_links = {r["path"]: r for r in inputs if r["mode"] == "120000"}
            if old_links != new_links:
                raise EvidenceError("changed-source-link-needs-independent-lineage-proof")
            for path in set(base_paths) | set(paths):
                # Test/helper edits are measured by the selected real populations.
                # Other Python/config dependencies cannot silently change beneath
                # a same-source branch/mutation comparison.
                if path in {"scripts/condense_evidence.py", "scripts/check_condensation_ledger.py"}:
                    continue
                if test_path(path) or Path(path).name.startswith("test_"):
                    continue
                if not path.endswith(".py") and path not in {"uv.lock", "pyproject.toml"}:
                    continue
                if (
                    path not in base_paths
                    or path not in paths
                    or input_record(copies["removed"], path)
                    != input_record(copies["survivors"], path)
                ):
                    raise EvidenceError(
                        "changed-production-dependency-needs-independent-lineage-proof"
                    )
            publish(
                output / "journal.json",
                {"owned_copy": str(owned), "status": "UNKNOWN", "mutations": journal},
            )

            def execute(kind, label):
                path = output / (label + ".json")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise EvidenceError("proof-total-timeout")
                run = suite(copies[kind], selections[kind], scope, nonce, path, label, remaining)
                run["side"] = kind
                publish(path, run)
                run["artifact"] = artifact(path, output)
                run["coverage_artifact"] = artifact(path.with_suffix(".coverage"), output)
                runs.append(run)
                return run

            for kind in selections:
                run = execute(kind, kind + "-baseline")
                if run["exit"] != 0:
                    raise EvidenceError("baseline-not-healthy")
                baseline[kind] = run
                if {r["node"] for r in run["cases"]} != set(selections[kind]):
                    raise EvidenceError("missing-selected-case-owner")
            for owner in overlap:
                populations = [
                    {(r["key"], r["node"]) for r in baseline[side]["cases"] if r["node"] == owner}
                    for side in ("removed", "survivors")
                ]
                if populations[0] != populations[1]:
                    raise EvidenceError("same-owner-case-population-change")
            generated = mutants(copies["removed"], scope)
            if not generated or len(generated) > 64:
                raise EvidenceError("empty-or-unbounded-mutation-scope")
            for mutant in generated:
                before = (copies["removed"] / mutant["path"]).read_bytes()
                mode = (copies["removed"] / mutant["path"]).stat().st_mode & 0o777
                saved = output / (mutant["id"] + ".before")
                saved.write_bytes(before)
                journal.append(
                    {k: v for k, v in mutant.items() if k != "body"}
                    | {
                        "before_file": saved.name,
                        "before_sha256": hashlib.sha256(before).hexdigest(),
                        "after_sha256": hashlib.sha256(mutant["body"]).hexdigest(),
                        "before_mode": mode,
                        "status": "UNKNOWN",
                    }
                )
                publish(
                    output / "journal.json",
                    {"owned_copy": str(owned), "status": "UNKNOWN", "mutations": journal},
                )
                try:
                    for copy_root in copies.values():
                        (copy_root / mutant["path"]).write_bytes(mutant["body"])
                    for kind in selections:
                        run = execute(kind, mutant["id"] + "-" + kind)
                        if {(r["key"], r["node"]) for r in run["cases"]} != {
                            (r["key"], r["node"]) for r in baseline[kind]["cases"]
                        }:
                            raise EvidenceError("changed-case-population")
                        if run["exit"] == 1 and any(
                            r["phases"]["call"] == "assertion-failed" for r in run["cases"]
                        ):
                            killed[kind].append(mutant["id"])
                        elif run["exit"] != 0:
                            raise EvidenceError("nonsemantic-mutation-outcome")
                finally:
                    for copy_root in copies.values():
                        path = copy_root / mutant["path"]
                        path.write_bytes(before)
                        path.chmod(mode)
                        if path.read_bytes() != before or path.stat().st_mode & 0o777 != mode:
                            raise EvidenceError("mutation-restore-unknown")
                for kind in selections:
                    restored = execute(kind, mutant["id"] + "-" + kind + "-restored")
                    if restored["exit"] != 0 or restored["cases"] != baseline[kind]["cases"]:
                        raise EvidenceError("restored-baseline-not-healthy")
                journal[-1]["status"] = "RESTORED"
                publish(
                    output / "journal.json",
                    {"owned_copy": str(owned), "status": "UNKNOWN", "mutations": journal},
                )
            before_arcs = {tuple(a) for arcs in baseline["removed"]["arcs"].values() for a in arcs}
            after_arcs = {tuple(a) for arcs in baseline["survivors"]["arcs"].values() for a in arcs}
            if not before_arcs or not after_arcs:
                raise EvidenceError("empty-measured-branch-population")
            residue = sorted(before_arcs - after_arcs)
            lost = sorted(set(killed["removed"]) - set(killed["survivors"]))
            # Check each removed owner against its named survivors, beyond the union of kills.
            for mutant_id in killed["removed"]:
                old_run = next(r for r in runs if r["id"] == mutant_id + "-removed")
                new_run = next(r for r in runs if r["id"] == mutant_id + "-survivors")
                old_owners = {
                    r["node"] for r in old_run["cases"] if r["phases"]["call"] == "assertion-failed"
                }
                new_owners = {
                    r["node"] for r in new_run["cases"] if r["phases"]["call"] == "assertion-failed"
                }
                if any(not new_owners & set(config["mapping"][n]["survivors"]) for n in old_owners):
                    lost.append(mutant_id)
            binding = {
                "base": base,
                "head": initial_head,
                "tree": initial_tree,
                "inputs": inputs,
                "inputs_hash": canonical(inputs),
                "nonce": nonce,
                "selection": selections,
                "scope": scope,
                "tools": baseline["removed"]["tools"],
            }
            receipt = {
                "status": "PASS" if not residue and not lost and killed["removed"] else "REFUSED",
                "mode": "cov+mut",
                "restored": True,
                "cleanup": True,
                "coverage": {
                    "core": "ctrace",
                    "branch": True,
                    "residue_arcs": len(residue),
                    "residue": residue,
                    "waived": [],
                },
                "mutation": {
                    "seed": 0,
                    "generated": len(generated),
                    "mutants": journal,
                    "killed_by_removed": killed["removed"],
                    "killed_by_survivors": killed["survivors"],
                    "lost": sorted(set(lost)),
                    "unknown": [],
                },
                "runs": runs,
                "binding": binding,
            }
            if (
                git(root, "rev-parse", "HEAD") != initial_head
                or git(root, "rev-parse", "HEAD^{tree}") != initial_tree
                or tracked_paths(root) != paths
                or any(input_record(root, row["path"]) != row for row in inputs)
            ):
                raise EvidenceError("live-source-changed-during-proof")
            publish(
                output / "journal.json",
                {"owned_copy": str(owned), "status": "RESTORED", "mutations": journal},
            )
        ledger = {
            "schema": "condensation/v1",
            "cluster": config["cluster"],
            "bead": config["bead"],
            "contract": config["contract"],
            "binding": binding,
            "proof": {k: v for k, v in receipt.items() if k != "binding"},
            "seconds": {"before": None, "after": None},
            "removed": [
                {
                    "node": n,
                    "params": sum(r["node"] == n for r in baseline["removed"]["cases"]),
                    **config["mapping"][n],
                }
                for n in selections["removed"]
            ],
            "survivors": [
                {"node": n, "params": sum(r["node"] == n for r in baseline["survivors"]["cases"])}
                for n in selections["survivors"]
            ],
        }
        publish(output / "ledger.json", ledger)
    except (
        EvidenceError,
        InterruptedError,
        KeyboardInterrupt,
        OSError,
        KeyError,
        TypeError,
        ValueError,
        SyntaxError,
    ) as exc:
        restored = owned is not None and not owned.exists()
        cleanup = all(strict_json(p).get("cleanup") is True for p in output.glob("*.process.json"))
        receipt = {
            "status": "UNKNOWN",
            "restored": restored,
            "cleanup": cleanup,
            "category": str(exc) if isinstance(exc, EvidenceError) else type(exc).__name__,
        }
        raise EvidenceError("proof-unknown-see-durable-receipt") from exc
    finally:
        publish(output / "receipt.json", receipt)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        lock.close()
    return receipt


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if arguments and arguments[0] == "_launch":
        if sys.stdin.buffer.read(1) != b"1":
            return 2
        os.execvpe(arguments[1], arguments[1:], os.environ)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    run = sub.add_parser("prove")
    run.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--timeout", type=float, default=60)
    recovery = sub.add_parser("recover")
    recovery.add_argument("output", type=Path)
    child = sub.add_parser("_worker")
    child.add_argument("request", type=Path)
    args = parser.parse_args(argv)
    if args.mode == "_worker":
        return worker(args.request)
    if args.mode == "recover":
        try:
            print(json.dumps(recover(args.output)))
            return 0
        except EvidenceError as exc:
            print(json.dumps({"status": "UNKNOWN", "category": str(exc)}))
            return 2
    if not 0 < args.timeout <= 300:
        parser.error("timeout must be finite and in (0,300]")
    try:
        receipt = prove(
            args.repo_root.resolve(),
            args.config,
            args.output.resolve(),
            timeout=args.timeout,
        )
    except EvidenceError as exc:
        print(json.dumps({"status": "UNKNOWN", "category": str(exc)}))
        return 2
    print(
        json.dumps(
            {
                "status": receipt["status"],
                "residue_arcs": receipt["coverage"]["residue_arcs"],
                "lost": receipt["mutation"]["lost"],
            }
        )
    )
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
