"""Native kernel controls using synthetic static payloads, never a model provider."""

import asyncio
import hashlib
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from butlers.session_host.bubblewrap import BubblewrapDomain, ImageFile
from butlers.session_host.contracts import Refused
from butlers.session_host.lifecycle import InvocationOwner
from scripts.session_host_ci import failure_category
from tests.session_host.test_core import request

_PAYLOAD = r"""
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <unistd.h>
int main(int argc, char **argv) {
    if (argc < 2) return 30;
    if (getpid() != 1) return 42;
    if (!strcmp(argv[1], "read")) {
        int fd = open("/invocation/model-credential", O_RDONLY);
        if (fd < 0) return 31;
        close(fd);
        fd = open(argv[2], O_RDONLY);
        if (fd >= 0) { close(fd); puts("PEER_VISIBLE"); return 0; }
        fd = open("/invocation/prompt", O_WRONLY);
        if (fd >= 0) { close(fd); return 33; }
        if (getenv("DATABASE_URL") != NULL) return 34;
        puts("OWN_READ_PEER_DENIED_READONLY");
        return 0;
    }
    if (!strcmp(argv[1], "resist")) {
        int ready[2];
        if (pipe(ready) != 0) return 38;
        signal(SIGTERM, SIG_IGN);
        pid_t child = fork();
        if (child < 0) return 35;
        if (!child) {
            close(ready[0]);
            if (setsid() < 0) return 39;
            signal(SIGTERM, SIG_IGN);
            if (write(ready[1], "1", 1) != 1) return 40;
            close(ready[1]);
            for (;;) pause();
        }
        close(ready[1]);
        char receipt;
        if (read(ready[0], &receipt, 1) != 1 || receipt != '1') return 41;
        close(ready[0]);
        puts("RESISTANT_DESCENDANT_READY"); fflush(stdout);
        for (;;) pause();
    }
    if (!strcmp(argv[1], "flood")) {
        char chunk[8192]; memset(chunk, 'x', sizeof(chunk));
        for (int i = 0; i < 192; i++)
            if (write(1, chunk, sizeof(chunk)) != sizeof(chunk)) return 37;
        return 0;
    }
    return 36;
}
"""


def _native_unavailable(reason):
    if (
        os.environ.get("CI", "").lower() in {"true", "1", "yes"}
        or os.environ.get("BUTLERS_REQUIRE_SESSION_HOST_NATIVE") == "1"
    ):
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason)


@pytest.mark.parametrize("hosted,strict", [("true", "0"), ("false", "1"), ("false", "0")])
def test_native_unavailability_cannot_skip_hosted_proof(monkeypatch, hosted, strict):
    monkeypatch.setenv("CI", hosted)
    monkeypatch.setenv("BUTLERS_REQUIRE_SESSION_HOST_NATIVE", strict)
    expected = pytest.fail.Exception if hosted == "true" or strict == "1" else pytest.skip.Exception
    with pytest.raises(BaseException, match="synthetic native setup refusal") as outcome:
        _native_unavailable("synthetic native setup refusal")
    assert type(outcome.value) is expected


@pytest.fixture(scope="module")
def native_image(tmp_path_factory):
    if not Path("/usr/bin/bwrap").exists():
        _native_unavailable("native boundary: Bubblewrap not installed")
    root = tmp_path_factory.mktemp("session-host-image")
    payload = root / "payload.c"
    payload.write_text(_PAYLOAD)
    source = Path(__file__).resolve().parents[2] / "scripts/runtime_cli_sandbox_init.c"
    for input_file, output in ((source, root / "init"), (payload, root / "program")):
        run = subprocess.run(
            [
                "cc",
                "-static",
                "-O2",
                "-Wall",
                "-Wextra",
                "-Werror",
                str(input_file),
                "-o",
                str(output),
            ],
            capture_output=True,
            timeout=30,
            env={"PATH": "/usr/bin:/bin"},
        )
        assert run.returncode == 0, "synthetic native fixture compilation failed"
        output.chmod(0o555)
    probe = subprocess.run(
        [
            "/usr/bin/bwrap",
            "--unshare-user",
            "--unshare-pid",
            "--as-pid-1",
            "--unshare-net",
            "--disable-userns",
            "--assert-userns-disabled",
            "--ro-bind",
            str(root / "program"),
            "/program",
            "--",
            "/program",
            "read",
            "/not-present",
        ],
        capture_output=True,
        timeout=10,
        env={},
    )
    # This payload intentionally cannot find staged material in the preflight;
    # exit31 demonstrates the namespace setup reached it (not a proof pass).
    if probe.returncode != 31:
        _native_unavailable(
            f"native boundary: namespace preflight refused (exit {probe.returncode}, "
            f"category={failure_category(probe.stderr)}, "
            f"stderr_sha256={hashlib.sha256(probe.stderr).hexdigest()})"
        )
    return tuple(
        ImageFile(
            root / name, f"/runtime/{name}", hashlib.sha256((root / name).read_bytes()).hexdigest()
        )
        for name in ("init", "program")
    )


@pytest.fixture
def native_stage(tmp_path):
    root = tmp_path / "private-stage"
    root.mkdir(mode=0o700)
    root.chmod(0o700)  # fixture scratch may inherit setgid from its parent
    return root


async def test_native_cross_invocation_read_denied_and_own_material_readable(
    native_image, native_stage
):
    req = replace(request(), timeout_s=5)
    owner = InvocationOwner(native_stage)

    class RecordingDomain(BubblewrapDomain):
        async def execute(self, material):
            self.material = material
            return await super().execute(material)

    peer = RecordingDomain(image=native_image, command=("/runtime/program", "resist"))
    peer_task = asyncio.create_task(owner.run(replace(request(), timeout_s=5), lambda: peer))
    async with asyncio.timeout(5):
        await peer.output_ready.wait()
    domain = BubblewrapDomain(
        image=native_image,
        command=("/runtime/program", "read", str(peer.material.directory / "model-credential")),
    )
    try:
        output = await owner.run(req, lambda: domain)
        assert output == b"OWN_READ_PEER_DENIED_READONLY\n"
        assert domain.namespace_dead and domain.direct_child_reaped
        assert list(native_stage.iterdir()) == [peer.material.directory]
    finally:
        peer_task.cancel()
        await asyncio.gather(peer_task, return_exceptions=True)
    assert peer.namespace_dead and peer.direct_child_reaped
    assert owner.healthy and not list(native_stage.iterdir())


async def test_native_resistant_detached_descendant_dies_before_material_removed(
    native_image, native_stage
):
    domain = BubblewrapDomain(image=native_image, command=("/runtime/program", "resist"))
    owner = InvocationOwner(native_stage)
    with pytest.raises(Refused, match="runtime_failed"):
        await owner.run(replace(request(), timeout_s=0.3), lambda: domain)
    assert domain._released  # actual namespace PID1 received both parent gates
    assert domain.namespace_dead and domain.direct_child_reaped
    assert owner.healthy and owner.pending_count == 0
    assert not list(native_stage.iterdir())


async def test_native_output_flood_is_bounded_and_namespace_settled(native_image, native_stage):
    domain = BubblewrapDomain(image=native_image, command=("/runtime/program", "flood"))
    owner = InvocationOwner(native_stage)
    with pytest.raises(Refused, match="runtime_failed"):
        await owner.run(replace(request(), timeout_s=5), lambda: domain)
    assert domain.output_bytes_received > 1024 * 1024
    assert domain.output_bytes_received <= 1024 * 1024 + 65536
    assert domain.namespace_dead and domain.direct_child_reaped
    assert owner.healthy and not list(native_stage.iterdir())


async def test_changed_image_refuses_before_any_native_process(native_image, native_stage):
    bad = (replace(native_image[0], sha256="0" * 64), native_image[1])
    domain = BubblewrapDomain(image=bad, command=("/runtime/program", "read", "/none"))
    owner = InvocationOwner(native_stage)
    with pytest.raises(Refused, match="runtime_failed"):
        await owner.run(request(), lambda: domain)
    assert domain._process is None and not domain._released
    assert owner.healthy and not list(native_stage.iterdir())


async def test_native_missing_namespace_observation_never_counts_leader_exit(
    native_image, native_stage, monkeypatch
):
    domain = BubblewrapDomain(image=native_image, command=("/runtime/program", "resist"))
    owner = InvocationOwner(native_stage)
    original_dead = domain._dead

    async def unavailable_observer(_deadline):
        return False

    monkeypatch.setattr(domain, "_dead", unavailable_observer)
    try:
        with pytest.raises(Refused, match="domain_cleanup_unproven"):
            await owner.run(replace(request(), timeout_s=0.3), lambda: domain)
        assert domain._released and domain.direct_child_reaped
        assert not domain.namespace_dead and not owner.healthy
        assert list(native_stage.iterdir())
    finally:
        # Independent actual observation for test-owned resource teardown,
        # never a conversion of the refused invocation into a success.
        monkeypatch.setattr(domain, "_dead", original_dead)
        assert await domain.settle() is True
        for material in owner._quarantined:
            material.discard()


async def test_native_repeated_cancellation_cannot_abandon_resistant_namespace(
    native_image, native_stage
):
    domain = BubblewrapDomain(image=native_image, command=("/runtime/program", "resist"))
    owner = InvocationOwner(native_stage)
    task = asyncio.create_task(owner.run(replace(request(), timeout_s=5), lambda: domain))
    try:
        async with asyncio.timeout(5):
            await domain.output_ready.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert domain.namespace_dead and domain.direct_child_reaped
        assert owner.healthy and owner.pending_count == 0
        assert not list(native_stage.iterdir())
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("mutate_after_verification", [False, True])
async def test_native_verified_image_is_the_image_consumed_at_launch(
    native_image, native_stage, tmp_path, monkeypatch, mutate_after_verification
):
    # Test-owned image copies: never alter another case's accepted native image.
    private_image = []
    for entry in native_image:
        target = tmp_path / (entry.source.name + "-pinned")
        target.write_bytes(entry.source.read_bytes())
        target.chmod(0o555)
        private_image.append(replace(entry, source=target))
    alternate_source = tmp_path / "alternate.c"
    alternate_source.write_text(
        '#include <stdio.h>\nint main(void){puts("MUTATED_IMAGE_EXECUTED");return 0;}\n'
    )
    alternate = tmp_path / "alternate"
    compilation = await asyncio.to_thread(
        subprocess.run,
        ["cc", "-static", "-O2", str(alternate_source), "-o", str(alternate)],
        capture_output=True,
        timeout=30,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert compilation.returncode == 0
    alternate_bytes = alternate.read_bytes()
    program = private_image[1].source
    original_spawn = asyncio.create_subprocess_exec
    handoff_seen = False

    async def change_original_then_launch(*args, **kwargs):
        nonlocal handoff_seen
        handoff_seen = True
        # This is after ImageFile.open returned and immediately before
        # Bubblewrap consumes pass_fds. In-place source replacement is owned
        # by this test and would corrupt an old live-file-FD handoff.
        if mutate_after_verification:
            program.chmod(0o755)
            program.write_bytes(alternate_bytes)
            program.chmod(0o555)
        return await original_spawn(*args, **kwargs)

    monkeypatch.setattr(
        "butlers.session_host.bubblewrap.asyncio.create_subprocess_exec",
        change_original_then_launch,
    )
    domain = BubblewrapDomain(
        image=tuple(private_image), command=("/runtime/program", "read", "/no-peer-mounted")
    )
    owner = InvocationOwner(native_stage)
    output = await owner.run(replace(request(), timeout_s=5), lambda: domain)
    assert handoff_seen
    assert output == b"OWN_READ_PEER_DENIED_READONLY\n"
    assert domain.namespace_dead and domain.direct_child_reaped
    assert owner.healthy and not list(native_stage.iterdir())
    if mutate_after_verification:
        assert program.read_bytes() == alternate_bytes


@pytest.mark.parametrize("name", ["model-credential", "prompt", "mcp.json"])
async def test_staged_authority_rewrite_refuses_before_process(native_image, native_stage, name):
    class ChangedMaterialDomain(BubblewrapDomain):
        async def execute(self, material):
            (material.directory / name).write_bytes(b"synthetic unauthorized replacement")
            return await super().execute(material)

    domain = ChangedMaterialDomain(
        image=native_image, command=("/runtime/program", "read", "/none")
    )
    owner = InvocationOwner(native_stage)
    with pytest.raises(Refused, match="runtime_failed"):
        await owner.run(replace(request(), timeout_s=5), lambda: domain)
    assert domain._process is None and not domain._released
    assert owner.healthy and not list(native_stage.iterdir())


@pytest.mark.parametrize("boundary", ["missing-bwrap", "namespace-denied"])
def test_hosted_fixture_setup_refuses_instead_of_skipping(monkeypatch, tmp_path_factory, boundary):
    monkeypatch.setenv("CI", "true")
    monkeypatch.delenv("BUTLERS_REQUIRE_SESSION_HOST_NATIVE", raising=False)
    if boundary == "missing-bwrap":
        exists = Path.exists
        monkeypatch.setattr(
            Path, "exists", lambda path: False if str(path) == "/usr/bin/bwrap" else exists(path)
        )
    else:
        run = subprocess.run

        def denied(arguments, **kwargs):
            if arguments[0] == "/usr/bin/bwrap":
                return subprocess.CompletedProcess(arguments, 77, stdout=b"", stderr=b"")
            return run(arguments, **kwargs)

        monkeypatch.setattr(subprocess, "run", denied)
    with pytest.raises(BaseException, match="native boundary:") as outcome:
        native_image.__wrapped__(tmp_path_factory)
    assert type(outcome.value) is pytest.fail.Exception
