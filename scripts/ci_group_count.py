"""Pytest plugin: record what this CI lane group collected and how long it took.

Loaded by ``scripts/ci_lanes.py run`` with ``-p ci_group_count`` (bu-29wkwj.2).

* ``CI_GROUP_COUNT_FILE`` receives the collected count. It is taken after every
  other ``pytest_collection_modifyitems`` hook, so marker deselection and the
  pytest-split group selection have both applied. Under xdist every worker
  collects the same group; the first worker writes it.
* ``CI_GROUP_DURATIONS_FILE`` receives each test's setup+call+teardown seconds
  in pytest-split's ``.test_durations`` shape. xdist forwards every report to
  the controller, so only the controller writes it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

_durations: dict[str, float] = {}


@pytest.hookimpl(wrapper=True)
def pytest_collection_modifyitems(session, config, items):
    result = yield
    target = os.environ.get("CI_GROUP_COUNT_FILE")
    worker = getattr(config, "workerinput", {}).get("workerid")
    if target and worker in (None, "gw0"):
        Path(target).write_text(json.dumps({"collected": len(items)}) + "\n", encoding="utf-8")
    return result


def pytest_runtest_logreport(report):
    _durations[report.nodeid] = _durations.get(report.nodeid, 0.0) + report.duration


def pytest_sessionfinish(session):
    target = os.environ.get("CI_GROUP_DURATIONS_FILE")
    if target and not hasattr(session.config, "workerinput"):
        Path(target).write_text(json.dumps(_durations, sort_keys=True) + "\n", encoding="utf-8")
