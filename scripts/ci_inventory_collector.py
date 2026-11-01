"""Source-owned actual-item collector; never persists raw parametrized identities."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from ci_partition import digest, file_path
from ci_shard_observer import node_digest


class Collector:
    def __init__(self):
        self.items = []
        self.plugins = []

    def pytest_collection_finish(self, session):
        self.items = list(session.items)
        self.plugins = sorted(
            (name, getattr(plugin, "__name__", type(plugin).__module__), type(plugin).__qualname__)
            for name, plugin in session.config.pluginmanager.list_name_plugin()
            if plugin is not None
        )

    def pytest_sessionfinish(self, session, exitstatus):
        if exitstatus != 0:
            return
        lanes = {"unit": {}, "integration": {}}
        smoke = {}
        seen = set()
        root = Path(os.environ["CI_INVENTORY_ROOT"])
        nonce = os.environ["CI_INVENTORY_NONCE"]
        for item in self.items:
            if not isinstance(item, pytest.Function):
                raise ValueError("unsupported collected item kind")
            name = str(item.path.resolve().relative_to(root.resolve()))
            file_path(name, root)
            key = node_digest(item.nodeid, nonce)
            if key in seen:
                raise ValueError("duplicate collected identity")
            seen.add(key)
            marks = {marker.name for marker in item.iter_markers()}
            excluded = bool(marks & {"nightly", "bench", "perf"})
            if "integration" in marks and not excluded:
                lanes["integration"].setdefault(name, []).append(key)
            elif not excluded and "e2e" not in marks and not name.startswith("tests/e2e/"):
                lanes["unit"].setdefault(name, []).append(key)
            if "smoke" in marks and name.startswith("tests/") and not name.startswith("tests/e2e/"):
                smoke.setdefault(name, []).append(key)
        for files in (*lanes.values(), smoke):
            for values in files.values():
                values.sort()
        body = {
            "complete": True,
            "lanes": lanes,
            "smoke": smoke,
            "pytest_version": pytest.__version__,
            "plugins_digest": digest(self.plugins),
        }
        Path(os.environ["CI_INVENTORY_OUTPUT"]).write_text(json.dumps(body, sort_keys=True) + "\n")


def pytest_configure(config):
    if os.environ.get("CI_INVENTORY_OUTPUT"):
        config.pluginmanager.register(Collector(), "ci-actual-inventory")
