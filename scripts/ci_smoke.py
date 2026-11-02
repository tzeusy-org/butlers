#!/usr/bin/env python3
"""Run the CI smoke tier and write its release evidence (REQ-testing-034/051).

Runs the dedicated smoke command once, tees its output to ``smoke.log`` and
writes ``release-evidence.json`` beside the raw JUnit file: the exact command,
commit SHA, wall-clock duration, pass/fail status, exit code and the skipped
test classes. Exits with the smoke command's own exit code. Standard library
only, so it runs before or without the project environment.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

SMOKE_COMMAND = (
    "uv",
    "run",
    "pytest",
    "tests/",
    "--ignore=tests/e2e",
    "-m",
    "smoke",
    "-q",
    "--tb=short",
)


def skipped_classes(junit: Path) -> list[str]:
    """Sorted class names of skipped cases, or ``["unavailable"]`` when unreadable."""
    try:
        root = ET.parse(junit).getroot()
    except (ET.ParseError, OSError) as exc:
        print(exc, file=sys.stderr)
        return ["unavailable"]
    return sorted(
        {
            case.get("classname") or case.get("name", "unknown")
            for case in root.iter("testcase")
            if case.find("skipped") is not None
        }
    )


def evidence(*, cmd: str, sha: str, duration_s: int, exit_code: int, junit: Path) -> dict:
    return {
        "cmd": cmd,
        "sha": sha,
        "duration_s": duration_s,
        "status": "PASS" if exit_code == 0 else "FAIL",
        "exit_code": exit_code,
        "skipped_classes": skipped_classes(junit),
    }


def run(output: Path, sha: str, command: tuple[str, ...] = SMOKE_COMMAND) -> int:
    output.mkdir(parents=True, exist_ok=True)
    junit = output / "raw-junit.xml"
    started = time.monotonic()
    with (output / "smoke.log").open("w") as log:
        process = subprocess.Popen(
            [*command, f"--junit-xml={junit}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        assert process.stdout is not None
        for line in process.stdout:
            sys.stdout.write(line)
            log.write(line)
        exit_code = process.wait()
    record = evidence(
        cmd=shlex.join(command),
        sha=sha,
        duration_s=int(time.monotonic() - started),
        exit_code=exit_code,
        junit=junit,
    )
    (output / "release-evidence.json").write_text(json.dumps(record, indent=2))
    print(
        f"SMOKE-RELEASE-EVIDENCE cmd={record['cmd']!r} sha={sha!r} "
        f"duration={record['duration_s']}s status={record['status']!r} "
        f"skipped_classes={record['skipped_classes']!r}"
    )
    print()
    print(json.dumps(record, indent=2))
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sha", default=os.environ.get("GITHUB_SHA", ""))
    args = parser.parse_args(argv)
    return run(args.output, args.sha)


if __name__ == "__main__":
    raise SystemExit(main())
