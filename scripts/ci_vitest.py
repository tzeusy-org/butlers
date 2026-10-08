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
import subprocess
from pathlib import Path

from ci_frontend_evidence import ROOT, identity


def key(file: str, title: str, root: Path) -> str:
    path = Path(file).resolve()
    if not path.is_relative_to((root / "frontend").resolve()) or not path.is_file():
        raise ValueError("Vitest item escaped actual frontend")
    return hashlib.sha256(json.dumps([str(path.relative_to(root)), title]).encode()).hexdigest()


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
    result = subprocess.run(
        command,
        cwd=root / "frontend",
        env={**os.environ, "CI": "1"},
        capture_output=True,
        timeout=180,
    )
    if result.returncode != 0:
        raise ValueError("actual locked Vitest collection failed")
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
    if type(shard) is not int or shard not in (1, 2):
        raise ValueError("unknown Vitest shard")
    source = identity(root)
    complete = collect(root, None)
    halves = {index: collect(root, index) for index in (1, 2)}
    full = collections.Counter(key(row["file"], row["name"], root) for row in complete)
    populations = {
        index: collections.Counter(key(row["file"], row["name"], root) for row in rows)
        for index, rows in halves.items()
    }
    if full != populations[1] + populations[2] or set(populations[1]) & set(populations[2]):
        raise ValueError("Vitest partition incomplete or overlapping")
    command = [
        str(root / "frontend/node_modules/.bin/vitest"),
        "run",
        "--configLoader",
        "runner",
        f"--shard={shard}/2",
        "--reporter=json",
    ]
    receipt = {
        "schema": "ci-vitest.v1",
        "identity": source,
        "shard": shard,
        "count": sum(populations[shard].values()),
        "selected": dict(populations[shard]),
        "full_population_digest": hashlib.sha256(
            json.dumps(sorted(full.items())).encode()
        ).hexdigest(),
        "complete": False,
    }
    result = subprocess.run(
        command,
        cwd=root / "frontend",
        env={**os.environ, "CI": "1"},
        capture_output=True,
        timeout=900,
    )
    try:
        report = json.loads(result.stdout)
        actual = collections.Counter()
        outcomes = collections.Counter()
        for suite in report["testResults"]:
            for assertion in suite["assertionResults"]:
                # Vitest list uses ' > ' separators; reporter supplies canonical
                # ancestor titles and title, avoiding an ambiguous text replace.
                title = " > ".join([*assertion["ancestorTitles"], assertion["title"]])
                actual[key(suite["name"], title, root)] += 1
                outcomes[assertion["status"]] += 1
        allowed = {"passed", "pending", "todo", "skipped"}
        receipt.update(
            exit_code=result.returncode,
            outcomes=dict(outcomes),
            complete=result.returncode == 0
            and report["success"] is True
            and actual == populations[shard]
            and set(outcomes) <= allowed,
        )
    except (ValueError, KeyError, TypeError):
        receipt["exit_code"] = result.returncode
    output.mkdir(parents=True, exist_ok=True)
    (output / "receipt.json").write_text(json.dumps(receipt, sort_keys=True) + "\n")
    return 0 if receipt["complete"] else 1


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
