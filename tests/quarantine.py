"""Pytest plugin that keeps quarantined flaky tests out of default runs.

A test is quarantined when it fails intermittently for a cause that cannot be
fixed yet.  ``pyproject.toml`` loads this plugin for every pytest run, so the
CI lanes, ``make`` targets and local runs all deselect it, and one flake can no
longer fail a blocking unit or integration shard.  The nightly ``quarantined``
job runs exactly these tests with ``--quarantined-full``, serially, so they keep
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
from collections import Counter
from collections.abc import Generator
from pathlib import Path

import pytest

REGISTRY = "tests/quarantine.json"
_BEAD = re.compile(r"bu-[0-9a-z]+(?:\.[0-9]+)*")
_ENTRIES = pytest.StashKey[dict[str, dict[str, str]]]()
_FULL_SELECTION_VALIDATED = pytest.StashKey[bool]()


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
    group = parser.getgroup("quarantine")
    group.addoption(
        "--quarantined",
        action="store_true",
        help=f"run only the tests listed in {REGISTRY} (by default they are deselected)",
    )
    group.addoption(
        "--quarantined-full",
        action="store_true",
        help="run the complete quarantine registry, refusing missing owners or filtered cases",
    )


def pytest_configure(config: pytest.Config) -> None:
    path = config.rootpath / REGISTRY
    if config.getoption("quarantined_full"):
        if not path.is_file():
            raise pytest.UsageError(f"{REGISTRY}: full quarantine requires an existing registry")
        if any("::" in arg for arg in config.args):
            raise pytest.UsageError(
                "full quarantine requires whole paths; use --quarantined to scope"
            )
    config.stash[_ENTRIES] = load_registry(path)
    config.stash[_FULL_SELECTION_VALIDATED] = False


def pytest_report_header(config: pytest.Config) -> str | None:
    count = len(config.stash[_ENTRIES])
    if not count:
        return None
    if config.getoption("quarantined") or config.getoption("quarantined_full"):
        return f"quarantine: running only the {count} test(s) listed in {REGISTRY}"
    return f"quarantine: {count} test(s) listed in {REGISTRY} are deselected"


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> Generator[None, object, None]:
    entries = config.stash[_ENTRIES]
    full = config.getoption("quarantined_full")
    # Snapshot actual items before marker, keyword and other selection hooks.
    collected = Counter(
        item.nodeid for item in items if full and item.nodeid.split("[", 1)[0] in entries
    )
    yield
    only_quarantined = config.getoption("quarantined") or full
    if not entries and not only_quarantined:
        return
    selected, deselected = [], []
    for item in items:
        listed = item.nodeid.split("[", 1)[0] in entries
        (selected if listed == only_quarantined else deselected).append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected
    if full:
        selected_nodes = Counter(item.nodeid for item in items)
        missing = sorted(set(entries) - {item.nodeid.split("[", 1)[0] for item in items})
        if missing or selected_nodes != collected:
            raise pytest.UsageError(
                f"{REGISTRY}: incomplete full quarantine population; "
                f"missing owners: {missing}; filtered collected items: "
                f"{sum((collected - selected_nodes).values())}"
            )
        config.stash[_FULL_SELECTION_VALIDATED] = True


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_collection(session: pytest.Session) -> Generator[None, object, object]:
    result = yield
    config = session.config
    if (
        config.getoption("quarantined_full")
        and not config.stash[_ENTRIES]
        and config.stash[_FULL_SELECTION_VALIDATED]
        and not session.testsfailed
    ):
        # Complete successful collection authenticated a genuinely empty registry.
        # Produce that explicit no-work result; never translate an arbitrary exit5.
        pytest.exit("quarantine: validated empty registry; no tests to run", returncode=0)
    return result
