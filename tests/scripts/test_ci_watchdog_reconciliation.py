"""Diagnostic-only carriers cannot satisfy complete execution admission."""

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import ci_partition as partition  # noqa: E402

from tests.scripts.test_ci_partition import _corpus  # noqa: E402

pytestmark = pytest.mark.unit


def test_diagnostic_carriers_cannot_replace_complete_execution_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-testing-060: protocol conformance; never actual execution/timing proof."""
    import ci_watchdog_diagnostics as diagnostic

    _corpus(tmp_path, monkeypatch)
    data = partition.collect_inventory(root=tmp_path)
    assignment = partition.partition(data, {}, root=tmp_path)
    receipts = {}
    for lane, bins in assignment["shards"].items():
        for item in bins:
            nodes = {node: name for name in item["files"] for node in data["lanes"][lane][name]}
            receipts[f"{lane}-{item['index']}"] = {
                "complete": True,
                "pytest_exit": 0,
                "lane": lane,
                "shard": item["index"],
                "inventory_digest": data["digest"],
                "assignment_digest": assignment["digest"],
                "inventory_identity": data["identity"],
                "nonce": data["nonce"],
                "selected_count": len(nodes),
                "selected_node_digest": partition.digest(sorted(nodes)),
                "nodes": {
                    node: {
                        phase: {"outcome": "passed", "duration_s": 0.0, "completed_s": 0.0}
                        for phase in ("setup", "call", "teardown")
                    }
                    for node in nodes
                },
                "logical_starts": {node: 1 for node in nodes},
                "node_files": nodes,
                "node_classes": {
                    node: name.removesuffix(".py").replace("/", ".") for node, name in nodes.items()
                },
                "test_step_elapsed_s": float(item["index"]),
                "actual_tracers": ["CTracer"],
                "file_durations_s": {name: 0.0 for name in item["files"]},
                "command": [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-m",
                    partition.SELECTORS[lane],
                    "--",
                    *item["files"],
                ],
            }
    assert partition.reconcile(data, assignment, receipts, root=tmp_path)["complete"] is True
    bound = diagnostic.binding(data, assignment, capture_root=Path(__file__).parents[2])
    output = tmp_path / "diagnostic"
    diagnostic.configure(output, bound, root=tmp_path)
    withheld = next(iter(bound["expected"]))
    state = {
        "role": "worker",
        "worker": "gw0",
        "process": {
            "pid": 123,
            "ppid": 1,
            "start_ticks": 1,
            "state": "R",
            "threads": 1,
            "cpu_ticks": 0,
            "rss_pages": 1,
        },
        "sequence": len(bound["expected"]) * 10,
        "elapsed_ms": 1,
        "collections": sorted(bound["expected"]),
        "controller_events": {"collections": {}, "departures": {}},
        "nodes": {
            node: {
                "starts": 1,
                "departures": 0,
                "phases": {
                    phase: {"entries": 1, "passed": 1, "failed": 0, "skipped": 0}
                    for phase in diagnostic.PHASES
                    if node != withheld or phase != "teardown"
                },
            }
            for node in bound["expected"]
        },
        "frames": [],
        "stack_elapsed_ms": 0,
        "gaps": [],
        "finished": False,
    }
    diagnostic.atomic(output / "states/123.json", state)
    checkpoint = diagnostic.checkpoint(output, 0, elapsed_ms=1)
    report = diagnostic.read_checkpoint(checkpoint, bound)
    assert report["complete"] is False
    assert report["unknown"] == [f"{withheld}:teardown"]
    carrier = diagnostic.read_json(checkpoint / "snapshot.json")
    for complete in (False, True):
        substituted = copy.deepcopy(receipts)
        substituted["unit-1"] = {**carrier, "complete": complete}
        with pytest.raises(ValueError):
            partition.reconcile(data, assignment, substituted, root=tmp_path)
    attacker = copy.deepcopy(receipts)
    attacker["unit-1"]["nodes"][withheld].pop("teardown")
    attacker["unit-1"]["complete"] = True
    with pytest.raises(ValueError, match="phase incomplete"):
        partition.reconcile(data, assignment, attacker, root=tmp_path)
    assert partition.reconcile(data, assignment, receipts, root=tmp_path)["complete"] is True
