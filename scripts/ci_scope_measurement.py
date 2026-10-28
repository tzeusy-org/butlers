#!/usr/bin/env python3
"""Opt-in hosted observation, never a profile installer or ordinary required gate.

No timings produced by conformance fixtures are measurement evidence. The outer
job owns the unchanged 82-minute setup/execute/finalization watchdog.
"""

from __future__ import annotations

import argparse
import json
import math
import signal
import sys
import time
from pathlib import Path

import ci_affected as affected
from ci_evidence import digest, file_path, read_json

from butlers.testing import scope_cost
from butlers.testing.source_test_map import configured_testpaths

ROOT = Path(__file__).resolve().parent.parent


def phase_union_seconds(receipt, *, elapsed):
    """Subtract only the union of actual phase intervals, never worker-divide.

    Serial file phase sums plus all time outside this union conservatively
    include collections, setup, worker startup, coordination and finalization.
    """
    elapsed = scope_cost.finite(elapsed)
    intervals = []
    for phases in receipt["nodes"].values():
        for phase in phases.values():
            end = scope_cost.finite(phase["completed_s"])
            duration = scope_cost.finite(phase["duration_s"])
            start = end - duration
            if start < 0 or end > elapsed:
                raise ValueError("phase clock is outside observation")
            intervals.append((start, end))
    if not intervals:
        raise ValueError("actual phase intervals unavailable")
    union, begin, finish = 0.0, None, None
    for start, end in sorted(intervals):
        if begin is None:
            begin, finish = start, end
        elif start <= finish:
            finish = max(finish, end)
        else:
            union += finish - begin
            begin, finish = start, end
    return math.fsum((union, finish - begin))


def observed_context(root, receipt):
    return scope_cost.actual_execution_context(root, receipt)


def observe(species, paths, *, root=ROOT, output):
    started = time.monotonic()
    deadline = started + affected.BUDGET_S
    nonce = affected.secrets.token_hex(32)
    population = affected.collect(
        configured_testpaths(root), root=root, nonce=nonce, deadline=deadline, complete=True
    )
    all_files = sorted(set(population["nodes"].values()))
    if species == "full":
        paths = [p for p in all_files if not p.startswith("tests/e2e/")]
    elif species != "affected":
        raise ValueError("measurement species unavailable")
    if not affected.admitted_paths(paths) or not set(paths) <= set(all_files):
        raise ValueError("measurement population is not admitted")
    for path in paths:
        file_path(path, root)
    execution_started = time.monotonic()
    proof = affected.execute(paths, root=root, output=output, deadline=deadline)
    execution_elapsed = time.monotonic() - execution_started
    receipt = read_json(output / "selected-execution.json")
    reference = read_json(output / "selected-reference.json")
    elapsed = time.monotonic() - started
    union = phase_union_seconds(receipt, elapsed=execution_elapsed)
    identity = proof["identity"]
    # A local fixture is not an authenticated hosted observation.
    if (
        identity["event"] != "workflow_dispatch"
        or identity["run"] == "local"
        or not str(identity["run"]).isdigit()
        or not str(identity["attempt"]).isdigit()
    ):
        raise ValueError("actual hosted observation identity required")
    if affected.checkout_identity(root) != identity:
        raise ValueError("measurement source changed")
    body = {
        "schema": "scope-measurement.v1",
        "species": species,
        "verified": True,
        "complete": True,
        "head": identity["head"],
        "run": int(identity["run"]),
        "attempt": int(identity["attempt"]),
        "identity": identity,
        "context": observed_context(root, receipt),
        "configuration": scope_cost.environment(root)["configuration"],
        "elapsed_seconds": elapsed,
        "outside_phase_union_seconds": max(0.0, elapsed - union),
        "complete_reference_digest": proof["complete_reference_digest"],
        "selected_reference_digest": digest(reference),
        "execution_digest": digest(receipt),
        "files": {
            path: {"sha256": scope_cost.file_digest(root / path), "serial_seconds": value}
            for path, value in receipt["file_durations_s"].items()
        },
        "limitation": "ROOT authenticates artifact and whole-job elapsed; candidate only",
    }
    (output / "measurement.json").write_text(json.dumps(body, sort_keys=True) + "\n")
    return body


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--species", choices=("full", "affected"), required=True)
    parser.add_argument("--paths-file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    def interrupted(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        paths = json.loads(args.paths_file.read_text()) if args.paths_file else []
        body = observe(args.species, paths, output=args.output)
        print(
            json.dumps({"complete": True, "species": body["species"], "files": len(body["files"])})
        )
        return 0
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        OverflowError,
        affected.subprocess.TimeoutExpired,
    ):
        print("scope observation unavailable; no admitted measurement", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("scope observation interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
