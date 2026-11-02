"""The Vitest shard runner owns its processes and refuses incomplete evidence.

Real bounded subprocess transport controls of ``scripts/ci_vitest.py``'s receipt protocol
(REQ-ci-shard-assurance-007). The helpers below are not Vitest and provide no installed
frontend proof; the hosted frontend-vitest and frontend-assurance jobs own that.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import ci_vitest as vitest  # noqa: E402


def test_vitest_concurrent_failure_terminates_both_owned_groups(tmp_path: Path) -> None:
    """REQ-ci-shard-assurance-007: either failed peer closes both owned trees."""
    helper = tmp_path / "peer.py"
    helper.write_text("""import os, signal, subprocess, sys, time
from pathlib import Path
root, name, failing = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
child = subprocess.Popen([sys.executable, '-c', "import os, signal, sys, time; from pathlib import Path; signal.signal(signal.SIGTERM, signal.SIG_IGN); Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(4); Path(sys.argv[2]).write_text('late')", str(root / (name + '-child')), str(root / (name + '-late'))])
(root / (name + '-parent')).write_text(str(os.getpid()))
limit = time.monotonic() + 3
other = 'execute' if name == 'collect-full' else 'collect-full'
while not (root / (other + '-child')).exists() or not (root / (name + '-child')).exists():
    if time.monotonic() > limit: sys.exit(7)
    time.sleep(0.01)
(root / (name + '-overlap')).write_text('ready')
if name == failing:
    time.sleep(0.1)
    sys.exit(1)
time.sleep(10)
""")
    for failing in ("collect-full", "execute", "deadline"):
        directory = tmp_path / failing
        directory.mkdir()
        started = time.monotonic()
        try:
            commands = {
                name: (
                    [sys.executable, str(helper), str(directory), name, failing],
                    os.environ.copy(),
                )
                for name in ("collect-full", "execute")
            }
            with pytest.raises((vitest.VitestSubprocessFailure, vitest.VitestTimeout)):
                with vitest.concurrent_processes(
                    commands, cwd=tmp_path, deadline=started + 3
                ) as outcomes:
                    for _, result, _ in outcomes:
                        if result.returncode:
                            raise vitest.VitestSubprocessFailure("controlled peer failure")
            assert time.monotonic() - started < 3.5
            for name in commands:
                assert (directory / (name + "-overlap")).read_text() == "ready"
                for kind in ("parent", "child"):
                    pid = int((directory / (name + "-" + kind)).read_text())
                    stat = Path(f"/proc/{pid}/stat")
                    drained = time.monotonic() + 0.2
                    while True:
                        try:
                            state = stat.read_text().split()[2]
                        except (FileNotFoundError, ProcessLookupError):
                            break
                        if state == "Z":
                            break
                        assert time.monotonic() < drained
                        time.sleep(0.01)
                assert not (directory / (name + "-late")).exists()
        finally:
            for ledger in directory.glob("*-child"):
                pid = int(ledger.read_text())
                try:
                    os.kill(pid, 9)
                except ProcessLookupError:
                    pass


def _vitest_concurrent_receipt_controls(root, pair, monkeypatch, tmp_path, bounded_helper):
    """Additional interval/cache cases retain the original consumer body verbatim."""
    # Unequal JSON lists are normal nonmatches in either direction; boolean
    # values must also remain distinct from numeric values inside equal lists.
    assert vitest.same_json_value({"nested": [[1, True], []]}, {"nested": [[1, True], []]})
    for observed, expected in (
        ([[1], []], [[1]]),
        ([[1]], [[1], []]),
        ([[1, True]], [[1, 1]]),
    ):
        assert vitest.same_json_value(observed, expected) is False

    with monkeypatch.context() as local:
        for name in ("GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "VITEST_CONTROL"):
            local.delenv(name, raising=False)
        local.setenv("VITEST_CONTROL_STARTUP_DELAY", "0.2")
        for child in pair:
            assert (
                max(child["reference_elapsed_s"], child["execute_elapsed_s"]) <= child["elapsed_s"]
            )
            assert child["reference_elapsed_s"] + child["execute_elapsed_s"] > child["elapsed_s"]
        concurrent = copy.deepcopy(pair)
        for child in concurrent:
            child.update(reference_elapsed_s=70, execute_elapsed_s=65, elapsed_s=80)
        vitest.reconcile(root, concurrent)
        for field, invalid in (
            ("reference_elapsed_s", -1),
            ("execute_elapsed_s", -1),
            ("elapsed_s", -1),
            ("reference_elapsed_s", float("nan")),
            ("execute_elapsed_s", float("inf")),
            ("elapsed_s", True),
            ("reference_elapsed_s", "70"),
            ("execute_elapsed_s", None),
            ("elapsed_s", 69),
            ("elapsed_s", 901),
        ):
            corrupted = copy.deepcopy(concurrent)
            corrupted[0][field] = invalid
            with pytest.raises(ValueError):
                vitest.reconcile(root, corrupted)
        caches = []

        def observe(command, **kwargs):
            if "BUTLERS_VITEST_CACHE_DIR" in kwargs["env"]:
                caches.append(kwargs["env"]["BUTLERS_VITEST_CACHE_DIR"])
            return bounded_helper(command, **kwargs)

        local.setattr(vitest, "run_process", observe)
        assert vitest.run(root, 1, tmp_path / "concurrent-private-caches") == 0
        assert len(caches) == 2 and caches[0] != caches[1]
        assert all(not Path(cache).exists() for cache in caches)
    assert _vitest_nonzero_diagnostic_controls(root, pair, monkeypatch, tmp_path)
    return True


def _vitest_nonzero_diagnostic_controls(root, pair, monkeypatch, tmp_path):
    """Actual bounded process transport, not installed Vitest/test performance."""
    import contextlib
    import io

    area = tmp_path / "nonzero-diagnostics"
    area.mkdir()
    original_root = root
    root = area / "checkout"
    (root / "frontend").mkdir(parents=True)
    for name in ("control-a.test.ts", "control-b.test.ts", "package-lock.json"):
        (root / "frontend" / name).write_bytes((original_root / "frontend" / name).read_bytes())
    for command in (
        ["git", "init", "-q"],
        ["git", "add", "frontend"],
        [
            "git",
            "-c",
            "user.name=Transport Control",
            "-c",
            "user.email=control@example.invalid",
            "commit",
            "-qm",
            "source",
        ],
    ):
        subprocess.run(command, cwd=root, check=True, capture_output=True)
    marker = area / "validated-reference"
    helper = area / "transport.py"
    helper.write_text(
        """import json, os, sys, time
from pathlib import Path
phase, output, payload, marker, mode = sys.argv[1:]
value = json.loads(Path(payload).read_text())
if phase == 'reference':
    if mode == 'no-reference':
        time.sleep(5)
    Path(output).write_text(json.dumps(value['reference']))
else:
    deadline = time.monotonic() + 2
    while mode != 'no-reference' and not Path(marker).exists():
        if time.monotonic() > deadline:
            sys.exit(4)
        time.sleep(.01)
    print('PRIVATE_PROVIDER_PARAMETER_STDOUT')
    print('PRIVATE_EXCEPTION_STDERR', file=sys.stderr)
    if mode == 'nonregular':
        os.mkfifo(output)
    elif mode != 'missing':
        Path(output).write_text('PRIVATE_RAW_MALFORMED' if mode == 'malformed'
                                else json.dumps(value['execution']))
        if mode == 'stale':
            os.utime(output, ns=(1, 1))
    sys.exit(0 if mode == 'healthy' else 1)
"""
    )
    native_run, native_validate = vitest.run_process, vitest.validate_reference
    native_identity = vitest.identity
    canaries = (
        "PRIVATE_PROVIDER_PARAMETER_STDOUT",
        "PRIVATE_EXCEPTION_STDERR",
        "PRIVATE_RAW_MALFORMED",
        "PRIVATE_TEST_NAME",
        "PRIVATE_PROVIDER_PARAMETER",
        "PRIVATE_EXCEPTION_MESSAGE",
    )
    healthy = []
    with monkeypatch.context() as local:
        for name in ("GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
            local.delenv(name, raising=False)
        mode = "failed"
        payload = area / "payload.json"

        def transport(command, **kwargs):
            if command[:2] == ["node", str(vitest.REPORTER)]:
                phase, output = "reference", command[4]
            elif command[0] == str(root / "frontend/node_modules/.bin/vitest"):
                phase, output = "execute", kwargs["env"]["BUTLERS_VITEST_EVIDENCE"]
            else:
                return native_run(command, **kwargs)
            kwargs["timeout"] = 5
            return native_run(
                [sys.executable, str(helper), phase, output, str(payload), str(marker), mode],
                **kwargs,
            )

        def validated(value, checkout):
            result = native_validate(value, checkout)
            if mode == "identity":
                local.setenv("GITHUB_RUN_ATTEMPT", "other-attempt")
            marker.write_text("validated")
            return result

        diagnostic_error_raised = False

        def current_identity(checkout):
            nonlocal diagnostic_error_raised
            if mode == "diagnostic-error" and marker.exists() and not diagnostic_error_raised:
                diagnostic_error_raised = True
                raise RuntimeError("PRIVATE_EXCEPTION_MESSAGE")
            return native_identity(checkout)

        local.setattr(vitest, "identity", current_identity)
        local.setattr(vitest, "run_process", transport)
        local.setattr(vitest, "validate_reference", validated)
        for mode in (
            "failed",
            "partial",
            "missing",
            "malformed",
            "stale",
            "nonregular",
            "wrong-item",
            "config",
            "extra",
            "private-state",
            "private-module",
            "boolean-count",
            "diagnostic-error",
            "identity",
            "no-reference",
            "healthy",
            "healthy",
        ):
            local.delenv("GITHUB_RUN_ATTEMPT", raising=False)
            diagnostic_error_raised = False
            marker.unlink(missing_ok=True)
            shard = 2 if mode == "healthy" and healthy else 1
            value = copy.deepcopy(pair[shard - 1])
            execution = value["execution"]
            file = next(iter(execution["modules"]))
            token = next(iter(execution["results"]))
            if mode != "healthy":
                execution["results"][token]["state"] = "failed"
                execution["ends"][file].update(errors=1, ok=False, state="failed")
                execution.update(unhandled_errors=1, update_errors=1, reason="failed")
                execution["reporter_problems"] = {"result_pending": 1}
            if mode == "partial":
                execution["results"] = {}
                execution["ready"] = {}
                execution["ends"] = {}
                execution["terminal_files"] = []
                execution["complete"] = False
            elif mode == "wrong-item":
                execution["results"]["f" * 64] = execution["results"].pop(token)
            elif mode == "config":
                execution["config"]["retry"] = True
            elif mode == "extra":
                execution["PRIVATE_TEST_NAME"] = "PRIVATE_PROVIDER_PARAMETER"
            elif mode == "private-state":
                execution["results"][token]["state"] = "PRIVATE_EXCEPTION_MESSAGE"
            elif mode == "boolean-count":
                execution["unhandled_errors"] = True
            elif mode == "private-module":
                execution["modules"]["PRIVATE_TEST_NAME"] = execution["modules"].pop(file)
            payload.write_text(json.dumps(value))
            destination = area / (mode + str(shard))
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = vitest.run(root, shard, destination)
            text = (destination / "receipt.json").read_text()
            receipt = json.loads(text)
            assert all(
                canary not in text + stdout.getvalue() + stderr.getvalue() for canary in canaries
            ), "private canary escaped"
            if mode == "healthy":
                assert result == 0 and receipt["complete"] is True
                healthy.append(receipt)
                continue
            assert result == receipt["wrapper_exit"] == 2
            assert receipt["complete"] is False and receipt["exit_code"] == 1, (
                "nonzero failure was admitted"
            )
            assert receipt["stage"] == "execute"
            assert receipt["failure_category"] == "subprocess_failed"
            diagnostic = receipt.get("failure_diagnostics", {})
            if mode in {"failed", "partial", "no-reference"}:
                assert diagnostic.get("status") == "available", "nonzero diagnostic was discarded"
                assert diagnostic["population_complete"] is False
                expected_reference = "UNKNOWN" if mode == "no-reference" else "validated"
                assert diagnostic["reference"] == expected_reference
                assert diagnostic["configuration_match"] == expected_reference
                assert diagnostic["membership"] == (
                    "UNKNOWN" if mode == "no-reference" else "reference_checked_subset"
                )
                assert diagnostic["unhandled_errors"] == diagnostic["update_errors"] == 1
                assert diagnostic["reporter_problems"] == {"result_pending": 1}
                if mode in {"failed", "no-reference"}:
                    assert diagnostic["outcomes"] == {"failed": 1}
                    assert diagnostic["items"][token] == {
                        **value["reference"]["modules"][file]["items"][token],
                        "state": "failed",
                    }
                    assert sum(end["errors"] for end in diagnostic["ends"].values()) == 1
                else:
                    assert diagnostic["items"] == diagnostic["outcomes"] == {}
            else:
                assert diagnostic["status"] == "UNKNOWN"
                assert diagnostic["reason"] in {
                    "missing_evidence",
                    "invalid_evidence",
                    "stale_evidence",
                    "identity_changed",
                    "reference_unavailable",
                    "diagnostic_unavailable",
                }
                assert "items" not in diagnostic and "modules" not in diagnostic
            with pytest.raises(ValueError):
                vitest.reconcile(root, [receipt, pair[1]])
        vitest.reconcile(root, healthy)
    return True


def test_vitest_receipt_protocol_refuses_incomplete_or_tampered_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-ci-shard-assurance-007: actual receipt, reconcile and CLI controls."""
    buildroot = tmp_path / "build-control"
    (buildroot / "frontend").mkdir(parents=True)
    (buildroot / "frontend/package-lock.json").write_bytes(
        (REPO_ROOT / "frontend/package-lock.json").read_bytes()
    )
    subprocess.run(["git", "init", "-q", str(buildroot)], check=True)
    subprocess.run(["git", "-C", str(buildroot), "add", "frontend/package-lock.json"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(buildroot),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    with monkeypatch.context() as local:
        for name in ("GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
            local.delenv(name, raising=False)

        # Real bounded subprocess transport controls of the receipt protocol.
        # This helper is not Vitest and provides no installed frontend proof.
        helper = buildroot / "frontend/node_modules/.bin/vitest"
        helper.parent.mkdir(parents=True)
        (buildroot / "frontend/control-a.test.ts").write_text("control stand-in")
        (buildroot / "frontend/control-b.test.ts").write_text("control stand-in")
        helper.write_text(
            f"#!{sys.executable}\n"
            + """import json, os, subprocess, sys, time
from pathlib import Path
root = Path(__file__).resolve().parents[2]
time.sleep(float(os.environ.get('VITEST_CONTROL_STARTUP_DELAY', '0')))
shard = next((a for a in sys.argv if a.startswith('--shard=')), None)
output = next((a[9:] for a in sys.argv if a.startswith('--output=')), None)
stage = 'collect-full' if sys.argv[1] == 'list' else 'execute'
mode = os.environ.get('VITEST_CONTROL')
(root / ('transport-ready-' + stage)).write_text('ready')
if mode == 'descendant-stall' and stage == 'collect-full':
    ready_read, ready_write = os.pipe()
    child = subprocess.Popen([sys.executable, '-c', "import os, signal, sys, time; from pathlib import Path; signal.signal(signal.SIGTERM, signal.SIG_IGN); Path(sys.argv[2]).write_text('ready'); os.write(int(sys.argv[1]), b'1'); time.sleep(3); Path(sys.argv[3]).write_text('late descendant output')", str(ready_write), str(root / 'descendant-ready'), str(root / 'descendant-late')], pass_fds=(ready_write,))
    os.close(ready_write)
    assert os.read(ready_read, 1) == b'1'
    os.close(ready_read)
    print('private control text', flush=True)
    time.sleep(5)
if mode == stage or (stage == 'execute' and mode == 'collect-shard-' + shard[8]):
    print('private control text', flush=True)
    time.sleep(5)
if mode == 'collect-failed' and stage == 'collect-full':
    print('private control text', file=sys.stderr)
    sys.exit(1)
import hashlib
config = {'node':'24.21.0','vitest':'3.2.4','pool':'forks','isolate':True,'available_parallelism':4,'max_workers':None,'min_workers':None,'file_parallelism':True,'test_timeout':5000,'hook_timeout':10000,'retry':0,'sequence_shuffle':False}
def opaque(file, name):
    return hashlib.sha256(json.dumps([file, name]).encode()).hexdigest()
modules = {}
for letter in ('a', 'b'):
    file = 'frontend/control-' + letter + '.test.ts'
    modules[file] = {'file':file,'items':{opaque(file, 'occurrence'): {'key':opaque(file, 'control ' + letter),'mode':'run'}},'errors':0,'ok':True}
files = sorted(modules)
if sys.argv[1] == 'list':
    reference = {'schema':'ci-vitest-reference.v2','config':config,'files':files,'halves':{'1':[files[0]],'2':[files[1]]},'modules':modules,'unhandled_errors':0,'controller_exit':0,'complete':True}
    Path(output).write_text(json.dumps(reference))
elif mode == 'report-malformed':
    print('private control text')
    Path(os.environ['BUTLERS_VITEST_EVIDENCE']).write_text('private control text')
else:
    file = files[int(shard[8]) - 1]
    items = modules[file]['items']
    execution = {'schema':'ci-vitest-execution.v2','config':config,'modules':{file:modules[file]},'queued':{file:1},'starts':{file:1},'ends':{file:{'count':1,'errors':0,'ok':True,'state':'passed'}},'ready':{token:1 for token in items},'results':{token:{'state':'passed','declared_mode':'run'} for token in items},'terminal_files':[file],'unhandled_errors':0,'reporter_problems':{},'update_errors':0,'reason':'passed','complete':True}
    Path(os.environ['BUTLERS_VITEST_EVIDENCE']).write_text(json.dumps(execution))
"""
        )
        helper.chmod(0o755)
        native_run = vitest.run_process

        def bounded_helper(command, **kwargs):
            reference_call = command[:2] == ["node", str(vitest.REPORTER)]
            if command[0] == str(helper) or reference_call:
                # Preserve finite readiness before the positioned stall. This
                # source-owned process double proves transport only, not Vitest.
                stage = "collect-full" if reference_call else "execute"
                if reference_call:
                    command = [str(helper), "list", "--output=" + command[4]]
                ready = buildroot / "frontend" / ("transport-ready-" + stage)
                ready.unlink(missing_ok=True)
                kwargs["timeout"] = 2
                try:
                    return native_run(command, **kwargs)
                finally:
                    assert ready.is_file() and ready.read_text() == "ready", (
                        "current helper entry unavailable"
                    )
            return native_run(command, **kwargs)

        local.setattr(vitest, "run_process", bounded_helper)
        local.setenv("VITEST_CONTROL_STARTUP_DELAY", "0.2")
        # The actual PATH-selected Node probe is optional diagnostic metadata,
        # not an identity or worker-admission substitute for the real collector.
        observed = vitest.runtime_observation(buildroot)
        assert observed is None or (
            type(observed["available_parallelism"]) is int and observed["available_parallelism"] > 0
        )
        for raw in (
            b"private control text",
            b'{"node":"24.21.0","available_parallelism":true}',
            b'{"node":"private control text","available_parallelism":4}',
            b'{"node":"24.21.0","available_parallelism":0}',
            b'{"node":"24.21.0","available_parallelism":4,"extra":"private control text"}',
        ):
            with monkeypatch.context() as diagnostic:
                diagnostic.setattr(
                    vitest,
                    "run_process",
                    lambda command, *, raw=raw, **kwargs: subprocess.CompletedProcess(
                        command, 0, raw, b""
                    ),
                )
                assert vitest.runtime_observation(buildroot) is None
        with monkeypatch.context() as diagnostic:
            diagnostic.setattr(
                vitest,
                "run_process",
                lambda command, **kwargs: subprocess.CompletedProcess(
                    command, 0, b'{"node":"24.21.0","available_parallelism":4}', b""
                ),
            )
            assert vitest.runtime_observation(buildroot) == {
                "node": "24.21.0",
                "available_parallelism": 4,
            }

        # An unavailable optional probe must not block the actual full-reference,
        # shard execution and independent verifier protocol below. Only this
        # diagnostic subprocess is neutralized; the real bounded helper remains.
        def unavailable_probe(command, **kwargs):
            if command[:2] == ["node", "-e"]:
                raise FileNotFoundError("optional control unavailable")
            return bounded_helper(command, **kwargs)

        local.setattr(vitest, "run_process", unavailable_probe)
        observed = None
        for mode, stage, category in (
            ("collect-full", "collect-full", "timeout"),
            ("collect-shard-1", "execute", "timeout"),
            ("collect-shard-2", "execute", "timeout"),
            ("execute", "execute", "timeout"),
            ("collect-failed", "collect-full", "subprocess_failed"),
            ("report-malformed", "report", "invalid_evidence"),
        ):
            local.setenv("VITEST_CONTROL", mode)
            destination = tmp_path / ("vitest-" + mode)
            assert vitest.run(buildroot, 2 if mode == "collect-shard-2" else 1, destination) == 2
            receipt_text = (destination / "receipt.json").read_text()
            receipt = json.loads(receipt_text)
            assert receipt["complete"] is False
            assert (receipt["stage"], receipt["failure_category"]) == (stage, category)
            assert receipt["identity"] == vitest.identity(buildroot)
            assert receipt["runtime_observation"] == observed
            assert "private control text" not in receipt_text
            if category == "timeout":
                assert receipt["process_cleanup"]["term_sent"] is True
                assert receipt["process_cleanup"]["pipes_drained"] is True
                assert receipt["process_cleanup"]["child_reaped"] is True
        local.setenv("VITEST_CONTROL", "descendant-stall")
        destination = tmp_path / "vitest-descendant-stall"
        assert vitest.run(buildroot, 1, destination) == 2
        receipt_text = (destination / "receipt.json").read_text()
        receipt = json.loads(receipt_text)
        assert (buildroot / "frontend/descendant-ready").read_text() == "ready"
        assert (receipt["stage"], receipt["failure_category"]) == ("collect-full", "timeout")
        assert receipt["process_cleanup"]["term_sent"] is True
        assert receipt["process_cleanup"]["kill_sent"] is True
        assert receipt["process_cleanup"]["pipes_drained"] is True
        assert receipt["process_cleanup"]["child_reaped"] is True
        assert "private control text" not in receipt_text
        time.sleep(3)
        assert not (buildroot / "frontend/descendant-late").exists()
        local.delenv("VITEST_CONTROL")
        destination = tmp_path / "vitest-healthy"
        assert vitest.run(buildroot, 1, destination) == 0
        receipt = json.loads((destination / "receipt.json").read_text())
        assert receipt["complete"] is True and receipt["count"] == 1
        assert receipt["stage"] == "complete" and "failure_category" not in receipt
        second = tmp_path / "vitest-healthy-two"
        assert vitest.run(buildroot, 2, second) == 0
        pair = [receipt, json.loads((second / "receipt.json").read_text())]
        vitest.reconcile(buildroot, pair)
        # Same counts cannot hide changed parameters, missing declarations,
        # duplicated logical starts, unfinished outcomes or stale attempts.
        for mutation in (
            "parameter",
            "missing",
            "duplicate",
            "result",
            "attempt",
            "full-reference",
            "reporter-error",
            "update-error",
            "late",
            "boolean-exit",
            "float-count",
            "float-selected",
            "float-outcome",
            "integer-isolate",
            "boolean-module-errors",
            "mandatory-config",
        ):
            corrupted = copy.deepcopy(pair)
            child = corrupted[0]
            execution = child["execution"]
            file = next(iter(execution["modules"]))
            token = next(iter(execution["ready"]))
            if mutation == "parameter":
                execution["modules"][file]["items"][token]["key"] = "0" * 64
            elif mutation == "missing":
                del execution["modules"][file]
            elif mutation == "duplicate":
                execution["ready"][token] = 2
            elif mutation == "result":
                execution["results"][token]["state"] = "pending"
            elif mutation == "attempt":
                child["identity"]["attempt"] = "other-attempt"
            elif mutation == "reporter-error":
                execution["reporter_problems"] = {"ready_multiplicity": 1}
            elif mutation == "update-error":
                execution["update_errors"] = 1
            elif mutation == "late":
                child["elapsed_s"] = 901
            elif mutation == "boolean-exit":
                child["exit_code"] = False
            elif mutation == "float-count":
                child["count"] = float(child["count"])
            elif mutation == "float-selected":
                item = next(iter(child["selected"]))
                child["selected"][item] = float(child["selected"][item])
            elif mutation == "float-outcome":
                outcome = next(iter(child["outcomes"]))
                child["outcomes"][outcome] = float(child["outcomes"][outcome])
            elif mutation == "integer-isolate":
                execution["config"]["isolate"] = 1
            elif mutation == "boolean-module-errors":
                execution["modules"][file]["errors"] = False
            elif mutation == "mandatory-config":
                for sibling in corrupted:
                    sibling["reference"]["config"]["node"] = "22.0.0"
                    sibling["execution"]["config"]["node"] = "22.0.0"
            else:
                child["reference"]["modules"][file]["items"][token]["key"] = "1" * 64
            with pytest.raises(ValueError):
                vitest.reconcile(buildroot, corrupted)
        vitest.reconcile(buildroot, pair)
        for declared_mode in ("skip", "todo"):
            reference = copy.deepcopy(pair[0]["reference"])
            execution = copy.deepcopy(pair[0]["execution"])
            file = next(iter(execution["modules"]))
            token = next(iter(execution["ready"]))
            reference["modules"][file]["items"][token]["mode"] = declared_mode
            execution["modules"][file]["items"][token]["mode"] = declared_mode
            execution["results"][token]["declared_mode"] = declared_mode
            with pytest.raises(ValueError):
                vitest.validate_execution(reference, execution, 1)
            execution["results"][token]["state"] = "skipped"
            assert vitest.validate_execution(reference, execution, 1)["count"] == 1
        # Ordinary declared run can dynamically skip: this is an actual Vitest
        # terminal species and must not be mistaken for an executed pass.
        dynamic = copy.deepcopy(pair[0]["execution"])
        token = next(iter(dynamic["results"]))
        dynamic["results"][token]["state"] = "skipped"
        assert vitest.validate_execution(pair[0]["reference"], dynamic, 1)["outcomes"] == {
            "skipped": 1
        }
        # The required fan-in executes this real CLI entrypoint, not just a
        # library function that happens to accept the healthy artifacts.
        with monkeypatch.context() as cli_control:
            cli_control.setattr(vitest, "ROOT", buildroot)
            cli_control.setattr(
                sys,
                "argv",
                [
                    "ci_vitest.py",
                    "--reconcile",
                    str(destination / "receipt.json"),
                    str(second / "receipt.json"),
                ],
            )
            assert vitest.main() == 0
            bad = copy.deepcopy(pair[0])
            bad["execution"]["update_errors"] = 1
            invalid = tmp_path / "invalid-vitest-receipt.json"
            invalid.write_text(json.dumps(bad))
            cli_control.setattr(
                sys,
                "argv",
                ["ci_vitest.py", "--reconcile", str(invalid), str(second / "receipt.json")],
            )
            assert vitest.main() == 2
    assert _vitest_concurrent_receipt_controls(
        buildroot, pair, monkeypatch, tmp_path, bounded_helper
    )


def test_vitest_ordered_bookkeeping_protocol_controls() -> None:
    protocol = subprocess.run(
        [shutil.which("node"), str(REPO_ROOT / "tests/ci_vitest_protocol_controls.mjs")],
        cwd=REPO_ROOT,
        capture_output=True,
        timeout=10,
    )
    assert protocol.returncode == 0, "Vitest ordered bookkeeping conformance refused"
