"""Private testcase identities and real controller completion times for CI shards.

This plugin observes the existing selection. It never selects, skips, or retries
tests. Raw parametrized identities are hashed before any receipt is written.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import resource
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest

from butlers.testing.scope_cost import context, environment


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def node_digest(node_id: str, nonce: str | None = None) -> str:
    """One identity implementation for fresh collection and actual execution.

    Nonces minimize cross-run linkability; public salts are not authority and
    cannot prevent guessing low-entropy raw parameter values.
    """
    return digest([nonce, node_id]) if nonce is not None else digest(node_id)


class Observer:
    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        self.started = float(os.environ["CI_SHARD_STARTED"])
        self.collections: list[list[str]] = []
        self.phases: dict[str, dict[str, dict]] = {}
        self.files: dict[str, str] = {}
        self.classes: dict[str, str] = {}
        self.completed: list[float] = []
        self.first_result: float | None = None
        self.first_logical_test: float | None = None
        self.phase_completions: list[float] = []
        self.workers: set[str] = set()
        self.resources: dict[str, dict] = {}
        self.tracers: set[str] = set()
        self.phase_counts: Counter = Counter()
        self.context = json.loads(os.environ["CI_SHARD_CONTEXT"])
        self.nonce = self.context.get("nonce")
        self.allowed_files = set(self.context["files"])
        self.logical_starts: Counter = Counter()
        self.unexpected_file = False

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        if not hasattr(self.config, "workerinput") and not self.config.getoption("numprocesses"):
            self.collections.append(
                [node_digest(item.nodeid, self.nonce) for item in session.items]
            )

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodeready(self, node) -> None:
        self.workers.add(node.workerinput["workerid"])

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_collection_finished(self, node, ids: list[str]) -> None:
        self.collections.append([node_digest(item, self.nonce) for item in ids])

    def pytest_runtest_logstart(self, nodeid, location) -> None:
        if self.first_logical_test is None:
            self.first_logical_test = time.monotonic() - self.started
        self.logical_starts[node_digest(nodeid, self.nonce)] += 1

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        key = node_digest(report.nodeid, self.nonce)
        name = report.nodeid.split("::", 1)[0]
        self.unexpected_file |= name not in self.allowed_files
        self.files[key] = name if name in self.allowed_files else "unknown"
        components = report.nodeid.split("::")
        classname = name.removesuffix(".py").replace("/", ".")
        if len(components) > 2:
            classname += "." + (
                components[1]
                if re.fullmatch(r"[A-Za-z_]\w*", components[1])
                else digest(components[1])
            )
        self.classes[key] = classname
        offset = time.monotonic() - self.started
        self.phase_completions.append(offset)
        self.phase_counts[(key, report.when)] += 1
        self.phases.setdefault(key, {})[report.when] = {
            "outcome": report.outcome,
            "duration_s": report.duration,
            "completed_s": offset,
        }
        self.workers.add(str(getattr(report, "worker_id", "controller")))
        for name, value in report.user_properties:
            if name == "ci_runtime":
                runtime = json.loads(value)
                self.resources[str(getattr(report, "worker_id", "controller"))] = runtime
                if runtime["tracer"] is not None:
                    self.tracers.add(runtime["tracer"])
        if report.when == "call" or (report.when == "setup" and not report.passed):
            if self.first_result is None:
                self.first_result = offset
        if report.when == "teardown":
            self.completed.append(offset)

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        selected = self.collections[0] if self.collections else []
        agree = bool(selected) and all(Counter(c) == Counter(selected) for c in self.collections)
        complete = (
            exitstatus == 0
            and agree
            and len(selected) == len(set(selected))
            and set(self.phases) == set(selected)
            and all("teardown" in phases for phases in self.phases.values())
            and all(count == 1 for count in self.phase_counts.values())
            and self.logical_starts == Counter({node: 1 for node in selected})
            and not self.unexpected_file
        )
        phase_durations: dict[str, list[float]] = {}
        for key, phases in self.phases.items():
            file = self.files[key]
            phase_durations.setdefault(file, []).extend(p["duration_s"] for p in phases.values())
        # Arrival order and JSON's sorted node/phase keys must reconstruct the
        # same exact binary float, without a tolerance that could hide tampering.
        durations = {file: math.fsum(values) for file, values in phase_durations.items()}
        ordered = sorted(self.completed)
        count = len(selected)
        one_percent = ordered[max(0, (count + 99) // 100 - 1)] if complete else None
        tail_start = ordered[max(0, int(count * 0.95) - 1)] if complete else None
        finish = time.monotonic() - self.started
        receipt = {
            **self.context,
            "schema": 1,
            "cost_context": context(Path.cwd()),
            "cost_environment": environment(Path.cwd()),
            "complete": complete,
            "pytest_exit": int(exitstatus),
            "collected_at": datetime.now(UTC).isoformat(),
            "selected_count": count,
            "selected_node_digest": digest(sorted(selected)),
            "nodes": self.phases,
            "logical_starts": dict(self.logical_starts),
            "node_files": self.files,
            "node_classes": self.classes,
            "file_durations_s": durations,
            "effective_workers": sorted(self.workers),
            "worker_resources": self.resources,
            "actual_tracers": sorted(self.tracers),
            "first_result_s": self.first_result,
            "first_logical_test_s": self.first_logical_test,
            "last_test_completed_s": max(self.phase_completions)
            if self.phase_completions
            else None,
            "first_one_percent_s": one_percent,
            "last_five_percent_s": finish - tail_start if tail_start is not None else None,
            "setup_complete_s": min(
                (p["setup"]["completed_s"] for p in self.phases.values() if "setup" in p),
                default=None,
            ),
            "test_step_elapsed_s": finish,
            "metrics_scope": "controller observation; job setup requires job metadata",
        }
        Path(os.environ["CI_SHARD_RECEIPT"]).write_text(json.dumps(receipt, sort_keys=True) + "\n")


def pytest_configure(config: pytest.Config) -> None:
    if os.environ.get("CI_SHARD_RECEIPT") and not hasattr(config, "workerinput"):
        config.pluginmanager.register(Observer(config), "ci-shard-observer")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call):
    result = yield
    report = result.get_result()
    usage = resource.getrusage(resource.RUSAGE_SELF)
    plugin = item.config.pluginmanager.getplugin("_cov")
    cov = getattr(getattr(plugin, "cov_controller", None), "cov", None)
    collector = getattr(cov, "_collector", None)
    report.user_properties.append(
        (
            "ci_runtime",
            json.dumps(
                {
                    "cpu_s": usage.ru_utime + usage.ru_stime,
                    "max_rss": usage.ru_maxrss,
                    "rss_units": "KiB on Linux; bytes on macOS",
                    "tracer": collector.tracer_name() if collector is not None else None,
                },
                sort_keys=True,
            ),
        )
    )
