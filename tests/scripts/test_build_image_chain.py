"""Image protocol controls use synthetic Docker metadata, never OCI execution credit."""

import hashlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "_test_image_chain", ROOT / "scripts/runtime_clis/build_image_chain.py"
)
assert SPEC is not None and SPEC.loader is not None
producer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(producer)


class Commands:
    def __init__(self, failure=None):
        self.calls = []
        self.failure = failure

    def run(self, argv, *, cwd, timeout=1800):
        self.calls.append((argv, timeout))
        if argv[0] in {"git", "bash"}:
            return subprocess.check_output(argv, cwd=cwd, text=True)
        if argv[:3] == ["docker", "buildx", "build"]:
            metadata = Path(argv[argv.index("--metadata-file") + 1])
            if self.failure == metadata.stem:
                raise RuntimeError("positioned build failure")
            metadata.write_text(
                json.dumps(
                    {
                        "containerimage.digest": "sha256:" + "a" * 64,
                        "containerimage.config.digest": "sha256:" + "b" * 64,
                    }
                )
            )
            return ""
        if argv[:3] == ["docker", "image", "inspect"]:
            return json.dumps(
                [
                    {
                        "Id": "sha256:" + ("c" if self.failure == "loaded" else "b") * 64,
                        "Os": "linux",
                        "Architecture": "amd64",
                    }
                ]
            )
        raise AssertionError(argv)


@pytest.fixture
def recipe(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "--quiet", str(project)], check=True)
    files = {
        *producer.BASE_INPUTS,
        "Dockerfile",
        "scripts/base-image-input-fingerprint.sh",
        producer.REGISTRY_INPUT,
    }
    for name in files:
        target = project / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    subprocess.run(["git", "add", "."], cwd=project, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "fixture",
        ],
        cwd=project,
        check=True,
    )
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    helpers = producer.shared()
    frozen = tmp_path / "context-source"
    identity = helpers.freeze(project, frozen, Commands())
    contexts = tmp_path / "contexts"
    contexts.mkdir()
    helpers.write_json(contexts / "source-inputs.json", identity)
    for name in ("system", "runtime", "python/default", "python/live-listener", "go"):
        (contexts / name).mkdir(parents=True)
    (contexts / "runtime/inputs.json").write_bytes(
        (project / "deploy/runtime-clis/runtime-inputs.json").read_bytes()
    )
    helpers.write_json(
        contexts / "verification.json",
        {
            "schema": "butlers.image-contexts/v1",
            "status": "CLOSED_CONTEXTS",
            "source_inputs_sha256": helpers.digest(contexts / "source-inputs.json"),
            "runtime_inputs_sha256": helpers.digest(contexts / "runtime/inputs.json"),
            "producers": [build.name for build in helpers.builds()],
        },
    )
    events = []

    class Registry:
        def __init__(self, commands, source, image):
            assert image == json.loads((project / producer.REGISTRY_INPUT).read_text())["image"]

        def start(self):
            events.append("start")
            return "127.0.0.1:32321/butlers-base"

        def cleanup(self):
            events.append("cleanup")

    monkeypatch.setattr(producer, "Registry", Registry)
    monkeypatch.setattr(producer, "local_manifest", lambda reference: events.append(reference))
    return project, contexts, revision, helpers, events


@pytest.mark.parametrize(
    "failure", [None, "base", "live-listener-production", "loaded", "manifest"]
)
def test_complete_chain_requires_real_manifest_all_profiles_and_owned_cleanup(
    recipe, tmp_path, monkeypatch, failure
):
    project, contexts, revision, helpers, events = recipe
    commands = Commands(failure=failure)
    work, output = tmp_path / "work", tmp_path / "output"
    work.mkdir()
    if failure == "manifest":

        def refuse(_reference):
            raise ValueError("positioned manifest refusal")

        monkeypatch.setattr(producer, "local_manifest", refuse)
    if failure:
        with pytest.raises(
            (RuntimeError, ValueError), match="build failure|identity|manifest refusal"
        ):
            producer.build(project, contexts, work, output, revision, helpers, commands)
        assert not output.exists()
        assert events[-1] == "cleanup"
        assert not list(tmp_path.glob("image-chain-*"))
        return
    result = producer.build(project, contexts, work, output, revision, helpers, commands)
    assert result["status"] == "BUILT_LOCAL_IMAGES"
    assert result["base_manifest"] == "sha256:" + "a" * 64
    assert events == ["start", "127.0.0.1:32321/butlers-base@sha256:" + "a" * 64, "cleanup"]
    builds = [argv for argv, _ in commands.calls if argv[:3] == ["docker", "buildx", "build"]]
    assert len(builds) == 5
    base, *apps = builds
    assert "runtime-artifacts=" + str(contexts / "runtime") in base
    assert "system-artifacts=" + str(contexts / "system") in base
    assert "butlers.base.input_sha=" + result["base_input_sha256"] in base
    assert all("--load" in argv and "go-artifacts=" + str(contexts / "go") in argv for argv in apps)
    assert all("BUTLERS_BASE_IMAGE=" + events[1] in argv for argv in apps)
    assert {argv[argv.index("--target") + 1] for argv in apps} == {"production", "development"}
    assert {arg for argv in apps for arg in argv if arg.startswith("EXTRAS=")} == {
        "EXTRAS=",
        "EXTRAS=live-listener",
    }
    assert result["variants"]["default-production"]["tag"] == "butlers-app:" + revision
    assert len(result["variants"]) == 4
    assert json.loads((output / "verification.json").read_text()) == result


@pytest.mark.parametrize("drift", ["source", "runtime", "verification", "context-link", "revision"])
def test_context_or_revision_mismatch_refuses_before_registry_with_healthy_companion(
    recipe, tmp_path, drift
):
    project, contexts, revision, helpers, events = recipe
    commands = Commands()
    frozen = tmp_path / "healthy"
    identity = helpers.freeze(project, frozen, commands)
    producer.contexts_identity(frozen, contexts, identity, helpers)
    if drift == "source":
        (project / "Dockerfile").write_text("changed source")
    elif drift == "runtime":
        (contexts / "runtime/inputs.json").write_text("foreign bytes")
    elif drift == "verification":
        (contexts / "verification.json").write_text("{}")
    elif drift == "context-link":
        (contexts / "go").rmdir()
        (contexts / "go").symlink_to(tmp_path / "healthy")
    else:
        revision = "f" * 40
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(ValueError, match="contexts|context|manifest|revision"):
        producer.build(project, contexts, work, tmp_path / "output", revision, helpers, commands)
    assert events == []


@pytest.mark.parametrize("status,wrong", [(200, False), (302, False), (200, True)])
def test_owned_manifest_reader_binds_actual_response_bytes_and_refuses_redirect(status, wrong):
    payload = b'{"schemaVersion":2,"mediaType":"application/vnd.oci.image.manifest.v1+json"}'
    digest = hashlib.sha256(payload).hexdigest()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(status)
            self.send_header("Docker-Content-Digest", "sha256:" + digest)
            self.send_header("Location", "/forbidden-follow")
            self.end_headers()
            self.wfile.write(b"wrong bytes" if wrong else payload)

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        reference = f"127.0.0.1:{server.server_port}/butlers-base@sha256:{digest}"
        if status != 200 or wrong:
            with pytest.raises(ValueError, match="registry"):
                producer.local_manifest(reference)
        else:
            assert producer.local_manifest(reference) == "sha256:" + digest
        assert requests == ["/v2/butlers-base/manifests/sha256:" + digest]
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_nightly_consumes_closed_contexts_before_unchanged_installed_export():
    import yaml

    job = yaml.safe_load((ROOT / ".github/workflows/nightly.yml").read_text())["jobs"][
        "exact-image-sandbox"
    ]
    steps = job["steps"]
    names = [step.get("name") for step in steps]
    assert names.index("Produce complete closed image contexts") < names.index(
        "Build butlers-app exact image"
    )
    assert names.index("Build butlers-app exact image") < names.index(
        "Export installed runtime image files"
    )
    context = next(
        step["run"]
        for step in steps
        if step.get("name") == "Produce complete closed image contexts"
    )
    chain = next(
        step["run"] for step in steps if step.get("name") == "Build butlers-app exact image"
    )
    assert "scripts/runtime_clis/produce_image_contexts.py" in context
    assert "scripts/runtime_clis/build_image_chain.py" in chain
    assert (
        '--revision "${GIT_SHA}"' in chain
        and '--contexts "${IMAGE_SCRATCH}/image-contexts-' in chain
    )
    # bu-kiuh8v.24: the producers' work, contexts and chain output, and docker's own data, live on
    # the runner's larger /mnt disk (falling back to RUNNER_TEMP only when /mnt is too small).
    assert '--work "${IMAGE_SCRATCH}/image-context-work-' in context
    assert '--output "${IMAGE_SCRATCH}/image-contexts-' in context
    assert "RUNNER_TEMP" not in context + chain
    move = next(
        step["run"]
        for step in steps
        if step.get("name") == "Keep docker and image scratch on the larger /mnt disk"
    )
    assert names.index("Keep docker and image scratch on the larger /mnt disk") < names.index(
        "Produce complete closed image contexts"
    )
    assert '"data-root": "/mnt/docker"' in move and "IMAGE_SCRATCH=/mnt/butlers-image" in move
    assert "IMAGE_SCRATCH=${RUNNER_TEMP}" in move  # the small-/mnt fallback
    assert "docker system df" in context  # the disk sampler that shows where space went
    assert "butlers-base:latest" not in context + chain
    row = next(
        row
        for row in json.loads((ROOT / "scripts/ci-job-timeouts.json").read_text())["jobs"]
        if row["workflow"] == ".github/workflows/nightly.yml"
        and row["job"] == "exact-image-sandbox"
    )
    assert row["installed_timeout_minutes"] == job["timeout-minutes"] == 330
    assert row["source_build_envelope"]["codex_actual_seconds"] == 5111.299
    assert row["successful_sample_n"] == 0 and "provisional" in row["state"]


@pytest.mark.parametrize("foreign", [False, True])
def test_registry_cleanup_requires_exact_owner_and_never_removes_foreign_container(
    tmp_path, foreign
):
    # Real child commands exercise the cleanup transaction, using a finite fake
    # Docker protocol ledger. This is neither Docker nor container execution.
    commands = type("Commands", (), {})()
    commands.boundary = types.ModuleType("boundary")
    path = ROOT / "scripts/runtime_clis/produce_default_plugin_artifact.py"
    exec(compile(path.read_bytes(), str(path), "exec"), commands.boundary.__dict__)
    commands.environment = {"PATH": str(tmp_path), "HOME": os.environ["HOME"]}
    registry = producer.Registry(commands, tmp_path, "unused")
    identifier = "d" * 64
    value = {
        "Id": identifier,
        "Name": "/" + registry.name,
        "Config": {"Labels": {producer.OWNER_LABEL: "foreign" if foreign else registry.owner}},
    }
    marker = tmp_path / "removed"
    executable = tmp_path / "docker"
    executable.write_text(
        f"#!{sys.executable}\nimport pathlib,sys\n"
        f"value={json.dumps(json.dumps([value]))}\n"
        f"if sys.argv[1]=='ps': print({identifier!r})\n"
        "elif sys.argv[1]=='inspect': print(value)\n"
        f"elif sys.argv[1]=='rm': pathlib.Path({str(marker)!r}).write_text('removed')\n"
        "else: raise SystemExit(4)\n"
    )
    executable.chmod(0o755)
    if foreign:
        with pytest.raises(ValueError, match="foreign registry"):
            registry.cleanup()
        assert not marker.exists()
    else:
        registry.cleanup()
        assert marker.read_text() == "removed"


@pytest.mark.parametrize("number", [signal.SIGINT, signal.SIGTERM])
def test_cleanup_defers_cancellation_until_owned_remove_child_settles(tmp_path, number):
    started, removed = tmp_path / "started", tmp_path / "removed"
    executable = tmp_path / "docker"
    executable.write_text(
        f"#!{sys.executable}\nimport json,os,pathlib,sys,time\n"
        "if sys.argv[1]=='ps': print('d'*64)\n"
        "elif sys.argv[1]=='inspect': print(pathlib.Path('container.json').read_text())\n"
        f"elif sys.argv[1]=='rm':\n pathlib.Path({str(started)!r}).write_text(str(os.getpid()))\n"
        f" time.sleep(.3)\n pathlib.Path({str(removed)!r}).write_text('removed')\n"
        "else: raise SystemExit(4)\n"
    )
    executable.chmod(0o755)
    driver = tmp_path / "driver.py"
    driver.write_text(f"""
import importlib.util,json,os,pathlib,signal,types
spec=importlib.util.spec_from_file_location('_chain_signal', {str(Path(producer.__file__).resolve())!r})
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
boundary=types.ModuleType('_boundary')
path=pathlib.Path({str(ROOT / "scripts/runtime_clis/produce_default_plugin_artifact.py")!r})
exec(compile(path.read_bytes(),str(path),'exec'),boundary.__dict__)
commands=types.SimpleNamespace(boundary=boundary,environment={{'PATH':{str(tmp_path)!r},'HOME':os.environ['HOME']}})
registry=module.Registry(commands,pathlib.Path({str(tmp_path)!r}),'unused')
pathlib.Path({str(tmp_path / "container.json")!r}).write_text(json.dumps([{{'Id':'d'*64,'Name':'/'+registry.name,'Config':{{'Labels':{{module.OWNER_LABEL:registry.owner}}}}}}]))
try:
    with boundary.owner_signals():
        registry.cleanup()
except boundary.OwnerSignal as error:
    signal.signal(error.number,signal.SIG_DFL)
    os.kill(os.getpid(),error.number)
""")
    child = subprocess.Popen([sys.executable, "-B", str(driver)])
    try:
        deadline = time.monotonic() + 5
        while not started.exists() and time.monotonic() < deadline and child.poll() is None:
            time.sleep(0.01)
        assert started.exists(), (
            "the actual cleanup remove child must be active before cancellation"
        )
        remove_pid = int(started.read_text())
        child.send_signal(number)
        assert child.wait(timeout=5) == -number
        assert removed.exists(), "owned removal must finish before cancellation settles"
        assert removed.read_text() == "removed"
        with pytest.raises(ProcessLookupError):
            os.kill(remove_pid, 0)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()
