"""Offline causal-harness controls: no command here changes real host policy."""

import fcntl
import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import session_host_causal_probe as probe
from scripts import session_host_ci as ci
from scripts import session_host_probe_adapter as adapter


def native_record(outcome="red", *, arm="A1", nonce="a" * 32, profile=None):
    record = {
        "version": 1,
        "node": adapter.NODE,
        "collected": 1,
        "arm": arm,
        "nonce": nonce,
        "profile_sha256": profile or ci.PROFILE_SHA256,
        "native_sha256": adapter.NATIVE_SHA256,
        "phases": {
            "setup": "passed",
            "call": "failed" if outcome == "red" else "passed",
            "teardown": "passed",
        },
        "images": [
            {"destination": f"/runtime/{name}", "sha256": digit * 64, "mode": 0o555, "size": 100}
            for name, digit in (("init", "1"), ("program", "2"))
        ],
        "adapter_ok": True,
        "diagnostic": dict(adapter.RED_DIAGNOSTIC),
        "peer_state": dict(adapter.RED_STATE),
        "settlement": {
            "owner_healthy": True,
            "owner_pending": 0,
            "stage_empty": True,
            "domains": [
                dict.fromkeys(
                    (
                        "namespace_dead",
                        "child_reaped",
                        "settled",
                        "fds_closed",
                        "spawn_done",
                        "reaper_done",
                    ),
                    True,
                )
                for _ in range(1 if outcome == "red" else 2)
            ],
        },
        "exitstatus": 1 if outcome == "red" else 0,
    }
    if outcome == "green":
        record["diagnostic"].update(
            ready_receipt="matched",
            ready_bytes=34,
            stderr_bytes=0,
            stderr_category="empty",
            stderr_reason="unclassified",
            stderr_sha256=hashlib.sha256(b"").hexdigest(),
        )
        record["peer_state"].update(
            ready=True, exit=-9, output_bytes=27, execution_failure="CancelledError"
        )
    files = [
        {
            "destination": image["destination"],
            "perms": "0555",
            "fd_mode": 0o777,
            "regular": True,
            "seals": 15,
            "sha256": image["sha256"],
            "size": image["size"],
        }
        for image in record["images"]
    ]
    files += [
        {
            "destination": f"/invocation/{name}",
            "perms": "0400",
            "fd_mode": 0o777,
            "regular": True,
            "seals": 15,
        }
        for name in ("model-credential", "prompt", "mcp.json")
    ]
    record["launches"] = [
        dict(
            role=role,
            files=files,
            inherited=[
                "--info-fd",
                "--block-fd",
                "--shim-gate-fd",
                *[f["destination"] for f in files],
            ],
            argv_sha256="4" * 64,
        )
        for role in (["peer"] if outcome == "red" else ["peer", "reader"])
    ]
    return record


def validate(record, returncode):
    return probe.closed_result(
        record, returncode, arm="A1", nonce="a" * 32, profile=ci.PROFILE_SHA256
    )


@pytest.fixture
def host(monkeypatch, tmp_path):
    original = (
        b"profile bwrap /usr/bin/bwrap flags=(attach_disconnected) {\n}\n"
        b"profile unpriv_bwrap flags=(attach_disconnected) {\n audit deny capability,\n}\n#"
    )
    original += b"x" * (1936 - len(original))
    derived = original.replace(b"attach_disconnected", b"attach_disconnected,mediate_deleted")
    monkeypatch.setattr(ci, "PROFILE_SHA256", hashlib.sha256(original).hexdigest())
    monkeypatch.setattr(ci, "RUNNER_PROFILE_SHA256", hashlib.sha256(derived).hexdigest())
    state = dict(
        names=set(),
        calls=[],
        load_exit=0,
        load_names={"bwrap", "unpriv_bwrap"},
        remove_exit=0,
        remove_names=set(),
        restriction=b"1",
        generic_exit=1,
        outcomes={"A1": "red", "B": "green", "A2": "red"},
        mutation=None,
    )
    exists = Path.exists
    monkeypatch.setattr(
        Path,
        "exists",
        lambda p: False if str(p).startswith("/etc/apparmor.d/local/") else exists(p),
    )
    identity = {"head": "a" * 40, "tree": "b" * 40}
    monkeypatch.setattr(probe, "source_identity", lambda budget: dict(identity))

    def run(args, **kwargs):
        state["calls"].append((args, kwargs))
        if args[0] == "sysctl":
            return subprocess.CompletedProcess(args, 0, state["restriction"], b"")
        if args[0] == "unshare":
            return subprocess.CompletedProcess(
                args, state["generic_exit"], b"", b"Operation not permitted"
            )
        if args[:3] == ["sudo", "-n", "cat"]:
            return subprocess.CompletedProcess(
                args, 0, "\n".join(f"{name} (enforce)" for name in state["names"]).encode(), b""
            )
        if args[:4] == ["sudo", "-n", "apparmor_parser", "-a"]:
            if state["load_exit"] == 0:
                state["names"] = set(state["load_names"])
            return subprocess.CompletedProcess(args, state["load_exit"], b"", b"PRIVATE")
        if args[:4] == ["sudo", "-n", "apparmor_parser", "-R"]:
            if state["remove_exit"] == 0:
                state["names"] = set(state["remove_names"])
            return subprocess.CompletedProcess(args, state["remove_exit"], b"", b"PRIVATE")
        assert args[0] == "env" and adapter.NODE in args
        assert args[args.index("-n") + 1] == "0"
        assert "BUTLERS_REQUIRE_SESSION_HOST_NATIVE=1" in args
        path = Path(args[args.index("--session-host-probe-report") + 1])
        if state["mutation"] == "timeout":
            raise ci.Refusal("probe_command_timeout")
        if state["mutation"] in {"output_limit", "cleanup_unproven"}:
            raise ci.Refusal("probe_command_" + state["mutation"])
        outcome = state["outcomes"][path.parent.name]
        record = native_record(
            outcome,
            arm=path.parent.name,
            nonce=args[args.index("--session-host-probe-nonce") + 1],
            profile=args[args.index("--session-host-probe-profile") + 1],
        )
        mutation = state["mutation"]
        if mutation == "unknown":
            record["diagnostic"]["stderr_reason"] = "PRIVATE"
        elif mutation == "images" and path.parent.name == "B":
            record["images"][0]["sha256"] = "3" * 64
            for launch in record["launches"]:
                launch["files"][0]["sha256"] = "3" * 64
        elif mutation in {"intent", "loaded", "profile", "symlink"}:
            target = (
                path.parent
                / {
                    "intent": "load-intent.json",
                    "loaded": "loaded.json",
                    "profile": "bwrap-userns-restrict",
                    "symlink": "loaded.json",
                }[mutation]
            )
            if mutation == "symlink":
                target.unlink()
                target.symlink_to(path.parent / "load-intent.json")
            else:
                target.write_bytes(b"tampered")
        elif mutation == "restriction_after":
            state["restriction"] = b"0"
        elif mutation == "generic_after":
            state["generic_exit"] = 0
        ci._write(path, json.dumps(record).encode())
        return subprocess.CompletedProcess(
            args, record["exitstatus"], b"PRIVATE stdout", b"PRIVATE stderr"
        )

    monkeypatch.setattr(probe, "_run_bounded", run)
    root = tmp_path / "probe"
    root.mkdir(mode=0o700)
    return state, root, original


def run_sequence(host):
    state, root, original = host
    report = {"version": 1, "status": "refused", "arms": []}
    probe.sequence(root, original, probe.Budget(), report)
    return state, root, report


def test_same_run_aba_requires_actual_node_fixed_variants_and_all_cleanup(host, capsys):
    state, root, report = run_sequence(host)
    assert report["status"] == "causal_aba_observed"
    assert [(a["arm"], a["outcome"], a["cleanup"]) for a in report["arms"]] == [
        ("A1", "red", "observed"),
        ("B", "green", "observed"),
        ("A2", "red", "observed"),
    ]
    assert [a["profile_sha256"] for a in report["arms"]] == [
        ci.PROFILE_SHA256,
        ci.RUNNER_PROFILE_SHA256,
        ci.PROFILE_SHA256,
    ]
    assert not state["names"] and state["restriction"] == b"1"
    assert len([a for a, _ in state["calls"] if "-R" in a]) == 3
    assert len([a for a, _ in state["calls"] if a[0] == "env"]) == 3
    assert all("-r" not in a and "-w" not in a for a, _ in state["calls"])
    assert all(
        a["native"]["settlement"]["stage_empty"] and a["global_restriction"] == 1
        for a in report["arms"]
    )
    assert len({a["nonce"] for a in report["arms"]}) == 3
    assert (root / "A1/load-intent.json").read_bytes() != (root / "B/load-intent.json").read_bytes()
    assert "PRIVATE" not in json.dumps(report) and capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "arm,outcome,count", [("A1", "green", 1), ("B", "red", 2), ("A2", "green", 3)]
)
def test_wrong_arm_outcome_refuses_after_owned_cleanup_and_stops(host, arm, outcome, count):
    state, _, _ = host
    state["outcomes"][arm] = outcome
    with pytest.raises(ci.Refusal, match="^probe_arm_result_refused$"):
        run_sequence(host)
    assert not state["names"]
    assert len([a for a, _ in state["calls"] if "-R" in a]) == count
    assert len([a for a, _ in state["calls"] if a[0] == "env"]) == count


def test_compiled_image_drift_is_not_a_causal_pass(host):
    state, _, _ = host
    state["mutation"] = "images"
    with pytest.raises(ci.Refusal, match="^probe_compiled_images_changed$"):
        run_sequence(host)
    assert not state["names"]
    assert len([a for a, _ in state["calls"] if "-R" in a]) == 2


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("load_exit", 1, "probe_quarantine_load_ownership_unproven"),
        ("load_names", {"bwrap"}, "probe_quarantine_load_ownership_unproven"),
        ("mutation", "timeout", "probe_quarantine_native_settlement_unproven"),
        ("mutation", "unknown", "probe_quarantine_native_settlement_unproven"),
        ("mutation", "output_limit", "probe_quarantine_native_settlement_unproven"),
        ("mutation", "cleanup_unproven", "probe_quarantine_native_settlement_unproven"),
    ],
)
def test_uncertain_ownership_or_native_settlement_quarantines_without_unrestriction(
    host, field, value, reason
):
    state, root, _ = host
    state[field] = value
    with pytest.raises(ci.Refusal) as refusal:
        run_sequence(host)
    assert not any("-R" in args for args, _ in state["calls"])
    assert not (root / "B").exists()
    assert str(refusal.value) == reason


@pytest.mark.parametrize("mutation", ["intent", "loaded", "profile", "symlink"])
def test_cleanup_requires_same_arm_owned_exact_files(host, mutation):
    state, _, _ = host
    state["mutation"] = mutation
    with pytest.raises(ci.Refusal, match="probe_quarantine_.*unproven"):
        run_sequence(host)
    assert state["names"] == {"bwrap", "unpriv_bwrap"}
    assert not any("-R" in args for args, _ in state["calls"])


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("remove_exit", 1, "probe_host_command_refused"),
        ("remove_names", {"bwrap"}, "probe_cleanup_unobserved"),
        ("mutation", "restriction_after", "global_restriction_not_one"),
        ("mutation", "generic_after", "generic_userns_not_denied"),
    ],
)
def test_cleanup_failure_or_weakened_restriction_never_admits_next_arm(host, field, value, reason):
    state, root, _ = host
    state[field] = value
    with pytest.raises(ci.Refusal, match=f"^{reason}$"):
        run_sequence(host)
    assert not (root / "B").exists()
    assert len([a for a, _ in state["calls"] if "-R" in a]) == 1


@pytest.mark.parametrize(
    "field,value", [("restriction", b"0"), ("generic_exit", 0), ("names", {"foreign-bwrap"})]
)
def test_unsafe_preconditions_never_add_or_adopt_policy(host, field, value):
    state, _, _ = host
    state[field] = value
    with pytest.raises(ci.Refusal):
        run_sequence(host)
    assert not any("apparmor_parser" in args for args, _ in state["calls"])


@pytest.mark.parametrize(
    "mutation",
    [
        "extra",
        "boolean_count",
        "skip",
        "teardown",
        "empty",
        "bool_bytes",
        "unreaped",
        "wrong_digest",
        "exit",
        "mode",
        "nonce",
        "arm",
        "source",
        "profile",
        "owner",
        "pending",
        "settlement",
        "seals",
        "inherited",
        "fd_hash",
        "stderr_eof",
    ],
)
def test_closed_result_refuses_missing_unknown_or_forged_native_proof(mutation):
    record = native_record()
    if mutation == "extra":
        record["raw_stderr"] = "PRIVATE"
    elif mutation == "boolean_count":
        record["collected"] = True
    elif mutation == "skip":
        record["phases"]["call"] = "skipped"
    elif mutation == "teardown":
        record["phases"]["teardown"] = "failed"
    elif mutation == "empty":
        record["settlement"]["stage_empty"] = False
    elif mutation == "bool_bytes":
        record["diagnostic"]["ready_bytes"] = False
    elif mutation == "unreaped":
        record["peer_state"]["child_reaped"] = False
    elif mutation == "wrong_digest":
        record["diagnostic"]["stderr_sha256"] = "0" * 64
    elif mutation == "exit":
        record["exitstatus"] = 0
    elif mutation == "mode":
        record["images"][0]["mode"] = True
    elif mutation == "nonce":
        record["nonce"] = "b" * 32
    elif mutation == "arm":
        record["arm"] = "B"
    elif mutation == "source":
        record["native_sha256"] = "0" * 64
    elif mutation == "profile":
        record["profile_sha256"] = "0" * 64
    elif mutation == "owner":
        record["settlement"]["owner_healthy"] = False
    elif mutation == "pending":
        record["settlement"]["owner_pending"] = 1
    elif mutation == "settlement":
        record["settlement"] = None
    elif mutation == "seals":
        record["launches"][0]["files"][0]["seals"] = 7
    elif mutation == "inherited":
        record["launches"][0]["inherited"].pop()
    elif mutation == "fd_hash":
        record["launches"][0]["files"][0]["sha256"] = "0" * 64
    elif mutation == "stderr_eof":
        record["diagnostic"]["stderr_complete"] = False
    with pytest.raises(ci.Refusal):
        validate(record, 1)


def test_closed_adapter_accepts_only_known_typed_diagnostic_and_settlement():
    assert adapter.closed_diagnostic(adapter.RED_DIAGNOSTIC) == adapter.RED_DIAGNOSTIC
    assert adapter.closed_peer_state(adapter.RED_STATE) == adapter.RED_STATE
    assert validate(native_record(), 1) == "red"
    assert validate(native_record("green"), 0) == "green"


@pytest.mark.parametrize("mutation", ["arbitrary", "extra", "typed", "digest", "long"])
def test_adapter_never_copies_unclassified_diagnostic_text(mutation, capsys):
    diagnostic = dict(adapter.RED_DIAGNOSTIC)
    if mutation == "arbitrary":
        diagnostic["stderr_category"] = "PRIVATE"
    elif mutation == "extra":
        diagnostic["stderr_preview"] = "PRIVATE"
    elif mutation == "typed":
        diagnostic["ready_bytes"] = False
    elif mutation == "digest":
        diagnostic["stderr_sha256"] = "PRIVATE"
    elif mutation == "long":
        diagnostic["stderr_reason"] = "PRIVATE" * 1000
    with pytest.raises(ci.Refusal) as caught:
        adapter.closed_diagnostic(diagnostic)
    assert "PRIVATE" not in str(caught.value) and capsys.readouterr().out == ""


@pytest.mark.parametrize("mutation", ["symlink", "hardlink", "mode", "oversize", "missing"])
def test_adapter_receipt_reads_are_bounded_owned_and_no_follow(tmp_path, mutation):
    path = tmp_path / "receipt"
    ci._write(path, b"known")
    if mutation == "symlink":
        path.unlink()
        path.symlink_to(tmp_path / "foreign")
    elif mutation == "hardlink":
        (tmp_path / "linked").hardlink_to(path)
    elif mutation == "mode":
        path.chmod(0o644)
    elif mutation == "oversize":
        path.write_bytes(b"x" * 11)
    else:
        path.unlink()
    with pytest.raises(ci.Refusal, match="^probe_owned_file_refused$"):
        probe.read_owned(path, 10)


def test_duplicate_json_keys_and_unknown_variants_refuse_without_loading(host):
    state, root, original = host
    with pytest.raises(ci.Refusal, match="^probe_adapter_receipt_refused$"):
        json.loads('{"exitstatus":1,"exitstatus":0}', object_pairs_hook=probe.unique_object)
    with pytest.raises(ci.Refusal, match="^probe_fixed_variant_required$"):
        probe.owned_arm(root / "foreign", "foreign", original, probe.Budget(), {})
    assert not state["calls"]


def test_budget_reserves_cleanup_time_and_bounds_every_command(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(probe.time, "monotonic", lambda: clock[0])
    budget = probe.Budget()
    calls = []
    monkeypatch.setattr(probe, "_run_bounded", lambda args, **kw: calls.append(kw))
    clock[0] = 309.5
    budget.run(["fixed"], timeout=45)
    assert calls == [{"timeout": 0.5}]
    clock[0] = 310.0
    with pytest.raises(ci.Refusal, match="^probe_deadline_expired$"):
        budget.run(["fixed"])
    budget.run(["cleanup"], cleanup=True)
    assert calls[-1] == {"timeout": 15}
    clock[0] = 400.0
    with pytest.raises(ci.Refusal, match="^probe_deadline_expired$"):
        budget.run(["cleanup"], cleanup=True)


def test_main_requires_explicit_hosted_authority_and_emits_only_fixed_refusal(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["session_host_causal_probe"])
    monkeypatch.setenv("CI", "false")
    monkeypatch.setattr(
        probe, "_run_bounded", lambda *a, **kw: pytest.fail("must not run host command")
    )
    assert probe.main() == 1
    record = json.loads(capsys.readouterr().out)
    assert record == {
        "version": 1,
        "status": "refused",
        "arms": [],
        "reason": "probe_explicit_hosted_noble_required",
    }


@pytest.fixture
def fd_plan(tmp_path, monkeypatch):
    from butlers.session_host.bubblewrap import BubblewrapDomain, ImageFile
    from butlers.session_host.material import InvocationMaterial
    from tests.session_host.test_core import request

    image = []
    for name in ("init", "program"):
        path = tmp_path / name
        path.write_bytes(f"synthetic-{name}".encode())
        path.chmod(0o555)
        image.append(
            ImageFile(path, f"/runtime/{name}", hashlib.sha256(path.read_bytes()).hexdigest())
        )
    domain = BubblewrapDomain(image=tuple(image), command=("/runtime/program", "resist"))

    class BwrapMetadata:
        def stat(self):
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o555, st_uid=0)

        def __str__(self):
            return "/usr/bin/bwrap"

    # Only executable metadata is a double; the real _plan builds real sealed FDs.
    monkeypatch.setattr(domain, "_bwrap", BwrapMetadata())
    stage = tmp_path / "stage"
    stage.mkdir(mode=0o700)
    stage.chmod(0o700)
    material = InvocationMaterial.stage(stage, request())
    try:
        argv, inherited, *_ = domain._plan(material)
        kwargs = dict(
            pass_fds=inherited,
            env={},
            close_fds=True,
            start_new_session=True,
            stdin=-3,
            stdout=-1,
            stderr=-1,
        )
        yield tuple(argv), kwargs, image
    finally:
        domain._close_all()
        material.discard()


def test_real_plan_receipt_hashes_sealed_inherited_images_without_moving_offsets(fd_plan):
    argv, kwargs, image = fd_plan
    image_fds = [int(argv[i + 1]) for i, token in enumerate(argv) if token == "--ro-bind-data"][:2]
    positions = [os.lseek(fd, 0, os.SEEK_CUR) for fd in image_fds]
    record = adapter.launch_identity(argv, kwargs)
    assert [f["sha256"] for f in record["files"][:2]] == [entry.sha256 for entry in image]
    assert all(f["seals"] & 15 == 15 and f["regular"] for f in record["files"])
    assert all("sha256" not in f and "size" not in f for f in record["files"][2:])
    assert positions == [os.lseek(fd, 0, os.SEEK_CUR) for fd in image_fds]
    for fd in image_fds:
        with pytest.raises(PermissionError):
            os.pwrite(fd, b"x", 0)
    # Rewriting a source path after snapshot cannot change the consumed-FD hash.
    image[0].source.chmod(0o600)
    image[0].source.write_bytes(b"changed source")
    assert adapter.launch_identity(argv, kwargs) == record


@pytest.mark.parametrize("mutation", ["unsealed", "missing_fd", "duplicate_fd", "env"])
def test_actual_fd_reader_refuses_unsealed_or_changed_launch(fd_plan, monkeypatch, mutation):
    argv, kwargs, _ = fd_plan
    kwargs = dict(kwargs)
    if mutation == "unsealed":
        original = fcntl.fcntl
        monkeypatch.setattr(
            adapter.fcntl, "fcntl", lambda fd, op: 7 if op == 1034 else original(fd, op)
        )
    elif mutation == "missing_fd":
        kwargs["pass_fds"] = kwargs["pass_fds"][:-1]
    elif mutation == "duplicate_fd":
        kwargs["pass_fds"] = (*kwargs["pass_fds"], kwargs["pass_fds"][0])
    else:
        kwargs["env"] = {"UNTRUSTED": "PRIVATE"}
    with pytest.raises(ci.Refusal, match="^probe_fd_plan_refused$"):
        adapter.launch_identity(argv, kwargs)


@pytest.mark.parametrize("mutation", [None, "owner", "pending", "stage", "domain"])
def test_a_arm_samples_post_finally_owner_and_domain_state_independently(tmp_path, mutation):
    record = native_record()
    item = SimpleNamespace(funcargs={"native_stage": tmp_path})
    observed = adapter.Observation(item)
    owner = SimpleNamespace(healthy=True, pending_count=0)
    peer = SimpleNamespace(
        _command=("/runtime/program", "resist"),
        output_ready=SimpleNamespace(is_set=lambda: False),
        _released=True,
        execution_failure="Refused",
        output_bytes_received=0,
        _process=SimpleNamespace(returncode=1),
        namespace_dead=True,
        direct_child_reaped=True,
        _settled=True,
        _fds=set(),
        _spawn=SimpleNamespace(done=lambda: True),
        _reaper=SimpleNamespace(done=lambda: True),
    )
    observed.owners = [owner]
    observed.domains = [(peer, (), ())]
    observed.startups = [SimpleNamespace(summary=lambda: dict(adapter.RED_DIAGNOSTIC))]
    observed.launches = record["launches"]
    if mutation == "owner":
        owner.healthy = False
    elif mutation == "pending":
        owner.pending_count = 1
    elif mutation == "stage":
        (tmp_path / "unsettled").touch()
    elif mutation == "domain":
        peer._settled = False
    observed.sample(record)
    if mutation is None:
        assert validate(record, 1) == "red"
    else:
        with pytest.raises(ci.Refusal):
            validate(record, 1)


@pytest.mark.parametrize("mutation", [None, "digest", "overcap", "command"])
def test_package_fetch_is_whole_process_bounded_and_exact_binary(monkeypatch, mutation):
    package = b"\n public binary\x00 \n"
    monkeypatch.setattr(ci, "PACKAGE_SHA256", hashlib.sha256(package).hexdigest())
    calls = []

    def command(args, **kwargs):
        calls.append((args, kwargs))
        data = package if mutation != "overcap" else b"x" * (ci._LIMIT + 1)
        if mutation == "digest":
            data += b"changed"
        return subprocess.CompletedProcess(
            args, 1 if mutation == "command" else 0, data, b"PRIVATE"
        )

    monkeypatch.setattr(probe, "_run_bounded", command)
    if mutation is None:
        assert probe.fetch_package(probe.Budget()) == package
    else:
        with pytest.raises(ci.Refusal):
            probe.fetch_package(probe.Budget())
    assert calls[0][1]["timeout"] <= 30
    assert calls[0][0][1] == "-c" and ci.PACKAGE_URL in calls[0][0][2]


def test_existing_probe_root_is_never_adopted_or_written(monkeypatch, tmp_path, capsys):
    root = tmp_path / "butlers-session-host-causal"
    root.mkdir()
    (root / "foreign").write_bytes(b"public marker")
    monkeypatch.setattr("sys.argv", ["probe", "--execute", "--disposable-job"])
    for key, value in {
        "CI": "true",
        "GITHUB_ACTIONS": "true",
        "GITHUB_RUN_ID": "1",
        "GITHUB_RUN_ATTEMPT": "1",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_OS": "Linux",
        "ImageOS": "ubuntu24",
        "RUNNER_TEMP": str(tmp_path),
    }.items():
        monkeypatch.setenv(key, value)
    original = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda p, *a, **kw: (
            'VERSION_ID="24.04"' if str(p) == "/etc/os-release" else original(p, *a, **kw)
        ),
    )
    monkeypatch.setattr(
        probe, "_run_bounded", lambda *a, **kw: pytest.fail("must not run a host command")
    )
    assert probe.main() == 1
    assert (root / "foreign").read_bytes() == b"public marker"
    assert not (root / "probe-report.json").exists()
    assert json.loads(capsys.readouterr().out)["status"] == "refused"


@pytest.mark.parametrize("stream", ["stdout", "stderr", "aggregate"])
def test_live_output_limit_refuses_before_producer_finishes(stream):
    import sys
    import time

    code = (
        "import os,time; "
        + (
            f"os.write(1,b'x'*{ci._LIMIT}); os.write(2,b'y'); "
            if stream == "aggregate"
            else f"os.write({1 if stream == 'stdout' else 2},b'x'*{ci._LIMIT + 1}); "
        )
        + "time.sleep(60)"
    )
    started = time.monotonic()
    with pytest.raises(ci.Refusal, match="^probe_command_output_limit$"):
        probe.Budget().run([sys.executable, "-c", code], timeout=3)
    assert time.monotonic() - started < 3


def test_live_output_exact_aggregate_limit_and_binary_bytes_survive():
    import sys

    result = probe.Budget().run(
        [
            sys.executable,
            "-c",
            f"import os; os.write(1,b'\\x00'*{ci._LIMIT - 1}); os.write(2,b'\\n')",
        ],
        timeout=3,
    )
    assert result.returncode == 0
    assert result.stdout == b"\x00" * (ci._LIMIT - 1)
    assert result.stderr == b"\n"


@pytest.mark.parametrize("escaped", [False, True])
def test_timeout_bounds_owned_group_and_quarantines_escaped_session(tmp_path, escaped):
    """An isolated subreaper reaps only this test's known synthetic descendants."""
    import sys
    import textwrap

    supervisor = tmp_path / "supervisor.py"
    supervisor.write_text(
        textwrap.dedent(r"""
        import ctypes,json,os,pathlib,signal,subprocess,sys,time
        from scripts import session_host_causal_probe as probe
        from scripts import session_host_ci as ci
        assert ctypes.CDLL(None, use_errno=True).prctl(36,1,0,0,0)==0
        root=pathlib.Path(sys.argv[1]); escaped=sys.argv[2]=='True'
        child_code=(
            "import os,signal,sys,time; "
            "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
            "os.write(int(sys.argv[1]),b'R'); os.close(int(sys.argv[1])); time.sleep(60)"
        )
        parent_code=(
            "import os,pathlib,select,subprocess,sys,time; r,w=os.pipe(); "
            "p=subprocess.Popen([sys.executable,'-c',sys.argv[3],str(w)],pass_fds=(w,),start_new_session=sys.argv[2]=='True'); "
            "os.close(w); assert select.select([r],[],[],2)[0] and os.read(r,1)==b'R'; os.close(r); "
            "pathlib.Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)"
        )
        marker=root/'owned-pid'; refused=False; reaped=[]
        started=time.monotonic()
        try:
            try:
                probe.Budget().run([sys.executable,'-c',parent_code,str(marker),str(escaped),child_code],timeout=2)
            except ci.Refusal as error:
                assert str(error)=='probe_command_timeout'; refused=True
            elapsed=time.monotonic()-started
            assert marker.is_file() and refused and elapsed<3
            pid=int(marker.read_text())
            # A separate session survives outer-group cleanup: it is never
            # represented as whole-tree settlement. Exact-PID test cleanup only.
            if escaped:
                os.kill(pid,0)
                os.kill(pid,signal.SIGKILL)
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                try:
                    got,status=os.waitpid(-1,os.WNOHANG)
                except ChildProcessError:
                    break
                if got: reaped.append(got)
                else: time.sleep(.01)
            else: raise AssertionError('synthetic descendants not reaped')
            assert pid in reaped
            print(json.dumps({'refused':refused,'owned_child_reaped':True,'escaped_requires_quarantine':escaped}))
        finally:
            if marker.exists():
                try: os.kill(int(marker.read_text()),signal.SIGKILL)
                except ProcessLookupError: pass
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                try: got,status=os.waitpid(-1,os.WNOHANG)
                except ChildProcessError: break
                if not got: time.sleep(.01)
            else: raise AssertionError('test finalizer failed to reap owned children')
    """)
    )
    result = subprocess.run(
        [sys.executable, str(supervisor), str(tmp_path), str(escaped)],
        capture_output=True,
        timeout=8,
        check=False,
        env={**os.environ, "PYTHONPATH": str(Path.cwd())},
    )
    assert result.returncode == 0, result.stderr.decode("ascii")
    assert json.loads(result.stdout) == {
        "refused": True,
        "owned_child_reaped": True,
        "escaped_requires_quarantine": escaped,
    }


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("ack", False, "probe_dedicated_disposable_vm_required"),
        ("RUNNER_ENVIRONMENT", "self-hosted", "probe_dedicated_disposable_vm_required"),
        ("RUNNER_OS", "Windows", "probe_dedicated_disposable_vm_required"),
        ("ImageOS", "ubuntu22", "probe_dedicated_disposable_vm_required"),
        ("valid", True, "probe_provider_identity_required"),
    ],
)
def test_disposable_vm_contract_refuses_before_any_host_command(
    monkeypatch, capsys, field, value, reason
):
    args = ["probe", "--execute", "--disposable-job"]
    if field == "ack":
        args.remove("--disposable-job")
    monkeypatch.setattr("sys.argv", args)
    for key, val in {
        "CI": "true",
        "GITHUB_ACTIONS": "true",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_OS": "Linux",
        "ImageOS": "ubuntu24",
    }.items():
        monkeypatch.setenv(key, str(value) if key == field else val)
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    original = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda p, *a, **kw: (
            'VERSION_ID="24.04"' if str(p) == "/etc/os-release" else original(p, *a, **kw)
        ),
    )
    monkeypatch.setattr(probe, "_run_bounded", lambda *a, **kw: pytest.fail("no host command"))
    assert probe.main() == 1
    record = json.loads(capsys.readouterr().out)
    assert record["status"] == "refused" and record["arms"] == []
    assert record["reason"] == reason


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "top",
        "identity",
        "sources",
        "arm",
        "native",
        "nonce",
        "cleanup",
        "provider",
        "fd_plan",
    ],
)
def test_success_report_is_closed_at_every_publication_boundary(mutation):
    sources = dict.fromkeys(probe._SOURCES, "1" * 64)
    report = {
        "version": 1,
        "status": "causal_aba_observed",
        "github_run_id": "1",
        "github_run_attempt": "2",
        "identity": {
            "head": "a" * 40,
            "tree": "b" * 40,
            "sources": dict(sources),
            **dict.fromkeys(("bwrap", "bwrap_version", "kernel", "apparmor"), "3" * 64),
        },
        "arms": [],
    }
    for name, digit in (("A1", "a"), ("B", "b"), ("A2", "c")):
        profile = ci.RUNNER_PROFILE_SHA256 if name == "B" else ci.PROFILE_SHA256
        outcome = "green" if name == "B" else "red"
        native = native_record(outcome, arm=name, nonce=digit * 32, profile=profile)
        report["arms"].append(
            {
                "arm": name,
                "nonce": digit * 32,
                "profile_sha256": profile,
                "outcome": outcome,
                "native": native,
                "cleanup": "observed",
                "global_restriction": 1,
                "generic_userns": "denied",
            }
        )
    if mutation in {"top", "identity", "sources", "arm", "native"}:
        target = {
            "top": report,
            "identity": report["identity"],
            "sources": report["identity"]["sources"],
            "arm": report["arms"][0],
            "native": report["arms"][0]["native"],
        }[mutation]
        target["unexpected"] = "PRIVATE"
    elif mutation == "nonce":
        report["arms"][2]["nonce"] = report["arms"][0]["nonce"]
        report["arms"][2]["native"]["nonce"] = report["arms"][0]["nonce"]
    elif mutation == "cleanup":
        report["arms"][0]["global_restriction"] = True
    elif mutation == "provider":
        report["github_run_id"] = "9"
    elif mutation == "fd_plan":
        report["arms"][1]["native"]["launches"][0]["argv_sha256"] = "f" * 64
    kwargs = dict(run_id="1", attempt="2", head="a" * 40, tree="b" * 40, sources=sources)
    if mutation is None:
        assert probe.closed_report(report, **kwargs) is report
    else:
        with pytest.raises(ci.Refusal):
            probe.closed_report(report, **kwargs)
