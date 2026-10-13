"""Bounded real-process controls; never hosted workload or persistence evidence."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import ci_partition as partition  # noqa: E402
import ci_watchdog_diagnostics as diagnostic  # noqa: E402

pytestmark = pytest.mark.unit


def _fixture(root, monkeypatch):
    from tests.scripts.test_ci_partition import _corpus

    _corpus(root, monkeypatch)
    for key in ("CI_WATCHDOG_CONFIG", "CI_WATCHDOG_COLLECT", "PYTEST_PLUGINS"):
        monkeypatch.delenv(key, raising=False)
    (root / "conftest.py").write_text(
        "import os,json,pytest\n"
        "from pathlib import Path\n"
        "@pytest.fixture(scope='module',autouse=True)\n"
        "def lifetime(request):\n"
        " out=os.environ.get('TEST_CALLBACK_OUTPUT')\n"
        " def record(phase):\n"
        "  if out:\n"
        "   with open(out,'a') as stream: stream.write(json.dumps([str(request.node.path.name),phase,os.getpid()])+'\\n')\n"
        " record('setup')\n yield\n record('teardown')\n"
    )
    (root / "tests/nestedproof.py").write_text("def test_nested(): pass\n")
    for name in root.glob("tests/test_*.py"):
        name.write_text(
            "import os,sys,time,subprocess,pytest\n"
            "@pytest.mark.parametrize('value',['PRIVATE_PARAM_CANARY','SECOND_PARAM_CANARY'])\n"
            "def test_unit(value,request):\n"
            " local_secret='PRIVATE_LOCALS_CANARY'\n"
            " provider_payload='PRIVATE_PROVIDER_CANARY'\n"
            " if os.environ.get('WATCHDOG_CONTROL')=='stall': time.sleep(15)\n"
            " if os.environ.get('WATCHDOG_CONTROL')=='fail': raise RuntimeError('PRIVATE_EXCEPTION_CANARY')\n"
            " if os.environ.get('WATCHDOG_CONTROL')=='overflow':\n"
            "  import queue\n"
            "  plugin=request.config.pluginmanager.get_plugin('watchdog-observer')\n"
            "  if plugin:\n"
            "   def drop(event): raise queue.Full\n"
            "   plugin.events.put_nowait=drop\n"
            " assert os.environ.get('PRIVATE_ENV_CANARY')=='PRIVATE_ENV_VALUE_CANARY'\n"
            " if value=='PRIVATE_PARAM_CANARY':\n"
            "  command=[sys.executable,'-m','pytest','tests/nestedproof.py','--collect-only','-q','-n','0']\n"
            "  assert subprocess.run(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0\n"
            " exec(compile('import time; time.sleep(0.2)','PRIVATE_FRAME_PATH_CANARY','exec'))\n"
            " assert value in ('PRIVATE_PARAM_CANARY','SECOND_PARAM_CANARY')\n"
            "@pytest.mark.integration\n"
            "def test_integration(): pass\n"
        )
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
            "bounded callback fixture",
        ],
        check=True,
    )
    data = partition.collect_inventory(root=root)
    assignment = partition.partition(data, {}, root=root)
    return data, assignment


def _capture(root, data, assignment, name="capture"):
    output = root / name
    binding = diagnostic.binding(data, assignment, capture_root=Path(__file__).parents[2])
    diagnostic.configure(output, binding, root=root)
    return output, binding


def _run(root, output, *, plugin=True, control="healthy"):
    environment = diagnostic.subject_environment(os.environ)
    if plugin:
        environment.update(diagnostic.observer_environment(output))
    directory = output.parent / (
        output.name + ("-with-observer" if plugin else "-without-observer")
    )
    directory.mkdir(exist_ok=True)
    environment.update(CI_COVERAGE="0", CI_UNIT_WORKERS="3", TEST_EVIDENCE_DIR=str(directory))
    environment["WATCHDOG_CONTROL"] = control
    environment["PRIVATE_ENV_CANARY"] = "PRIVATE_ENV_VALUE_CANARY"
    environment["TEST_CALLBACK_OUTPUT"] = str(directory / "callbacks.jsonl")
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(Path(__file__).parents[2] / "scripts"), environment.get("PYTHONPATH", "")]
    )
    command = [
        sys.executable,
        "-c",
        "from pathlib import Path; import sys; from check_ci_test_shards import run_shard; sys.exit(run_shard(lane='unit',shard=1,repo_root=Path(sys.argv[1]),inventory_dir=Path(sys.argv[2]),evidence_dir=Path(sys.argv[3]),coverage_file=None))",
        str(root),
        str(output / "inventory"),
        str(directory),
    ]
    return subprocess.Popen(
        command,
        cwd=root,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def test_watchdog_diagnostics_preserve_full_observed_identity_and_privacy(tmp_path, monkeypatch):
    """REQ-testing-060: real three-worker callbacks, privacy and nested fencing."""
    data, assignment = _fixture(tmp_path, monkeypatch)
    output, bound = _capture(tmp_path, data, assignment)
    diagnostic.write_inventory(output / "inventory", data, assignment)
    plain = _run(tmp_path, output, plugin=False)
    assert plain.wait(timeout=45) == 0
    observed = _run(tmp_path, output)
    assert observed.wait(timeout=45) == 0
    checkpoint = diagnostic.checkpoint(output, 0, elapsed_ms=1000)
    report = diagnostic.read_checkpoint(checkpoint, bound)
    assert report["complete"] is False
    assert report["observed_phase_count"] > 0
    assert report["unknown"] == sorted(
        f"{node}:{phase}"
        for node in bound["expected"]
        for phase in diagnostic.PHASES
        if f"{node}:{phase}" not in report["observed"]
    )
    snapshot = diagnostic.read_json(checkpoint / "snapshot.json")
    assert len([p for p in snapshot["processes"] if p["role"] == "worker"]) == 3
    assert any(p["frames"] for p in snapshot["processes"])
    controller = next(p for p in snapshot["processes"] if p["role"] == "controller")
    assert controller["controller_events"]["departures"] == {"gw0": 1, "gw1": 1, "gw2": 1}
    assert set(controller["controller_events"]["collections"]) == {"gw0", "gw1", "gw2"}
    assert all(
        entry["count"] == 1 and entry["nodes"] == sorted(bound["expected"])
        for entry in controller["controller_events"]["collections"].values()
    )
    assert all(
        p["collections"] == sorted(bound["expected"])
        for p in snapshot["processes"]
        if p["role"] == "worker"
    )
    for node in bound["expected"]:
        assert (
            sum(
                p["nodes"].get(node, {}).get("starts", 0)
                for p in snapshot["processes"]
                if p["role"] == "worker"
            )
            == 1
        )
    for name in ("capture-without-observer", "capture-with-observer"):
        callbacks = [
            json.loads(line)
            for line in (tmp_path / name / "callbacks.jsonl").read_text().splitlines()
        ]
        expected_files = [Path(f).name for f in assignment["shards"]["unit"][0]["files"]]
        assert sorted((f, phase) for f, phase, _ in callbacks) == sorted(
            (f, phase) for f in expected_files for phase in ("setup", "teardown")
        )
        assert all(
            len({pid for f, _, pid in callbacks if f == name}) == 1 for name in expected_files
        )
    assert "PRIVATE_PARAM_CANARY" in (tmp_path / "capture-with-observer/raw-junit.xml").read_text()
    for canary in (
        "PRIVATE_PARAM_CANARY",
        "PRIVATE_LOCALS_CANARY",
        "PRIVATE_PROVIDER_CANARY",
        "PRIVATE_ENV_VALUE_CANARY",
        "PRIVATE_FRAME_PATH_CANARY",
    ):
        assert canary not in (checkpoint / "snapshot.json").read_text()
    assert diagnostic.subject_environment({"ACTIONS_RUNTIME_TOKEN": "planted", "KEEP": "kept"}) == {
        "KEEP": "kept"
    }
    # Closed schemas reject every prohibited channel, rather than sanitize after publication.
    for field in ("argv", "environment", "params", "error", "locals", "source", "provider"):
        wrong = copy.deepcopy(snapshot)
        wrong[field] = "PRIVATE_CANARY"
        with pytest.raises(ValueError):
            diagnostic.validate_snapshot(wrong, bound)
    assert "PRIVATE_CANARY" not in (checkpoint / "snapshot.json").read_text()


def test_watchdog_diagnostics_preserve_unknown_on_observer_failure_and_cancellation(
    tmp_path, monkeypatch
):
    """REQ-testing-060: incomplete prefix, strict negatives and restored healthy path."""
    data, assignment = _fixture(tmp_path, monkeypatch)
    output, bound = _capture(tmp_path, data, assignment)
    diagnostic.write_inventory(output / "inventory", data, assignment)
    empty = diagnostic.checkpoint(output, 0, elapsed_ms=0)
    report = diagnostic.read_checkpoint(empty, bound)
    assert len(report["unknown"]) == len(bound["expected"]) * 3
    snapshot = diagnostic.read_json(empty / "snapshot.json")
    for field, value in (
        ("complete", True),
        ("sequence", True),
        ("elapsed_ms", -1),
        ("elapsed_ms", 1.5),
        ("slots", []),
    ):
        wrong = copy.deepcopy(snapshot)
        wrong[field] = value
        with pytest.raises(ValueError):
            diagnostic.validate_snapshot(wrong, bound)
    (empty / "snapshot.json").write_text('{"complete":false,"complete":false}')
    with pytest.raises(ValueError):
        diagnostic.read_checkpoint(empty, bound)
    for name in ("snapshot.json", "manifest.json"):
        (empty / name).unlink()
    (empty / "snapshot.json").symlink_to(output / "config.json")
    with pytest.raises(ValueError):
        diagnostic.read_checkpoint(empty, bound)
    restored = _run(tmp_path, output)
    assert restored.wait(timeout=45) == 0
    healthy = diagnostic.checkpoint(output, 1, elapsed_ms=1000)
    assert diagnostic.read_checkpoint(healthy, bound)["observed_phase_count"] > 0
    failed_output, failed_bound = _capture(tmp_path, data, assignment, "failure")
    diagnostic.write_inventory(failed_output / "inventory", data, assignment)
    failed = _run(tmp_path, failed_output, control="fail")
    failure_status = failed.wait(timeout=45)
    plain_failure = _run(tmp_path, failed_output, plugin=False, control="fail")
    assert failure_status != 0 and plain_failure.wait(timeout=45) == failure_status
    failure_checkpoint = diagnostic.checkpoint(failed_output, 0, elapsed_ms=1000)
    failure_report = diagnostic.read_checkpoint(failure_checkpoint, failed_bound)
    assert failure_report["complete"] is False
    assert "PRIVATE_EXCEPTION_CANARY" not in (failure_checkpoint / "snapshot.json").read_text()
    assert any(
        counts["failed"]
        for process in diagnostic.read_json(failure_checkpoint / "snapshot.json")["processes"]
        for counters in process["nodes"].values()
        for counts in counters["phases"].values()
    )
    broken_output, broken_bound = _capture(tmp_path, data, assignment, "writer-failure")
    diagnostic.write_inventory(broken_output / "inventory", data, assignment)
    (broken_output / "states").rmdir()
    (broken_output / "states").write_text("PRIVATE_ERROR_PATH_CANARY")
    broken = _run(tmp_path, broken_output)
    assert broken.wait(timeout=45) == 0
    broken_checkpoint = diagnostic.checkpoint(broken_output, 0, elapsed_ms=1000)
    broken_report = diagnostic.read_checkpoint(broken_checkpoint, broken_bound)
    assert len(broken_report["unknown"]) == len(broken_bound["expected"]) * 3
    assert "observer_failure" in broken_report["gaps"]
    overflow_output, overflow_bound = _capture(tmp_path, data, assignment, "overflow")
    diagnostic.write_inventory(overflow_output / "inventory", data, assignment)
    overflow = _run(tmp_path, overflow_output, control="overflow")
    assert overflow.wait(timeout=45) == 0
    overflow_report = diagnostic.read_checkpoint(
        diagnostic.checkpoint(overflow_output, 0, elapsed_ms=1000), overflow_bound
    )
    assert "queue_overflow" in overflow_report["gaps"] and overflow_report["unknown"]
    valid = diagnostic.read_json(healthy / "snapshot.json")
    worker = next(p for p in valid["processes"] if p["role"] == "worker" and p["nodes"])
    selected_node = next(iter(worker["nodes"]))
    # Every admission path applies the same numeric rule before private bytes can
    # reach the isolated upload client. Neutralize only that rule in a private
    # companion to prove these controls detect the original omission.
    source_root = Path(__file__).resolve().parents[2]
    source = (source_root / "scripts/ci_watchdog_diagnostics.py").read_text()
    guard = '    number(value["stack_elapsed_ms"])\n'
    assert source.count(guard) == 1
    unsafe_source = source.replace(guard, "")
    unsafe = dict(diagnostic.__dict__, __name__="numeric_companion")
    exec(compile(unsafe_source, "numeric-companion", "exec"), unsafe)
    unsafe_cli = tmp_path / "numeric-companion.py"
    unsafe_cli.write_text(unsafe_source)
    binding_file = tmp_path / "numeric-binding.json"
    diagnostic.atomic(binding_file, bound)
    module = str(source_root / ".github/actions/ci-shard-watchdog-capture/src/index.js")
    uploader = tmp_path / "numeric-uploader.js"
    driver = tmp_path / "numeric-driver.js"
    uploader.write_text(
        "const fs=require('node:fs'),{spawnSync}=require('node:child_process');\n"
        f"const {{uploadWithClient}}=require({json.dumps(module)});\n"
        "const control=JSON.parse(fs.readFileSync(require('node:path').join(__dirname,'numeric-control.json')));\n"
        "process.once('message',async message=>{\n"
        f" const gate=spawnSync({json.dumps(sys.executable)},"
        "[control.cli,'check-seal','--directory',message.directory,"
        f"'--binding',{json.dumps(str(binding_file))}],{{stdio:'ignore'}});\n"
        " if(gate.status!==0){process.send({code:'failed',id:null,digest:null,size:null},"
        "()=>process.exit(0));return;}\n"
        " const client={async uploadArtifact(name,files){\n"
        "  const body=JSON.parse(fs.readFileSync(files.find(p=>p.endsWith('snapshot.json'))));\n"
        "  fs.writeFileSync(control.marker,JSON.stringify("
        "body.processes.map(p=>p.stack_elapsed_ms)));\n"
        "  return {id:9,digest:'a'.repeat(64),size:1};}};\n"
        " process.send(await uploadWithClient(client,message),()=>process.exit(0));\n"
        "});\n"
    )
    driver.write_text(
        f"const {{boundedUpload}}=require({json.dumps(module)});\n"
        "boundedUpload({name:'ci-watchdog-unit-1-123-1-cccccccccccc-0',directory:process.argv[2]},"
        "process.env,2000,process.argv[3]).then(value=>console.log(JSON.stringify(value)))"
        ".catch(()=>{process.exitCode=2});\n"
    )

    def admitted(slot, cli, marker):
        environment = diagnostic.subject_environment(os.environ)
        diagnostic.atomic(
            tmp_path / "numeric-control.json", {"cli": str(cli), "marker": str(marker)}
        )
        result = subprocess.run(
            ["node", str(driver), str(slot), str(uploader)],
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        receipt = json.loads(result.stdout)
        assert receipt["cleanup_complete"] is True
        return receipt["result"]["code"]

    cli = source_root / "scripts/ci_watchdog_diagnostics.py"
    for index, value in enumerate(
        (
            0,
            "PRIVATE_STACK_CLOCK_CANARY",
            {"provider": "PRIVATE_STACK_OBJECT_CANARY"},
            True,
            1.5,
            -1,
            2**31,
            1,
        )
    ):
        case, case_bound = _capture(tmp_path, data, assignment, f"numeric-{index}")
        state = copy.deepcopy(worker)
        state["stack_elapsed_ms"] = value
        diagnostic.atomic(case / "states/123.json", state)
        slot = diagnostic.checkpoint(case, 0, elapsed_ms=1000)
        checked = diagnostic.read_json(slot / "snapshot.json")
        marker = tmp_path / f"numeric-{index}-sdk.json"
        healthy_clock = index in (0, 7)
        if healthy_clock:
            diagnostic.validate_process(state, case_bound)
            assert checked["processes"][0]["stack_elapsed_ms"] == value
        else:
            with pytest.raises(ValueError, match="diagnostic integer malformed"):
                diagnostic.validate_process(state, case_bound)
            forged = copy.deepcopy(valid)
            selected = next(p for p in forged["processes"] if p["worker"] == worker["worker"])
            selected["stack_elapsed_ms"] = value
            with pytest.raises(ValueError, match="diagnostic integer malformed"):
                diagnostic.validate_snapshot(forged, bound)
            assert checked["processes"] == []
            assert "observer_failure" in checked["gaps"]
            assert "PRIVATE_STACK" not in (slot / "snapshot.json").read_text()
        assert admitted(slot, cli, marker) == "finalized"
        assert json.loads(marker.read_text()) == ([value] if healthy_clock else [])
        if healthy_clock:
            continue
        # The unguarded companion seals the exact malformed state. The real
        # strict reader and uploader must refuse those immutable sealed bytes.
        unsafe["validate_process"](state, case_bound)
        unsafe_slot = unsafe["checkpoint"](case, 1, elapsed_ms=1000)
        with pytest.raises(ValueError, match="diagnostic integer malformed"):
            diagnostic.read_checkpoint(unsafe_slot, case_bound)
        refused_marker = tmp_path / f"numeric-{index}-refused-sdk.json"
        assert admitted(unsafe_slot, cli, refused_marker) == "failed"
        assert not refused_marker.exists()
        unsafe_marker = tmp_path / f"numeric-{index}-unsafe-sdk.json"
        assert admitted(unsafe_slot, unsafe_cli, unsafe_marker) == "finalized"
        assert json.loads(unsafe_marker.read_text()) == [value]
        # The same refusal assertion is RED against the neutralized reader.
        with pytest.raises(pytest.fail.Exception):
            with pytest.raises(ValueError):
                unsafe["read_checkpoint"](unsafe_slot, case_bound)
    for mutation in ("bool_count", "duplicate_phase", "unknown_frame", "counter_overflow"):
        wrong = copy.deepcopy(valid)
        selected_worker = next(p for p in wrong["processes"] if p["worker"] == worker["worker"])
        if mutation == "bool_count":
            selected_worker["nodes"][selected_node]["starts"] = True
        elif mutation == "duplicate_phase":
            selected_worker["nodes"][selected_node]["phases"]["setup"]["entries"] = 2
        elif mutation == "unknown_frame":
            selected_worker["frames"] = [
                {
                    "file": "tests/PRIVATE_PATH_CANARY.py",
                    "blob": "a" * 40,
                    "line": 1,
                    "thread": 0,
                    "depth": 0,
                }
            ]
        else:
            selected_worker["sequence"] = 2**31
        with pytest.raises(ValueError):
            diagnostic.validate_snapshot(wrong, bound)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(ValueError):
        diagnostic.read_json(fifo)
    fifo.unlink()
    extra = healthy / "unknown.json"
    extra.write_text("PRIVATE_FILE_CANARY")
    with pytest.raises(ValueError):
        diagnostic.read_checkpoint(healthy, bound)
    extra.unlink()
    assert diagnostic.read_checkpoint(healthy, bound)["observed_phase_count"] > 0
    stopped_output, stopped_bound = _capture(tmp_path, data, assignment, "cancellation")
    diagnostic.write_inventory(stopped_output / "inventory", data, assignment)
    stopped = _run(tmp_path, stopped_output, control="stall")
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        states = [diagnostic.read_json(p) for p in (stopped_output / "states").glob("*.json")]
        if any(
            counters["phases"].get("call", {}).get("entries") == 1
            for process in states
            for counters in process["nodes"].values()
        ):
            break
        time.sleep(0.05)
    else:
        os.killpg(stopped.pid, signal.SIGKILL)
        stopped.wait(timeout=5)
        pytest.fail("real call boundary unavailable before bounded cancellation")
    live = diagnostic.checkpoint(stopped_output, 0, elapsed_ms=5000)
    assert not (tmp_path / "cancellation-with-observer/shard-observation.json").exists()
    os.killpg(stopped.pid, signal.SIGKILL)
    assert stopped.wait(timeout=5) != 0
    assert diagnostic.read_checkpoint(live, stopped_bound)["unknown"]
    assert not (tmp_path / "cancellation-with-observer/shard-observation.json").exists()
    again_output, again_bound = _capture(tmp_path, data, assignment, "restored")
    diagnostic.write_inventory(again_output / "inventory", data, assignment)
    again = _run(tmp_path, again_output)
    assert again.wait(timeout=45) == 0
    assert (
        diagnostic.read_checkpoint(
            diagnostic.checkpoint(again_output, 0, elapsed_ms=1000), again_bound
        )["observed_phase_count"]
        > 0
    )
    # Sessionfinish-only evidence is unavailable while a live process is stopped.
    for name in assignment["shards"]["unit"][0]["files"]:
        (tmp_path / name).write_text("import time\ndef test_wait(): time.sleep(10)\n")
    # Deliberate population drift is refused by the original child before execution.
    drift = _run(tmp_path, output)
    assert drift.wait(timeout=45) != 0
    assert diagnostic.read_checkpoint(healthy, bound)["complete"] is False


def test_diagnostic_workflow_binds_original_profile_and_transport_domains(tmp_path, monkeypatch):
    """REQ-testing-060: original real collector correspondence and domain refusals."""
    data, assignment = _fixture(tmp_path, monkeypatch)
    original = {
        "inventory_nonce": data["nonce"],
        "complete_expected_files": assignment["shards"]["unit"][0]["files"],
        "complete_expected_nodes": [
            n for f in assignment["shards"]["unit"][0]["files"] for n in data["lanes"]["unit"][f]
        ],
    }
    fresh, plan, mapping = diagnostic.collect_correspondence(tmp_path, original)
    assert fresh["nonce"] != data["nonce"]
    assert sorted(p["old"] for p in mapping) == sorted(original["complete_expected_nodes"])
    assert {p["new"] for p in mapping} == {
        n for f in plan["shards"]["unit"][0]["files"] for n in fresh["lanes"]["unit"][f]
    }
    wrong = copy.deepcopy(original)
    wrong["complete_expected_nodes"].pop()
    with pytest.raises(ValueError):
        diagnostic.collect_correspondence(tmp_path, wrong)
    output, bound = _capture(tmp_path, fresh, plan)
    checkpoint = diagnostic.checkpoint(output, 0, elapsed_ms=0)
    assert diagnostic.read_checkpoint(checkpoint, bound)["complete"] is False
    for field in ("capture_head", "subject_head", "subject_tree", "run", "attempt", "nonce"):
        altered = copy.deepcopy(bound)
        altered[field] = "wrong"
        with pytest.raises(ValueError):
            diagnostic.read_checkpoint(checkpoint, altered)
    workflow = (
        Path(__file__).parents[2] / ".github/workflows/ci-shard-watchdog-diagnostic.yml"
    ).read_text()
    assert "workflow_dispatch:" in workflow and "timeout-minutes: 28" in workflow
    assert "timeout-minutes: 35" in workflow
    assert "pull_request_target" not in workflow
    with pytest.raises(ValueError, match="frozen diagnostic subject mismatch"):
        diagnostic.prepare_profile(tmp_path, tmp_path, tmp_path / "must-not-launch", original)
    assert not (tmp_path / "must-not-launch").exists()

    # Closed protocol controls only: this is the full adopted opaque population,
    # not a fresh executable 3418-case receipt or provider authentication proof.
    source_root = Path(__file__).resolve().parents[2]
    profile = diagnostic.read_json(
        source_root / ".github/actions/ci-shard-watchdog-capture/profile.json"
    )
    reviewed = diagnostic.reviewed_source_reference(
        source_root, diagnostic.git(source_root, "rev-parse", "HEAD")
    )
    assert reviewed["capture_tree"] == diagnostic.git(
        source_root, "rev-parse", reviewed["capture_head"] + "^{tree}"
    )
    files = profile["complete_expected_files"]
    lanes = {name: [] for name in files}
    for index, node in enumerate(profile["complete_expected_nodes"]):
        lanes[files[index % len(files)]].append(node)
    expected = {node: name for name, nodes in lanes.items() for node in nodes}
    synthetic = {"nonce": profile["inventory_nonce"], "lanes": {"unit": lanes}}
    synthetic["digest"] = partition.body_digest(synthetic)
    planned = {"inventory_digest": synthetic["digest"], "shards": {"unit": [{"files": files}]}}
    planned["digest"] = partition.body_digest(planned)
    protocol = {
        **reviewed,
        "profile": diagnostic.PROFILE,
        "repository": "tzeusy-org/butlers",
        "workflow": "CI Shard Watchdog Diagnostic",
        "run": "123",
        "attempt": "1",
        "provider_head": "b" * 40,
        "subject_head": diagnostic.SUBJECT,
        "subject_tree": diagnostic.TREE,
        "subject_parents": diagnostic.PARENTS,
        "inventory_digest": synthetic["digest"],
        "assignment_digest": planned["digest"],
        "nonce": synthetic["nonce"],
        "child": "unit-1",
        "expected": expected,
        "population_digest": diagnostic.digest(expected),
        "source_refs": {},
    }
    correspondence = [{"old": node, "new": node, "file": name} for node, name in expected.items()]

    def downloaded(name, *, field=None, value=None, sequence=0, authenticated_sequence=0):
        case = tmp_path / name
        inventory = case / "inventory"
        snapshots = case / "snapshots"
        slot = snapshots / "slot-0"
        inventory.mkdir(parents=True)
        slot.mkdir(parents=True)
        identity = copy.deepcopy(protocol)
        if field:
            identity[field] = value
        body = {
            "schema": diagnostic.SCHEMA,
            "complete": False,
            "binding": identity,
            "sequence": sequence,
            "elapsed_ms": 0,
            "processes": [],
            "slots": diagnostic.slots(),
            "gaps": ["missing_process", "transport_unknown"],
            "numeric_samples": {"samples": [], "truncated": False},
        }
        for filename, contents in (
            ("binding.json", identity),
            ("inventory.json", synthetic),
            ("assignment.json", planned),
            ("correspondence.json", correspondence),
        ):
            diagnostic.atomic(inventory / filename, contents)
        diagnostic.atomic(slot / "snapshot.json", body)
        raw = (slot / "snapshot.json").read_bytes()
        snapshot_hash = hashlib.sha256(raw).hexdigest()
        diagnostic.atomic(
            slot / "manifest.json",
            {
                "schema": "ci-watchdog-manifest.v1",
                "complete": False,
                "binding_digest": diagnostic.digest(identity),
                "sequence": sequence,
                "files": {"snapshot.json": {"sha256": snapshot_hash, "bytes": len(raw)}},
            },
        )
        diagnostic.atomic(
            inventory / "provider-authenticated.json",
            [
                {
                    "artifact_id": 42,
                    "name": f"ci-watchdog-unit-1-123-1-{protocol['nonce'][:12]}-0",
                    "repository": protocol["repository"],
                    "workflow": protocol["workflow"],
                    "run": "123",
                    "attempt": "1",
                    "sequence": authenticated_sequence,
                    "snapshot_sha256": snapshot_hash,
                }
            ],
        )
        report = case / "report.json"
        result = subprocess.run(
            [
                sys.executable,
                str(source_root / "scripts/ci_watchdog_diagnostics.py"),
                "validate",
                "--snapshot-dir",
                str(snapshots),
                "--inventory-dir",
                str(inventory),
                "--expected-profile",
                diagnostic.PROFILE,
                "--capture-head",
                reviewed["capture_head"],
                "--subject-head",
                diagnostic.SUBJECT,
                "--subject-tree",
                diagnostic.TREE,
                "--repository",
                protocol["repository"],
                "--workflow",
                protocol["workflow"],
                "--run",
                "123",
                "--attempt",
                "1",
                "--output",
                str(report),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode:
            assert result.returncode == 2
            assert result.stderr == "watchdog diagnostic refused: UNKNOWN\n"
            assert not report.exists()
            return None
        admitted = diagnostic.read_json(report)
        assert admitted["complete"] is False and admitted["diagnostic_only"] is True
        assert admitted["reports"][0]["sequence"] == 0
        assert len(admitted["reports"][0]["unknown"]) == 3 * 3418
        return admitted

    healthy_download = downloaded("download-healthy")
    assert healthy_download
    assert downloaded("sealed-wrong-slot", sequence=1) is None
    for index, boolean in enumerate((True, False)):
        assert downloaded(f"sealed-bool-{index}", sequence=boolean) is None
        assert downloaded(f"authenticated-bool-{index}", authenticated_sequence=boolean) is None
    for field in (
        "capture_tree",
        "capture_bundle",
        "capture_lock",
        "capture_observer",
        "capture_action",
    ):
        assert (
            downloaded(
                f"wrong-{field}", field=field, value="a" * (40 if field == "capture_tree" else 64)
            )
            is None
        )
        for index, boolean in enumerate((True, False)):
            assert downloaded(f"bool-{field}-{index}", field=field, value=boolean) is None
    assert downloaded("download-restored") == healthy_download
