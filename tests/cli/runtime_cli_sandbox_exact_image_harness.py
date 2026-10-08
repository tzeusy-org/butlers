"""Credential-free exact-image probe for the Dashboard CLI-auth sandbox."""

from __future__ import annotations

import asyncio
import json
import os

from butlers.cli_auth.registry import PROVIDERS
from butlers.cli_auth.sandbox_platform import (
    BubblewrapDashboardCLIAuthSandbox,
    SandboxStage,
    _BubblewrapDeviceAuthHandle,
    _close_fd,
    _outer_identity_preexec,
    _read_bubblewrap_info,
    build_bubblewrap_launch_plan,
    resolve_readonly_runtime_inputs,
    resolve_shim_runtime_inputs,
)


def _closed_launch_diagnostic(content: bytes) -> dict[str, str]:
    """Classify only public executable/error vocabulary; never reflect bytes."""
    lowered = content.lower()
    origin = "bubblewrap" if content.startswith(b"bwrap:") else "unknown"
    if content.startswith(b"runtime-cli-sandbox-init failed"):
        origin = "shim"
    error = "unknown"
    for phrase, label in (
        (b"permission denied", "permission-denied"),
        (b"operation not permitted", "operation-not-permitted"),
        (b"no such file or directory", "missing-path"),
    ):
        if phrase in lowered:
            error = label
            break
    operation = "unknown"
    for phrase, label in (
        (b"mount", "mount"),
        (b"execvp", "exec"),
        (b"uid map", "uid-map"),
        (b"gid map", "gid-map"),
        (b"chdir", "chdir"),
        (b"namespace", "namespace"),
    ):
        if phrase in lowered:
            operation = label
            break
    return {"origin": origin, "os_error": error, "operation": operation}


async def _run() -> None:
    provider = PROVIDERS["codex"]
    invocation = resolve_readonly_runtime_inputs(provider, (provider.binary(), "--version"))
    sandbox = BubblewrapDashboardCLIAuthSandbox()
    sandbox._exact_image_preflight()
    shim_readonly_inputs = resolve_shim_runtime_inputs(sandbox._shim_path)
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
            command=invocation.command,
            readonly_inputs=invocation.readonly_inputs,
            shim_readonly_inputs=shim_readonly_inputs,
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
        try:
            pre_release = await asyncio.wait_for(process.stdout.readline(), timeout=0.1)
        except TimeoutError:
            pass
        else:
            # A closed pipe and actual output are distinct failures. Never emit
            # the child bytes: stderr is merged here and may contain secrets.
            try:
                await asyncio.wait_for(process.wait(), timeout=0.2)
            except TimeoutError:
                pass
            raise RuntimeError(
                json.dumps(
                    {
                        "probe": "exact-image-release-v1",
                        "phase": "before-release",
                        "observation": "early-eof" if pre_release == b"" else "nonempty-output",
                        "process_state": ("running" if process.returncode is None else "exited"),
                        "exit_kind": (
                            "unknown"
                            if process.returncode is None
                            else "zero"
                            if process.returncode == 0
                            else "shim-refused"
                            if process.returncode == 125
                            else "nonzero"
                        ),
                        **_closed_launch_diagnostic(pre_release),
                    },
                    sort_keys=True,
                )
            )

        sandbox._release_payload(block_write)
        _close_fd(block_write)
        block_write = None
        sandbox._release_payload(shim_gate_write)
        _close_fd(shim_gate_write)
        shim_gate_write = None
        ready = await asyncio.wait_for(process.stdout.readline(), timeout=5)
        if ready != b"BUTLERS_RUNTIME_CLI_SANDBOX_READY\n":
            raise RuntimeError("namespace PID1 did not acknowledge the exact gate release")

        output = await asyncio.wait_for(process.stdout.read(), timeout=10)
        terminated = await handle.complete_readonly()
        if not terminated or process.returncode != 0 or not output:
            raise RuntimeError("exact-image sandbox probe did not complete")
        print(json.dumps({"launch": "ok", "termination": "proven"}, sort_keys=True))
    finally:
        for fd in (info_read, info_write, block_read, block_write, shim_gate_read, shim_gate_write):
            _close_fd(fd)
        if pidfd is not None:
            _close_fd(pidfd)
        if handle is not None:
            await handle.terminate()


asyncio.run(_run())
