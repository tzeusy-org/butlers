"""Native kernel controls using synthetic static payloads, never a model provider."""

import asyncio
import hashlib
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

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


async def _wait_for_peer_output(peer, peer_task, *, timeout_s=5):
    ready = asyncio.create_task(peer.output_ready.wait())
    try:
        await asyncio.wait(
            {ready, peer_task}, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED
        )
        process = peer._process
        state = (
            f"ready={peer.output_ready.is_set()}, released={peer._released}, "
            f"execution_failure={getattr(peer, 'execution_failure', None)}, "
            f"output_bytes={peer.output_bytes_received}, "
            f"exit={None if process is None else process.returncode}, "
            f"namespace_dead={peer.namespace_dead}, child_reaped={peer.direct_child_reaped}"
        )
        # A ready marker cannot establish a live peer if its invocation also ended.
        if peer_task.done():
            try:
                peer_task.result()
            except BaseException as error:
                raise AssertionError(
                    f"synthetic peer failed before isolation read: {state}"
                ) from error
            raise AssertionError(f"synthetic peer exited before isolation read: {state}")
        assert ready.done(), f"synthetic peer readiness deadline expired: {state}"
        assert ready.result() is True
    finally:
        ready.cancel()
        await asyncio.gather(ready, return_exceptions=True)


class _SyntheticPeerStartup:
    """Observe only this test's fixed payload; never expose arbitrary stderr."""

    def __init__(self, monkeypatch):
        self.ready = "not_observed"
        self.ready_bytes = 0
        self.stderr_bytes = 0
        self._prefix = bytearray()
        self._digest = hashlib.sha256()
        self._drain = None
        spawn = asyncio.create_subprocess_exec

        async def observed_spawn(*argv, **kwargs):
            if argv[-2:] != ("/runtime/program", "resist"):
                return await spawn(*argv, **kwargs)
            assert argv[0] == "/usr/bin/bwrap"
            assert kwargs["stderr"] == asyncio.subprocess.DEVNULL
            process = await spawn(*argv, **{**kwargs, "stderr": asyncio.subprocess.PIPE})
            self._drain = asyncio.create_task(self._read_stderr(process.stderr))
            readexactly = process.stdout.readexactly

            async def observed_ready(length):
                try:
                    data = await readexactly(length)
                except asyncio.IncompleteReadError as error:
                    self.ready = "eof"
                    self.ready_bytes = len(error.partial)
                    raise
                except asyncio.CancelledError:
                    self.ready = "cancelled"
                    raise
                self.ready_bytes = len(data)
                self.ready = (
                    "matched" if data == b"BUTLERS_RUNTIME_CLI_SANDBOX_READY\n" else "mismatch"
                )
                return data

            monkeypatch.setattr(process.stdout, "readexactly", observed_ready)
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", observed_spawn)

    async def _read_stderr(self, stream):
        while chunk := await stream.read(4096):
            self.stderr_bytes += len(chunk)
            self._digest.update(chunk)
            self._prefix.extend(chunk[: 4096 - len(self._prefix)])

    async def finish(self):
        if self._drain is not None:
            try:
                # Called after invocation settlement. A diagnostic reader must
                # neither block cleanup nor leave an unobserved task behind.
                await asyncio.wait({self._drain}, timeout=1)
            finally:
                self._drain.cancel()
                await asyncio.gather(self._drain, return_exceptions=True)

    def summary(self):
        prefix = bytes(self._prefix)
        category = "empty" if self.stderr_bytes == 0 else "unclassified"
        for marker, label in (
            (b"bwrap: execvp /runtime/init:", "shim_exec_failed"),
            (b"bwrap: Can't create tmpfile for ", "bind_data_tmpfile_failed"),
            (b"bwrap: Can't write data to file ", "bind_data_copy_failed"),
            (b"bwrap: Can't set mode ", "bind_data_mode_failed"),
            (b"bwrap: Can't bind mount ", "bind_mount_failed"),
            (b"bwrap: Can't chdir to ", "chdir_failed"),
            (b"bwrap: setsid:", "session_setup_failed"),
            (b"runtime-cli-sandbox-init failed\n", "shim_refused"),
        ):
            if prefix.startswith(marker):
                category = label
                break
        reason = "unclassified"
        first_line = prefix.split(b"\n", 1)[0]
        for suffix, label in (
            (b": Permission denied", "permission_denied"),
            (b": Operation not permitted", "operation_not_permitted"),
            (b": No such file or directory", "not_found"),
            (b": Exec format error", "exec_format"),
            (b": Text file busy", "text_busy"),
            (b": Bad file descriptor", "bad_fd"),
        ):
            if first_line.startswith(b"bwrap: ") and first_line.endswith(suffix):
                reason = label
                break
        return {
            "ready_receipt": self.ready,
            "ready_bytes": self.ready_bytes,
            "stderr_category": category,
            "stderr_reason": reason,
            "stderr_bytes": self.stderr_bytes,
            "stderr_sha256": self._digest.hexdigest(),
            "stderr_complete": self._drain is not None
            and self._drain.done()
            and not self._drain.cancelled()
            and self._drain.exception() is None,
        }


async def test_native_cross_invocation_read_denied_and_own_material_readable(
    native_image, native_stage, monkeypatch
):
    req = replace(request(), timeout_s=5)
    owner = InvocationOwner(native_stage)

    class RecordingDomain(BubblewrapDomain):
        execution_failure = None

        async def execute(self, material):
            self.material = material
            try:
                return await super().execute(material)
            except BaseException as error:
                # Class names distinguish owner-deadline cancellation from domain refusal;
                # never retain staged material, runtime output or arbitrary exception text.
                self.execution_failure = type(error).__name__
                raise

    startup = _SyntheticPeerStartup(monkeypatch)
    peer = RecordingDomain(image=native_image, command=("/runtime/program", "resist"))
    peer_task = asyncio.create_task(owner.run(replace(request(), timeout_s=5), lambda: peer))
    try:
        try:
            await _wait_for_peer_output(peer, peer_task)
            domain = BubblewrapDomain(
                image=native_image,
                command=(
                    "/runtime/program",
                    "read",
                    str(peer.material.directory / "model-credential"),
                ),
            )
            output = await owner.run(req, lambda: domain)
            assert output == b"OWN_READ_PEER_DENIED_READONLY\n"
            assert domain.namespace_dead and domain.direct_child_reaped
            assert list(native_stage.iterdir()) == [peer.material.directory]
        finally:
            peer_task.cancel()
            await asyncio.gather(peer_task, return_exceptions=True)
            await startup.finish()
    except Exception as error:
        raise AssertionError(f"synthetic native isolation failed: {startup.summary()}") from error
    finally:
        # Also covers cancellation while gathering the peer or diagnostics.
        await startup.finish()
    assert peer.namespace_dead and peer.direct_child_reaped
    assert owner.healthy and not list(native_stage.iterdir())
    assert startup.summary()["ready_receipt"] == "matched"
    assert startup.summary()["ready_bytes"] == len(b"BUTLERS_RUNTIME_CLI_SANDBOX_READY\n")
    assert startup.summary()["stderr_complete"] is True


@pytest.mark.parametrize("receipt", ["matched", "eof", "mismatch", "cancelled"])
async def test_synthetic_startup_observes_ready_without_changing_launch(monkeypatch, receipt):
    process = SimpleNamespace(stdout=asyncio.StreamReader(), stderr=asyncio.StreamReader())
    launches = []

    async def spawn(*argv, **kwargs):
        launches.append((argv, kwargs))
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    capture = _SyntheticPeerStartup(monkeypatch)
    argv = ("/usr/bin/bwrap", "--", "/runtime/program", "resist")
    options = {
        "stderr": asyncio.subprocess.DEVNULL,
        "pass_fds": (101, 102, 103),
        "env": {},
        "close_fds": True,
        "start_new_session": True,
    }
    assert await asyncio.create_subprocess_exec(*argv, **options) is process
    assert launches == [(argv, {**options, "stderr": asyncio.subprocess.PIPE})]
    ready = b"BUTLERS_RUNTIME_CLI_SANDBOX_READY\n"
    reading = asyncio.create_task(process.stdout.readexactly(len(ready)))
    if receipt == "cancelled":
        await asyncio.sleep(0)
        reading.cancel()
        with pytest.raises(asyncio.CancelledError):
            await reading
    else:
        data = ready if receipt == "matched" else b"x" * len(ready)
        if receipt == "eof":
            data = ready[:7]
        process.stdout.feed_data(data)
        process.stdout.feed_eof()
        if receipt == "eof":
            with pytest.raises(asyncio.IncompleteReadError):
                await reading
        else:
            assert await reading == data
    stderr = b"bwrap: execvp /runtime/init: Permission denied\n" + b"synthetic-private-path" * 500
    process.stderr.feed_data(stderr)
    process.stderr.feed_eof()
    await capture.finish()
    summary = capture.summary()
    assert summary["ready_receipt"] == receipt
    assert summary["ready_bytes"] == (0 if receipt == "cancelled" else len(data))
    assert summary["stderr_category"] == "shim_exec_failed"
    assert summary["stderr_reason"] == "permission_denied"
    assert summary["stderr_bytes"] == len(stderr)
    assert summary["stderr_sha256"] == hashlib.sha256(stderr).hexdigest()
    assert summary["stderr_complete"] is True
    assert len(capture._prefix) == 4096  # a long stream is drained without retaining its body
    assert "synthetic-private-path" not in str(summary)


@pytest.mark.parametrize(
    "stderr,category",
    [
        (b"bwrap: Can't create tmpfile for /runtime/init: denied\n", "bind_data_tmpfile_failed"),
        (b"bwrap: Can't bind mount /private/source: denied\n", "bind_mount_failed"),
        (b"runtime-cli-sandbox-init failed\n", "shim_refused"),
        (b"synthetic private arbitrary diagnostic", "unclassified"),
        (b"", "empty"),
    ],
)
async def test_synthetic_startup_reports_closed_categories_only(monkeypatch, stderr, category):
    process = SimpleNamespace(stdout=asyncio.StreamReader(), stderr=asyncio.StreamReader())
    launches = []

    async def spawn(*argv, **kwargs):
        launches.append((argv, kwargs))
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    capture = _SyntheticPeerStartup(monkeypatch)
    await asyncio.create_subprocess_exec(
        "/usr/bin/bwrap", "--", "/runtime/program", "resist", stderr=asyncio.subprocess.DEVNULL
    )
    process.stderr.feed_data(stderr)
    process.stderr.feed_eof()
    await capture.finish()
    assert capture.summary()["stderr_category"] == category
    assert set(capture.summary()) == {
        "ready_receipt",
        "ready_bytes",
        "stderr_category",
        "stderr_reason",
        "stderr_bytes",
        "stderr_sha256",
        "stderr_complete",
    }
    # The isolation reader and every other command retain the original launch.
    other = ("/usr/bin/bwrap", "--", "/runtime/program", "read", "/no-peer")
    await asyncio.create_subprocess_exec(*other, stderr=asyncio.subprocess.DEVNULL)
    assert launches[-1] == (other, {"stderr": asyncio.subprocess.DEVNULL})


async def test_synthetic_startup_cancellation_reaps_its_stderr_reader(monkeypatch):
    process = SimpleNamespace(stdout=asyncio.StreamReader(), stderr=asyncio.StreamReader())

    async def spawn(*argv, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    capture = _SyntheticPeerStartup(monkeypatch)
    await asyncio.create_subprocess_exec(
        "/usr/bin/bwrap", "--", "/runtime/program", "resist", stderr=asyncio.subprocess.DEVNULL
    )
    finishing = asyncio.create_task(capture.finish())
    await asyncio.sleep(0)
    finishing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await finishing
    assert capture._drain.cancelled()
    assert capture.summary()["stderr_complete"] is False


@pytest.mark.parametrize(
    "outcome",
    ["ready", "refused", "returned", "cancelled", "ready_and_refused", "stalled", "cancel_wait"],
)
async def test_peer_readiness_observes_completion_and_always_reaps_its_waiter(outcome):
    # An inert domain supplies fixed diagnostic state; no native process is launched.
    peer = BubblewrapDomain(
        image=(ImageFile(Path("/not-opened"), "/runtime/init", "0" * 64),),
        command=("/runtime/init",),
    )
    started, finished = asyncio.Event(), asyncio.Event()

    class ObservedEvent(asyncio.Event):
        async def wait(self):
            started.set()
            try:
                return await super().wait()
            finally:
                finished.set()

    peer.output_ready = ObservedEvent()

    async def invoke():
        if outcome in {"ready", "ready_and_refused"}:
            peer.output_ready.set()
        if outcome in {"refused", "ready_and_refused"}:
            raise Refused("runtime_failed")
        if outcome == "returned":
            return b"synthetic early result"
        if outcome == "cancelled":
            raise asyncio.CancelledError
        await asyncio.Event().wait()

    peer_task = asyncio.create_task(invoke())
    waiting = asyncio.create_task(_wait_for_peer_output(peer, peer_task, timeout_s=0.05))
    try:
        if outcome == "cancel_wait":
            await asyncio.wait_for(started.wait(), timeout=1)
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
        elif outcome == "ready":
            await waiting
            assert not peer_task.done()  # readiness is useful only while the peer is live
        else:
            reason = "deadline expired" if outcome == "stalled" else "before isolation read"
            with pytest.raises(AssertionError, match=reason) as failure:
                await waiting
            assert "released=False" in str(failure.value)
            assert "output_bytes=0" in str(failure.value)
            assert "exit=None" in str(failure.value)
            if outcome in {"refused", "ready_and_refused", "cancelled"}:
                expected = asyncio.CancelledError if outcome == "cancelled" else Refused
                assert type(failure.value.__cause__) is expected
    finally:
        waiting.cancel()
        peer_task.cancel()
        await asyncio.gather(waiting, peer_task, return_exceptions=True)
    assert finished.is_set()  # the helper also owns its event waiter on every exit


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
