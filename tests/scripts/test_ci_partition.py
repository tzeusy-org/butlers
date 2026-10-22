"""Real miniature collection/execution controls, never full hosted corpus proof."""

from __future__ import annotations

import copy
import hashlib
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
    # the hosted checkout/attempt identity or instrumentation mode; explicit
    # profile cases set their own overrides and pytest restores the environment.
    for name in (
        "GITHUB_SHA",
        "GITHUB_REPOSITORY",
        "GITHUB_WORKFLOW",
        "GITHUB_RUN_ID",
        "GITHUB_RUN_ATTEMPT",
        "GITHUB_EVENT_NAME",
        "CI_COVERAGE",
        "CI_COVERAGE_CORE",
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


def _weights(data: dict, **overrides) -> dict:
    profile = {
        "runtime": partition.weight_runtime(data),
        "file_digests": data["file_digests"],
        "collected_at": datetime.now(UTC).isoformat(),
        "config_digest": data["identity"]["config_digest"],
        "lanes": {
            lane: {name: index + 1 for index, name in enumerate(files)}
            for lane, files in data["lanes"].items()
        },
        **overrides,
    }
    return {"schema": "ci-weights.v2", "profiles": {"CTracer": profile}}


def test_advisory_costs_survive_source_changes_without_changing_membership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-ci-shard-assurance-002: compatibility concerns cost, never current authority."""
    _corpus(tmp_path, monkeypatch)
    original = partition.collect_inventory(root=tmp_path)
    weights = _weights(original)
    before = partition.partition(original, weights, root=tmp_path)
    assert before["weights_degraded"] is False
    (tmp_path / "src").mkdir()
    (tmp_path / "src/product.py").write_text("VALUE = 42\n")
    changed = partition.collect_inventory(root=tmp_path)
    assert changed["identity"] != original["identity"]
    after = partition.partition(changed, weights, root=tmp_path)
    assert after["shards"] == before["shards"]
    assert after["weights_degraded"] is False
    with pytest.raises(ValueError, match="source/config/attempt"):
        partition.validate_inventory(original, root=tmp_path)
    changed_test = tmp_path / "tests/test_0.py"
    changed_test.write_text(changed_test.read_text() + "\n# changed fixture cost\n")
    changed_file = partition.collect_inventory(root=tmp_path)
    partial = partition.partition(changed_file, weights, root=tmp_path)
    assert partial["weights_degraded"] is True
    partition.validate_assignment(changed_file, partial, root=tmp_path)
    costs = weights["profiles"]["CTracer"]["lanes"]["unit"]
    # The changed file uses the remaining median (4); the five untouched hints survive.
    assert sum(bin["predicted_seconds"] for bin in partial["shards"]["unit"]) == 24
    assert len(costs) == 6
    defaults = partition.partition(changed_file, {}, root=tmp_path)["shards"]
    for field, bad in (
        ("python", "0.0.0"),
        ("lock_digest", "0" * 64),
        ("selectors", {}),
        ("pytest_version", "0"),
    ):
        incompatible = copy.deepcopy(weights)
        incompatible["profiles"]["CTracer"]["runtime"][field] = bad
        assert partition.partition(changed_file, incompatible, root=tmp_path)["shards"] == defaults
    for bad in (True, -1, 0, float("nan"), float("inf"), 10**400, sys.float_info.max):
        malformed = copy.deepcopy(weights)
        malformed["profiles"]["CTracer"]["lanes"]["unit"] = dict.fromkeys(costs, bad)
        result = partition.partition(changed_file, malformed, root=tmp_path)
        assert result["shards"]["unit"] == defaults["unit"]
        partition.validate_assignment(changed_file, result, root=tmp_path)
    monkeypatch.setenv("CI_COVERAGE", "0")
    assert partition.partition(changed_file, weights, root=tmp_path)["shards"] == defaults
    untraced = copy.deepcopy(weights)
    untraced["profiles"]["none"] = untraced["profiles"].pop("CTracer")
    assert partition.partition(changed_file, untraced, root=tmp_path)["shards"] == partial["shards"]


def test_observer_refresh_is_bounded_and_advisory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-ci-shard-assurance-002: a main observer can refresh costs, never membership."""
    _corpus(tmp_path, monkeypatch)
    inventory = partition.collect_inventory(root=tmp_path)
    weights = _weights(inventory)
    seed_dir = tmp_path / ".github"
    seed_dir.mkdir()
    seed = {
        "schema": "ci-weights.v2",
        "profiles": {"none": copy.deepcopy(weights["profiles"]["CTracer"])},
    }
    (seed_dir / "ci-test-weights.json").write_text(json.dumps(seed))
    profile = weights["profiles"]["CTracer"]
    profile["origin"] = {
        **inventory["identity"],
        "repository": "fixture/repo",
        "run": "123",
        "attempt": "1",
        "event": "merge_group",
    }
    receipt = {
        "state": "CANDIDATE",
        "run": "123",
        "attempt": "1",
        "head": profile["origin"]["head"],
    }
    receipt["provenance"] = {
        "failure": None,
        "selected_run": {k: receipt[k] for k in ("run", "attempt", "head")},
    }
    observer = {
        "id": 321,
        "head_branch": "main",
        "head_sha": "a" * 40,
        "head_repository": {"full_name": "fixture/repo"},
        "conclusion": "success",
        "status": "completed",
        "event": "schedule",
        "path": ".github/workflows/ci-shard-weights.yml",
    }
    artifact = {
        "id": 456,
        "name": "ci-weight-candidate",
        "expired": False,
        "size_in_bytes": 1000,
        "workflow_run": {"id": 321, "head_sha": "a" * 40},
    }
    requests = []

    def public_api(path, *, binary=False, timeout=30):
        requests.append((path, timeout))
        assert timeout == 5
        if binary:
            memory = io.BytesIO()
            with zipfile.ZipFile(memory, "w") as zipped:
                zipped.writestr("candidate.json", json.dumps(weights))
                zipped.writestr("receipt.json", json.dumps(receipt))
            return memory.getvalue()
        if "/workflows/" in path:
            return {"workflow_runs": [observer]}
        return {"artifacts": [artifact]}

    monkeypatch.setattr(candidates, "api", public_api)
    directory = tmp_path / "restored"
    candidates.restore(repository="fixture/repo", output=directory, root=tmp_path)
    restored = partition.read_json(directory / "weights.json")
    assert set(restored["profiles"]) == {"none", "CTracer"}
    assert restored["profiles"]["CTracer"] == profile
    assert len(requests) == 3
    assert partition.partition(inventory, restored, root=tmp_path)["weights_degraded"] is False
    for target, field, invalid in (
        (observer, "head_branch", "untrusted"),
        (observer, "path", ".github/workflows/other.yml"),
        (observer, "conclusion", "failure"),
        (artifact, "expired", True),
        (artifact, "workflow_run", {"id": 123, "head_sha": "a" * 40}),
        (receipt, "state", "UNKNOWN"),
        (receipt, "head", "b" * 40),
        (profile, "collected_at", "2000-01-01T00:00:00+00:00"),
        (profile, "runtime", {**profile["runtime"], "lock_digest": "0" * 64}),
        (profile, "file_digests", {}),
        (
            profile,
            "lanes",
            {**profile["lanes"], "unit": dict.fromkeys(profile["lanes"]["unit"], float("nan"))},
        ),
        (
            profile,
            "lanes",
            {**profile["lanes"], "unit": dict.fromkeys(profile["lanes"]["unit"], 10**400)},
        ),
        (
            profile,
            "lanes",
            {
                **profile["lanes"],
                "unit": dict.fromkeys(profile["lanes"]["unit"], sys.float_info.max),
            },
        ),
        (weights, "profiles", {"unsupported": profile}),
    ):
        original = target[field]
        target[field] = invalid
        candidates.restore(repository="fixture/repo", output=directory, root=tmp_path)
        assert partition.read_json(directory / "weights.json") == seed
        assert partition.read_json(directory / "restore-receipt.json")["state"] == "FALLBACK"
        target[field] = original

    def outage(*args, **kwargs):
        raise subprocess.TimeoutExpired("gh", 5)

    monkeypatch.setattr(candidates, "api", outage)
    candidates.restore(repository="fixture/repo", output=directory, root=tmp_path)
    assert partition.read_json(directory / "weights.json") == seed


def test_partition_preserves_fresh_membership_with_unknown_weights(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-ci-shard-assurance-001/002/008 and REQ-testing-052: actual collector, not a glob/weights mirror."""
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
        _weights(current, lanes=None),
        _weights(current, collected_at="2000-01-01T00:00:00+00:00"),
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
        (json.dumps(weights)[:-1] + ',"schema":"ci-weights.v2"}', True),
        (json.dumps(_weights(current, collected_at="2000-01-01T00:00:00+00:00")), True),
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
        if raw in ("{", "[]") or raw.endswith(',"schema":"ci-weights.v2"}'):
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
    candidate = json.loads((candidate_dir / "candidate.json").read_text())["profiles"]["CTracer"]
    assert candidate["origin"] == historical["identity"]
    assert candidate["config_digest"] == data["identity"]["config_digest"]
    assert set(candidate["lanes"]["unit"]) == set(data["lanes"]["unit"])
    assert len(requested) == 14  # two bounded listings + twelve archive reads
    # A full untraced run supplies its own profile, never relabels covered costs.
    for carrier in historic_receipts.values():
        carrier["actual_tracers"] = []
    for index, carrier in enumerate(historic_receipts.values(), 1):
        payloads[index] = archive({"shard-observation.json": carrier})
    untraced_dir = tmp_path / "untraced"
    candidates.candidate(repository="fixture/repo", output=untraced_dir)
    untraced = json.loads((untraced_dir / "candidate.json").read_text())
    assert set(untraced["profiles"]) == {"none"}
    assert untraced["profiles"]["none"]["collected_at"] == historical["collected_at"]
    assert untraced["profiles"]["none"]["file_digests"] == historical["file_digests"]
    historic_receipts[first].pop("actual_tracers")
    payloads[1] = archive({"shard-observation.json": historic_receipts[first]})
    with pytest.raises(ValueError, match="tracing"):
        candidates.candidate(repository="fixture/repo", output=tmp_path / "missing-tracer")
    for index, carrier in enumerate(historic_receipts.values(), 1):
        carrier["actual_tracers"] = ["CTracer"]
        payloads[index] = archive({"shard-observation.json": carrier})
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

    # P12: actual reader stages retain closed attempted/selected provenance,
    # including partial and unselected refusals. These API/archive wrappers and
    # tracer declarations are synthetic; they are not hosted merge_group proof.
    unknown = json.loads((tmp_path / "unknown/receipt.json").read_text())
    assert unknown.get("provenance", {}).get("selected_run") == {
        "run": "123",
        "attempt": "1",
        "head": historical["identity"]["head"],
    }
    assert unknown["provenance"]["failure"] == {
        "stage": "tracing-species",
        "kind": "evidence-refused",
    }
    original_tracers = receipts[first]["actual_tracers"]
    historic_receipts[first]["actual_tracers"] = original_tracers
    payloads[1] = archive({"shard-observation.json": historic_receipts[first]})
    run_record = {
        "id": 123,
        "head_sha": historical["identity"]["head"],
        "run_attempt": 1,
        "event": "merge_group",
        "conclusion": "success",
    }
    private_error = "synthetic-private-error-must-not-be-exported"

    def observed_api(mode):
        def read(path, *, binary=False):
            if "/workflows/" in path:
                if mode == "run-read":
                    raise OSError(private_error)
                if mode == "run-partial":
                    if "page=2" in path:
                        raise ValueError(private_error)
                    return {"workflow_runs": [run_record] * 50}
                if mode == "unselected":
                    return {"workflow_runs": []}
                row = copy.deepcopy(run_record)
                if mode == "partial-head":
                    row.pop("head_sha")
                return {"workflow_runs": [row]}
            if not binary:
                if mode == "artifact-read":
                    raise subprocess.TimeoutExpired("synthetic-private-command", 30)
                rows = copy.deepcopy(artifacts)
                if mode == "expired":
                    rows[0]["expired"] = True
                elif mode == "missing":
                    rows.pop()
                elif mode == "duplicate":
                    rows.append(dict(rows[0]))
                return {"artifacts": rows}
            index = int(path.split("/")[-2])
            if mode == "download" and index != 0:
                raise ValueError(private_error)
            if mode == "member" and index == 0:
                return b"synthetic-invalid-archive"
            return payloads[index]

        return read

    def observer(mode):
        directory = tmp_path / f"provenance-{mode}"
        monkeypatch.setattr(candidates, "api", observed_api(mode))
        monkeypatch.setattr(sys, "argv", ["observer", "--output", str(directory)])
        code = candidates.main()
        body = (directory / "receipt.json").read_text()
        assert private_error not in body
        assert "synthetic-private-command" not in body
        return code, json.loads(body), directory

    healthy_code, healthy_receipt, _ = observer("healthy")
    assert healthy_code == 0 and healthy_receipt["state"] == "CANDIDATE"
    assert healthy_receipt["provenance"]["failure"] is None
    assert healthy_receipt["provenance"]["selected_run"] == unknown["provenance"]["selected_run"]
    attempted = healthy_receipt["provenance"]["attempted_inputs"]
    downloads = [row for row in attempted if row["kind"] == "artifact-download"]
    assert len(downloads) == 12
    assert all(row["state"] == "available" and len(row["sha256"]) == 64 for row in downloads)
    assert {row["artifact"] for row in downloads} == set(artifact_names)
    assert all(
        row["bytes"] == len(payloads[int(row["id"])])
        and row["sha256"] == hashlib.sha256(payloads[int(row["id"])]).hexdigest()
        for row in downloads
    )
    for mode, stage, kind in (
        ("run-read", "run-index", "io-unavailable"),
        ("run-partial", "run-index", "evidence-refused"),
        ("unselected", "run-selection", "evidence-refused"),
        ("partial-head", "inventory-binding", "missing-field"),
        ("artifact-read", "artifact-index", "read-timeout"),
        ("expired", "artifact-selection", "evidence-refused"),
        ("missing", "artifact-population", "evidence-refused"),
        ("duplicate", "artifact-selection", "evidence-refused"),
        ("download", "artifact-download", "evidence-refused"),
        ("member", "artifact-member", "invalid-archive"),
    ):
        code, receipt, directory = observer(mode)
        assert code == 2 and receipt["state"] == "UNKNOWN"
        assert not (directory / "candidate.json").exists()
        provenance = receipt["provenance"]
        assert provenance["failure"] == {"stage": stage, "kind": kind}
        assert provenance["repository"] == "fixture/repo"
        assert provenance["attempted_inputs"]
        if mode in {"run-read", "run-partial", "unselected"}:
            assert provenance["selection"] == "unselected" and provenance["selected_run"] is None
        else:
            assert provenance["selection"] == "selected"
            assert provenance["selected_run"]["run"] == "123"
            assert provenance["selected_run"]["attempt"] == "1"
            assert provenance["selected_run"]["head"] == (
                None if mode == "partial-head" else historical["identity"]["head"]
            )
        if mode == "run-partial":
            assert provenance["attempted_inputs"] == [
                {
                    "kind": "run-index",
                    "page": 1,
                    "state": "available",
                    "rows": 50,
                    "body_sha256": hashlib.sha256(
                        json.dumps(
                            {"workflow_runs": [run_record] * 50},
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode()
                    ).hexdigest(),
                },
                {"kind": "run-index", "page": 2, "state": "unavailable"},
            ]
        if mode in {"expired", "duplicate"}:
            assert provenance["attempted_inputs"][-1]["reason"] == (
                "expired" if mode == "expired" else "duplicate"
            )
            assert provenance["attempted_inputs"][-1]["artifact"] == "ci-inventory"
        if mode in {"download", "member"}:
            assert provenance["attempted_inputs"][-1]["state"] == "unavailable"
    monkeypatch.delenv("GITHUB_REPOSITORY")
    code, receipt, directory = observer("configuration")
    assert code == 2 and not (directory / "candidate.json").exists()
    assert receipt["provenance"]["failure"] == {"stage": "configuration", "kind": "missing-field"}
    assert receipt["provenance"]["selected_run"] is None
    assert receipt["provenance"]["attempted_inputs"] == []
    monkeypatch.setenv("GITHUB_REPOSITORY", "fixture/repo")
    restored_code, restored_receipt, _ = observer("restored")
    assert restored_code == 0 and restored_receipt["state"] == "CANDIDATE"
    assert restored_receipt["provenance"] == healthy_receipt["provenance"]


def test_covered_weight_refresh_is_independent_and_source_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-ci-shard-assurance-002/006: genuine source policy, independent cost hints."""
    import struct

    _corpus(tmp_path, monkeypatch)
    inventory = partition.collect_inventory(root=tmp_path)
    covered = _weights(inventory)
    profile = covered["profiles"]["CTracer"]
    profile["origin"] = {
        **inventory["identity"],
        "repository": "fixture/repo",
        "workflow": "Nightly Tests",
        "run": "123",
        "attempt": "1",
        "event": "workflow_dispatch",
    }
    coordinates = {key: profile["origin"][key] for key in ("run", "attempt", "head")}
    receipt = {
        "state": "CANDIDATE",
        **coordinates,
        "provenance": {"failure": None, "selected_run": coordinates},
        "source": {
            "repository": "fixture/repo",
            "workflow": ".github/workflows/nightly.yml",
            "branch": "main",
            "event": "workflow_dispatch",
            **coordinates,
        },
    }
    untraced = copy.deepcopy(covered)
    untraced["profiles"]["none"] = untraced["profiles"].pop("CTracer")
    untraced["profiles"]["none"]["origin"]["event"] = "merge_group"
    legacy_receipt = {key: value for key, value in receipt.items() if key != "source"}
    bodies = {
        "candidate.json": untraced,
        "receipt.json": legacy_receipt,
        "covered-candidate.json": covered,
        "covered-receipt.json": receipt,
    }
    observer = {
        "id": 321,
        "head_branch": "main",
        "head_sha": "a" * 40,
        "head_repository": {"full_name": "fixture/repo"},
        "conclusion": "success",
        "status": "completed",
        "event": "schedule",
        "path": ".github/workflows/ci-shard-weights.yml",
    }
    artifact = {
        "id": 456,
        "name": "ci-weight-candidate",
        "expired": False,
        "size_in_bytes": 1000,
        "workflow_run": {"id": 321, "head_sha": "a" * 40},
    }
    requests = []
    duplicate_member = False
    damaged_member = None

    def public_api(path, *, binary=False, timeout=30):
        requests.append((path, timeout))
        assert timeout == 5
        if binary:
            memory = io.BytesIO()
            with zipfile.ZipFile(memory, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, body in bodies.items():
                    archive.writestr(name, json.dumps(body))
                if duplicate_member:
                    archive.writestr("covered-candidate.json", json.dumps(covered))
            original = memory.getvalue()
            if damaged_member is None:
                return original
            name, kind = damaged_member
            broken = bytearray(original)
            with zipfile.ZipFile(io.BytesIO(original)) as archive:
                info = archive.getinfo(name)
                central = archive.start_dir
                for item in archive.infolist():
                    if item.filename == name:
                        break
                    lengths = struct.unpack_from("<HHH", broken, central + 28)
                    central += 46 + sum(lengths)
            local = info.header_offset
            data = local + 30 + len(info.filename.encode()) + len(info.extra)
            if kind == "reserved-deflate":
                broken[data] = 7
            elif kind == "unsupported-compression":
                struct.pack_into("<H", broken, local + 8, 99)
                struct.pack_into("<H", broken, central + 10, 99)
            elif kind == "encrypted":
                struct.pack_into("<H", broken, local + 6, info.flag_bits | 1)
                struct.pack_into("<H", broken, central + 8, info.flag_bits | 1)
            elif kind == "crc":
                struct.pack_into("<I", broken, local + 14, info.CRC ^ 1)
                struct.pack_into("<I", broken, central + 16, info.CRC ^ 1)
            elif kind == "zero-length":
                struct.pack_into("<I", broken, local + 18, 0)
                struct.pack_into("<I", broken, central + 20, 0)
            else:
                raise AssertionError(kind)
            sibling = "candidate.json" if name.startswith("covered-") else "covered-candidate.json"
            with (
                zipfile.ZipFile(io.BytesIO(broken)) as damaged,
                zipfile.ZipFile(io.BytesIO(original)) as healthy,
            ):
                assert damaged.read(sibling) == healthy.read(sibling)
            return bytes(broken)
        if "/workflows/" in path:
            return {"workflow_runs": [observer]}
        return {"artifacts": [artifact]}

    monkeypatch.setattr(candidates, "api", public_api)
    output = tmp_path / "restored"

    def restored():
        requests.clear()
        candidates.restore(repository="fixture/repo", output=output, root=tmp_path)
        assert len(requests) == 3
        return partition.read_json(output / "weights.json")["profiles"]

    assert set(restored()) == {"none", "CTracer"}
    for event in ("schedule", "workflow_dispatch"):
        profile["origin"]["event"] = event
        receipt["source"]["event"] = event
        assert restored()["CTracer"] == profile
    for target, field, invalid in (
        (profile["origin"], "event", "pull_request"),
        (profile["origin"], "workflow", "Other workflow"),
        (profile["origin"], "repository", "foreign/repo"),
        (profile["origin"], "attempt", "2"),
        (receipt["source"], "workflow", ".github/workflows/ci.yml"),
        (receipt["source"], "branch", "foreign"),
        (receipt["source"], "event", "schedule"),
        (receipt, "state", "UNKNOWN"),
        (profile, "collected_at", "2000-01-01T00:00:00+00:00"),
        (profile, "runtime", {**profile["runtime"], "lock_digest": "0" * 64}),
        (profile, "file_digests", {}),
    ):
        original = target[field]
        target[field] = invalid
        assert set(restored()) == {"none"}
        target[field] = original
    duplicate_member = True
    with pytest.warns(UserWarning, match="Duplicate name"):
        assert set(restored()) == {"none"}
    duplicate_member = False
    removed = bodies.pop("covered-receipt.json")
    assert set(restored()) == {"none"}
    bodies["covered-receipt.json"] = removed
    legacy_receipt["state"] = "UNKNOWN"
    assert set(restored()) == {"CTracer"}
    legacy_receipt["state"] = "CANDIDATE"
    assert restored()["none"] == untraced["profiles"]["none"]

    # Real malformed ZIP members must refuse only their own advisory pair.
    for kind in ("reserved-deflate", "unsupported-compression", "encrypted", "crc", "zero-length"):
        for name, valid, invalid in (
            ("candidate.json", "CTracer", "uncovered"),
            ("covered-candidate.json", "none", "covered"),
        ):
            damaged_member = (name, kind)
            assert set(restored()) == {valid}
            status = partition.read_json(output / "restore-receipt.json")
            assert status["sources"][invalid]["state"] == "FALLBACK"
    damaged_member = None
    assert set(restored()) == {"none", "CTracer"}

    # This is source selection, not synthetic execution masquerading as a run.
    run = {
        "id": 123,
        "run_attempt": 1,
        "head_sha": coordinates["head"],
        "head_branch": "main",
        "head_repository": {"full_name": "fixture/repo"},
        "repository": {"full_name": "fixture/repo"},
        "path": ".github/workflows/nightly.yml",
        "name": "Nightly Tests",
        "event": "workflow_dispatch",
        "status": "completed",
        "conclusion": "success",
    }
    monkeypatch.setattr(candidates, "api", lambda *args, **kwargs: {"workflow_runs": [run]})
    for event in ("schedule", "workflow_dispatch"):
        run["event"] = event
        assert candidates.covered_run("fixture/repo", candidates.Provenance()) == run
    for field, invalid in (
        ("event", "pull_request"),
        ("event", "merge_group"),
        ("head_branch", "foreign"),
        ("head_repository", {"full_name": "foreign/repo"}),
        ("repository", {"full_name": "foreign/repo"}),
        ("path", ".github/workflows/ci.yml"),
        ("status", "in_progress"),
        ("conclusion", "failure"),
        ("run_attempt", True),
    ):
        original = run[field]
        run[field] = invalid
        with pytest.raises(ValueError):
            candidates.covered_run("fixture/repo", candidates.Provenance())
        run[field] = original
