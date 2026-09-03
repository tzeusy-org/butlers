"""Adversarial signer/protected-environment isolation probe (bu-xj2gi).

Runs inside the exact production Dashboard image and drives the real
Bubblewrap CLI-auth sandbox with an adversarial payload command instead of a
provider CLI.  The payload attempts to open the real, absolute, root-owned
mode-0400 signer path and to recover a marker value planted in the
orchestrator's own environment before spawn, proving both that the signer
path is absent from the child's mount view (``ENOENT``, not a permission
error on a visible file) and that the child cannot observe the parent's
protected environment -- against a real kernel enforcing a real Bubblewrap
mount and PID namespace, not a mock.
"""

from __future__ import annotations

import asyncio
import errno
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

# Importing butlers.core.runtime_probe_control here would pull in the
# runtime-probe-control HTTP client and its httpx dependency, which the
# production image does not install; the caller (the test module, run
# outside this exact image) is responsible for asserting this literal stays
# in lockstep with the real ``SIGNER_PATH`` constant and the payload's
# compiled-in path.
SIGNER_PATH = Path("/run/secrets/runtime_probe_control_signing_key")

_PAYLOAD_HOST_PATH = "/tmp/bu-xj2gi-signer-isolation-payload"
_PAYLOAD_SANDBOX_PATH = "/usr/local/bin/bu-xj2gi-signer-isolation-payload"
# The statically-linked payload itself needs no shared libraries, but the
# image-owned PID1 shim it execs from is dynamically linked against glibc.
# The provider manifest resolver normally supplies this closure as a side
# effect of the provider's own ldd dependencies; this harness bypasses that
# resolver, so it must mount the shim's runtime closure explicitly.
_SHIM_RUNTIME_LIBRARIES = (
    "/lib/x86_64-linux-gnu/libc.so.6",
    "/lib64/ld-linux-x86-64.so.2",
)
_MARKER_ENV_NAME = "BU_XJ2GI_PROTECTED_MARKER"
_MARKER_ENV_VALUE = "bu-xj2gi-parent-only-secret-should-never-leak"


async def _run() -> None:
    # A synthetic (non-secret) fixture is bind-mounted at the real signer
    # path in this outer container so the proof below is about mount-view
    # exclusion, not mere host-wide absence of the file.
    if not SIGNER_PATH.exists():
        raise RuntimeError(
            "test setup error: synthetic signer fixture is missing from the outer container"
        )

    sandbox = BubblewrapDashboardCLIAuthSandbox()
    sandbox._exact_image_preflight()
    identity = await sandbox._identity_pool.acquire()
    # Simulate the real Dashboard process: a live secret sits in the
    # orchestrator's own environment right up until spawn.
    os.environ[_MARKER_ENV_NAME] = _MARKER_ENV_VALUE
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
        terminated = await handle.complete_readonly()
        lines = [
            line for line in output.decode("utf-8", errors="replace").splitlines() if line.strip()
        ]
        if not terminated or process.returncode != 0 or not lines:
            raise RuntimeError(
                f"signer-isolation sandbox probe did not complete cleanly: {output!r}"
            )

        result = json.loads(lines[-1])
        if result.get("signer_opened") is not False:
            raise RuntimeError(f"sandboxed child was able to open the real signer path: {result}")
        if result.get("signer_errno") != errno.ENOENT:
            raise RuntimeError(
                f"signer open failed with an unexpected errno (want ENOENT): {result}"
            )
        if result.get("marker_in_getenv") is not False:
            raise RuntimeError(
                f"sandboxed child recovered the protected marker via getenv: {result}"
            )
        if result.get("marker_in_proc_environ") is not False:
            raise RuntimeError(
                f"sandboxed child's own kernel-tracked environ contained the protected marker: {result}"
            )

        print(
            json.dumps(
                {
                    "outer_signer_present": True,
                    "signer_absent_in_sandbox": True,
                    "signer_errno_is_enoent": True,
                    "protected_environment_isolated": True,
                },
                sort_keys=True,
            )
        )
    finally:
        del os.environ[_MARKER_ENV_NAME]
        for fd in (info_read, info_write, block_read, block_write, shim_gate_read, shim_gate_write):
            _close_fd(fd)
        if pidfd is not None:
            _close_fd(pidfd)
        if handle is not None:
            await handle.terminate()


asyncio.run(_run())
