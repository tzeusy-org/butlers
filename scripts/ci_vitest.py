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
import os
import re
import signal
import subprocess
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


def run(root: Path, shard: int, output: Path) -> int:
    # Prepare the failure carrier before identity/collection: a bounded failure
    # must leave honest evidence even when no testcase population was reached.
    receipt = {
        "schema": "ci-vitest.v1",
        "identity": None,
        "shard": shard,
        "complete": False,
    }
    stage = "identity"
    exit_code = 2
    try:
        if type(shard) is not int or shard not in (1, 2):
            raise ValueError("unknown Vitest shard")
        receipt["identity"] = identity(root)
        receipt["runtime_observation"] = runtime_observation(root)
        stage = "collect-full"
        complete = collect(root, None)
        halves = {}
        for index in (1, 2):
            stage = f"collect-shard-{index}"
            halves[index] = collect(root, index)
        stage = "partition"
        full = collections.Counter(key(row["file"], row["name"], root) for row in complete)
        populations = {
            index: collections.Counter(key(row["file"], row["name"], root) for row in rows)
            for index, rows in halves.items()
        }
        if full != populations[1] + populations[2] or set(populations[1]) & set(populations[2]):
            raise ValueError("Vitest partition incomplete or overlapping")
        receipt.update(
            count=sum(populations[shard].values()),
            selected=dict(populations[shard]),
            full_population_digest=hashlib.sha256(
                json.dumps(sorted(full.items())).encode()
            ).hexdigest(),
        )
        command = [
            str(root / "frontend/node_modules/.bin/vitest"),
            "run",
            "--configLoader",
            "runner",
            f"--shard={shard}/2",
            "--reporter=json",
        ]
        stage = "execute"
        result = run_process(
            command,
            cwd=root / "frontend",
            env={**os.environ, "CI": "1"},
            timeout=900,
        )
        receipt["exit_code"] = result.returncode
        stage = "report"
        report = json.loads(result.stdout)
        actual = collections.Counter()
        outcomes = collections.Counter()
        for suite in report["testResults"]:
            for assertion in suite["assertionResults"]:
                # List and reporter have the same canonical title separators.
                title = " > ".join([*assertion["ancestorTitles"], assertion["title"]])
                actual[key(suite["name"], title, root)] += 1
                outcomes[assertion["status"]] += 1
        allowed = {"passed", "pending", "todo", "skipped"}
        receipt.update(
            outcomes=dict(outcomes),
            complete=result.returncode == 0
            and report["success"] is True
            and actual == populations[shard]
            and set(outcomes) <= allowed,
        )
        exit_code = 0 if receipt["complete"] else 1
        if receipt["complete"]:
            stage = "complete"
        else:
            receipt["failure_category"] = (
                "test_failure" if result.returncode != 0 else "invalid_evidence"
            )
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        # Categories and stages are closed source literals, never exception
        # messages, subprocess output, test names or arbitrary provider values.
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
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        return run(ROOT, args.shard, args.output)
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        print("::error::locked Vitest execution evidence unavailable")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
