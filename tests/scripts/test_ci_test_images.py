"""Real acquisition protocol/software controls; these never claim Docker/SQL proof."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from docker.errors import APIError, ImageNotFound
from docker.models.containers import ContainerCollection
from requests import Response
from testcontainers.core.docker_client import DockerClient

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("ci_test_images", ROOT / "scripts/ci_test_images.py")
assert spec and spec.loader
images = importlib.util.module_from_spec(spec)
spec.loader.exec_module(images)


class ImageProtocol:
    """Explicitly synthetic Docker CLI/store, backed by committed public pins."""

    def __init__(self, *, mirror_exit=0, upstream_exit=0, corrupt=False):
        self.calls = []
        self.local = {}
        self.mirror_exit = mirror_exit
        self.upstream_exit = upstream_exit
        self.corrupt = corrupt

    def __call__(self, command, timeout):
        self.calls.append(command)
        assert command[:2] == ["docker", "image"] and 0 < timeout <= 120
        operation = command[2]
        stdout = b""
        exit_code = 0
        if operation == "inspect":
            if command[3] not in self.local:
                exit_code = 1
            else:
                stdout = json.dumps([self.local[command[3]]]).encode()
        elif operation == "pull":
            assert command[3] == "--platform=linux/amd64"
            source = command[4]
            exit_code = (
                self.mirror_exit if source.startswith("mirror.gcr.io/") else self.upstream_exit
            )
            if not exit_code:
                pin = next(
                    p for p in images.IMAGES.values() if source.endswith("@" + p["manifest"])
                )
                self.local[source] = {
                    "Id": "sha256:" + "0" * 64 if self.corrupt else pin["config"],
                    "Os": "linux",
                    "Architecture": "amd64",
                    "Config": {
                        "Env": [] if pin["pg_major"] is None else ["PG_MAJOR=" + pin["pg_major"]]
                    },
                }
        elif operation == "tag":
            self.local[command[4]] = self.local[command[3]].copy()
        else:
            raise AssertionError("unexpected Docker authority")
        return subprocess.CompletedProcess(
            command, exit_code, stdout, b"unretained-private-progress"
        )


def test_preload_positions_real_docker_sdk_missing_image_and_preserves_local_consumer(monkeypatch):
    protocol = ImageProtocol()
    alias = images.IMAGES["postgres"]["alias"]
    pulls = []

    def original_pull(image, **kwargs):
        pulls.append(image)
        # Actual installed SDK catches ImageNotFound, then reaches this original
        # Hub pull boundary. This is a synthetic HTTP500/registry429 source model.
        response = Response()
        response.status_code = 500
        raise APIError(
            "closed synthetic registry refusal",
            response=response,
            explanation="toomanyrequests: synthetic public registry429",
        )

    client = SimpleNamespace(images=SimpleNamespace(pull=original_pull))
    collection = ContainerCollection(client=client)
    container = SimpleNamespace(start=lambda: None, short_id="synthetic-known-container")

    def create(image, **kwargs):
        if image not in protocol.local:
            raise ImageNotFound("closed synthetic missing alias")
        return container

    collection.create = create
    with pytest.raises(APIError):
        collection.run(alias, detach=True)
    assert pulls == [alias]
    prepared = images.prepare(["postgres", "ryuk"], execute=protocol)
    assert prepared["complete"] is True
    assert collection.run(alias, detach=True) is container
    assert pulls == [alias], "original registry must not be reached after valid preload"
    assert all(row["state"] == "verified-mirror" for row in prepared["images"])
    before = len(protocol.calls)
    reused = images.prepare(["postgres", "ryuk"], execute=protocol)
    assert all(row["state"] == "compatible-local" for row in reused["images"])
    assert all(call[2] == "inspect" for call in protocol.calls[before:])
    # Neutralization/restoration is on the same actual SDK consumer, not an
    # assertion over a mocked SQL outcome or a missing future symbol.
    del protocol.local[alias]
    with pytest.raises(APIError):
        collection.run(alias, detach=True)
    assert images.prepare(["postgres"], execute=protocol)["complete"] is True
    assert collection.run(alias, detach=True) is container
    # Invoke the ACTUAL root-installed serialized Testcontainers wrapper and
    # actual SDK consumer, with only its Docker transport/store synthesized.
    import scripts.ci_test_images as hosted_images

    monkeypatch.setattr(
        hosted_images, "prepare", lambda names: images.prepare(names, execute=protocol)
    )
    docker_client = object.__new__(DockerClient)
    client.containers = collection
    docker_client.client = client
    docker_client.find_host_network = lambda: None
    del protocol.local[alias]
    monkeypatch.setenv("GITHUB_ACTIONS", "false")
    with pytest.raises(APIError):
        docker_client.run(alias, detach=True)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert docker_client.run(alias, detach=True) is container
    # Existing disabled Ryuk is not acquired by a PG consumer; only actual
    # separate Ryuk startup invokes its own image. Mock-only calls do nothing.
    before = len(protocol.calls)
    assert docker_client.run(alias, detach=True) is container
    assert [call[2] for call in protocol.calls[before:]] == ["inspect"]
    other = "synthetic/unrelated-image"
    protocol.local[other] = {}
    before = len(protocol.calls)
    assert docker_client.run(image=other, detach=True) is container
    assert len(protocol.calls) == before
    ryuk = images.IMAGES["ryuk"]["alias"]
    del protocol.local[ryuk]
    assert docker_client.run(ryuk, detach=True) is container
    monkeypatch.setattr(hosted_images, "prepare", lambda names: None)
    del protocol.local[alias]
    with pytest.raises(APIError):
        docker_client.run(alias, detach=True)
    monkeypatch.setattr(
        hosted_images, "prepare", lambda names: images.prepare(names, execute=protocol)
    )
    assert docker_client.run(alias, detach=True) is container
    # Execute installed DockerContainer.start's real policy branches. The
    # Reaper transport is synthetic here, not a running Ryuk/container claim.
    import testcontainers.core.container as container_module

    starts = []
    monkeypatch.setattr(
        container_module.Reaper,
        "get_instance",
        lambda: starts.append(docker_client.run(ryuk, detach=True)),
    )
    subject = object.__new__(container_module.DockerContainer)
    subject.image = alias
    subject.env = {}
    subject.ports = {}
    subject.volumes = {}
    subject._command = None
    subject._name = None
    subject._network = None
    subject._kwargs = {}
    subject._wait_strategy = None
    subject.get_docker_client = lambda: docker_client
    monkeypatch.setattr(container_module.c, "ryuk_disabled", False)
    assert subject.start() is subject
    assert starts == [container]
    monkeypatch.setattr(container_module.c, "ryuk_disabled", True)
    assert subject.start() is subject
    assert starts == [container], "disabled Ryuk must not start or acquire an image"
    # The actual browser entrypoint must preload only an image the configured
    # SDK will consume. Stop at its original constructor: no SQL/browser runs.
    from testcontainers.core.config import testcontainers_config

    import scripts.test_owner_auth_browser as browser

    class ReachedOriginalContainer(Exception):
        pass

    for hosted, disabled, image, expected_ryuk in [
        (True, False, ryuk, True),
        (True, False, "synthetic/custom-reaper:1", False),
        (True, True, ryuk, False),
        (False, False, "synthetic/custom-reaper:1", False),
        (True, False, ryuk, True),
    ]:
        calls = []

        def preload(command, **kwargs):
            calls.append(command)
            assert kwargs == {"check": True, "timeout": 365}
            if image != ryuk and "--ryuk" in command:
                raise AssertionError("unused default Ryuk must not block a custom consumer")
            return subprocess.CompletedProcess(command, 0)

        def stop_container(*args, **kwargs):
            raise ReachedOriginalContainer()

        with (
            patch.dict(os.environ, {"GITHUB_ACTIONS": "true" if hosted else "false"}),
            patch.object(testcontainers_config, "ryuk_image", image),
            patch.object(testcontainers_config, "_ryuk_disabled", disabled),
            patch.object(browser.subprocess, "run", preload),
            patch.object(browser, "PostgresContainer", stop_container),
            patch.object(browser.secrets, "token_urlsafe", lambda *args: "synthetic-unused"),
            pytest.raises(ReachedOriginalContainer),
        ):
            browser.main()
        assert len(calls) == int(hosted)
        if calls:
            assert ("--ryuk" in calls[0]) is expected_ryuk


def test_preload_refuses_corrupt_incompatible_unavailable_inputs_and_bounds_fallback(monkeypatch):
    fallback = ImageProtocol(mirror_exit=1)
    assert (
        images.prepare(["postgres"], execute=fallback)["images"][0]["state"] == "verified-upstream"
    )
    for alias in images.IMAGES:
        fresh = ImageProtocol()
        assert images.prepare([alias], execute=fresh)["complete"] is True
        assert len([c for c in fresh.calls if c[2] == "pull"]) == 1
    for protocol, category in [
        (ImageProtocol(mirror_exit=1, upstream_exit=1), "required-image-unavailable"),
        (ImageProtocol(corrupt=True), "pulled-content-incompatible"),
    ]:
        with pytest.raises(images.AcquisitionError) as failure:
            images.prepare(["postgres"], execute=protocol)
        diagnostic = json.loads(str(failure.value))
        assert diagnostic["complete"] is False
        assert diagnostic["images"][0]["failure_kind"] == category
        assert "unretained-private-progress" not in str(failure.value)
        assert not any(c[2] == "tag" for c in protocol.calls)
        assert len([c for c in protocol.calls if c[2] == "pull"]) == (
            2 if not protocol.corrupt else 1
        )
    pin = images.IMAGES["postgres"]
    valid = {
        "Id": pin["config"],
        "Os": "linux",
        "Architecture": "amd64",
        "Config": {"Env": ["PG_MAJOR=17"]},
    }
    for mutation in [
        {"Id": "sha256:" + "0" * 64},
        {"Architecture": "arm64"},
        {"Os": "windows"},
        {"Config": {"Env": ["PG_MAJOR=16"]}},
        {"Config": {"Env": "PG_MAJOR=17"}},
        {"Config": {"Env": ["PG_MAJOR=17", "PG_MAJOR=16"]}},
    ]:
        value = {**valid, **mutation}
        assert not images.matching_image(
            subprocess.CompletedProcess([], 0, json.dumps([value])), pin
        )
    for raw in ("{", "{}", "[]", "[true]", '[{"Id":"wrong","Id":"' + pin["config"] + '"}]'):
        assert not images.matching_image(subprocess.CompletedProcess([], 0, raw), pin)
    assert images.matching_image(subprocess.CompletedProcess([], 0, json.dumps([valid])), pin)
    corrupt_local = ImageProtocol()
    corrupt_local.local[pin["alias"]] = {**valid, "Id": "sha256:" + "0" * 64}
    assert images.prepare(["postgres"], execute=corrupt_local)["complete"] is True
    assert corrupt_local.local[pin["alias"]]["Id"] == pin["config"]
    with monkeypatch.context() as context:
        context.setattr(images.platform, "machine", lambda: "aarch64")
        with pytest.raises(images.AcquisitionError, match="unsupported-runner-platform"):
            images.prepare(["postgres"], execute=ImageProtocol())
    assert images.prepare(["postgres"], execute=ImageProtocol())["complete"] is True
    with pytest.raises(images.AcquisitionError, match="invalid-required-image-set"):
        images.prepare(["postgres", "postgres"], execute=ImageProtocol())
    ticks = iter([0, images.TOTAL_SECONDS])
    with pytest.raises(images.AcquisitionError, match="acquisition-deadline"):
        images.prepare(["postgres"], execute=ImageProtocol(), now=lambda: next(ticks))


def test_preload_timeout_reaps_only_its_owned_process_group(tmp_path):
    pid_file = tmp_path / "owned-child.pid"
    source = (
        "import subprocess,sys,time;from pathlib import Path;"
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
        "Path(sys.argv[1]).write_text(str(p.pid));time.sleep(30)"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        images.run([sys.executable, "-c", source, str(pid_file)], timeout=0.5)
    pid = int(pid_file.read_text())
    # A terminated orphan may briefly be a zombie awaiting the host's reaper;
    # it cannot remain a running pipe holder or extend this invocation's bound.
    assert not _process_is_running(pid)
    assert os.getpid() != pid
    healthy = images.run([sys.executable, "-c", "print('healthy-owned-child')"], timeout=2)
    assert healthy.returncode == 0 and healthy.stdout == b"healthy-owned-child\n"
    # A real unrelated session remains alive across both owned-group outcomes.
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time;time.sleep(30)"], start_new_session=True
    )
    try:
        for mode in ("success", "timeout"):
            descendant_pid = tmp_path / (mode + "-descendant.pid")
            ready = tmp_path / (mode + "-ready")
            child = (
                "import signal,time,sys;from pathlib import Path;"
                "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                "Path(sys.argv[1]).write_text('ready');time.sleep(30)"
            )
            parent = (
                "import subprocess,sys,time;from pathlib import Path;"
                "p=subprocess.Popen([sys.executable,'-c',sys.argv[2],sys.argv[3]],"
                "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
                "deadline=time.monotonic()+3;"
                'exec("while not Path(sys.argv[3]).exists():\\n'
                ' if time.monotonic()>deadline: raise SystemExit(2)\\n time.sleep(.01)");'
                "Path(sys.argv[1]).write_text(str(p.pid));"
                + ("sys.exit(0)" if mode == "success" else "time.sleep(30)")
            )
            command = [sys.executable, "-c", parent, str(descendant_pid), child, str(ready)]
            try:
                if mode == "success":
                    assert images.run(command, timeout=0.5).returncode == 0
                else:
                    with pytest.raises(subprocess.TimeoutExpired):
                        images.run(command, timeout=0.5)
                assert ready.is_file()
                descendant = int(descendant_pid.read_text())
                # SIGKILL delivery may precede the final kernel state change.
                # This only observes termination; it never retries acquisition
                # or extends the actual helper's unchanged timeout/reserve.
                deadline = time.monotonic() + 1
                while _process_is_running(descendant) and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert not _process_is_running(descendant)
                assert unrelated.poll() is None
            finally:
                if descendant_pid.is_file():
                    descendant = int(descendant_pid.read_text())
                    if _process_is_running(descendant):
                        # Failed/neutralized controls clean only their own
                        # descendant, so a falsification cannot leak a child.
                        os.kill(descendant, 9)
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=2)


def _process_is_running(pid):
    # Read once. A concurrent reaper can remove /proc between any two calls;
    # disappearance is a terminated process, while other read errors remain red.
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state not in {"Z", "X"}
