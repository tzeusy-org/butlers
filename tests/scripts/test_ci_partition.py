"""Real miniature collection/execution controls, never full hosted corpus proof."""

from __future__ import annotations

import copy
import io
import json
import math
import random
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import check_ci_test_shards as legacy  # noqa: E402
import ci_affected as affected  # noqa: E402
import ci_partition as partition  # noqa: E402
import ci_preflight_reconcile as preflight  # noqa: E402
import ci_weight_candidates as candidates  # noqa: E402
from ci_shard_observer import node_digest  # noqa: E402

pytestmark = pytest.mark.unit


def _corpus(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # This real miniature Git checkout is local fixture evidence. Never borrow
    # the hosted checkout/attempt identity; pytest restores it after this test.
    for name in (
        "GITHUB_SHA",
        "GITHUB_REPOSITORY",
        "GITHUB_WORKFLOW",
        "GITHUB_RUN_ID",
        "GITHUB_RUN_ATTEMPT",
        "GITHUB_EVENT_NAME",
    ):
        monkeypatch.delenv(name, raising=False)
    (root / "tests").mkdir()
    (root / "roster").mkdir()
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths=["tests","roster"]\n'
        'markers=["unit","integration","smoke","e2e","nightly","bench","perf"]\n'
    )
    for index in range(6):
        (root / f"tests/test_{index}.py").write_text(
            "import pytest\n"
            "@pytest.mark.parametrize('value',[1,2])\n"
            "def test_unit(value): assert value in (1,2)\n"
            "@pytest.mark.integration\n"
            "def test_integration(): pass\n"
        )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=CI fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )


def _weights(data: dict) -> dict:
    return {
        "schema": "ci-weights.v1",
        "collected_at": datetime.now(UTC).isoformat(),
        "config_digest": data["identity"]["config_digest"],
        "lanes": {
            lane: {name: index + 1 for index, name in enumerate(files)}
            for lane, files in data["lanes"].items()
        },
    }


def test_partition_preserves_fresh_membership_with_unknown_weights(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-ci-shard-assurance-001/002/008: actual collector, not a glob/weights mirror."""
    _corpus(tmp_path, monkeypatch)
    first = partition.collect_inventory(root=tmp_path)
    # Real hosted mismatch remains a refusal; fixture isolation does not weaken
    # the production admission check or claim a hosted source identity.
    with monkeypatch.context() as hosted:
        hosted.setenv("GITHUB_SHA", "0" * 40)
        with pytest.raises(ValueError, match="checkout does not match workflow"):
            partition.checkout_identity(tmp_path)
    assert partition.checkout_identity(tmp_path) == first["identity"]
    for lane in partition.DIMENSIONS:
        independent = legacy.collect_lane_node_ids(lane, repo_root=tmp_path)
        expected = {node_digest(node, first["nonce"]) for node in independent}
        assert expected == {node for values in first["lanes"][lane].values() for node in values}
    # Positive files missing from weights require no registration edits.
    for index in random.Random(41).sample(range(100), 5):
        (tmp_path / f"tests/test_new_{index}.py").write_text("def test_new(): pass\n")
    (tmp_path / "conftest.py").write_text(
        "import pytest\n"
        "def pytest_collection_modifyitems(items):\n"
        " for item in items:\n"
        "  if item.name=='test_new': item.add_marker(pytest.mark.smoke)\n"
    )
    current = partition.collect_inventory(root=tmp_path)
    assert len(current["lanes"]["unit"]) == 11
    assert len(current["smoke"]) == 5
    assert current["identity"]["config_digest"] != first["identity"]["config_digest"]
    assert not (tmp_path / ".github").exists()
    # Actual immutable old verifier cannot admit the five eligible files when
    # its historical registrations remain unchanged. This is a positioned RED,
    # not an absent-new-helper failure and not a hosted scratch-PR canary.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures/ci_shards"))
    import hand_manifest

    old_files = list(first["lanes"]["unit"])
    old_specs = []
    historical = tmp_path / ".github/ci-test-shards"
    historical.mkdir(parents=True)
    for index in range(1, 6):
        names = old_files[index - 1 :: 5]
        path = historical / f"unit-{index}.txt"
        path.write_text("\n".join(sorted(names)) + "\n")
        old_specs.append(hand_manifest.ShardSpec("unit", index, path.relative_to(tmp_path)))
    with pytest.raises(ValueError, match="Missing selected test files"):
        hand_manifest._validate_lane(lane="unit", shard_specs=old_specs, repo_root=tmp_path)
    weights = _weights(current)
    for malformed in (
        {},
        {**weights, "lanes": None},
        {**weights, "collected_at": "2000-01-01T00:00:00+00:00"},
    ):
        result = partition.partition(current, malformed, root=tmp_path)
        assert result["weights_degraded"] is True
        partition.validate_assignment(current, result, root=tmp_path)
    # Advisory-file parsing must reach the real default partition algorithm;
    # authoritative inventory/assignment/receipt readers remain strict.
    weights_path = tmp_path / ".github/ci-test-weights.json"
    budget_path = tmp_path / "scripts/test-budget-baseline.json"
    budget_path.parent.mkdir()
    budget_path.write_text((partition.ROOT / "scripts/test-budget-baseline.json").read_text())
    native_partition = partition.partition
    native_budgets = partition.check_budgets
    fallback = native_partition(current, {}, root=tmp_path)
    for raw, degraded in (
        (json.dumps(weights), False),
        ("{", True),
        ("[]", True),
        (json.dumps(weights)[:-1] + ',"schema":"ci-weights.v1"}', True),
        (json.dumps({**weights, "collected_at": "2000-01-01T00:00:00+00:00"}), True),
        (json.dumps(weights), False),
    ):
        weights_path.write_text(raw)
        with monkeypatch.context() as entry:
            entry.setattr(partition, "ROOT", tmp_path)
            # Reuse this actual fresh miniature collector output, not a fake
            # inventory. Budget validation and LPT are the production algorithms.
            entry.setattr(partition, "collect_inventory", lambda **kwargs: copy.deepcopy(current))
            entry.setattr(
                partition,
                "check_budgets",
                lambda data, **kwargs: native_budgets(data, root=tmp_path),
            )
            entry.setattr(
                partition,
                "partition",
                lambda data, timing, **kwargs: native_partition(data, timing, root=tmp_path),
            )
            entry.setattr(sys, "argv", ["ci_partition", "--output", str(tmp_path / "output")])
            assert partition.main() == 0
            observed = partition.read_json(tmp_path / "output/assignment.json")
            assert observed["weights_degraded"] is degraded
            assert (
                observed["shards"]
                == (fallback if degraded else native_partition(current, weights, root=tmp_path))[
                    "shards"
                ]
            )
            assert legacy.verify(repo_root=tmp_path) == {
                lane: (sum(map(len, files.values())), len(files))
                for lane, files in current["lanes"].items()
            }
        if raw in ("{", "[]") or raw.endswith(',"schema":"ci-weights.v1"}'):
            with pytest.raises(ValueError):
                partition.read_json(weights_path)
    # The standalone path genuinely executes the miniature assigned shard;
    # malformed advisory metadata cannot hide any collected item or selector.
    weights_path.write_text("{")
    with monkeypatch.context() as standalone:
        standalone.setenv("CI_COVERAGE", "0")
        assert (
            legacy.run_shard(
                lane="unit",
                shard=1,
                repo_root=tmp_path,
                coverage_file=None,
                evidence_dir=tmp_path / "standalone-evidence",
            )
            == 0
        )
        receipt = partition.read_json(tmp_path / "standalone-evidence/shard-observation.json")
        assert receipt["complete"] is True and receipt["pytest_exit"] == 0
    weights_path.unlink()
    result = partition.partition(current, weights, root=tmp_path)
    reordered = copy.deepcopy(current)
    reordered["lanes"] = {
        lane: dict(reversed(list(files.items())))
        for lane, files in reversed(list(current["lanes"].items()))
    }
    reordered["digest"] = partition.body_digest(reordered)
    assert partition.partition(reordered, weights, root=tmp_path)["shards"] == result["shards"]
    assert len(result["shards"]["unit"]) == 5
    assert len(result["shards"]["integration"]) == 6
    assert all(item["files"] for bins in result["shards"].values() for item in bins)
    missing = copy.deepcopy(result)
    missing["shards"]["unit"][0]["files"].pop()
    missing["digest"] = partition.body_digest(missing)
    with pytest.raises(ValueError, match="omits or duplicates"):
        partition.validate_assignment(current, missing, root=tmp_path)
    duplicate = copy.deepcopy(result)
    duplicate["shards"]["unit"][1]["files"].append(duplicate["shards"]["unit"][0]["files"][0])
    duplicate["digest"] = partition.body_digest(duplicate)
    with pytest.raises(ValueError, match="omits or duplicates"):
        partition.validate_assignment(current, duplicate, root=tmp_path)


def test_reconciliation_requires_complete_node_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pytestconfig: pytest.Config
) -> None:
    """REQ-ci-shard-assurance-003/005/006/008; REQ-testing-051: identity and origin."""
    _corpus(tmp_path, monkeypatch)
    # Fresh default scoped reference + real xdist execution + independent
    # recollection. This miniature is genuine software, never hosted/SQL proof.
    scoped_output = tmp_path / "scoped-proof"
    paths = ["tests/test_0.py"]
    # A surrounding real matrix child must not lend its collection schedule or
    # output destination to this independent subprocess protocol.
    with monkeypatch.context() as inherited:
        inherited.setenv("CI_SHARD_CONTEXT", json.dumps({"files": ["tests/not_selected.py"]}))
        inherited.setenv("CI_SHARD_RECEIPT", str(tmp_path / "parent-must-stay-absent.json"))
        inherited.setenv("CI_SHARD_STARTED", "0")
        scoped = affected.execute(paths, root=tmp_path, output=scoped_output, workers="3")
    assert not (tmp_path / "parent-must-stay-absent.json").exists()
    assert scoped["verified"] is True and scoped["selected_count"] == 3
    reference = partition.read_json(scoped_output / "selected-reference.json")
    observed = partition.read_json(scoped_output / "selected-execution.json")
    arguments = dict(
        identity=observed["inventory_identity"],
        paths=paths,
        nonce=observed["nonce"],
        command=observed["command"],
    )
    for corrupt in (
        {"complete": False},
        {"pytest_exit": 1},
        {"nonce": "0" * 64},
        {"logical_starts": {key: 2 for key in reference["nodes"]}},
        {"nodes": {}},
        {"inventory_identity": {}},
        {"actual_selector": {}},
        {"complete": 1},
        {"pytest_exit": False},
        {"pytest_exit": 0.0},
        {"selected_count": float(len(reference["nodes"]))},
        {"selected_count": True},
        {"logical_starts": {key: True for key in reference["nodes"]}},
        {"logical_starts": {key: 1.0 for key in reference["nodes"]}},
        {"schema": True},
        {"schema": 1.0},
        {"test_step_elapsed_s": False},
        {"file_durations_s": {path: False for path in observed["file_durations_s"]}},
    ):
        with pytest.raises(ValueError):
            affected.verify(reference, {**observed, **corrupt}, **arguments)
    wrong = copy.deepcopy(observed)
    node = next(iter(wrong["nodes"]))
    substitute = "0" * 64
    wrong["nodes"][substitute] = wrong["nodes"].pop(node)
    wrong["node_files"][substitute] = wrong["node_files"].pop(node)
    wrong["logical_starts"][substitute] = wrong["logical_starts"].pop(node)
    wrong["selected_node_digest"] = partition.digest(sorted(wrong["nodes"]))
    with pytest.raises(ValueError):
        affected.verify(reference, wrong, **arguments)
    assert affected.verify(reference, observed, **arguments)["verified"] is True
    for corrupt_reference in ({**reference, "complete": False}, {**reference, "digest": "0" * 64}):
        with pytest.raises(ValueError):
            affected.verify(corrupt_reference, observed, **arguments)
    for mutation in ("missing_phase", "failed_phase", "nonfinite_timer", "bad_total"):
        corrupt = copy.deepcopy(observed)
        target = next(iter(corrupt["nodes"]))
        if mutation == "missing_phase":
            corrupt["nodes"][target].pop("teardown")
        elif mutation == "failed_phase":
            corrupt["nodes"][target]["call"]["outcome"] = "failed"
        elif mutation == "nonfinite_timer":
            corrupt["nodes"][target]["call"]["duration_s"] = float("inf")
        else:
            corrupt["file_durations_s"][paths[0]] += 1
        with pytest.raises(ValueError):
            affected.verify(reference, corrupt, **arguments)
    # Preserve actual default marker deselection, declared and dynamic skips.
    selected_file = tmp_path / paths[0]
    original_file = selected_file.read_text()
    config_file = tmp_path / "pyproject.toml"
    original_config = config_file.read_text()
    config_file.write_text(
        original_config + "addopts=\"-m 'not nightly and not bench and not perf'\"\n"
    )
    selected_file.write_text(
        original_file
        + "@pytest.mark.skip(reason='conformance')\ndef test_declared_skip(): assert False\n"
        + "def test_dynamic_skip(): pytest.skip('conformance')\n"
        + "@pytest.mark.nightly\ndef test_not_selected(): assert False\n"
    )
    skips = affected.execute(paths, root=tmp_path, output=tmp_path / "skips", workers="3")
    assert skips["verified"] is True and skips["selected_count"] == 5
    skipped_observed = partition.read_json(tmp_path / "skips/selected-execution.json")
    assert (
        sum(
            any(phase["outcome"] == "skipped" for phase in phases.values())
            for phases in skipped_observed["nodes"].values()
        )
        == 2
    )
    selected_file.write_text(original_file + "def test_real_failure(): assert False\n")
    with pytest.raises(ValueError, match="selected pytest failed"):
        affected.execute(paths, root=tmp_path, output=tmp_path / "failed", workers="3")
    assert not (tmp_path / "failed/selected-proof.json").exists()
    failed = partition.read_json(tmp_path / "failed/selected-execution.json")
    assert failed["pytest_exit"] == 1 and failed["complete"] is False
    selected_file.write_text(original_file)
    config_file.write_text(original_config)
    # Actual malformed collection refuses before any body/verification output.
    (tmp_path / "conftest.py").write_text(
        "def pytest_collection_modifyitems(items): items.append(items[0])\n"
    )
    with pytest.raises(ValueError, match="collection unavailable"):
        affected.execute(paths, root=tmp_path, output=tmp_path / "duplicate", workers="3")
    assert not (tmp_path / "duplicate/selected-proof.json").exists()
    (tmp_path / "conftest.py").unlink()
    # The original dedicated selector sees this actual marked miniature item.
    (tmp_path / "conftest.py").write_text(
        "import pytest\n"
        "def pytest_collection_modifyitems(items):\n"
        " for item in items:\n"
        "  if item.nodeid=='tests/test_0.py::test_unit[1]': item.add_marker(pytest.mark.smoke)\n"
    )
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
    # Synthetic protocol fixtures never claim actual execution provenance.
    assert partition.reconcile(data, assignment, receipts, root=tmp_path)["complete"]
    first = next(iter(receipts))
    for field, malformed in (
        ("complete", 1),
        ("pytest_exit", False),
        ("pytest_exit", 0.0),
        ("selected_count", float(receipts[first]["selected_count"])),
        ("selected_count", True),
        ("logical_starts", {node: True for node in receipts[first]["nodes"]}),
        ("logical_starts", {node: 1.0 for node in receipts[first]["nodes"]}),
        ("shard", float(receipts[first]["shard"])),
        ("schema", True),
        ("schema", 1.0),
        ("test_step_elapsed_s", False),
    ):
        malformed_receipts = copy.deepcopy(receipts)
        malformed_receipts[first][field] = malformed
        with pytest.raises(ValueError):
            partition.reconcile(data, assignment, malformed_receipts, root=tmp_path)
    for field, malformed in (("weights_degraded", 1),):
        malformed_assignment = copy.deepcopy(assignment)
        malformed_assignment[field] = malformed
        malformed_assignment["digest"] = partition.body_digest(malformed_assignment)
        with pytest.raises(ValueError):
            partition.validate_assignment(data, malformed_assignment, root=tmp_path)
    malformed_assignment = copy.deepcopy(assignment)
    malformed_assignment["shards"]["unit"][0]["index"] = 1.0
    malformed_assignment["digest"] = partition.body_digest(malformed_assignment)
    with pytest.raises(ValueError):
        partition.validate_assignment(data, malformed_assignment, root=tmp_path)
    assert partition.reconcile(data, assignment, receipts, root=tmp_path)["complete"] is True
    # Synthetic phase relations use the same fresh actual miniature inventory.
    # Setup-skip has no body phase; a dynamic skip legitimately occurs in call.
    for kind in ("setup-skip", "dynamic-call-skip"):
        skipped = copy.deepcopy(receipts)
        node = next(iter(skipped[first]["nodes"]))
        phases = skipped[first]["nodes"][node]
        if kind == "setup-skip":
            phases["setup"]["outcome"] = "skipped"
            phases.pop("call")
        else:
            phases["call"]["outcome"] = "skipped"
        assert partition.reconcile(data, assignment, skipped, root=tmp_path)["complete"] is True
    inconsistent = copy.deepcopy(receipts)
    node = next(iter(inconsistent[first]["nodes"]))
    inconsistent[first]["nodes"][node]["setup"]["outcome"] = "skipped"
    with pytest.raises(ValueError, match="phase incomplete"):
        partition.reconcile(data, assignment, inconsistent, root=tmp_path)
    # Synthetic raw timers pin deterministic wire reconstruction, not hosted
    # execution. JSON sorts node/phase keys and must not change exact totals.
    timed = copy.deepcopy(receipts)
    timer_label = "unit-1"
    timer_file = min(timed[timer_label]["file_durations_s"])
    timer_nodes = sorted(
        timed[timer_label]["nodes"],
        key=lambda node: (timed[timer_label]["node_files"][node] != timer_file, node),
    )
    for node, durations in zip(timer_nodes, ((0.001, 0.001, 0.001), (0.001, 0.3, 0.7))):
        for phase, seconds in zip(("setup", "call", "teardown"), durations):
            timed[timer_label]["nodes"][node][phase]["duration_s"] = seconds
    timed[timer_label]["file_durations_s"] = {
        name: math.fsum(
            phase["duration_s"]
            for node, phases in timed[timer_label]["nodes"].items()
            if timed[timer_label]["node_files"][node] == name
            for phase in phases.values()
        )
        for name in timed[timer_label]["file_durations_s"]
    }
    for values in (timed, json.loads(json.dumps(timed, sort_keys=True))):
        assert partition.reconcile(data, assignment, values, root=tmp_path)["complete"]
    reversed_timers = copy.deepcopy(timed)
    reversed_timers[timer_label]["nodes"] = {
        node: dict(reversed(list(phases.items())))
        for node, phases in reversed(list(timed[timer_label]["nodes"].items()))
    }
    flattened = 0.0
    for node, phases in reversed_timers[timer_label]["nodes"].items():
        if reversed_timers[timer_label]["node_files"][node] == timer_file:
            for phase in phases.values():
                flattened += phase["duration_s"]
    assert flattened != timed[timer_label]["file_durations_s"][timer_file]
    assert partition.reconcile(data, assignment, reversed_timers, root=tmp_path)["complete"]
    for kind in (
        "aggregate",
        "one-ulp",
        "aggregate-bool",
        "timer-nan",
        "timer-negative",
        "timer-bool",
    ):
        bad = copy.deepcopy(timed)
        name = bad[timer_label]["node_files"][timer_nodes[0]]
        if kind == "aggregate":
            bad[timer_label]["file_durations_s"][name] += 1.0
        elif kind == "one-ulp":
            bad[timer_label]["file_durations_s"][name] = math.nextafter(
                bad[timer_label]["file_durations_s"][name], math.inf
            )
        elif kind == "aggregate-bool":
            zero = next(label for label in bad if label != timer_label)
            name = next(iter(bad[zero]["file_durations_s"]))
            bad[zero]["file_durations_s"][name] = False
        else:
            bad[timer_label]["nodes"][timer_nodes[0]]["setup"]["duration_s"] = {
                "timer-nan": math.nan,
                "timer-negative": -1.0,
                "timer-bool": False,
            }[kind]
        with pytest.raises(ValueError, match="aggregate|timer"):
            partition.reconcile(data, assignment, bad, root=tmp_path)

    # Drive the real producer callback with explicit synthetic phase values.
    # Neither this fixture nor its receipt claims real execution provenance.
    from ci_shard_observer import Observer

    observer_receipt = tmp_path / "synthetic-observer.json"
    monkeypatch.setenv("CI_SHARD_STARTED", "0")
    monkeypatch.setenv(
        "CI_SHARD_CONTEXT", json.dumps({"files": list(timed[timer_label]["file_durations_s"])})
    )
    monkeypatch.setenv("CI_SHARD_RECEIPT", str(observer_receipt))
    observer = Observer(pytestconfig)
    observer.phases = timed[timer_label]["nodes"]
    observer.files = timed[timer_label]["node_files"]
    observer.classes = timed[timer_label]["node_classes"]
    observer.collections = [timer_nodes]
    observer.logical_starts.update({node: 1 for node in timer_nodes})
    observer.phase_counts.update(
        {(node, phase): 1 for node in timer_nodes for phase in observer.phases[node]}
    )
    observer.completed = [0.0 for node in timer_nodes]
    observer.pytest_sessionfinish(None, 0)
    produced = json.loads(observer_receipt.read_text())
    assert produced["complete"] is True
    assert produced["file_durations_s"] == timed[timer_label]["file_durations_s"]
    for key, value in (("nonce", "0" * 64), ("complete", False), ("pytest_exit", 1)):
        bad = copy.deepcopy(receipts)
        bad[first][key] = value
        with pytest.raises(ValueError):
            partition.reconcile(data, assignment, bad, root=tmp_path)
    missing = {key: value for key, value in receipts.items() if key != first}
    with pytest.raises(ValueError, match="eleven"):
        partition.reconcile(data, assignment, missing, root=tmp_path)
    wrong = copy.deepcopy(receipts)
    original = next(iter(wrong[first]["nodes"]))
    wrong[first]["nodes"]["0" * 64] = wrong[first]["nodes"].pop(original)
    with pytest.raises(ValueError, match="identities"):
        partition.reconcile(data, assignment, wrong, root=tmp_path)
    duplicate = copy.deepcopy(receipts)
    duplicate[first]["logical_starts"][original] = 2
    with pytest.raises(ValueError, match="logical execution"):
        partition.reconcile(data, assignment, duplicate, root=tmp_path)

    # Real CLI/verifier with explicitly synthetic trusted execution inputs:
    # this tests derivation fields, never collected hosted execution provenance.
    inventory_dir, carriers_dir = tmp_path / "inventory", tmp_path / "carriers"
    inventory_dir.mkdir()
    carriers_dir.mkdir()
    (inventory_dir / "inventory.json").write_text(json.dumps(data))
    (inventory_dir / "assignment.json").write_text(json.dumps(assignment))
    for label, carrier in receipts.items():
        directory = carriers_dir / f"ci-{label}-test-evidence"
        directory.mkdir()
        (directory / "shard-observation.json").write_text(json.dumps(carrier))
    monkeypatch.setattr(partition, "ROOT", tmp_path)
    # reconcile's root default is source-bound, so the CLI integration fixture
    # fixes its owning root rather than bypassing its production verifier.
    monkeypatch.setattr(
        preflight, "reconcile", lambda *args: partition.reconcile(*args, root=tmp_path)
    )
    output = tmp_path / "preflight"
    github_output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))
    arguments = [
        "preflight",
        "--inventory-dir",
        str(inventory_dir),
        "--receipts",
        str(carriers_dir),
        "--output",
        str(output),
    ]
    monkeypatch.setattr(sys, "argv", arguments)
    assert preflight.main() == 0
    release = json.loads((output / "release-evidence.json").read_text())
    assert release["cmd"] == [
        receipts[f"{lane}-{index}"]["command"]
        for lane, count in partition.DIMENSIONS.items()
        for index in range(1, count + 1)
    ]
    assert release["sha"] == data["identity"]["head"]
    assert release["duration_s"] == 6.0
    assert "no dedicated smoke command timer" in release["duration_scope"]
    assert (
        release["smoke_selector"]
        == "uv run pytest tests/ --ignore=tests/e2e -m smoke -q --tb=short"
    )
    assert github_output.read_text() == "smoke_covered=true\n"
    smoke_missing = copy.deepcopy(data)
    smoke_missing["smoke"]["tests/test_0.py"].append("f" * 64)
    smoke_missing["smoke"]["tests/test_0.py"].sort()
    smoke_missing["digest"] = partition.body_digest(smoke_missing)
    missing_assignment = partition.partition(smoke_missing, {}, root=tmp_path)
    missing_receipts = copy.deepcopy(receipts)
    for carrier in missing_receipts.values():
        carrier["inventory_digest"] = smoke_missing["digest"]
        carrier["assignment_digest"] = missing_assignment["digest"]
    assert (
        partition.reconcile(smoke_missing, missing_assignment, missing_receipts, root=tmp_path)[
            "smoke_covered"
        ]
        is False
    )

    # Bounded historical reader exercises its genuine protocol verifier using
    # synthetic test artifacts. It never authenticates these as real GitHub runs.
    historical = copy.deepcopy(data)
    historical["identity"].update(
        repository="fixture/repo", run="123", attempt="1", event="merge_group"
    )
    historical["digest"] = partition.body_digest(historical)
    historical_assignment = copy.deepcopy(assignment)
    historical_assignment.update(
        identity=historical["identity"], inventory_digest=historical["digest"]
    )
    historical_assignment["digest"] = partition.body_digest(historical_assignment)
    historic_receipts = copy.deepcopy(receipts)
    for carrier in historic_receipts.values():
        carrier.update(
            inventory_identity=historical["identity"],
            inventory_digest=historical["digest"],
            assignment_digest=historical_assignment["digest"],
        )

    def archive(values):
        memory = io.BytesIO()
        with zipfile.ZipFile(memory, "w") as opened:
            for name, value in values.items():
                opened.writestr(name, json.dumps(value))
        return memory.getvalue()

    payloads = [archive({"inventory.json": historical, "assignment.json": historical_assignment})]
    payloads.extend(
        archive({"shard-observation.json": carrier}) for carrier in historic_receipts.values()
    )
    artifact_names = ["ci-inventory", *(f"ci-{label}-test-evidence" for label in historic_receipts)]
    artifacts = [
        {"id": index, "name": name, "expired": False, "size_in_bytes": len(payloads[index])}
        for index, name in enumerate(artifact_names)
    ]
    requested = []

    def public_api(path, *, binary=False):
        requested.append(path)
        if binary:
            return payloads[int(path.split("/")[-2])]
        if "/workflows/" in path:
            return {
                "workflow_runs": [
                    {
                        "id": 123,
                        "head_sha": historical["identity"]["head"],
                        "run_attempt": 1,
                        "event": "merge_group",
                        "conclusion": "success",
                    }
                ]
            }
        return {"artifacts": artifacts}

    monkeypatch.setattr(candidates, "api", public_api)
    monkeypatch.setattr(candidates, "ROOT", tmp_path)
    candidate_dir = tmp_path / "candidate"
    candidates.candidate(repository="fixture/repo", output=candidate_dir)
    candidate = json.loads((candidate_dir / "candidate.json").read_text())
    assert candidate["origin"] == historical["identity"]
    assert candidate["config_digest"] == data["identity"]["config_digest"]
    assert set(candidate["lanes"]["unit"]) == set(data["lanes"]["unit"])
    assert len(requested) == 14  # two bounded listings + twelve archive reads
    artifacts.append(dict(artifacts[0]))
    with pytest.raises(ValueError, match="ambiguous"):
        candidates.candidate(repository="fixture/repo", output=tmp_path / "refused")
    artifacts.pop()
    historic_receipts[first]["actual_tracers"] = ["SysMonitor"]
    payloads[1] = archive({"shard-observation.json": historic_receipts[first]})
    with pytest.raises(ValueError, match="tracing"):
        candidates.candidate(repository="fixture/repo", output=tmp_path / "mixed")
    monkeypatch.setenv("GITHUB_REPOSITORY", "fixture/repo")
    monkeypatch.setattr(sys, "argv", ["observer", "--output", str(tmp_path / "unknown")])
    assert candidates.main() == 2
    assert json.loads((tmp_path / "unknown/receipt.json").read_text())["state"] == "UNKNOWN"
    assert not (tmp_path / "unknown/candidate.json").exists()
