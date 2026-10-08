"""Private testcase identities and real controller completion times for CI shards.

This plugin preserves the existing selection and applies its validated file
schedule after lexical collection. It never selects, skips, or retries tests.
Raw parametrized identities are hashed before any receipt is written.
"""

from __future__ import annotations

import hashlib
import json
import os
import resource
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]):
    """Apply file priority last, in each worker, without changing its selection.

    The outer wrapper resumes after fixture reordering and marker deselection.
    Stable sorting retains the resulting intra-file order and loadfile groups.
    ``CI_SHARD_CONTEXT.files`` is the runner's already validated schedule;
    collection argv remains lexical to preserve conftest collector identity.
    """
    result = yield
    if os.environ.get("CI_SHARD_CONTEXT"):
        files = json.loads(os.environ["CI_SHARD_CONTEXT"])["files"]
        if (
            not isinstance(files, list)
            or not all(isinstance(file, str) for file in files)
            or len(files) != len(set(files))
        ):
            raise pytest.UsageError("invalid CI shard file schedule")
        priority = {file: index for index, file in enumerate(files)}
        if any(item.nodeid.split("::", 1)[0] not in priority for item in items):
            raise pytest.UsageError("collected item outside CI shard file schedule")
        items.sort(key=lambda item: priority[item.nodeid.split("::", 1)[0]])
    return result


class Observer:
    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        self.started = float(os.environ["CI_SHARD_STARTED"])
        self.collections: list[list[str]] = []
        self.phases: dict[str, dict[str, dict]] = {}
        self.files: dict[str, str] = {}
        self.completed: list[float] = []
        self.first_result: float | None = None
        self.workers: set[str] = set()
        self.resources: dict[str, dict] = {}
        self.tracers: set[str] = set()
        self.phase_counts: Counter = Counter()
        self.allowed_files = set(json.loads(os.environ["CI_SHARD_CONTEXT"])["files"])
        self.unexpected_file = False

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        if not hasattr(self.config, "workerinput") and not self.config.getoption("numprocesses"):
            self.collections.append([digest(item.nodeid) for item in session.items])

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodeready(self, node) -> None:
        self.workers.add(node.workerinput["workerid"])

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_collection_finished(self, node, ids: list[str]) -> None:
        self.collections.append([digest(item) for item in ids])

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        key = digest(report.nodeid)
        name = report.nodeid.split("::", 1)[0]
        self.unexpected_file |= name not in self.allowed_files
        self.files[key] = name if name in self.allowed_files else "unknown"
        offset = time.monotonic() - self.started
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
            and not self.unexpected_file
        )
        durations: dict[str, float] = {}
        for key, phases in self.phases.items():
            file = self.files[key]
            durations[file] = durations.get(file, 0) + sum(p["duration_s"] for p in phases.values())
        ordered = sorted(self.completed)
        count = len(selected)
        one_percent = ordered[max(0, (count + 99) // 100 - 1)] if complete else None
        tail_start = ordered[max(0, int(count * 0.95) - 1)] if complete else None
        finish = time.monotonic() - self.started
        receipt = {
            **json.loads(os.environ["CI_SHARD_CONTEXT"]),
            "schema": 1,
            "complete": complete,
            "pytest_exit": int(exitstatus),
            "collected_at": datetime.now(UTC).isoformat(),
            "selected_count": count,
            "selected_node_digest": digest(sorted(selected)),
            "nodes": self.phases,
            "node_files": self.files,
            "file_durations_s": durations,
            "effective_workers": sorted(self.workers),
            "worker_resources": self.resources,
            "actual_tracers": sorted(self.tracers),
            "first_result_s": self.first_result,
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
