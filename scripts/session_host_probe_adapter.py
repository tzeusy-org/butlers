"""Closed pytest receipts for one unchanged synthetic native node; no raw output."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import re
import stat
from pathlib import Path

import pytest

from scripts import session_host_ci as ci

NODE = (
    "tests/session_host/test_bubblewrap.py::"
    "test_native_cross_invocation_read_denied_and_own_material_readable"
)
NATIVE_SHA256 = "20426d3dc17c1b87e66c713dd32196337f0aa3347c1ce01507db907cf873db77"
RED_DIAGNOSTIC = {
    "ready_receipt": "eof",
    "ready_bytes": 0,
    "stderr_category": "shim_exec_failed",
    "stderr_reason": "not_found",
    "stderr_bytes": 55,
    "stderr_sha256": "e8cd8cae9f20f0fe83e5393fc231cf1f3f1d2e16e166e2ff58f7c5196a30040e",
    "stderr_complete": True,
}
RED_STATE = {
    "ready": False,
    "released": True,
    "execution_failure": "Refused",
    "output_bytes": 0,
    "exit": 1,
    "namespace_dead": True,
    "child_reaped": True,
}


def closed_diagnostic(value):
    if type(value) is not dict or set(value) != set(RED_DIAGNOSTIC):
        raise ci.Refusal("probe_unknown_diagnostic")
    if any(type(value[k]) is not type(v) for k, v in RED_DIAGNOSTIC.items()):
        raise ci.Refusal("probe_unknown_diagnostic")
    if (
        value["ready_receipt"] not in {"not_observed", "matched", "eof", "mismatch", "cancelled"}
        or value["stderr_category"]
        not in {
            "empty",
            "unclassified",
            "shim_exec_failed",
            "bind_data_tmpfile_failed",
            "bind_data_copy_failed",
            "bind_data_mode_failed",
            "bind_mount_failed",
            "chdir_failed",
            "session_setup_failed",
            "shim_refused",
        }
        or value["stderr_reason"]
        not in {
            "unclassified",
            "permission_denied",
            "operation_not_permitted",
            "not_found",
            "exec_format",
            "text_busy",
            "bad_fd",
        }
        or not 0 <= value["ready_bytes"] <= 65536
        or not 0 <= value["stderr_bytes"] <= 2 * 1024 * 1024
        or re.fullmatch("[0-9a-f]{64}", value["stderr_sha256"]) is None
    ):
        raise ci.Refusal("probe_unknown_diagnostic")
    return dict(value)


def closed_peer_state(value):
    if (
        type(value) is not dict
        or set(value) != set(RED_STATE)
        or any(
            type(value[k]) is not bool
            for k in ("ready", "released", "namespace_dead", "child_reaped")
        )
        or (value["execution_failure"] is not None and type(value["execution_failure"]) is not str)
        or value["execution_failure"] not in {None, "Refused", "CancelledError"}
        or type(value["output_bytes"]) is not int
        or not 0 <= value["output_bytes"] <= 1048576
        or type(value["exit"]) is not int
        or not -128 <= value["exit"] <= 255
    ):
        raise ci.Refusal("probe_unknown_peer_state")
    return dict(value)


def launch_identity(argv, kwargs):
    """Read actual post-seal inherited FDs without changing their file offsets."""
    roles = {}
    normalized = list(argv)
    files = []
    for index, token in enumerate(argv):
        if token == "--ro-bind-data":
            fd, destination = int(argv[index + 1]), argv[index + 2]
            if (
                fd in roles
                or destination
                not in {
                    "/runtime/init",
                    "/runtime/program",
                    "/invocation/model-credential",
                    "/invocation/prompt",
                    "/invocation/mcp.json",
                }
                or argv[index - 2] != "--perms"
            ):
                raise ci.Refusal("probe_fd_plan_refused")
            info = os.fstat(fd)
            seals = fcntl.fcntl(fd, 1034)
            if not stat.S_ISREG(info.st_mode) or seals & 15 != 15 or info.st_uid != os.geteuid():
                raise ci.Refusal("probe_fd_plan_refused")
            role = {
                "destination": destination,
                "perms": argv[index - 1],
                "fd_mode": stat.S_IMODE(info.st_mode),
                "regular": True,
                "seals": seals,
            }
            if destination.startswith("/runtime/"):
                if not 0 < info.st_size <= 256 * 1024 * 1024:
                    raise ci.Refusal("probe_fd_plan_refused")
                digest = hashlib.sha256()
                offset = 0
                while offset < info.st_size:
                    chunk = os.pread(fd, min(65536, info.st_size - offset), offset)
                    if not chunk:
                        raise ci.Refusal("probe_fd_plan_refused")
                    digest.update(chunk)
                    offset += len(chunk)
                role.update(sha256=digest.hexdigest(), size=info.st_size)
            roles[fd] = destination
            files.append(role)
            normalized[index + 1] = f"<{destination}>"
        elif token in {"--info-fd", "--block-fd", "--shim-gate-fd"}:
            fd = int(argv[index + 1])
            if fd in roles or not stat.S_ISFIFO(os.fstat(fd).st_mode):
                raise ci.Refusal("probe_fd_plan_refused")
            roles[fd] = token
            normalized[index + 1] = f"<{token}>"
    inherited = [roles.get(fd) for fd in kwargs["pass_fds"]]
    if inherited != [
        "--info-fd",
        "--block-fd",
        "--shim-gate-fd",
        "/runtime/init",
        "/runtime/program",
        "/invocation/model-credential",
        "/invocation/prompt",
        "/invocation/mcp.json",
    ]:
        raise ci.Refusal("probe_fd_plan_refused")
    if (
        kwargs["env"] != {}
        or kwargs["close_fds"] is not True
        or kwargs["start_new_session"] is not True
        or kwargs["stdin"] != asyncio.subprocess.DEVNULL
        or kwargs["stdout"] != asyncio.subprocess.PIPE
        or kwargs["stderr"] not in {asyncio.subprocess.PIPE, asyncio.subprocess.DEVNULL}
    ):
        raise ci.Refusal("probe_fd_plan_refused")
    if argv[-2:] == ("/runtime/program", "resist"):
        role = "peer"
    elif argv[-3:-1] == ("/runtime/program", "read"):
        role = "reader"
        normalized[-1] = "<peer-credential-path>"
    else:
        raise ci.Refusal("probe_fd_plan_refused")
    return {
        "role": role,
        "files": files,
        "inherited": inherited,
        "argv_sha256": hashlib.sha256(json.dumps(normalized).encode()).hexdigest(),
    }


class Observation:
    """Transparent wrappers; original functions receive/return unchanged values."""

    def __init__(self, item):
        self.item = item
        self.patch = pytest.MonkeyPatch()
        self.owners = []
        self.domains = []
        self.startups = []
        self.launches = []
        self.ok = True

    def install(self):
        from butlers.session_host.bubblewrap import BubblewrapDomain
        from butlers.session_host.lifecycle import InvocationOwner

        plan = BubblewrapDomain._plan
        run = InvocationOwner.run
        startup = self.item.module._SyntheticPeerStartup
        initialize = startup.__init__
        spawn = asyncio.create_subprocess_exec

        def observed_plan(domain, *args, **kwargs):
            result = plan(domain, *args, **kwargs)
            self.domains.append((domain, tuple(result[0]), result[1]))
            return result

        async def observed_run(owner, *args, **kwargs):
            if owner not in self.owners:
                self.owners.append(owner)
            return await run(owner, *args, **kwargs)

        def observed_init(instance, *args, **kwargs):
            initialize(instance, *args, **kwargs)
            self.startups.append(instance)

        async def observed_spawn(*argv, **kwargs):
            try:
                matches = [
                    d for d, a, f in self.domains if a == argv and f == kwargs.get("pass_fds")
                ]
                if len(matches) != 1:
                    raise ci.Refusal("probe_fd_plan_refused")
                self.launches.append(launch_identity(argv, kwargs))
            except Exception:
                self.ok = False
            return await spawn(*argv, **kwargs)

        self.patch.setattr(BubblewrapDomain, "_plan", observed_plan)
        self.patch.setattr(InvocationOwner, "run", observed_run)
        self.patch.setattr(startup, "__init__", observed_init)
        self.patch.setattr(asyncio, "create_subprocess_exec", observed_spawn)

    def sample(self, record):
        try:
            if len(self.owners) != 1 or len(self.startups) != 1:
                raise ci.Refusal("probe_observation_missing")
            record["diagnostic"] = closed_diagnostic(self.startups[0].summary())
            peers = [d for d, _, _ in self.domains if d._command == ("/runtime/program", "resist")]
            if len(peers) != 1:
                raise ci.Refusal("probe_observation_missing")
            peer = peers[0]
            record["peer_state"] = closed_peer_state(
                {
                    "ready": peer.output_ready.is_set(),
                    "released": peer._released,
                    "execution_failure": getattr(peer, "execution_failure", None),
                    "output_bytes": peer.output_bytes_received,
                    "exit": peer._process.returncode,
                    "namespace_dead": peer.namespace_dead,
                    "child_reaped": peer.direct_child_reaped,
                }
            )
            record["launches"] = self.launches
            record["settlement"] = {
                "owner_healthy": self.owners[0].healthy,
                "owner_pending": self.owners[0].pending_count,
                "stage_empty": not any(self.item.funcargs["native_stage"].iterdir()),
                "domains": [
                    {
                        "namespace_dead": d.namespace_dead,
                        "child_reaped": d.direct_child_reaped,
                        "settled": d._settled,
                        "fds_closed": not d._fds,
                        "spawn_done": d._spawn is not None and d._spawn.done(),
                        "reaper_done": d._reaper is not None and d._reaper.done(),
                    }
                    for d, _, _ in self.domains
                ],
            }
            record["adapter_ok"] = record["adapter_ok"] and self.ok
        except Exception:
            record["adapter_ok"] = False


def image_identity(image: tuple) -> list[dict]:
    if type(image) is not tuple or len(image) != 2:
        raise ci.Refusal("probe_image_identity_refused")
    result = []
    for entry, destination in zip(image, ("/runtime/init", "/runtime/program"), strict=True):
        metadata = entry.source.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o555
            or entry.destination != destination
        ):
            raise ci.Refusal("probe_image_identity_refused")
        with entry.source.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != entry.sha256:
            raise ci.Refusal("probe_image_identity_refused")
        result.append(
            {"destination": destination, "sha256": digest, "mode": 0o555, "size": metadata.st_size}
        )
    return result


def pytest_addoption(parser):
    parser.addoption("--session-host-probe-report", default=None)
    parser.addoption("--session-host-probe-arm", choices=("A1", "B", "A2"))
    parser.addoption("--session-host-probe-nonce")
    parser.addoption("--session-host-probe-profile")


def pytest_configure(config):
    arm = config.getoption("--session-host-probe-arm")
    nonce = config.getoption("--session-host-probe-nonce")
    profile = config.getoption("--session-host-probe-profile")
    if (
        arm not in {"A1", "B", "A2"}
        or type(nonce) is not str
        or re.fullmatch("[0-9a-f]{32}", nonce) is None
        or profile != (ci.RUNNER_PROFILE_SHA256 if arm == "B" else ci.PROFILE_SHA256)
        or hashlib.sha256(Path(NODE.split("::")[0]).read_bytes()).hexdigest() != NATIVE_SHA256
    ):
        raise pytest.UsageError("probe_bound_native_identity_required")
    config._session_host_probe = {
        "version": 1,
        "node": NODE,
        "arm": arm,
        "nonce": nonce,
        "profile_sha256": profile,
        "native_sha256": NATIVE_SHA256,
        "collected": 0,
        "phases": {},
        "images": [],
        "diagnostic": None,
        "peer_state": None,
        "settlement": None,
        "launches": [],
        "adapter_ok": True,
    }


def pytest_collection_modifyitems(config, items):
    if len(items) != 1 or items[0].nodeid != NODE:
        raise pytest.UsageError("probe_exact_native_node_required")
    config._session_host_probe["collected"] = 1


@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup(fixturedef, request):
    outcome = yield
    if fixturedef.argname == "native_image" and outcome.excinfo is None:
        try:
            request.config._session_host_probe["images"] = image_identity(outcome.get_result())
        except Exception:
            request.config._session_host_probe["adapter_ok"] = False


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    record = item.config._session_host_probe
    if hasattr(report, "wasxfail"):
        record["adapter_ok"] = False
    if report.when in record["phases"] or item.nodeid != NODE:
        record["adapter_ok"] = False
    record["phases"][report.when] = report.outcome


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    observation = Observation(item)
    item.config._session_host_observation = observation
    observation.install()
    try:
        yield
    finally:
        observation.sample(item.config._session_host_probe)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item):
    try:
        yield
    finally:
        observation = getattr(item.config, "_session_host_observation", None)
        if observation is not None:
            observation.patch.undo()


def pytest_sessionfinish(session, exitstatus):
    record = session.config._session_host_probe
    record["exitstatus"] = int(exitstatus)
    path = Path(session.config.getoption("--session-host-probe-report"))
    parent = path.parent.lstat()
    if (
        path.name != "native-result.json"
        or not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.geteuid()
        or stat.S_IMODE(parent.st_mode) != 0o700
    ):
        raise pytest.UsageError("probe_private_report_required")
    ci._write(path, json.dumps(record, sort_keys=True).encode())
