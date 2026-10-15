"""Pytest plugin that keeps quarantined flaky tests out of default runs.

A test is quarantined when it fails intermittently for a cause that cannot be
fixed yet.  ``pyproject.toml`` loads this plugin for every pytest run, so the
CI lanes, ``make`` targets and local runs all deselect it, and one flake can no
longer fail a blocking unit or integration shard.  The nightly ``quarantined``
job runs exactly these tests with ``--quarantined``, serially, so they keep
producing a signal instead of rotting.

``tests/quarantine.json`` is the registry.  Each entry names a whole test (no
parameter suffix), the bead that owns its root cause, and why it flakes.  The
PR that fixes the cause deletes the entry; nothing else un-quarantines a test.

The plugin lives here rather than in the ``butlers`` package, and imports
nothing from it: pytest loads ``-p`` plugins before the root ``conftest.py``,
whose guard explains a ``butlers`` that resolves outside this checkout.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REGISTRY = "tests/quarantine.json"
_BEAD = re.compile(r"bu-[0-9a-z]+(?:\.[0-9]+)*")
_ENTRIES = pytest.StashKey[dict[str, dict[str, str]]]()


def load_registry(path: Path) -> dict[str, dict[str, str]]:
    """Return the registry keyed by node id; a malformed entry is a usage error."""
    if not path.is_file():
        return {}
    data = json.loads(path.read_text())
    rows = data.get("tests") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise pytest.UsageError(f"{REGISTRY}: expected an object with a 'tests' list")
    entries: dict[str, dict[str, str]] = {}
    for entry in rows:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"node", "bead", "reason"}
            or not all(isinstance(value, str) for value in entry.values())
            or ".py::" not in entry["node"]
            or "[" in entry["node"]
            or not _BEAD.fullmatch(entry["bead"])
            or not entry["reason"].strip()
        ):
            raise pytest.UsageError(f"{REGISTRY}: malformed entry {entry!r}")
        if entry["node"] in entries:
            raise pytest.UsageError(f"{REGISTRY}: duplicate entry for {entry['node']}")
        entries[entry["node"]] = entry
    return entries


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.getgroup("quarantine").addoption(
        "--quarantined",
        action="store_true",
        help=f"run only the tests listed in {REGISTRY} (by default they are deselected)",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.stash[_ENTRIES] = load_registry(config.rootpath / REGISTRY)


def pytest_report_header(config: pytest.Config) -> str | None:
    count = len(config.stash[_ENTRIES])
    if not count:
        return None
    if config.getoption("quarantined"):
        return f"quarantine: running only the {count} test(s) listed in {REGISTRY}"
    return f"quarantine: {count} test(s) listed in {REGISTRY} are deselected"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    entries = config.stash[_ENTRIES]
    only_quarantined = config.getoption("quarantined")
    if not entries and not only_quarantined:
        return
    selected, deselected = [], []
    for item in items:
        listed = item.nodeid.split("[", 1)[0] in entries
        (selected if listed == only_quarantined else deselected).append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected
