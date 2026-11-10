"""Source/orchestration controls; fake Docker exports are not image conformance."""

import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "_test_image_contexts", ROOT / "scripts/runtime_clis/produce_image_contexts.py"
)
assert SPEC is not None and SPEC.loader is not None
producer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = producer
SPEC.loader.exec_module(producer)


class SyntheticCommands:
    def __init__(self, *, failure=None):
        self.calls = []
        self.failure = failure

    def run(self, argv, *, cwd, timeout=1800):
        self.calls.append((argv, cwd, timeout))
        if argv[:2] == ["git", "ls-files"]:
            return subprocess.check_output(argv, cwd=cwd, text=True)
        if argv[:3] == ["docker", "buildx", "build"]:
            destination = Path(argv[argv.index("--output") + 1].split("dest=", 1)[1])
            if self.failure == destination.name:
                raise RuntimeError("positioned producer failure")
            destination.mkdir()
            (destination / "synthetic-output").write_text("not an installed graph")
        elif "close" in argv:
            destination = Path(argv[argv.index("--output") + 1])
            destination.mkdir()
            manifest = Path(argv[argv.index("--manifest") + 1]).read_bytes()
            (destination / "inputs.json").write_bytes(
                b"wrong manifest" if self.failure == "closure" else manifest
            )
        else:
            destination = Path(argv[argv.index("--output") + 1])
            destination.mkdir()
        return ""


@pytest.fixture
def project(tmp_path):
    path = tmp_path / "project"
    path.mkdir()
    subprocess.run(["git", "init", "--quiet", str(path)], check=True)
    for name, content in {
        "src/retained.py": b"complete tracked source\n",
        "deploy/runtime-clis/runtime-inputs.json": b'{"expected":"source-owned identity"}\n',
        ".env.example": b"PUBLIC_EXAMPLE=\n",
    }.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    (path / ".env").write_text("untracked fixture must never be copied")
    (path / "untracked.py").write_text("untracked fixture payload")
    return path


def test_named_context_graph_matches_every_real_dockerfile_consumer():
    available = {"system", "compiler", "python-source"}
    for build in producer.builds():
        source = (ROOT / build.dockerfile).read_text()
        internal = set(re.findall(r"(?im)^FROM .* AS ([a-z0-9-]+)$", source))
        consumers = set(re.findall(r"--from=([a-z0-9-]+)", source))
        consumers.update(re.findall(r"--mount=from=([a-z0-9-]+)", source))
        assert consumers - internal == {name for name, _ in build.contexts}
        assert all(dependency in available for _, dependency in build.contexts)
        assert "RUN --network=none" in source
        available.add(build.name)
    assert available >= {
        "codex",
        "google-auth",
        "genai",
        "plugin",
        "gemini",
        "opencode",
        "native",
        "python",
        "go",
        "toolchains",
    }


def test_freeze_uses_only_tracked_current_bytes_without_git_or_dotenv(project, tmp_path):
    (project / "src/retained.py").write_bytes(b"tracked dirty current bytes\n")
    commands = SyntheticCommands()
    frozen = tmp_path / "source"
    identity = producer.freeze(project, frozen, commands)
    producer.verify_frozen(frozen, identity)
    assert (frozen / "src/retained.py").read_bytes() == b"tracked dirty current bytes\n"
    assert not any(
        (frozen / name).exists() for name in (".git", ".env", ".env.example", "untracked.py")
    )
    assert (project / ".env").exists()  # Source exclusions do not remove caller data.


@pytest.mark.parametrize("drift", ["bytes", "mode", "extra"])
def test_frozen_source_drift_refuses_with_healthy_companion(project, tmp_path, drift):
    frozen = tmp_path / "source"
    identity = producer.freeze(project, frozen, SyntheticCommands())
    producer.verify_frozen(frozen, identity)
    if drift == "bytes":
        (frozen / "src/retained.py").write_text("changed bytes")
    elif drift == "mode":
        (frozen / "src/retained.py").chmod(0o755)
    else:
        (frozen / "foreign").write_text("undeclared")
    with pytest.raises(ValueError, match="frozen producer"):
        producer.verify_frozen(frozen, identity)


def test_tracked_environment_file_refuses_before_read_or_copy(project, tmp_path):
    subprocess.run(["git", "add", "-f", ".env"], cwd=project, check=True)
    with pytest.raises(ValueError, match="environment files"):
        producer.freeze(project, tmp_path / "source", SyntheticCommands())
    assert not (tmp_path / "source").exists()


@pytest.mark.parametrize("failure", [None, "codex", "python", "closure"])
def test_whole_context_publication_waits_for_every_producer_and_exact_runtime_manifest(
    project, tmp_path, failure
):
    work = tmp_path / "work"
    output = tmp_path / "output"
    commands = SyntheticCommands(failure=failure)
    if failure:
        with pytest.raises(
            (RuntimeError, ValueError), match="producer failure|runtime did not close"
        ):
            producer.produce(project, work, output, commands)
        assert not output.exists()
        assert not list(tmp_path.glob("image-contexts-*"))
        return
    result = producer.produce(project, work, output, commands)
    assert result["status"] == "CLOSED_CONTEXTS"
    assert (output / "runtime/inputs.json").read_bytes() == (
        project / "deploy/runtime-clis/runtime-inputs.json"
    ).read_bytes()
    docker = [(argv, timeout) for argv, _cwd, timeout in commands.calls if argv[0] == "docker"]
    assert len(docker) == 10
    assert all(argv[1:3] == ["buildx", "build"] and timeout == 10800 for argv, timeout in docker)
    assert {argv[argv.index("--build-arg") + 1] for argv, _ in docker if "--build-arg" in argv} == {
        "VENDOR=google-auth-library",
        "VENDOR=js-genai",
    }
    assert json.loads((output / "source-inputs.json").read_text())


@pytest.mark.parametrize("kind", ["overlap", "existing", "link"])
def test_output_preflight_keeps_existing_owner_data(project, tmp_path, kind):
    output = tmp_path / "output"
    work = tmp_path / "work"
    if kind == "overlap":
        output = project / "nested-output"
    elif kind == "existing":
        output.mkdir()
        (output / "retained").write_text("owner data")
    else:
        output.symlink_to(project, target_is_directory=True)
    with pytest.raises(ValueError, match="disjoint|outputs must|real absolute"):
        producer.produce(project, work, output, SyntheticCommands())
    assert not work.exists()
    assert (project / "src/retained.py").is_file()


@pytest.mark.parametrize("target", ["src/retained.py", "../foreign", "/absolute/foreign"])
def test_tracked_links_retain_internal_target_and_refuse_escape(project, tmp_path, target):
    (project / "tracked-link").symlink_to(target)
    subprocess.run(["git", "add", "tracked-link"], cwd=project, check=True)
    frozen = tmp_path / "source"
    if target != "src/retained.py":
        with pytest.raises(ValueError, match="link escapes"):
            producer.freeze(project, frozen, SyntheticCommands())
        assert not frozen.exists()
        return
    identity = producer.freeze(project, frozen, SyntheticCommands())
    producer.verify_frozen(frozen, identity)
    assert (frozen / "tracked-link").is_symlink()
    assert (frozen / "tracked-link").readlink() == Path(target)


@pytest.mark.parametrize("external", [False, True])
def test_tracked_regular_leaf_refuses_symlinked_parent_before_read(
    project, tmp_path, monkeypatch, external
):
    healthy = tmp_path / "healthy"
    producer.verify_frozen(healthy, producer.freeze(project, healthy, SyntheticCommands()))
    original = project / "src"
    saved = project / "original-src"
    original.rename(saved)
    foreign = tmp_path / "public-foreign" if external else saved
    if external:
        foreign.mkdir()
        (foreign / "retained.py").write_bytes(b"public canary outside checkout")
    original.symlink_to(foreign, target_is_directory=True)
    digest = producer.digest

    def guarded_digest(path):
        assert path != project / "src/retained.py", "foreign leaf was read"
        return digest(path)

    monkeypatch.setattr(producer, "digest", guarded_digest)
    with pytest.raises(ValueError, match="parent must be a real contained"):
        producer.freeze(project, tmp_path / "refused", SyntheticCommands())
    assert not (tmp_path / "refused").exists()


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_actual_command_cancellation_settles_child_removes_stage_and_keeps_signal(
    project, tmp_path, signum
):
    helper = project / "scripts/runtime_clis/produce_default_plugin_artifact.py"
    helper.parent.mkdir(parents=True)
    helper.write_bytes(
        (ROOT / "scripts/runtime_clis/produce_default_plugin_artifact.py").read_bytes()
    )
    subprocess.run(["git", "add", "scripts"], cwd=project, check=True)
    pid_file = tmp_path / "owned-child.pid"
    driver = tmp_path / "driver.py"
    work = tmp_path / "work"
    output = tmp_path / "output"
    child_code = (
        f"import os,time,pathlib;pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()));"
        "time.sleep(120)"
    )
    driver.write_text(
        "import importlib.util,sys\n"
        f"s=importlib.util.spec_from_file_location('_actual_context_probe',{str(ROOT / 'scripts/runtime_clis/produce_image_contexts.py')!r})\n"
        "m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m)\n"
        "original=m.Commands.run\n"
        "def command(self,argv,*,cwd,timeout=1800):\n"
        " if argv[:2]==['git','ls-files']:return original(self,argv,cwd=cwd,timeout=timeout)\n"
        f" code={child_code!r}\n"
        " return original(self,[sys.executable,'-I','-B','-c',code],cwd=cwd,timeout=timeout)\n"
        "m.Commands.run=command\n"
        f"m.produce(m.Path({str(project)!r}),m.Path({str(work)!r}),m.Path({str(output)!r}))\n"
    )
    with (tmp_path / "driver.log").open("wb") as log:
        owner = subprocess.Popen(
            [sys.executable, "-I", "-B", str(driver)],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        child = None
        try:
            deadline = time.monotonic() + 10
            while not pid_file.exists() and owner.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert pid_file.exists(), (tmp_path / "driver.log").read_text()
            child = int(pid_file.read_text())
            assert Path("/proc", str(child)).exists()
            assert list(tmp_path.glob("image-contexts-*"))
            os.kill(owner.pid, signum)
            assert owner.wait(timeout=10) == -signum
            assert not Path("/proc", str(child)).exists()
            assert not output.exists()
            assert not list(tmp_path.glob("image-contexts-*"))
        finally:
            if owner.poll() is None:
                os.killpg(owner.pid, signal.SIGKILL)
                owner.wait()
            if child is not None and Path("/proc", str(child)).exists():
                os.killpg(child, signal.SIGKILL)
