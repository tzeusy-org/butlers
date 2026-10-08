#!/usr/bin/env python3
"""Run one fixed nightly species and retain sanitized external-exit evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from summarize_junit_durations import sanitize_junit_report

from butlers.nightly_assurance import (
    EXACT_IMAGE_FUNCTIONS,
    VARIANTS,
    RunIdentity,
    atomic_json,
    digest,
    read_json,
)

MARKERS = "not bench and not perf and not nightly and not pg_clock and not faketime_fragile"
EXACT_NODES = EXACT_IMAGE_FUNCTIONS


def command(variant: str) -> list[str]:
    common = ["uv", "run", "--no-sync", "pytest", "-p", "butlers.testing.nightly_evidence"]
    if variant == "schema":
        return common + [
            "tests/config/test_schema_matrix_migrations.py",
            "tests/migrations/",
            "-q",
            "--tb=short",
            "-m",
            "(nightly or integration) and not bench and not perf",
            "-n",
            "auto",
        ]
    if variant == "exact-image":
        return common + [
            "tests/cli/test_runtime_cli_sandbox.py",
            "-v",
            "--tb=short",
            "-k",
            " or ".join(EXACT_NODES),
        ]
    return (
        ["timeout", "--signal=SIGABRT", "--kill-after=30s", "3600"]
        + common
        + [
            "tests/",
            "roster/",
            "-v",
            "-rf",
            "--tb=short",
            "--ignore=tests/e2e",
            "-m",
            MARKERS,
            "-n",
            "auto",
            "--dist",
            "loadfile",
            "--timeout=300",
            "--timeout-method=thread",
        ]
    )


def clock_conformance(library: Path, environment: dict[str, str]) -> dict:
    """Measure the actual child environment, not an assumed libfaketime default."""

    script = (
        "import datetime,json,time; s=time.monotonic(); w=datetime.datetime.now(datetime.UTC);"
        "time.sleep(.05); print(json.dumps({'wall':w.isoformat(),"
        "'monotonic_start':s,'monotonic_delta':time.monotonic()-s}))"
    )
    parent_monotonic = time.monotonic()
    parent_wall = datetime.now(UTC)
    child = subprocess.run(
        ["uv", "run", "--no-sync", "python", "-c", script],
        env=environment,
        capture_output=True,
        timeout=15,
        check=True,
    )
    observation = json.loads(child.stdout)
    if not 0.03 <= observation["monotonic_delta"] <= 5:
        raise ValueError("monotonic-conformance-failed")
    if not parent_monotonic <= observation["monotonic_start"] <= time.monotonic():
        raise ValueError("child-monotonic-clock-shifted")
    wall = datetime.fromisoformat(observation["wall"])
    request = environment["FAKETIME"]
    if request in {"+45d", "+120d"}:
        from datetime import timedelta

        expected_wall = parent_wall + timedelta(days=45 if request == "+45d" else 120)
    else:
        expected_wall = datetime.strptime(request, "@%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    if not -1 <= (wall - expected_wall).total_seconds() <= 15:
        raise ValueError("wall-conformance-failed")
    restarted = subprocess.run(
        ["uv", "run", "--no-sync", "python", "-c", script],
        env=environment,
        capture_output=True,
        timeout=15,
        check=True,
    )
    restart = json.loads(restarted.stdout)
    restart_wall = datetime.fromisoformat(restart["wall"])
    if request.startswith("@"):
        # Start-at resets for an independently execed child; it is not a
        # shared clock origin across controller/xdist workers.
        if not -1 <= (restart_wall - expected_wall).total_seconds() <= 15:
            raise ValueError("child-start-at-reset-unavailable")
    elif not -1 <= (restart_wall - wall).total_seconds() <= 15:
        raise ValueError("child-offset-wall-unavailable")
    if (
        not parent_monotonic <= restart["monotonic_start"] <= time.monotonic()
        or not 0.03 <= restart["monotonic_delta"] <= 5
    ):
        raise ValueError("child-restart-monotonic-unavailable")
    # Capture the installed package version without reflecting arbitrary CLI output.
    package = subprocess.run(
        ["dpkg-query", "-W", "-f=${Version}", "libfaketime"],
        capture_output=True,
        timeout=10,
        check=False,
    )
    import re

    version = package.stdout.decode("ascii", errors="ignore").strip()
    if package.returncode or not re.fullmatch(r"[0-9][A-Za-z0-9.+~:-]{0,100}", version):
        raise ValueError("installed-library-version-unavailable")
    return {
        **observation,
        "parent_wall": parent_wall.isoformat(),
        "package_version": version,
        "child_restart": restart,
        "start_at_resets_per_child": request.startswith("@"),
        "wall_verified": True,
        "library_sha256": hashlib.sha256(library.read_bytes()).hexdigest(),
        "faketime": environment["FAKETIME"],
        "monotonic_unshifted": True,
    }


def process_clocks_verified(evidence: object, clock: dict, *, started: float, ended: float) -> bool:
    """Malformed or missing worker evidence is unavailable, never a finalizer crash."""
    import math

    if not isinstance(evidence, dict):
        return False
    processes = evidence.get("process_clocks")
    expected = evidence.get("expected_clock_processes")
    if (
        not isinstance(processes, dict)
        or not isinstance(expected, list)
        or not expected
        or any(not isinstance(item, str) for item in expected)
        or len(expected) != len(set(expected))
        or set(processes) != set(expected)
    ):
        return False
    request = clock["faketime"]
    if request.startswith("@"):
        anchor = datetime.strptime(request, "@%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    else:
        from datetime import timedelta

        anchor = datetime.fromisoformat(clock["parent_wall"]) + timedelta(
            days=45 if request == "+45d" else 120
        )
    for observed in processes.values():
        if not isinstance(observed, dict):
            return False
        try:
            worker_wall = datetime.fromisoformat(observed["wall_at_start"])
        except (KeyError, TypeError, ValueError):
            return False
        if (
            worker_wall.tzinfo is None
            or not -1 <= (worker_wall - anchor).total_seconds() <= ended - started + 30
        ):
            return False
        numeric = (observed.get("monotonic_start"), observed.get("monotonic_delta"))
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in numeric):
            return False
        if (
            observed.get("library_sha256") != clock["library_sha256"]
            or observed.get("monotonic_unshifted") is not True
            or not started <= numeric[0] <= ended
            or not 0.01 <= numeric[1] <= 5
        ):
            return False
    return True


def _run_phase(
    *,
    identity: RunIdentity,
    variant: str,
    output: Path,
    library: Path | None = None,
    boundary: bool = False,
) -> int:
    invocation = command(variant)
    if boundary:
        invocation = [
            "timeout",
            "--signal=SIGABRT",
            "--kill-after=30s",
            "3600",
            "uv",
            "run",
            "--no-sync",
            "pytest",
            "-p",
            "butlers.testing.nightly_evidence",
            "tests/core/test_core_scheduler.py::test_qa_tick_rechecks_own_policy_before_each_dispatch",
            "-v",
            "--tb=short",
            "-m",
            MARKERS,
            "-n",
            "0",
            "--timeout=300",
            "--timeout-method=thread",
        ]
    started = datetime.now(UTC).isoformat()
    environment = dict(os.environ)
    environment["BUTLERS_NIGHTLY_PYTEST_EVIDENCE"] = str(output / "pytest.json")
    environment["PYTHONFAULTHANDLER"] = "1"
    environment["BUTLERS_NIGHTLY_CLOCK_VARIANT"] = variant
    output.mkdir(parents=True, exist_ok=True)
    clock = None
    try:
        if variant != "schema" and variant != "exact-image":
            if library is None:
                raise ValueError("faketime-library-required")
            environment.update(
                LD_PRELOAD=str(library.resolve()), TZ="UTC", FAKETIME_DONT_FAKE_MONOTONIC="1"
            )
            if variant.startswith("offset-"):
                environment["FAKETIME"] = {"offset-45": "+45d", "offset-120": "+120d"}[variant]
            else:
                # @ is progressing, not the frozen absolute syntax. Boundary
                # conformance is separately registered; suite start alone is no proof.
                hour = "15:30:00" if variant == "folded-hour" else "23:59:40"
                environment["FAKETIME"] = f"@{identity.night} {hour}"
            environment["BUTLERS_NIGHTLY_CLOCK_REQUIRED"] = "1"
            if boundary:
                environment["FAKETIME"] = f"@{identity.night} 12:34:50"
            clock = clock_conformance(library, environment)
            if boundary:
                output.mkdir(parents=True, exist_ok=True)
                timestamp = output / "clock.txt"
                timestamp.write_text(f"@{identity.night} 12:34:50\n")
                environment.pop("FAKETIME", None)
                environment.update(
                    FAKETIME_TIMESTAMP_FILE=str(timestamp.resolve()),
                    FAKETIME_NO_CACHE="1",
                    BUTLERS_NIGHTLY_MINUTE_MILESTONE="1",
                    BUTLERS_NIGHTLY_CLOCK_WITNESS=str((output / "boundary.json").resolve()),
                    BUTLERS_NIGHTLY_QA_OLD_CRON_CONTROL=(
                        "1"
                        if os.environ.get("BUTLERS_NIGHTLY_QA_OLD_CRON_REQUESTED") == "1"
                        else "0"
                    ),
                )
    except (OSError, ValueError, subprocess.SubprocessError):
        # A preflight refusal must survive as an external receipt, rather than
        # leaving a missing producer artifact that looks like an empty green.
        # Never retain exception arguments or the child's stdout/stderr.
        sanitize_junit_report(
            raw_report=output / "absent-raw-junit.xml",
            sanitized_report=output / "junit.xml",
            duration_report=output / "durations.txt",
            limit=25,
        )
        atomic_json(
            output / "receipt.json",
            {
                **identity.document(),
                "variant": variant,
                "manifest": [],
                "manifest_digest": digest([]),
                "outcomes": {},
                "controller_complete": False,
                "command": invocation,
                "exit_code": 2,
                "started_at": started,
                "completed_at": datetime.now(UTC).isoformat(),
                "clock": None,
                "clock_conformance_verified": False,
                "boundary": None,
                "unavailable": ["clock-preflight-unavailable"],
                "junit_sha256": hashlib.sha256((output / "junit.xml").read_bytes()).hexdigest(),
            },
        )
        return 2
    exit_code = None
    monotonic_started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="nightly-junit-") as temporary:
            raw = Path(temporary) / "junit.xml"
            try:
                completed = subprocess.run(
                    invocation + [f"--junitxml={raw}"], env=environment, check=False
                )
                exit_code = completed.returncode
            finally:
                sanitize_junit_report(
                    raw_report=raw,
                    sanitized_report=output / "junit.xml",
                    duration_report=output / "durations.txt",
                    limit=25,
                )
    finally:
        try:
            pytest = read_json(output / "pytest.json")
            if not isinstance(pytest, dict):
                raise ValueError("invalid-pytest-receipt-shape")
        except (OSError, ValueError):
            pytest = {
                "manifest": [],
                "manifest_digest": digest([]),
                "outcomes": {},
                "controller_complete": False,
            }
        clock_verified = clock is None
        boundary_observed = None
        if clock is not None:
            clock_verified = process_clocks_verified(
                pytest, clock, started=monotonic_started, ended=time.monotonic()
            )
            if not clock_verified and exit_code == 0:
                exit_code = 2
        if boundary:
            try:
                boundary_observed = read_json(output / "boundary.json")
            except (OSError, ValueError):
                boundary_observed = {}
            if (
                not isinstance(boundary_observed, dict)
                or set(boundary_observed) != {"0", "1"}
                or not all(
                    isinstance(item, dict) and item.get("actual_boundary_crossed") is True
                    for item in boundary_observed.values()
                )
            ):
                if exit_code == 0:
                    exit_code = 2
        receipt = {
            **identity.document(),
            "variant": variant,
            **pytest,
            "command": invocation,
            "exit_code": exit_code,
            "started_at": started,
            "completed_at": datetime.now(UTC).isoformat(),
            "clock": clock,
            "clock_conformance_verified": clock_verified,
            "boundary": boundary_observed,
            "junit_sha256": hashlib.sha256((output / "junit.xml").read_bytes()).hexdigest()
            if (output / "junit.xml").is_file()
            else None,
        }
        atomic_json(output / "receipt.json", receipt)
        (output / "pytest.json").unlink(missing_ok=True)
    return exit_code if exit_code is not None else 2


def run(*, identity: RunIdentity, variant: str, output: Path, library: Path | None = None) -> int:
    """Full population first; folded minute additionally reaches an isolated barrier.

    Phase records retain their real command/manifest/clock. The combined
    function-local case indexes append boundary cases, rather than silently
    overwriting the same nodes from the complete population.
    """
    if variant != "folded-minute":
        return _run_phase(identity=identity, variant=variant, output=output, library=library)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nightly-phases-") as temporary:
        phases = []
        roots = []
        codes = []
        for name, boundary in (("population", False), ("qa-minute-barrier", True)):
            path = Path(temporary) / name
            codes.append(
                _run_phase(
                    identity=identity,
                    variant=variant,
                    output=path,
                    library=library,
                    boundary=boundary,
                )
            )
            phases.append({"phase": name, **read_json(path / "receipt.json")})
            roots.append(ET.parse(path / "junit.xml").getroot())
        root = ET.Element("testsuites")
        for document in roots:
            if document.tag == "testsuite":
                root.append(document)
            else:
                root.extend(document.findall("testsuite"))
        raw = Path(temporary) / "joined.xml"
        ET.ElementTree(root).write(raw, encoding="utf-8", xml_declaration=True)
        sanitize_junit_report(
            raw_report=raw,
            sanitized_report=output / "junit.xml",
            duration_report=output / "durations.txt",
            limit=25,
        )
        counts = defaultdict(int)
        manifest, outcomes = [], {}
        for phase in phases:
            for node in phase["manifest"]:
                base = node.rsplit("::case-", 1)[0]
                counts[base] += 1
                appended = f"{base}::case-{counts[base]}"
                manifest.append(appended)
                if node in phase["outcomes"]:
                    outcomes[appended] = phase["outcomes"][node]
        code = next((code for code in codes if code != 0), 0)
        atomic_json(
            output / "receipt.json",
            {
                **identity.document(),
                "variant": variant,
                "phases": phases,
                "manifest": manifest,
                "manifest_digest": digest(manifest),
                "outcomes": outcomes,
                "controller_complete": all(p["controller_complete"] for p in phases),
                "exit_code": code,
                "started_at": phases[0]["started_at"],
                "completed_at": phases[-1]["completed_at"],
                "clock_conformance_verified": all(p["clock_conformance_verified"] for p in phases),
                "boundary": phases[-1]["boundary"],
                "junit_sha256": hashlib.sha256((output / "junit.xml").read_bytes()).hexdigest(),
            },
        )
    return code


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=VARIANTS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--library", type=Path)
    args = parser.parse_args()
    identity = RunIdentity(
        int(os.environ["GITHUB_RUN_ID"]),
        int(os.environ["GITHUB_RUN_ATTEMPT"]),
        os.environ["GITHUB_SHA"],
        os.environ["GITHUB_EVENT_NAME"],
        os.environ["GITHUB_REF_NAME"],
        os.environ["NIGHTLY_LOGICAL_DATE"],
    )
    raise SystemExit(
        run(identity=identity, variant=args.variant, output=args.output, library=args.library)
    )


if __name__ == "__main__":
    main()
