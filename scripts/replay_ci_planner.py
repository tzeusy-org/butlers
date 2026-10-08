"""Replay current path vectors or verified historical diffs without elapsed claims."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from butlers.testing.changed_files import ChangedFiles  # noqa: E402
from butlers.testing.resource_readers import safe_path  # noqa: E402
from butlers.testing.scoped_runner import (  # noqa: E402
    FULL_SUITE_FALLBACK_ALLOWLIST,
    _plan_for_changed_files,
)


def _git(root: Path, *args: str) -> bytes | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def _commit(root: Path, value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    body = _git(root, "rev-parse", "--verify", "--end-of-options", value + "^{commit}")
    return body.decode().strip() if body is not None else None


def _historical_refs(root: Path, row: dict, files: list[str]) -> tuple[str, str] | None:
    base, head = _commit(root, row.get("base")), _commit(root, row.get("head"))
    if base is None or head is None:
        return None
    if _git(root, "merge-base", "--is-ancestor", base, head) is None:
        return None
    actual = _git(root, "diff", "--name-only", "--no-renames", "-z", base, head, "--")
    if actual is None or set(actual.decode().rstrip("\0").split("\0")) != set(files):
        return None
    return base, head


def replay(rows: list[dict], *, root: Path = ROOT, mode: str = "current-vectors") -> dict:
    """Use current source for both modes; qualify historical diffs separately.

    CURRENT vector replay is P7's scratch-corpus comparison. Its pinned base is
    the current planner checkout, not a reconstructed historical branch. A
    manifest exception still needs an independently verified actual historical
    diff, supplied either as an optional annotation or in historical-diffs mode.
    """
    if mode not in {"current-vectors", "historical-diffs"}:
        raise ValueError("UNSAFE_REPLAY_MODE")
    head = _commit(root, "HEAD")
    if head is None or _git(root, "status", "--porcelain", "--untracked-files=no") != b"":
        raise ValueError("CURRENT_SOURCE_UNAVAILABLE_OR_DIRTY")
    if not isinstance(rows, list):
        raise ValueError("UNSAFE_CORPUS")
    outcomes = []
    for row in rows:
        files = row.get("files") if isinstance(row, dict) else None
        # The frozen original rows retain the exact old decision-line grammar.
        # Do not mistake that carrier for the three malformed first-row modes.
        if (
            isinstance(row, dict)
            and "mode" in row
            and row["mode"]
            not in {"full", "scoped", "[CI DECISION] mode=full", "[CI DECISION] mode=scoped"}
        ):
            outcomes.append(
                {
                    "id": row.get("id"),
                    "evidence": "UNKNOWN",
                    "historical_diff": "UNKNOWN",
                    "reason": "MALFORMED_CORPUS_ROW",
                }
            )
            continue
        if (
            not isinstance(files, list)
            or not files
            or any(not isinstance(name, str) or not safe_path(name) for name in files)
            or len(set(files)) != len(files)
        ):
            outcomes.append(
                {
                    "id": row.get("id") if isinstance(row, dict) else None,
                    "evidence": "UNKNOWN",
                    "historical_diff": "UNKNOWN",
                    "reason": "UNSAFE_DIFF",
                }
            )
            continue
        # Optional genuine annotations can admit a historical manifest delta
        # within CURRENT vector replay. They never qualify an altered list and
        # are not a prerequisite for unrelated current path vectors.
        refs = _historical_refs(root, row, files)
        qualified = mode == "current-vectors" or refs is not None
        base, plan_head = refs if refs else (head, head)
        plan = _plan_for_changed_files(
            ChangedFiles(
                files=files,
                base_ref=base if qualified else "unavailable-replay-base",
                head_ref=plan_head,
                sources=(mode,),
            ),
            repo_dir=root,
            fallback_allowlist=FULL_SUITE_FALLBACK_ALLOWLIST + ("tests/e2e/",),
        )
        outcomes.append(
            {
                "id": row.get("id"),
                "evidence": "qualified" if qualified else "UNKNOWN",
                "historical_diff": "verified" if refs else "UNKNOWN",
                "decision": plan.data(),
            }
        )
    counts = Counter(r.get("decision", {}).get("mode", "unknown") for r in outcomes)
    scoped = sum(
        r["evidence"] == "qualified" and r.get("decision", {}).get("mode") == "scoped"
        for r in outcomes
    )
    return {
        "schema": "planner-replay.v2",
        "replay_mode": mode,
        "planner_head": head,
        "planner_tree": _git(root, "rev-parse", "HEAD^{tree}").decode().strip(),
        "records": outcomes,
        "counts": dict(counts),
        "qualified_scoped": scoped,
        "unknown": sum(r["evidence"] == "UNKNOWN" for r in outcomes),
        "limits": (
            "Current path-vector source decisions are not historical Git-diff or last100 "
            "reader evidence. Historical mode verifies actual complete base/head differences. "
            "Neither mode executes tests or supplies testcase, timing or protected evidence."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--mode", choices=("current-vectors", "historical-diffs"), default="current-vectors"
    )
    args = parser.parse_args()
    result = replay(json.loads(args.input.read_text()), mode=args.mode)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(
        json.dumps(
            {
                name: result[name]
                for name in ("replay_mode", "counts", "qualified_scoped", "unknown")
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
