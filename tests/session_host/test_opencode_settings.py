"""Closed native settings transport; synthetic files grant no CLI eligibility."""

import fcntl
import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from butlers.session_host.bubblewrap import BubblewrapDomain, ImageFile
from butlers.session_host.contracts import Refused
from butlers.session_host.lifecycle import InvocationOwner
from butlers.session_host.material import InvocationMaterial
from butlers.session_host.opencode import OpenCodeSettings
from butlers.session_host.profiles import Profile, ProfileFactory
from tests.session_host.test_core import request
from tests.session_host.test_profile_admission import reviewed

pytest_plugins = ("tests.session_host.test_control", "tests.session_host.test_bubblewrap")


def profile(tmp_path):
    entries = []
    for name in ("init", "opencode"):
        source = tmp_path / name
        source.write_bytes(b"synthetic-image-input-" + name.encode())
        source.chmod(0o555)
        entries.append(
            ImageFile(source, "/runtime/" + name, hashlib.sha256(source.read_bytes()).hexdigest())
        )
    return Profile(
        runtime="opencode",
        model="openai/test-model",
        image=tuple(entries),
        command=(
            "/runtime/opencode",
            "run",
            "--format",
            "json",
            "--agent",
            "build",
            "--model",
            "openai/test-model",
        ),
    )


def test_actual_profile_backend_consumes_readonly_native_settings_and_prompt(tmp_path):
    selected = profile(tmp_path)
    domain = ProfileFactory((reviewed(selected),))(selected.runtime, selected.model)
    # Only exercise the real FD-plan, never a CLI or namespace here.
    domain._bwrap = Path("/usr/bin/true")
    root = tmp_path / "stage"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    req = replace(request(), runtime=selected.runtime, model=selected.model)
    material = InvocationMaterial.stage(root, req)
    try:
        argv, inherited, *_ = domain._plan(material)
        bindings = [argv[i + 2] for i, value in enumerate(argv) if value == "--ro-bind-data"]
        assert "/invocation/opencode.jsonc" in bindings
        index = next(
            i
            for i, value in enumerate(argv)
            if value == "/invocation/opencode.jsonc" and argv[i - 2] == "--ro-bind-data"
        )
        assert argv[index - 4 : index - 1] == ["--perms", "0400", "--ro-bind-data"]
        config_fd = int(argv[index - 1])
        assert config_fd in inherited
        config = json.loads(os.pread(config_fd, 65536, 0))
        assert config["permission"] == {"*": "deny", "broker_*": "allow"}
        assert list(config["mcp"]) == ["broker"]
        assert config["mcp"]["broker"] == {
            "type": "remote",
            "url": req.broker.endpoint,
            "enabled": True,
            "oauth": False,
            "headers": {"Authorization": "Bearer " + req.broker.token},
        }
        assert (
            config["provider"]["openai"]["options"]["apiKey"]
            == "{file:/invocation/model-credential}"
        )
        env = {argv[i + 1]: argv[i + 2] for i, item in enumerate(argv) if item == "--setenv"}
        assert env == {
            "PATH": "/runtime",
            "HOME": "/tmp/home",
            "BUTLERS_MODEL_CREDENTIAL": "/invocation/model-credential",
            "OPENCODE_CONFIG": "/invocation/opencode.jsonc",
        }
        assert os.pread(domain._stdin, 65536, 0) == req.prompt.encode()
        assert fcntl.fcntl(domain._stdin, 1034) & 15 == 15
        assert fcntl.fcntl(config_fd, 1034) & 15 == 15
    finally:
        domain._close_all()
        material.discard()
    assert not list(root.iterdir())


@pytest.mark.parametrize(
    "credential",
    [
        b"",
        b"\xff",
        b" padded",
        b"padded ",
        b"a\nb",
        b"a\rb",
        b"a\0b",
        b'{"access_token":"synthetic"}',
    ],
)
def test_api_key_format_refusal_has_valid_companion_without_authority_claim(credential):
    settings = OpenCodeSettings("openai/test-model")
    valid = json.loads(settings.render(request().broker, b"synthetic-key"))
    assert valid["enabled_providers"] == ["openai"]
    assert valid["instructions"] == valid["plugin"] == []
    assert valid["share"] == "disabled" and valid["autoupdate"] is False
    with pytest.raises(Refused, match="unsupported_opencode_credential"):
        settings.render(request().broker, credential)


@pytest.mark.parametrize("mutation", ["command", "model", "pointer", "permission"])
def test_settings_subject_binds_bytes_pointers_and_command(tmp_path, monkeypatch, mutation):
    import butlers.session_host.opencode as opencode

    selected = profile(tmp_path)
    proof = reviewed(selected)
    identity = selected.subject["settings_sha256"]
    if mutation == "command":
        with pytest.raises(Refused, match="invalid_domain_profile"):
            replace(selected, command=(*selected.command, "--dangerously-skip-permissions"))
        return
    if mutation == "model":
        selected = replace(
            selected, model="openai/another", command=(*selected.command[:-1], "openai/another")
        )
    elif mutation == "pointer":
        monkeypatch.setattr(
            opencode,
            "_ENVIRONMENT",
            (*opencode._ENVIRONMENT[:-1], ("OPENCODE_CONFIG", "/tmp/untrusted")),
        )
    else:
        original = OpenCodeSettings._config

        def relaxed(self, endpoint, token):
            value = original(self, endpoint, token)
            value["permission"]["*"] = "allow"
            return value

        monkeypatch.setattr(OpenCodeSettings, "_config", relaxed)
    assert selected.subject["settings_sha256"] != identity
    # Reusing the old report against the altered subject must actually refuse.
    import json

    from tests.session_host.test_profile_admission import synthetic_report

    data = synthetic_report(selected)
    data["subject"] = proof.profile.subject | {"settings_sha256": identity}
    with pytest.raises(Refused, match="runtime_proof_refused"):
        reviewed(selected, json.dumps(data).encode())


@pytest.mark.parametrize("mutation", ["changed-bytes", "symlink", "mode"])
def test_native_config_drift_refuses_before_spawn_and_keeps_owned_cleanup(
    tmp_path, monkeypatch, mutation
):
    selected = profile(tmp_path)
    domain = ProfileFactory((reviewed(selected),))(selected.runtime, selected.model)
    domain._bwrap = Path("/usr/bin/true")
    root = tmp_path / "stage"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    material = InvocationMaterial.stage(
        root, replace(request(), runtime=selected.runtime, model=selected.model)
    )
    original = ImageFile.open
    changed = False

    def mutate(entry):
        nonlocal changed
        if not changed:
            changed = True
            config = material.directory / "opencode.jsonc"
            if mutation == "changed-bytes":
                config.write_bytes(b"{}")
            elif mutation == "symlink":
                config.unlink()
                config.symlink_to(material.directory / "mcp.json")
            else:
                config.chmod(0o644)
        return original(entry)

    monkeypatch.setattr(ImageFile, "open", mutate)
    try:
        with pytest.raises(Refused):
            domain._plan(material)
        assert changed and domain._spawn is None
    finally:
        domain._close_all()
        material.discard()
    assert not list(root.iterdir())


async def test_partial_native_write_uses_existing_owner_cleanup_and_quarantine(
    tmp_path, monkeypatch
):
    import butlers.session_host.material as staging

    selected = profile(tmp_path)
    factory = ProfileFactory((reviewed(selected),))
    root = tmp_path / "stage"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    owner = InvocationOwner(root, cleanup_timeout_s=0.2)
    original_fdopen = staging.os.fdopen
    writes = 0

    async def plan_only(domain, material):
        domain._bwrap = Path("/usr/bin/true")
        try:
            domain._plan(material)
            return b"synthetic plan complete"
        finally:
            domain._close_all()

    monkeypatch.setattr(BubblewrapDomain, "execute", plan_only)

    async def run():
        req = replace(request(), runtime=selected.runtime, model=selected.model, timeout_s=1)
        return await owner.run(req, lambda: factory(req.runtime, req.model))

    assert await run() == b"synthetic plan complete"
    assert owner.healthy and not list(root.iterdir())

    class PartialWrite:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def write(self, content):
            self.stream.write(content[:2])
            self.stream.flush()
            raise OSError("synthetic partial native write")

    def fdopen(fd, mode):
        nonlocal writes
        writes += 1
        stream = original_fdopen(fd, mode)
        return PartialWrite(stream) if writes % 4 == 0 else stream

    monkeypatch.setattr(staging.os, "fdopen", fdopen)
    with pytest.raises(Refused, match="runtime_failed"):
        await run()
    assert writes == 4 and owner.healthy and not list(root.iterdir())

    def failed_discard(material):
        raise OSError("synthetic discard refusal")

    monkeypatch.setattr(InvocationMaterial, "discard", failed_discard)
    with pytest.raises(Refused, match="stage_cleanup_failed"):
        await run()
    assert writes == 8 and not owner.healthy and len(list(root.iterdir())) == 1


@pytest.mark.parametrize("accepted", [False, True])
async def test_real_local_tls_selects_settings_before_owned_native_preparation(
    tmp_path, pki, monkeypatch, accepted
):
    from unittest.mock import patch

    from butlers.session_host.control import LocalControl, local_command
    from tests.session_host.test_control import launch

    selected = profile(tmp_path)
    factory = ProfileFactory((reviewed(selected),)) if accepted else ProfileFactory()
    root = tmp_path / "stage"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    owner = InvocationOwner(root, cleanup_timeout_s=0.2)
    server, client, pin = pki
    entered = []

    async def plan_only(domain, material):
        domain._bwrap = Path("/usr/bin/true")
        try:
            argv, *_ = domain._plan(material)
            assert "/invocation/opencode.jsonc" in argv
            entered.append(
                json.loads((material.directory / "opencode.jsonc").read_bytes())["model"]
            )
            return b"synthetic plan only"
        finally:
            domain._close_all()

    monkeypatch.setattr(BubblewrapDomain, "execute", plan_only)
    host = LocalControl(
        owner=owner,
        factory=factory,
        **server(),
        caller_sha256=pin("runner"),
        server_sha256=pin("server"),
        broker_origin="https://broker.test",
    )
    port = await host.start()
    command = launch()
    command.update(runtime=selected.runtime, model=selected.model)
    try:
        with patch.object(InvocationMaterial, "stage", wraps=InvocationMaterial.stage) as stage:
            result = await local_command(
                port,
                context=client("runner"),
                server_hostname="host.test",
                server_sha256=pin("server"),
                command=command,
            )
            assert result == {"status": "completed" if accepted else "refused"}
            assert stage.call_count == int(accepted)
            assert entered == ([selected.model] if accepted else [])
        assert owner.healthy and not list(root.iterdir())
    finally:
        await host.close()


async def test_actual_execute_passes_the_sealed_prompt_fd_as_stdin(tmp_path, monkeypatch):
    import asyncio

    selected = profile(tmp_path)
    domain = ProfileFactory((reviewed(selected),))(selected.runtime, selected.model)
    domain._bwrap = Path("/usr/bin/true")
    root = tmp_path / "stage"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    req = replace(request(), runtime=selected.runtime, model=selected.model)
    material = InvocationMaterial.stage(root, req)
    observed = []

    async def intercepted_spawn(*argv, **options):
        assert options["stdin"] != asyncio.subprocess.DEVNULL
        prompt_index = next(
            i
            for i, value in enumerate(argv)
            if value == "/invocation/prompt" and argv[i - 2] == "--ro-bind-data"
        )
        mount_fd = int(argv[prompt_index - 1])
        # Bubblewrap0.12 copy_file_data reads this descriptor to EOF before
        # exec. pread alone cannot detect a shared open-file-description offset.
        while os.read(mount_fd, 8192):
            pass
        assert os.read(options["stdin"], 65536) == req.prompt.encode()
        assert fcntl.fcntl(options["stdin"], 1034) & 15 == 15
        assert options["env"] == {} and options["start_new_session"] is True
        observed.append(True)
        raise OSError("synthetic interception: no subprocess was started")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", intercepted_spawn)
    try:
        with pytest.raises(Refused, match="sandbox_execution_failed"):
            await domain.execute(material)
        assert observed == [True]
        # Production cannot infer settlement from a failed spawn. This test
        # knows its interceptor started nothing; it grants no native evidence.
        assert await domain.settle() is False
    finally:
        domain._close_all()
        material.discard()


_TRANSPORT_PAYLOAD = r"""
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
int main(int argc, char **argv) {
    const char *config = getenv("OPENCODE_CONFIG");
    const char *home = getenv("HOME");
    if (getpid() != 1) return 78;
    if (argc != 8 || strcmp(argv[1], "run") || strcmp(argv[7], "openai/test-model")) return 70;
    if (!config || strcmp(config, "/invocation/opencode.jsonc")) return 71;
    if (!home || strcmp(home, "/tmp/home") || getenv("DATABASE_URL")) return 72;
    int fd = open(config, O_RDONLY);
    if (fd < 0) return 73;
    char data[8192]; ssize_t count = read(fd, data, sizeof(data)-1); close(fd);
    if (count <= 0) return 74;
    data[count] = 0;
    if (!strstr(data, "\"permission\":{\"*\":\"deny\",\"broker_*\":\"allow\"}")) return 75;
    fd = open(config, O_WRONLY);
    if (fd >= 0) { close(fd); return 76; }
    char prompt[128]; count = read(0, prompt, sizeof(prompt));
    const char *expected = "synthetic-prompt";
    if (count != (ssize_t)strlen(expected) || memcmp(prompt, expected, (size_t)count)) return 77;
    puts("SYNTHETIC_CONFIG_READONLY_STDIN");
    return 0;
}
"""


@pytest.fixture
def transport_image(tmp_path, native_image):
    import subprocess

    source = tmp_path / "transport.c"
    executable = tmp_path / "transport"
    source.write_text(_TRANSPORT_PAYLOAD)
    result = subprocess.run(
        ["cc", "-static", "-O2", "-Wall", "-Wextra", "-Werror", str(source), "-o", str(executable)],
        capture_output=True,
        timeout=30,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, "synthetic transport compilation failed"
    executable.chmod(0o555)
    return (
        native_image[0],
        ImageFile(
            executable, "/runtime/opencode", hashlib.sha256(executable.read_bytes()).hexdigest()
        ),
    )


async def test_native_synthetic_config_pointer_stdin_readonly_and_settlement(
    tmp_path, transport_image
):
    # This synthetic C consumer is not the actual OpenCode parser or provider.
    settings = OpenCodeSettings("openai/test-model")
    selected = Profile(
        runtime="opencode", model=settings.model, image=transport_image, command=settings.command
    )
    domain = ProfileFactory((reviewed(selected),))(selected.runtime, selected.model)
    root = tmp_path / "stage"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    owner = InvocationOwner(root)
    req = replace(request(), runtime=selected.runtime, model=selected.model, timeout_s=5)
    assert await owner.run(req, lambda: domain) == b"SYNTHETIC_CONFIG_READONLY_STDIN\n"
    assert domain.namespace_dead and domain.direct_child_reaped
    assert owner.healthy and not list(root.iterdir())


@pytest.mark.parametrize("duplicate_only", [False, True])
def test_bind_data_read_position_is_independent_of_real_child_stdin(tmp_path, duplicate_only):
    import subprocess
    import sys

    selected = profile(tmp_path)
    domain = ProfileFactory((reviewed(selected),))(selected.runtime, selected.model)
    domain._bwrap = Path("/usr/bin/true")
    root = tmp_path / "stage"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    req = replace(request(), runtime=selected.runtime, model=selected.model)
    material = InvocationMaterial.stage(root, req)
    duplicate = None
    try:
        argv, *_ = domain._plan(material)
        index = next(
            i
            for i, value in enumerate(argv)
            if value == "/invocation/prompt" and argv[i - 2] == "--ro-bind-data"
        )
        mount_fd = int(argv[index - 1])
        if duplicate_only:
            duplicate = os.dup(mount_fd)
        # Namespace-free real child: model the pinned Bubblewrap read loop on
        # the actual planned bind FD, then consume stdin using read, not pread.
        child = """
import os, sys
fd = int(sys.argv[1])
while os.read(fd, 8192):
    pass
prompt = os.read(0, 65536)
if prompt != b"synthetic-prompt":
    os.write(1, b"STDIN_EMPTY" if not prompt else b"STDIN_WRONG")
    sys.exit(44)
os.write(1, b"PROMPT_STDIN_EXACT")
"""
        result = subprocess.run(
            [sys.executable, "-c", child, str(mount_fd)],
            stdin=duplicate if duplicate_only else domain._stdin,
            pass_fds=(mount_fd,),
            close_fds=True,
            env={},
            capture_output=True,
            timeout=5,
        )
        assert result.returncode == (44 if duplicate_only else 0)
        assert result.stdout == (b"STDIN_EMPTY" if duplicate_only else b"PROMPT_STDIN_EXACT")
        assert fcntl.fcntl(domain._stdin, 1034) & 15 == 15
    finally:
        if duplicate is not None:
            os.close(duplicate)
        domain._close_all()
        material.discard()
    assert not list(root.iterdir())
