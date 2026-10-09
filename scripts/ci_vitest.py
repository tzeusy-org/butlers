"""Execute two locked Vitest shards and retain opaque actual-item evidence.

List and JSON reporter bodies stay in RAM. Neither test names, parameter values,
failure messages nor console output are artifacts. This is source-bound execution
evidence, not a provider corpus or authentication receipt.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from ci_frontend_evidence import ROOT, identity


class VitestSubprocessFailure(ValueError):
    """Nonzero collection outcome; no raw subprocess output is retained."""


class VitestTimeout(subprocess.TimeoutExpired):
    """Closed timeout evidence after terminating only this invocation's group."""

    def __init__(self, timeout: float, diagnostics: dict):
        # Do not attach argv, stdout, stderr or exception text to the failure.
        super().__init__("locked-vitest", timeout)
        self.diagnostics = diagnostics


def run_process(command: list[str], *, cwd: Path, env: dict, timeout: float):
    """Include TERM/KILL and pipe drainage in the existing invocation deadline.

    subprocess.run's timeout kills only the direct child; a Vitest fork can
    keep inherited pipes open after that child dies. A fresh session binds the
    signals to this invocation, including its ordinary descendants. The cleanup
    reserve reduces useful work time rather than extending the 180/900s limits.
    """
    started = time.monotonic()
    deadline = started + timeout
    reserve = min(10.0, timeout / 2)
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=max(0, deadline - reserve - time.monotonic()))
    except subprocess.TimeoutExpired:
        term_sent = kill_sent = False
        try:
            os.killpg(process.pid, signal.SIGTERM)
            term_sent = True
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = process.communicate(timeout=max(0, (deadline - time.monotonic()) / 2))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
                kill_sent = True
            except ProcessLookupError:
                pass
            try:
                stdout, stderr = process.communicate(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                # An escaped process cannot extend the wrapper or supply item
                # authority. Close our handles; do not search/kill other PIDs.
                process.stdout.close()
                process.stderr.close()
                raise VitestTimeout(
                    timeout,
                    {
                        "term_sent": term_sent,
                        "kill_sent": kill_sent,
                        "pipes_drained": False,
                        "child_reaped": process.poll() is not None,
                        "stdout_bytes": None,
                        "stderr_bytes": None,
                    },
                ) from None
        raise VitestTimeout(
            timeout,
            {
                "term_sent": term_sent,
                "kill_sent": kill_sent,
                "pipes_drained": True,
                "child_reaped": process.poll() is not None,
                "stdout_bytes": len(stdout),
                "stderr_bytes": len(stderr),
            },
        ) from None
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def key(file: str, title: str, root: Path) -> str:
    path = Path(file).resolve()
    if not path.is_relative_to((root / "frontend").resolve()) or not path.is_file():
        raise ValueError("Vitest item escaped actual frontend")
    return hashlib.sha256(json.dumps([str(path.relative_to(root)), title]).encode()).hexdigest()


def runtime_observation(root: Path) -> dict | None:
    """Observe the CLI's PATH-selected Node without changing collection inputs.

    This optional diagnostic never admits a population, selects workers or
    changes a deadline. Missing/malformed observations are UNKNOWN. The fixed
    program reads only public runtime version and available CPU count.
    """
    try:
        result = run_process(
            [
                "node",
                "-e",
                "console.log(JSON.stringify({node:process.versions.node,"
                "available_parallelism:require('node:os').availableParallelism()}))",
            ],
            cwd=root / "frontend",
            env={**os.environ, "CI": "1"},
            timeout=10,
        )
        value = json.loads(result.stdout)
        if (
            result.returncode != 0
            or not isinstance(value, dict)
            or set(value) != {"node", "available_parallelism"}
            or not isinstance(value["node"], str)
            or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value["node"]) is None
            or type(value["available_parallelism"]) is not int
            or not 1 <= value["available_parallelism"] <= 4096
        ):
            return None
        return value
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        return None


def collect(root: Path, shard: int | None) -> list[dict]:
    command = [
        str(root / "frontend/node_modules/.bin/vitest"),
        "list",
        "--run",
        "--configLoader",
        "runner",
    ]
    if shard is not None:
        command.append(f"--shard={shard}/2")
    command.append("--json")
    result = run_process(
        command,
        cwd=root / "frontend",
        env={**os.environ, "CI": "1"},
        timeout=180,
    )
    if result.returncode != 0:
        raise VitestSubprocessFailure("actual locked Vitest collection failed")
    rows = json.loads(result.stdout)
    if not isinstance(rows, list) or not rows:
        raise ValueError("required Vitest population empty")
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != {"file", "name"}
            or not all(isinstance(v, str) for v in row.values())
        ):
            raise ValueError("Vitest collector shape changed")
    return rows


REPORTER = Path(__file__).with_name("ci_vitest_reporter.mjs")
HASH = re.compile(r"[0-9a-f]{64}")


def positive_count(value) -> bool:
    return type(value) is int and value > 0


def same_json_value(observed, expected) -> bool:
    """Compare validated JSON structure without boolean/numeric coercion."""
    if type(observed) is not type(expected):
        return False
    if isinstance(expected, dict):
        return observed.keys() == expected.keys() and all(
            same_json_value(observed[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(observed) == len(expected) and all(
            same_json_value(actual, required) for actual, required in zip(observed, expected)
        )
    return observed == expected


def validate_reference(reference: dict, root: Path) -> dict:
    """Validate genuine current unsharded declarations, not cached file counts."""
    if (
        not isinstance(reference, dict)
        or set(reference)
        != {
            "schema",
            "config",
            "files",
            "halves",
            "modules",
            "unhandled_errors",
            "controller_exit",
            "complete",
        }
        or reference["schema"] != "ci-vitest-reference.v2"
        or reference["complete"] is not True
        or type(reference["controller_exit"]) is not int
        or reference["controller_exit"] != 0
        or type(reference["unhandled_errors"]) is not int
        or reference["unhandled_errors"] != 0
    ):
        raise ValueError("current full Vitest reference incomplete")
    files, halves, modules = reference["files"], reference["halves"], reference["modules"]
    if not isinstance(files, list) or not files or len(files) != len(set(files)):
        raise ValueError("current full Vitest files invalid")
    if files != sorted(files) or set(modules) != set(files) or set(halves) != {"1", "2"}:
        raise ValueError("current full Vitest module cover invalid")
    for file in files:
        path = (root / file).resolve()
        if (
            not isinstance(file, str)
            or not file.startswith("frontend/")
            or not path.is_relative_to((root / "frontend").resolve())
            or not path.is_file()
            or str(path.relative_to(root)) != file
        ):
            raise ValueError("Vitest reference file escaped actual source")
        module = modules[file]
        if (
            not isinstance(module, dict)
            or set(module) != {"file", "items", "errors", "ok"}
            or module["file"] != file
            or type(module["errors"]) is not int
            or module["errors"] != 0
            or module["ok"] is not True
        ):
            raise ValueError("Vitest reference module unavailable")
        items = module["items"]
        if not isinstance(items, dict) or not items:
            raise ValueError("required Vitest module empty")
        for token, item in items.items():
            if (
                not isinstance(token, str)
                or HASH.fullmatch(token) is None
                or not isinstance(item, dict)
                or set(item) != {"key", "mode"}
                or not isinstance(item["key"], str)
                or HASH.fullmatch(item["key"]) is None
                or item["mode"] not in {"run", "skip", "todo", "only"}
            ):
                raise ValueError("Vitest declaration malformed")
    left, right = halves["1"], halves["2"]
    if (
        not isinstance(left, list)
        or not isinstance(right, list)
        or not left
        or not right
        or left != sorted(set(left))
        or right != sorted(set(right))
        or set(left) & set(right)
        or sorted(left + right) != files
    ):
        raise ValueError("installed Vitest half cover incomplete")
    validate_config(reference["config"], root)
    return reference


def validate_config(config: dict, root: Path) -> None:
    keys = {
        "node",
        "vitest",
        "pool",
        "isolate",
        "available_parallelism",
        "max_workers",
        "min_workers",
        "file_parallelism",
        "test_timeout",
        "hook_timeout",
        "retry",
        "sequence_shuffle",
    }
    if not isinstance(config, dict) or set(config) != keys:
        raise ValueError("Vitest runtime configuration incomplete")
    installed = json.loads((root / "frontend/package-lock.json").read_text())["packages"][
        "node_modules/vitest"
    ]["version"]
    if (
        not isinstance(config["node"], str)
        or re.fullmatch(r"24\.[0-9]+\.[0-9]+", config["node"]) is None
        or config["vitest"] != installed
        or config["pool"] != "forks"
        or config["isolate"] is not True
        or config["file_parallelism"] is not True
        or config["sequence_shuffle"] is not False
        or not positive_count(config["available_parallelism"])
    ):
        raise ValueError("Vitest runtime configuration changed")
    for name in ("max_workers", "min_workers"):
        if config[name] is not None and not positive_count(config[name]):
            raise ValueError("Vitest worker configuration invalid")
    for name in ("test_timeout", "hook_timeout"):
        if not positive_count(config[name]):
            raise ValueError("Vitest timeout configuration invalid")
    if type(config["retry"]) is not int or config["retry"] < 0:
        raise ValueError("Vitest retry configuration invalid")


def validate_execution(reference: dict, execution: dict, shard: int) -> dict:
    """Independently reconcile fresh declarations, logical starts and results."""
    fields = {
        "schema",
        "config",
        "modules",
        "queued",
        "starts",
        "ends",
        "ready",
        "results",
        "terminal_files",
        "unhandled_errors",
        "reporter_problems",
        "update_errors",
        "reason",
        "complete",
    }
    if (
        not isinstance(execution, dict)
        or set(execution) != fields
        or execution["schema"] != "ci-vitest-execution.v2"
        or not same_json_value(execution["config"], reference["config"])
        or execution["complete"] is not True
        or execution["reason"] != "passed"
        or type(execution["unhandled_errors"]) is not int
        or execution["unhandled_errors"] != 0
        or execution["reporter_problems"] != {}
        or type(execution["update_errors"]) is not int
        or execution["update_errors"] != 0
    ):
        raise ValueError("Vitest execution controller incomplete")
    selected = reference["halves"][str(shard)]
    for name in ("modules", "queued", "starts", "ends"):
        if not isinstance(execution[name], dict) or set(execution[name]) != set(selected):
            raise ValueError("Vitest selected module cover differs")
    if execution["terminal_files"] != selected:
        raise ValueError("Vitest terminal module cover differs")
    occurrences = {}
    actual = collections.Counter()
    for file in selected:
        expected = reference["modules"][file]
        observed = execution["modules"][file]
        if not same_json_value(observed, expected):
            raise ValueError("Vitest shard declaration differs from current full reference")
        if type(execution["queued"][file]) is not int or execution["queued"][file] != 1:
            raise ValueError("Vitest module queued more or less than once")
        if type(execution["starts"][file]) is not int or execution["starts"][file] != 1:
            raise ValueError("Vitest module started more or less than once")
        end = execution["ends"][file]
        if (
            not isinstance(end, dict)
            or set(end) != {"count", "errors", "ok", "state"}
            or type(end["count"]) is not int
            or end["count"] != 1
            or type(end["errors"]) is not int
            or end["errors"] != 0
            or end["ok"] is not True
            or end["state"] not in {"passed", "skipped"}
        ):
            raise ValueError("Vitest module terminal unavailable")
        for token, item in expected["items"].items():
            if token in occurrences:
                raise ValueError("Vitest occurrence repeated across modules")
            occurrences[token] = item
            actual[item["key"]] += 1
    if (
        not isinstance(execution["ready"], dict)
        or not isinstance(execution["results"], dict)
        or set(execution["ready"]) != set(occurrences)
        or set(execution["results"]) != set(occurrences)
    ):
        raise ValueError("Vitest logical event population differs")
    outcomes = collections.Counter()
    for token, item in occurrences.items():
        if type(execution["ready"][token]) is not int or execution["ready"][token] != 1:
            raise ValueError("Vitest logical item did not start exactly once")
        result = execution["results"][token]
        if (
            not isinstance(result, dict)
            or set(result) != {"state", "declared_mode"}
            or result["declared_mode"] != item["mode"]
            or result["state"] not in {"passed", "skipped"}
            or (item["mode"] in {"skip", "todo"} and result["state"] != "skipped")
        ):
            raise ValueError("Vitest item terminal unavailable")
        outcomes["todo" if item["mode"] == "todo" else result["state"]] += 1
    return {"selected": dict(actual), "count": sum(actual.values()), "outcomes": dict(outcomes)}


def reconcile(root: Path, receipts: list[dict]) -> None:
    """Required frontend's independent, current-head two-artifact verifier."""
    if (
        not isinstance(receipts, list)
        or len(receipts) != 2
        or any(not isinstance(r, dict) or type(r.get("shard")) is not int for r in receipts)
        or {r.get("shard") for r in receipts} != {1, 2}
    ):
        raise ValueError("both exact Vitest children required")
    expected_identity = identity(root)
    full = None
    populations = []
    for receipt in receipts:
        if (
            receipt.get("schema") != "ci-vitest.v2"
            or not same_json_value(receipt.get("identity"), expected_identity)
            or receipt.get("complete") is not True
            or type(receipt.get("exit_code")) is not int
            or receipt["exit_code"] != 0
            or type(receipt.get("wrapper_exit")) is not int
            or receipt["wrapper_exit"] != 0
            or receipt.get("stage") != "complete"
        ):
            raise ValueError("Vitest child evidence unavailable")
        times = [
            receipt.get(name) for name in ("reference_elapsed_s", "execute_elapsed_s", "elapsed_s")
        ]
        if (
            any(
                type(value) not in (int, float) or not math.isfinite(value) or value < 0
                for value in times
            )
            or times[2] > 900
            or math.fsum(times[:2]) > times[2]
        ):
            raise ValueError("Vitest child evidence unavailable")
        reference = validate_reference(receipt["reference"], root)
        if full is None:
            full = reference
        elif not same_json_value(full, reference):
            raise ValueError("independent current full Vitest references differ")
        proof = validate_execution(reference, receipt["execution"], receipt["shard"])
        if any(not same_json_value(receipt.get(key), value) for key, value in proof.items()):
            raise ValueError("Vitest declared summary differs from actual occurrence proof")
        populations.append(collections.Counter(proof["selected"]))
    required = collections.Counter(
        item["key"] for module in full["modules"].values() for item in module["items"].values()
    )
    if populations[0] + populations[1] != required or set(populations[0]) & set(populations[1]):
        raise ValueError("actual Vitest item partition incomplete or overlapping")


def run(root: Path, shard: int, output: Path) -> int:
    # One900s total envelope, not a refreshed900s after the full reference.
    started = time.monotonic()
    deadline = started + 900
    receipt = {"schema": "ci-vitest.v2", "identity": None, "shard": shard, "complete": False}
    stage, exit_code = "identity", 2
    try:
        if type(shard) is not int or shard not in (1, 2):
            raise ValueError("unknown Vitest shard")
        receipt["identity"] = identity(root)
        receipt["runtime_observation"] = runtime_observation(root)
        with tempfile.TemporaryDirectory(prefix="ci-vitest-evidence-") as temporary:
            reference_path = Path(temporary) / "reference.json"
            execution_path = Path(temporary) / "execution.json"
            environment = {**os.environ, "CI": "1"}
            stage = "collect-full"
            phase_started = time.monotonic()
            result = run_process(
                ["node", str(REPORTER), "collect", str(root), str(reference_path)],
                cwd=root / "frontend",
                env=environment,
                timeout=max(0, deadline - time.monotonic()),
            )
            if result.returncode:
                raise VitestSubprocessFailure("actual full Vitest reference failed")
            reference = validate_reference(json.loads(reference_path.read_text()), root)
            receipt["reference"] = reference
            receipt["reference_elapsed_s"] = time.monotonic() - phase_started
            stage = "execute"
            phase_started = time.monotonic()
            result = run_process(
                [
                    str(root / "frontend/node_modules/.bin/vitest"),
                    "run",
                    "--configLoader",
                    "runner",
                    f"--shard={shard}/2",
                    f"--reporter={REPORTER}",
                ],
                cwd=root / "frontend",
                env={**environment, "BUTLERS_VITEST_EVIDENCE": str(execution_path)},
                timeout=max(0, deadline - time.monotonic()),
            )
            receipt["exit_code"] = result.returncode
            receipt["execute_elapsed_s"] = time.monotonic() - phase_started
            stage = "report"
            execution = json.loads(execution_path.read_text())
            receipt["execution"] = execution
            proof = validate_execution(reference, execution, shard)
            receipt.update(proof)
            if time.monotonic() > deadline:
                raise VitestTimeout(900, {"phase": "report"})
            receipt["complete"] = result.returncode == 0
            exit_code = 0 if receipt["complete"] else 1
            stage = "complete" if receipt["complete"] else "report"
            if not receipt["complete"]:
                receipt["failure_category"] = "test_failure"
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        if isinstance(exc, subprocess.TimeoutExpired):
            category = "timeout"
            if isinstance(exc, VitestTimeout):
                receipt["process_cleanup"] = exc.diagnostics
        elif isinstance(exc, VitestSubprocessFailure):
            category = "subprocess_failed"
        elif isinstance(exc, OSError):
            category = "io_unavailable"
        else:
            category = "invalid_evidence"
        receipt["failure_category"] = category
    finally:
        receipt["stage"] = stage
        receipt["wrapper_exit"] = exit_code
        receipt["elapsed_s"] = time.monotonic() - started
        output.mkdir(parents=True, exist_ok=True)
        (output / "receipt.json").write_text(json.dumps(receipt, sort_keys=True) + "\n")
    if not receipt["complete"]:
        print(
            f"::error::locked Vitest evidence unavailable stage={stage} "
            f"category={receipt['failure_category']}"
        )
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard", type=int)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--reconcile", type=Path, nargs=2)
    args = parser.parse_args()
    if args.reconcile:
        if args.shard is not None or args.output is not None:
            parser.error("reconciliation cannot execute a shard")
    elif args.shard is None or args.output is None:
        parser.error("shard and output are required for execution")
    try:
        if args.reconcile:
            reconcile(ROOT, [json.loads(path.read_text()) for path in args.reconcile])
            return 0
        return run(ROOT, args.shard, args.output)
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        print("::error::locked Vitest execution evidence unavailable")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
