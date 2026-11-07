"""Opt-in disposable Noble causal probe; offline tests never load host policy.

No workflow invokes this module. Only an explicitly authorized hosted execution
may run main. Unknown native settlement keeps policy loaded and quarantines the
disposable runner instead of unrestricting a possibly live child.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import selectors
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

from scripts import session_host_ci as ci
from scripts import session_host_probe_adapter as adapter

HELPER_SHA256 = "3064a4e370337513f7070a86fe85ce168c1ebbcdd41ac77293883c126886efe3"
_NAMES = {"bwrap", "unpriv_bwrap"}
_SOURCES = (
    "scripts/session_host_ci.py",
    "scripts/session_host_causal_probe.py",
    "scripts/session_host_probe_adapter.py",
    "scripts/runtime_cli_sandbox_init.c",
    "tests/session_host/test_bubblewrap.py",
    "tests/session_host/test_core.py",
    "src/butlers/session_host/bubblewrap.py",
    "src/butlers/session_host/lifecycle.py",
    "uv.lock",
    "pyproject.toml",
)


class Budget:
    def __init__(self):
        self.start = time.monotonic()

    def remaining(self, *, cleanup=False):
        remaining = self.start + (300 if cleanup else 210) - time.monotonic()
        if remaining <= 0:
            raise ci.Refusal("probe_deadline_expired")
        return remaining

    def run(self, args, *, timeout=15, cleanup=False):
        return _run_bounded(args, timeout=min(timeout, self.remaining(cleanup=cleanup)))

    def checked(self, args, *, cleanup=False):
        result = self.run(args, cleanup=cleanup)
        if result.returncode:
            raise ci.Refusal("probe_host_command_refused")
        return result.stdout.strip()


def _signal_owned_group(process, signum):
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        pass
    except OSError:
        raise ci.Refusal("probe_command_cleanup_unproven") from None


def _run_bounded(args, *, timeout):
    """Bound aggregate retained bytes and our direct child/group, never a native session.

    A native bwrap child deliberately starts another session. Any incomplete
    adapter receipt therefore still quarantines policy and stops the sequence;
    only disposal of the separately reviewed dedicated hosted VM contains an
    unobserved escaped child. Group cleanup is not native settlement evidence.
    """
    deadline = time.monotonic() + timeout
    reserve = min(1.0, timeout / 4)
    process = subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    output, error = bytearray(), bytearray()
    with selectors.DefaultSelector() as selector:
        try:
            for stream, captured in ((process.stdout, output), (process.stderr, error)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, captured)
            while selector.get_map() or process.poll() is None:
                remaining = deadline - reserve - time.monotonic()
                if remaining <= 0:
                    raise ci.Refusal("probe_command_timeout")
                for key, _ in selector.select(min(0.05, remaining)):
                    available = ci._LIMIT - len(output) - len(error)
                    try:
                        chunk = os.read(key.fd, min(65536, available + 1))
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                    elif len(chunk) > available:
                        # One extra byte detects overflow; it is never retained.
                        raise ci.Refusal("probe_command_output_limit")
                    else:
                        key.data.extend(chunk)
            return subprocess.CompletedProcess(
                args, process.returncode, bytes(output), bytes(error)
            )
        except BaseException:
            # Never communicate(): escaped inherited pipes may never reach EOF.
            # TERM then unconditional KILL also covers a TERM-ignoring group
            # member after its direct parent has exited. Only our PID/group is
            # signalled; a separate session remains uncertain and quarantined.
            cleanup_deadline = min(deadline, time.monotonic() + reserve)
            _signal_owned_group(process, signal.SIGTERM)
            try:
                process.wait(timeout=max(0, min(0.2, (cleanup_deadline - time.monotonic()) / 2)))
            except subprocess.TimeoutExpired:
                pass
            _signal_owned_group(process, signal.SIGKILL)
            try:
                process.wait(timeout=max(0, cleanup_deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                raise ci.Refusal("probe_command_cleanup_unproven") from None
            raise
        finally:
            process.stdout.close()
            process.stderr.close()


def restrictions(budget, *, cleanup=False):
    if budget.checked(["sysctl", "-n", ci._SYSCTL], cleanup=cleanup) != b"1":
        raise ci.Refusal("global_restriction_not_one")
    result = budget.run(["unshare", "--user", "--map-root-user", "/usr/bin/true"], cleanup=cleanup)
    if result.returncode != 1 or b"Operation not permitted" not in result.stderr:
        raise ci.Refusal("generic_userns_not_denied")


def loaded(budget, *, cleanup=False):
    raw = budget.checked(
        ["sudo", "-n", "cat", "/sys/kernel/security/apparmor/profiles"], cleanup=cleanup
    )
    return {line.split(" ", 1)[0] for line in raw.decode("ascii").splitlines()}


def absent(budget):
    if any("bwrap" in name for name in loaded(budget)):
        raise ci.Refusal("preloaded_profile_conflict")
    for name in ("bwrap-userns-restrict", "unpriv_bwrap"):
        path = Path("/etc/apparmor.d/local") / name
        if path.exists() or path.is_symlink():
            raise ci.Refusal("local_profile_override_conflict")


def read_owned(path: Path, maximum: int) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_size > maximum
            ):
                raise ci.Refusal("probe_owned_file_refused")
            raw = stream.read(maximum + 1)
            if len(raw) > maximum:
                raise ci.Refusal("probe_owned_file_refused")
            return raw
    except OSError:
        raise ci.Refusal("probe_owned_file_refused") from None


def owned_bytes(path: Path, expected: bytes) -> bool:
    try:
        return read_owned(path, len(expected)) == expected
    except ci.Refusal:
        return False


def source_identity(budget):
    if budget.checked(["git", "status", "--porcelain"]):
        raise ci.Refusal("probe_clean_checkout_required")
    hashes = {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in _SOURCES}
    if (
        hashes["scripts/session_host_ci.py"] != HELPER_SHA256
        or hashes["tests/session_host/test_bubblewrap.py"] != adapter.NATIVE_SHA256
    ):
        raise ci.Refusal("probe_source_pin_mismatch")
    head = budget.checked(["git", "rev-parse", "HEAD"]).decode("ascii")
    tree = budget.checked(["git", "rev-parse", "HEAD^{tree}"]).decode("ascii")
    if re.fullmatch("[0-9a-f]{40}", head) is None or re.fullmatch("[0-9a-f]{40}", tree) is None:
        raise ci.Refusal("probe_source_identity_refused")
    return {
        "head": head,
        "tree": tree,
        "sources": hashes,
        "bwrap": hashlib.sha256(Path("/usr/bin/bwrap").read_bytes()).hexdigest(),
        "bwrap_version": hashlib.sha256(
            budget.checked(["/usr/bin/bwrap", "--version"])
        ).hexdigest(),
        "kernel": hashlib.sha256(budget.checked(["uname", "-r"])).hexdigest(),
        "apparmor": hashlib.sha256(budget.checked(["apparmor_parser", "--version"])).hexdigest(),
    }


def closed_result(record: dict, returncode: int, *, arm, nonce, profile) -> str:
    keys = {
        "version",
        "node",
        "arm",
        "nonce",
        "profile_sha256",
        "native_sha256",
        "collected",
        "phases",
        "images",
        "diagnostic",
        "peer_state",
        "adapter_ok",
        "exitstatus",
        "settlement",
        "launches",
    }
    if (
        type(record) is not dict
        or set(record) != keys
        or type(record["version"]) is not int
        or record["version"] != 1
        or record["node"] != adapter.NODE
        or record["arm"] != arm
        or record["nonce"] != nonce
        or record["profile_sha256"] != profile
        or record["native_sha256"] != adapter.NATIVE_SHA256
        or type(nonce) is not str
        or re.fullmatch("[0-9a-f]{32}", nonce) is None
        or type(record["collected"]) is not int
        or record["collected"] != 1
        or record["adapter_ok"] is not True
        or type(record["exitstatus"]) is not int
        or record["exitstatus"] != returncode
        or type(record["images"]) is not list
        or len(record["images"]) != 2
    ):
        raise ci.Refusal("probe_native_settlement_unproven")
    for image, destination in zip(
        record["images"], ("/runtime/init", "/runtime/program"), strict=True
    ):
        if (
            type(image) is not dict
            or set(image) != {"destination", "sha256", "mode", "size"}
            or image["destination"] != destination
            or type(image["mode"]) is not int
            or image["mode"] != 0o555
            or type(image["sha256"]) is not str
            or re.fullmatch("[0-9a-f]{64}", image["sha256"]) is None
            or type(image["size"]) is not int
            or not 0 < image["size"] <= 256 * 1024 * 1024
        ):
            raise ci.Refusal("probe_native_settlement_unproven")
    diagnostic = adapter.closed_diagnostic(record["diagnostic"])
    peer = adapter.closed_peer_state(record["peer_state"])
    settlement = record["settlement"]
    if (
        type(settlement) is not dict
        or set(settlement) != {"owner_healthy", "owner_pending", "stage_empty", "domains"}
        or settlement["owner_healthy"] is not True
        or settlement["stage_empty"] is not True
        or type(settlement["owner_pending"]) is not int
        or settlement["owner_pending"] != 0
        or type(settlement["domains"]) is not list
        or len(settlement["domains"]) != (1 if returncode == 1 else 2)
        or any(
            type(d) is not dict
            or set(d)
            != {
                "namespace_dead",
                "child_reaped",
                "settled",
                "fds_closed",
                "spawn_done",
                "reaper_done",
            }
            or any(value is not True for value in d.values())
            for d in settlement["domains"]
        )
    ):
        raise ci.Refusal("probe_native_settlement_unproven")
    validate_launches(record)
    phases = record["phases"]
    if (
        returncode == 0
        and phases == {"setup": "passed", "call": "passed", "teardown": "passed"}
        and diagnostic["ready_receipt"] == "matched"
        and diagnostic["ready_bytes"] == 34
        and diagnostic["stderr_complete"] is True
        and peer["ready"] is True
        and peer["released"] is True
        and peer["namespace_dead"] is True
        and peer["child_reaped"] is True
    ):
        return "green"
    if (
        returncode == 1
        and phases == {"setup": "passed", "call": "failed", "teardown": "passed"}
        and diagnostic == adapter.RED_DIAGNOSTIC
        and peer == adapter.RED_STATE
    ):
        return "red"
    raise ci.Refusal("probe_native_settlement_unproven")


def validate_launches(record):
    launches = record["launches"]
    expected_roles = ["peer"] if record["exitstatus"] == 1 else ["peer", "reader"]
    destinations = [
        "/runtime/init",
        "/runtime/program",
        "/invocation/model-credential",
        "/invocation/prompt",
        "/invocation/mcp.json",
    ]
    if type(launches) is not list or len(launches) != len(expected_roles):
        raise ci.Refusal("probe_fd_plan_refused")
    for launch, role in zip(launches, expected_roles, strict=True):
        if (
            type(launch) is not dict
            or set(launch) != {"role", "files", "inherited", "argv_sha256"}
            or launch["role"] != role
            or type(launch["argv_sha256"]) is not str
            or re.fullmatch("[0-9a-f]{64}", launch["argv_sha256"]) is None
            or launch["inherited"] != ["--info-fd", "--block-fd", "--shim-gate-fd", *destinations]
            or type(launch["files"]) is not list
            or len(launch["files"]) != 5
        ):
            raise ci.Refusal("probe_fd_plan_refused")
        for index, (file, destination) in enumerate(
            zip(launch["files"], destinations, strict=True)
        ):
            keys = {"destination", "perms", "fd_mode", "regular", "seals"}
            if index < 2:
                keys |= {"sha256", "size"}
            if (
                type(file) is not dict
                or set(file) != keys
                or file["destination"] != destination
                or file["perms"] != ("0555" if index < 2 else "0400")
                or file["regular"] is not True
                or type(file["fd_mode"]) is not int
                or not 0 <= file["fd_mode"] <= 0o777
                or type(file["seals"]) is not int
                or file["seals"] & 15 != 15
                or (
                    index < 2
                    and (
                        file["sha256"] != record["images"][index]["sha256"]
                        or file["size"] != record["images"][index]["size"]
                        or type(file["size"]) is not int
                    )
                )
            ):
                raise ci.Refusal("probe_fd_plan_refused")


def invoke(root, budget, *, name, nonce, profile):
    path = root / "native-result.json"
    result = budget.run(
        [
            "env",
            "-u",
            "PYTEST_ADDOPTS",
            "-u",
            "PYTEST_PLUGINS",
            "CI=true",
            "GITHUB_ACTIONS=true",
            "BUTLERS_REQUIRE_SESSION_HOST_NATIVE=1",
            "uv",
            "run",
            "--no-sync",
            "pytest",
            adapter.NODE,
            "-n",
            "0",
            "-q",
            "--tb=short",
            "-p",
            "scripts.session_host_probe_adapter",
            "--session-host-probe-report",
            str(path),
            "--session-host-probe-arm",
            name,
            "--session-host-probe-nonce",
            nonce,
            "--session-host-probe-profile",
            profile,
            "--basetemp",
            str(root / "pytest"),
        ],
        timeout=45,
    )
    # Live-bounded private output is discarded: never print it.
    raw = read_owned(path, 32768)
    record = json.loads(raw, object_pairs_hook=unique_object)
    return closed_result(record, result.returncode, arm=name, nonce=nonce, profile=profile), record


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ci.Refusal("probe_adapter_receipt_refused")
        result[key] = value
    return result


def owned_arm(root, name, profile, budget, identity):
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    digest = hashlib.sha256(profile).hexdigest()
    expected = ci.PROFILE_SHA256 if name in {"A1", "A2"} else ci.RUNNER_PROFILE_SHA256
    if name not in {"A1", "B", "A2"} or digest != expected:
        raise ci.Refusal("probe_fixed_variant_required")
    path = root / "bwrap-userns-restrict"
    ci._write(path, profile)
    nonce = secrets.token_hex(16)
    receipt = json.dumps(
        {
            "version": 1,
            "arm": name,
            "nonce": nonce,
            "profile": digest,
            "upstream_profile": ci.PROFILE_SHA256,
        },
        sort_keys=True,
    ).encode()
    restrictions(budget)
    absent(budget)
    if source_identity(budget) != identity:
        raise ci.Refusal("probe_source_identity_changed")
    ci._write(root / "load-intent.json", receipt)
    safe_to_remove = True
    try:
        if budget.run(["sudo", "-n", "apparmor_parser", "-a", str(path)]).returncode:
            raise ci.Refusal("probe_load_unproven")
        if not _NAMES.issubset(loaded(budget)):
            raise ci.Refusal("probe_load_unobserved")
        ci._write(root / "loaded.json", receipt)
        restrictions(budget)
        safe_to_remove = False
        outcome, record = invoke(root, budget, name=name, nonce=nonce, profile=digest)
        safe_to_remove = True  # Only the closed settled RED or fully passing node.
    finally:
        if not owned_bytes(root / "loaded.json", receipt):
            raise ci.Refusal("probe_quarantine_load_ownership_unproven")
        if not safe_to_remove:
            raise ci.Refusal("probe_quarantine_native_settlement_unproven")
        if (
            not owned_bytes(root / "load-intent.json", receipt)
            or not owned_bytes(path, profile)
            or not _NAMES.issubset(loaded(budget, cleanup=True))
        ):
            raise ci.Refusal("probe_quarantine_cleanup_identity_unproven")
        budget.checked(["sudo", "-n", "apparmor_parser", "-R", str(path)], cleanup=True)
        if _NAMES & loaded(budget, cleanup=True):
            raise ci.Refusal("probe_cleanup_unobserved")
        restrictions(budget, cleanup=True)
    return {
        "arm": name,
        "nonce": nonce,
        "profile_sha256": digest,
        "outcome": outcome,
        "native": record,
        "cleanup": "observed",
        "global_restriction": 1,
        "generic_userns": "denied",
    }


def sequence(root, original, budget, report):
    derived = ci.runner_profile(original)
    identity = source_identity(budget)
    report["identity"] = identity
    images = None
    peer_launch = None
    for name, profile, expected in (
        ("A1", original, "red"),
        ("B", derived, "green"),
        ("A2", original, "red"),
    ):
        arm = owned_arm(root / name, name, profile, budget, identity)
        report["arms"].append(arm)
        if arm["outcome"] != expected:
            raise ci.Refusal("probe_arm_result_refused")
        if images is not None and images != arm["native"]["images"]:
            raise ci.Refusal("probe_compiled_images_changed")
        if peer_launch is not None and peer_launch != arm["native"]["launches"][0]:
            raise ci.Refusal("probe_actual_fd_plan_changed")
        images = arm["native"]["images"]
        peer_launch = arm["native"]["launches"][0]
    if source_identity(budget) != identity:
        raise ci.Refusal("probe_source_identity_changed")
    restrictions(budget, cleanup=True)
    if any("bwrap" in name for name in loaded(budget, cleanup=True)):
        raise ci.Refusal("probe_cleanup_unobserved")
    report["status"] = "causal_aba_observed"


def closed_report(record, *, run_id, attempt, head, tree, sources):
    """Validate success-only publication against independently pinned launcher inputs."""
    if (
        type(record) is not dict
        or set(record)
        != {"version", "status", "arms", "identity", "github_run_id", "github_run_attempt"}
        or type(record["version"]) is not int
        or record["version"] != 1
        or record["status"] != "causal_aba_observed"
        or record["github_run_id"] != run_id
        or record["github_run_attempt"] != attempt
        or any(
            type(v) is not str or re.fullmatch("[0-9]{1,20}", v) is None for v in (run_id, attempt)
        )
        or any(type(v) is not str or re.fullmatch("[0-9a-f]{40}", v) is None for v in (head, tree))
        or type(sources) is not dict
        or set(sources) != set(_SOURCES)
        or any(
            type(v) is not str or re.fullmatch("[0-9a-f]{64}", v) is None for v in sources.values()
        )
        or type(record["arms"]) is not list
        or len(record["arms"]) != 3
    ):
        raise ci.Refusal("probe_success_report_refused")
    identity = record["identity"]
    if (
        type(identity) is not dict
        or set(identity)
        != {"head", "tree", "sources", "bwrap", "bwrap_version", "kernel", "apparmor"}
        or identity["head"] != head
        or identity["tree"] != tree
        or type(identity["sources"]) is not dict
        or identity["sources"] != sources
        or any(
            type(identity[k]) is not str or re.fullmatch("[0-9a-f]{64}", identity[k]) is None
            for k in ("bwrap", "bwrap_version", "kernel", "apparmor")
        )
    ):
        raise ci.Refusal("probe_success_report_refused")
    nonces = set()
    first = record["arms"][0]
    for arm, name, profile, expected in zip(
        record["arms"],
        ("A1", "B", "A2"),
        (ci.PROFILE_SHA256, ci.RUNNER_PROFILE_SHA256, ci.PROFILE_SHA256),
        ("red", "green", "red"),
        strict=True,
    ):
        if (
            type(arm) is not dict
            or set(arm)
            != {
                "arm",
                "nonce",
                "profile_sha256",
                "outcome",
                "native",
                "cleanup",
                "global_restriction",
                "generic_userns",
            }
            or arm["arm"] != name
            or arm["profile_sha256"] != profile
            or arm["outcome"] != expected
            or arm["cleanup"] != "observed"
            or type(arm["global_restriction"]) is not int
            or arm["global_restriction"] != 1
            or arm["generic_userns"] != "denied"
            or type(arm["nonce"]) is not str
            or re.fullmatch("[0-9a-f]{32}", arm["nonce"]) is None
            or arm["nonce"] in nonces
            or closed_result(
                arm["native"],
                0 if name == "B" else 1,
                arm=name,
                nonce=arm["nonce"],
                profile=profile,
            )
            != expected
            or arm["native"]["images"] != first["native"]["images"]
            or arm["native"]["launches"][0] != first["native"]["launches"][0]
        ):
            raise ci.Refusal("probe_success_report_refused")
        nonces.add(arm["nonce"])
    return record


def fetch_package(budget):
    # A child deadline bounds the entire download, including slow reads.
    code = (
        "import sys, urllib.request; "
        f"r=urllib.request.urlopen({ci.PACKAGE_URL!r}, timeout=25); "
        f"sys.stdout.buffer.write(r.read({ci._LIMIT + 1}))"
    )
    result = budget.run([sys.executable, "-c", code], timeout=30)
    if result.returncode:
        raise ci.Refusal("probe_package_fetch_refused")
    package = result.stdout  # Binary package bytes must never be stripped.
    if len(package) > ci._LIMIT or hashlib.sha256(package).hexdigest() != ci.PACKAGE_SHA256:
        raise ci.Refusal("package_digest_mismatch")
    return package


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--disposable-job", action="store_true")
    options = parser.parse_args()
    report = {"version": 1, "status": "refused", "arms": []}
    root = None
    try:
        if (
            not options.execute
            or os.environ.get("CI") != "true"
            or os.environ.get("GITHUB_ACTIONS") != "true"
            or 'VERSION_ID="24.04"' not in Path("/etc/os-release").read_text()
        ):
            raise ci.Refusal("probe_explicit_hosted_noble_required")
        if (
            not options.disposable_job
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
            or os.environ.get("RUNNER_OS") != "Linux"
            or os.environ.get("ImageOS") != "ubuntu24"
        ):
            raise ci.Refusal("probe_dedicated_disposable_vm_required")
        for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
            value = os.environ.get(key, "")
            if re.fullmatch("[0-9]{1,20}", value) is None:
                raise ci.Refusal("probe_provider_identity_required")
            report[key.lower()] = value
        proposed = (
            Path(os.environ["RUNNER_TEMP"]).resolve(strict=True) / "butlers-session-host-causal"
        )
        proposed.mkdir(mode=0o700)
        root = proposed
        root.chmod(0o700)
        budget = Budget()
        restrictions(budget)
        absent(budget)
        package = fetch_package(budget)
        ci._write(root / "profile.deb", package)
        archive = budget.checked(["dpkg-deb", "--fsys-tarfile", str(root / "profile.deb")])
        original = ci.verified_profile(package, archive)
        sequence(root, original, budget, report)
        closed_report(
            report,
            run_id=report["github_run_id"],
            attempt=report["github_run_attempt"],
            head=report["identity"]["head"],
            tree=report["identity"]["tree"],
            sources=report["identity"]["sources"],
        )
    except ci.Refusal as error:
        report["status"] = "refused"
        report["reason"] = str(error)
    except Exception:
        report["status"] = "refused"
        report["reason"] = "probe_failed_closed"
    # Only typed records and fixed refusal categories reach stdout/artifacts.
    raw = json.dumps(report, sort_keys=True).encode()
    if root is not None and root.is_dir():
        ci._write(root / "probe-report.json", raw)
    print(raw.decode())
    return 0 if report["status"] == "causal_aba_observed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
