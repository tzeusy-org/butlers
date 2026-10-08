"""Real miniature collection/execution controls, never full hosted corpus proof."""

from __future__ import annotations

import copy
import io
import json
import random
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import check_ci_test_shards as legacy  # noqa: E402
import ci_partition as partition  # noqa: E402
import ci_preflight_reconcile as preflight  # noqa: E402
import ci_weight_candidates as candidates  # noqa: E402
from ci_shard_observer import node_digest  # noqa: E402

pytestmark = pytest.mark.unit


def _corpus(root: Path) -> None:
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


def test_partition_preserves_fresh_membership_with_unknown_weights(tmp_path: Path) -> None:
    """REQ-ci-shard-assurance-001/002/008: actual collector, not a glob/weights mirror."""
    _corpus(tmp_path)
    first = partition.collect_inventory(root=tmp_path)
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-ci-shard-assurance-003/005/006/008; REQ-testing-051: identity and origin."""
    _corpus(tmp_path)
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
