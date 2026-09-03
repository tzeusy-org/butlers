"""Adversarial fork/double-fork/setsid descendant-survival probe (bu-q6vjl).

Runs inside the exact production Dashboard image and drives the real
Bubblewrap CLI-auth sandbox with an adversarial payload command instead of a
provider CLI.  The payload forks a child that setsid()s and double-forks to
fully detach a grandchild, then exits immediately.  This harness proves the
kernel's PID-namespace teardown (triggered by namespace PID1 -- the payload
itself -- exiting) reaps that detached descendant before it can complete a
deliberately delayed write, and that no artifact survives sandbox teardown.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from butlers.cli_auth.sandbox_platform import (
    BubblewrapDashboardCLIAuthSandbox,
    ReadonlySandboxInput,
    SandboxStage,
    _BubblewrapDeviceAuthHandle,
    _close_fd,
    _outer_identity_preexec,
    _read_bubblewrap_info,
    build_bubblewrap_launch_plan,
)

_PAYLOAD_HOST_PATH = "/tmp/bu-q6vjl-descendant-survival-payload"
_PAYLOAD_SANDBOX_PATH = "/usr/local/bin/bu-q6vjl-descendant-survival-payload"
_READY_LINE = b"BUTLERS_DESCENDANT_SURVIVAL_READY\n"
# The statically-linked payload itself needs no shared libraries, but the
# image-owned PID1 shim it execs from is dynamically linked against glibc.
# The provider manifest resolver normally supplies this closure as a side
# effect of the provider's own ldd dependencies; this harness bypasses that
# resolver, so it must mount the shim's runtime closure explicitly.
_SHIM_RUNTIME_LIBRARIES = (
    "/lib/x86_64-linux-gnu/libc.so.6",
    "/lib64/ld-linux-x86-64.so.2",
)
# Longer than the payload's own 2s delayed-write window, so an unproven
# process would have finished writing by the time this harness checks.
_SURVIVAL_WINDOW_S = 4.0


def _scan_container_cmdlines(*, exclude_pid: int) -> str:
    """Read every visible process's cmdline; PIDs are visible across nested namespaces.

    ``exclude_pid`` is namespace PID1 itself (the payload's own container-level
    PID) -- a legitimately exited, not-yet-reaped PID1 is not a surviving
    descendant.
    """
    matches: list[str] = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == exclude_pid:
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as handle:
                raw = handle.read()
        except OSError:
            continue
        rendered = raw.replace(b"\0", b" ").decode("utf-8", errors="replace").strip()
        if _PAYLOAD_SANDBOX_PATH in rendered:
            matches.append(f"{entry}:{rendered}")
    return ";".join(matches)


async def _run() -> None:
    sandbox = BubblewrapDashboardCLIAuthSandbox()
    sandbox._exact_image_preflight()
    identity = await sandbox._identity_pool.acquire()
    stage: SandboxStage | None = None
    process = None
    pidfd: int | None = None
    handle: _BubblewrapDeviceAuthHandle | None = None
    info_read = info_write = block_read = block_write = shim_gate_read = shim_gate_write = None
    try:
        stage = sandbox._stage_factory(identity)
        info_read, info_write = os.pipe2(os.O_CLOEXEC)
        block_read, block_write = os.pipe2(os.O_CLOEXEC)
        shim_gate_read, shim_gate_write = os.pipe2(os.O_CLOEXEC)
        plan = build_bubblewrap_launch_plan(
            bwrap_path=sandbox._bwrap_path,
            shim_path=sandbox._shim_path,
            identity=identity,
            stage_home=stage.path,
            command=(_PAYLOAD_SANDBOX_PATH,),
            readonly_inputs=(
                ReadonlySandboxInput(
                    source=Path(_PAYLOAD_HOST_PATH),
                    destination=Path(_PAYLOAD_SANDBOX_PATH),
                ),
                *(
                    ReadonlySandboxInput(source=Path(library), destination=Path(library))
                    for library in _SHIM_RUNTIME_LIBRARIES
                ),
            ),
            info_fd=info_write,
            block_fd=block_read,
            shim_gate_fd=shim_gate_read,
        )
        process = await sandbox._spawn(
            *plan.argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=plan.environment,
            close_fds=plan.close_fds,
            pass_fds=plan.pass_fds,
            preexec_fn=_outer_identity_preexec(identity),
        )
        _close_fd(info_write)
        info_write = None
        _close_fd(block_read)
        block_read = None
        _close_fd(shim_gate_read)
        shim_gate_read = None

        child_pid = await asyncio.wait_for(_read_bubblewrap_info(info_read), timeout=5)
        pidfd = sandbox._pidfd_open(child_pid, 0)
        handle = _BubblewrapDeviceAuthHandle(
            process=process,
            pidfd=pidfd,
            stage=stage,
            identity=identity,
            identity_pool=sandbox._identity_pool,
            relative_output_path=None,
            shim_ready=False,
            pidfd_send_signal=sandbox._pidfd_send_signal,
            pidfd_is_dead=sandbox._pidfd_is_dead,
        )
        pidfd = None

        sandbox._release_payload(block_write)
        _close_fd(block_write)
        block_write = None
        sandbox._release_payload(shim_gate_write)
        _close_fd(shim_gate_write)
        shim_gate_write = None

        output = await asyncio.wait_for(process.stdout.read(), timeout=10)
        if _READY_LINE not in output:
            raise RuntimeError(f"payload did not report readiness: {output!r}")

        started_marker = stage.path / "descendant-started.marker"
        survived_marker = stage.path / "descendant-survived.marker"

        # Give a live (unkilled) descendant strictly more time than its own
        # delayed-write window before treating its absence as proof.
        await asyncio.sleep(_SURVIVAL_WINDOW_S)

        descendant_started = started_marker.exists()
        descendant_survived = survived_marker.exists()
        lingering_cmdlines = _scan_container_cmdlines(exclude_pid=child_pid)

        terminated = await handle.complete_readonly()
        stage_discarded = not stage.path.exists()

        if not descendant_started:
            raise RuntimeError("descendant never ran; attack payload did not exercise the path")
        if descendant_survived:
            raise RuntimeError("descendant mutated staged output after direct-child exit")
        if lingering_cmdlines:
            raise RuntimeError(f"descendant process survived teardown: {lingering_cmdlines}")
        if not terminated:
            raise RuntimeError("sandbox could not prove namespace PID1 death")
        if not stage_discarded:
            raise RuntimeError("staged tree was not discarded after teardown")

        print(
            json.dumps(
                {
                    "descendant_started": descendant_started,
                    "descendant_survived": descendant_survived,
                    "lingering_process": bool(lingering_cmdlines),
                    "pid1_terminated": terminated,
                    "stage_discarded": stage_discarded,
                },
                sort_keys=True,
            )
        )
    finally:
        for fd in (info_read, info_write, block_read, block_write, shim_gate_read, shim_gate_write):
            _close_fd(fd)
        if pidfd is not None:
            _close_fd(pidfd)
        if handle is not None:
            await handle.terminate()


asyncio.run(_run())
