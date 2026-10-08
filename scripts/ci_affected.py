"""Fresh default scoped collection, actual execution and independent reconciliation.

The original 82-minute job watchdog remains authoritative over setup and all
stages. This wrapper also bounds its own processes within that same envelope;
neither a JUnit count nor a successful child exit can authorize a scoped PASS.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ci_partition import checkout_identity, digest, file_path, read_json
from ci_route import admitted_paths

ROOT = Path(__file__).resolve().parent.parent
BUDGET_S = 82 * 60


def run_process(command, *, root, environment, deadline, capture):
    """Keep collection output private; timeouts terminate only our child group."""
    with subprocess.Popen(
        command,
        cwd=root,
        env=environment,
        start_new_session=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    ) as child:
        try:
            child.communicate(timeout=max(0, deadline - time.monotonic() - 10))
        except BaseException:
            try:
                os.killpg(child.pid, signal.SIGTERM)
                child.communicate(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.communicate(timeout=5)
            raise
        return child.returncode


def collect(paths, *, root, nonce, deadline):
    if not admitted_paths(paths):
        raise ValueError("selected scope is not admitted")
    for path in paths:
        file_path(path, root)
    with tempfile.TemporaryDirectory(prefix="ci-selected-") as directory:
        output = Path(directory) / "items.json"
        environment = {
            **os.environ,
            "CI_SELECTED_OUTPUT": str(output),
            "CI_SELECTED_ROOT": str(root),
            "CI_SELECTED_NONCE": nonce,
            "CI_SELECTED_IDENTITY": json.dumps(checkout_identity(root)),
            "PYTHONPATH": os.pathsep.join(
                [str(ROOT / "scripts"), os.environ.get("PYTHONPATH", "")]
            ),
        }
        # This fresh collector owns its protocol; a surrounding shard must not
        # reorder it or receive its terminal record through inherited variables.
        for name in ("CI_SHARD_CONTEXT", "CI_SHARD_RECEIPT", "CI_SHARD_STARTED"):
            environment.pop(name, None)
        command = [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-n",
            "0",
            "-p",
            "ci_affected_collector",
            "--",
            *paths,
        ]
        if run_process(
            command, root=root, environment=environment, deadline=deadline, capture=True
        ):
            raise ValueError("fresh selected collection unavailable")
        body = read_json(output)
    if body.get("complete") is not True or not body.get("nodes"):
        raise ValueError("complete nonempty selected collection required")
    return body


def verify(reference, receipt, *, identity, paths, nonce, command):
    """Independent strict identity/start/phase verifier; no duration tolerance."""
    if reference.get("identity") != identity or reference.get("nonce") != nonce:
        raise ValueError("selected reference source or invocation mismatch")
    nodes = reference["nodes"]
    if (
        reference.get("complete") is not True
        or reference.get("digest") != digest({k: v for k, v in reference.items() if k != "digest"})
        or not isinstance(nodes, dict)
        or not nodes
        or any(
            not isinstance(node, str)
            or len(node) != 64
            or any(character not in "0123456789abcdef" for character in node)
            or file not in paths
            for node, file in nodes.items()
        )
    ):
        raise ValueError("selected reference incomplete or malformed")
    files = sorted(set(nodes.values()))
    expected = {
        "complete": True,
        "pytest_exit": 0,
        "inventory_identity": identity,
        "reference_digest": digest(reference),
        "nonce": nonce,
        "files": files,
        "command": command,
        "actual_selector": reference["actual_selector"],
        "pytest_version": reference["pytest_version"],
        "selected_count": len(nodes),
        "selected_node_digest": digest(sorted(nodes)),
        "node_files": nodes,
        "logical_starts": {node: 1 for node in nodes},
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise ValueError("selected execution provenance or multiplicity mismatch")
    if reference["actual_selector"]["args"] != paths or set(receipt["nodes"]) != set(nodes):
        raise ValueError("selected actual identity mismatch")
    durations = {file: [] for file in files}
    for node, phases in receipt["nodes"].items():
        if (
            not isinstance(phases, dict)
            or not {"setup", "teardown"} <= phases.keys()
            or set(phases) - {"setup", "call", "teardown"}
            or (phases["setup"]["outcome"] == "passed" and "call" not in phases)
            or (phases["setup"]["outcome"] == "skipped" and "call" in phases)
        ):
            raise ValueError("selected execution phase incomplete")
        for phase in phases.values():
            if phase["outcome"] not in {"passed", "skipped"}:
                raise ValueError("selected execution not successful")
            for name in ("duration_s", "completed_s"):
                if (
                    type(phase[name]) not in (int, float)
                    or not math.isfinite(phase[name])
                    or phase[name] < 0
                ):
                    raise ValueError("selected phase timer malformed")
            durations[nodes[node]].append(phase["duration_s"])
    if receipt.get("file_durations_s") != {
        file: math.fsum(values) for file, values in durations.items()
    }:
        raise ValueError("selected aggregate differs from actual phases")
    return {
        "verified": True,
        "identity": identity,
        "selected_count": len(nodes),
        "selected_node_digest": digest(sorted(nodes)),
        "reference_digest": digest(reference),
    }


def execute(paths, *, root=ROOT, output, workers="auto"):
    started = time.monotonic()
    deadline = started + BUDGET_S
    if not admitted_paths(paths):
        raise ValueError("selected scope is not admitted")
    paths = sorted(paths)
    identity = checkout_identity(root)
    nonce = secrets.token_hex(32)
    output.mkdir(parents=True, exist_ok=True)
    reference = collect(paths, root=root, nonce=nonce, deadline=deadline)
    (output / "selected-reference.json").write_text(json.dumps(reference, sort_keys=True) + "\n")
    # Preserve the console-script import semantics of the original uv run pytest
    # command, using this already-installed interpreter's sibling executable.
    command = [
        str(Path(sys.executable).with_name("pytest")),
        "-q",
        "--tb=short",
        "-n",
        workers,
        "--dist",
        "loadfile",
        "-p",
        "ci_affected_collector",
        f"--junitxml={output / 'raw-junit.xml'}",
        "--",
        *paths,
    ]
    context = {
        "inventory_identity": identity,
        "reference_digest": digest(reference),
        "nonce": nonce,
        "files": sorted(set(reference["nodes"].values())),
        "command": command,
    }
    environment = {
        **os.environ,
        "CI_SHARD_STARTED": str(started),
        "CI_SHARD_CONTEXT": json.dumps(context),
        "CI_SHARD_RECEIPT": str(output / "selected-execution.json"),
        "PYTHONPATH": os.pathsep.join([str(ROOT / "scripts"), os.environ.get("PYTHONPATH", "")]),
    }
    environment.pop("CI_SELECTED_OUTPUT", None)
    code = run_process(
        command, root=root, environment=environment, deadline=deadline, capture=False
    )
    if code:
        raise ValueError("selected pytest failed")
    # A second real default collection independently derives current membership.
    independent = collect(paths, root=root, nonce=nonce, deadline=deadline)
    if independent != reference or checkout_identity(root) != identity:
        raise ValueError("selected current population changed")
    proof = verify(
        independent,
        read_json(output / "selected-execution.json"),
        identity=identity,
        paths=paths,
        nonce=nonce,
        command=command,
    )
    (output / "selected-proof.json").write_text(json.dumps(proof, sort_keys=True) + "\n")
    return proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        proof = execute(json.loads(os.environ["TEST_PATHS_JSON"]), output=args.output)
        with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
            stream.write("verified=true\n")
        print(json.dumps(proof, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        print("::error::fresh scoped execution proof unavailable", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
