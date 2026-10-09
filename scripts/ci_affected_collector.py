"""Actual default-selected items and phases; raw parametrized IDs stay in RAM."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from ci_partition import digest, file_path
from ci_shard_observer import (  # noqa: F401 - source hooks retained for execution
    Observer,
    node_digest,
    pytest_collection_modifyitems,
    pytest_runtest_makereport,
)


def selector(config):
    return {"markexpr": config.getoption("markexpr"), "args": list(config.args)}


class Collector:
    def pytest_collection_finish(self, session):
        root = Path(os.environ["CI_SELECTED_ROOT"])
        nonce = os.environ["CI_SELECTED_NONCE"]
        nodes = {}
        for item in session.items:
            if not isinstance(item, pytest.Function):
                raise ValueError("unsupported selected item")
            name = str(item.path.resolve().relative_to(root.resolve()))
            file_path(name, root)
            key = node_digest(item.nodeid, nonce)
            if key in nodes:
                raise ValueError("duplicate selected identity")
            nodes[key] = name
        self.body = {
            "identity": json.loads(os.environ["CI_SELECTED_IDENTITY"]),
            "nonce": nonce,
            "nodes": nodes,
            "actual_selector": selector(session.config),
            "pytest_version": pytest.__version__,
        }

    def pytest_sessionfinish(self, session, exitstatus):
        if exitstatus == 0 and hasattr(self, "body"):
            self.body["complete"] = True
            self.body["digest"] = digest(self.body)
            Path(os.environ["CI_SELECTED_OUTPUT"]).write_text(
                json.dumps(self.body, sort_keys=True) + "\n"
            )


class SelectedObserver(Observer):
    def pytest_sessionfinish(self, session, exitstatus):
        self.context["actual_selector"] = selector(self.config)
        self.context["pytest_version"] = pytest.__version__
        super().pytest_sessionfinish(session, exitstatus)


def pytest_configure(config):
    if os.environ.get("CI_SELECTED_OUTPUT"):
        config.pluginmanager.register(Collector(), "ci-selected-items")
    elif os.environ.get("CI_SHARD_RECEIPT") and not hasattr(config, "workerinput"):
        config.pluginmanager.register(SelectedObserver(config), "ci-selected-execution")
