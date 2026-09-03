"""Concurrent adversarial peer-isolation probe for two exact-image sandboxes.

Launches two real Bubblewrap-sandboxed children under the exact production
image and proves, from inside the attacker child, that it cannot read or
write the victim child's staged HOME by its exact real path and cannot
signal-probe or enumerate the victim's real outer PID (REQ-core-credentials-002,
task 3.6b). See runtime_cli_sandbox_peer_isolation_probe.c for the in-sandbox
adversarial actions this drives.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path

from butlers.cli_auth.sandbox_platform import (
    BubblewrapDashboardCLIAuthSandbox,
    SandboxIdentity,
    SandboxStage,
    _BubblewrapDeviceAuthHandle,
    _close_fd,
    _outer_identity_preexec,
    _read_bubblewrap_info,
    build_bubblewrap_launch_plan,
)

_PROBE_PATH = "/tmp/runtime-cli-sandbox-peer-isolation-probe"
_VICTIM_SLEEP_SECONDS = "6"


def _shim_ldd_closure(shim_path: Path) -> tuple[Path, ...]:
    """Resolve the PID1 shim's own dynamic-linker closure.

    Production providers (codex, opencode) are themselves dynamically linked
    against the same base-image glibc the shim needs, so their manifest
    closures incidentally satisfy the shim's own ld.so/libc requirement. This
    probe's payload is a static binary with no closure of its own, so the
    shim's requirement has to be resolved and bound explicitly here -- without
    it the kernel's own execve() of the shim fails with ENOENT for the
    missing interpreter, which bwrap reports as though the shim itself were
    absent.
    """
    result = subprocess.run(["ldd", str(shim_path)], check=False, capture_output=True, text=True)
    if result.returncode != 0:
        return ()
    dependencies: list[Path] = []
    for line in result.stdout.splitlines():
        rendered = line.strip()
        if not rendered or "not a dynamic executable" in rendered:
            continue
        if "=>" in rendered:
            _, _, remainder = rendered.partition("=>")
            candidate = remainder.strip().split(" ", 1)[0]
        else:
            candidate = rendered.split(" ", 1)[0]
        if candidate.startswith("/"):
            dependencies.append(Path(candidate))
    return tuple(dependencies)


async def _launch(
    sandbox: BubblewrapDashboardCLIAuthSandbox,
    identity: SandboxIdentity,
    stage: SandboxStage,
    command: tuple[str, ...],
    readonly_inputs: tuple[Path, ...],
) -> tuple[int, _BubblewrapDeviceAuthHandle]:
    """Run the exact production handshake up to the shim's ready line."""
    process = None
    pidfd: int | None = None
    info_read = info_write = block_read = block_write = shim_gate_read = shim_gate_write = None
    try:
        info_read, info_write = os.pipe2(os.O_CLOEXEC)
        block_read, block_write = os.pipe2(os.O_CLOEXEC)
        shim_gate_read, shim_gate_write = os.pipe2(os.O_CLOEXEC)
        plan = build_bubblewrap_launch_plan(
            bwrap_path=sandbox._bwrap_path,
            shim_path=sandbox._shim_path,
            identity=identity,
            stage_home=stage.path,
            command=command,
            readonly_inputs=readonly_inputs,
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

        pid = await asyncio.wait_for(_read_bubblewrap_info(info_read), timeout=5)
        pidfd = sandbox._pidfd_open(pid, 0)
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

        ready = await asyncio.wait_for(process.stdout.readline(), timeout=5)
        if ready != b"BUTLERS_RUNTIME_CLI_SANDBOX_READY\n":
            raise RuntimeError(
                f"namespace PID1 did not acknowledge the exact gate release: {ready!r}"
            )
        return pid, handle
    finally:
        for fd in (info_read, info_write, block_read, block_write, shim_gate_read, shim_gate_write):
            _close_fd(fd)
        if pidfd is not None:
            _close_fd(pidfd)


async def _run() -> None:
    sandbox = BubblewrapDashboardCLIAuthSandbox()
    sandbox._exact_image_preflight()

    readonly_inputs = (Path(_PROBE_PATH), *_shim_ldd_closure(sandbox._shim_path))

    identity_b = await sandbox._identity_pool.acquire()
    identity_a = await sandbox._identity_pool.acquire()
    stage_b = sandbox._stage_factory(identity_b)
    stage_a = sandbox._stage_factory(identity_a)

    secret_path = stage_b.path / "victim-secret.txt"
    secret_path.write_text("top-secret-peer-isolation-proof\n", encoding="utf-8")
    os.chown(secret_path, identity_b.uid, identity_b.gid)
    os.chmod(secret_path, 0o600)

    handle_a: _BubblewrapDeviceAuthHandle | None = None
    handle_b: _BubblewrapDeviceAuthHandle | None = None
    try:
        peer_pid, handle_b = await _launch(
            sandbox,
            identity_b,
            stage_b,
            (_PROBE_PATH, "victim", _VICTIM_SLEEP_SECONDS),
            readonly_inputs,
        )
        _, handle_a = await _launch(
            sandbox,
            identity_a,
            stage_a,
            (_PROBE_PATH, "attacker", str(stage_b.path), str(peer_pid)),
            readonly_inputs,
        )
        output = await asyncio.wait_for(handle_a.process.stdout.read(), timeout=10)
        terminated = await handle_a.complete_readonly()
        if not terminated or handle_a.process.returncode != 0 or not output:
            raise RuntimeError("attacker sandbox probe did not complete")
        handle_a = None

        result = json.loads(output)
        result["host_confirms_no_attacker_write"] = not (
            stage_b.path / "attacker-write.txt"
        ).exists()
        print(json.dumps(result, sort_keys=True))
    finally:
        if handle_a is not None:
            await handle_a.terminate()
        if handle_b is not None:
            await handle_b.terminate()


asyncio.run(_run())
