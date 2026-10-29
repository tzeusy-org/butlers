"""Shared identity and evidence helpers for the scoped (affected) CI lane.

Opaque identities are privacy minimization, not authentication or anonymity.
These helpers moved here from the retired computed-partition tooling
(bu-29wkwj.2); ``ci_affected.py`` and its collector are their consumers.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import subprocess
from pathlib import Path, PurePosixPath

from ci_lanes import LANES

SELECTORS = {lane: config.marker for lane, config in LANES.items()}
HEX = re.compile(r"[0-9a-f]{64}")


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def exact_json(value: object, expected: object) -> bool:
    """JSON representation equality: boolean/count coercions are never authority."""
    if type(value) is not type(expected):
        return False
    if isinstance(expected, dict):
        return value.keys() == expected.keys() and all(
            isinstance(key, str) and exact_json(value[key], item) for key, item in expected.items()
        )
    if isinstance(expected, list):
        return len(value) == len(expected) and all(
            exact_json(item, other) for item, other in zip(value, expected, strict=True)
        )
    if isinstance(expected, float):
        return math.isfinite(value) and math.isfinite(expected) and value == expected
    return type(expected) in (str, bool, int, type(None)) and value == expected


def require_receipt_types(receipt: dict) -> None:
    """Shared scoped/matrix admission; never normalize malformed wire values."""
    if (
        type(receipt) is not dict
        or type(receipt.get("complete")) is not bool
        or type(receipt.get("pytest_exit")) is not int
        or receipt["pytest_exit"] < 0
        or type(receipt.get("selected_count")) is not int
        or receipt["selected_count"] < 1
        or type(receipt.get("logical_starts")) is not dict
        or any(
            type(node) is not str or not HEX.fullmatch(node) or type(count) is not int or count < 1
            for node, count in receipt["logical_starts"].items()
        )
        or ("schema" in receipt and not exact_json(receipt["schema"], 1))
        or ("shard" in receipt and (type(receipt["shard"]) is not int or receipt["shard"] < 1))
    ):
        raise ValueError("execution verdict/count/start representation malformed")
    if type(receipt.get("nodes")) is not dict or type(receipt.get("node_files")) is not dict:
        raise ValueError("execution item binding representation malformed")
    if "test_step_elapsed_s" in receipt:
        value = receipt["test_step_elapsed_s"]
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("execution elapsed timer malformed")
    aggregate = receipt.get("file_durations_s")
    if type(aggregate) is not dict or any(
        type(value) not in (int, float) or not math.isfinite(value) or value < 0
        for value in aggregate.values()
    ):
        raise ValueError("execution aggregate timer malformed")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate evidence key")
        result[key] = value
    return result


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(), object_pairs_hook=unique_object)
    if not isinstance(value, dict):
        raise ValueError("evidence must be an object")
    return value


def file_path(name: str, root: Path) -> Path:
    if not isinstance(name, str) or "\\" in name or "::" in name:
        raise ValueError("invalid inventory file")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or path.as_posix() != name
        or ".." in path.parts
        or path.suffix != ".py"
        or path.parts[0] not in {"tests", "roster"}
        or any(ord(character) < 32 for character in name)
    ):
        raise ValueError("inventory file must be canonical repository-relative Python")
    candidate = root / name
    if not candidate.is_file() or not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError("inventory file is missing or escapes the checkout")
    return candidate


def checkout_identity(root: Path) -> dict:
    def git(*args):
        return (
            subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.DEVNULL)
            .decode()
            .strip()
        )

    head = git("rev-parse", "HEAD")
    workflow = os.environ.get("GITHUB_SHA")
    if workflow is not None and workflow != head:
        raise ValueError("inventory checkout does not match workflow")
    # Include working bytes for local conformance; actual hosted checkouts are
    # additionally bound to the exact committed tree and workflow attempt.
    inputs = {}
    for directory in ("src", "tests", "roster", "scripts", "alembic"):
        for path in sorted((root / directory).rglob("*.py")):
            if path.is_file():
                inputs[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ("conftest.py", "pyproject.toml", "uv.lock"):
        path = root / name
        if path.is_file():
            inputs[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "repository": os.environ.get("GITHUB_REPOSITORY", "local"),
        "head": head,
        "tree": git("rev-parse", "HEAD^{tree}"),
        "workflow": os.environ.get("GITHUB_WORKFLOW", "local"),
        "run": os.environ.get("GITHUB_RUN_ID", "local"),
        "attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "local"),
        "event": os.environ.get("GITHUB_EVENT_NAME", "local"),
        "python": platform.python_version(),
        "config_digest": digest(inputs),
        "lock_digest": inputs.get("uv.lock"),
        "selectors": SELECTORS,
    }
