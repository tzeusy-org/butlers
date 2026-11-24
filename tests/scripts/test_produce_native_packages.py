"""Synthetic complete package assembly; no downloads or native operations."""

import base64
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts/runtime_clis"
SPEC = importlib.util.spec_from_file_location(
    "_test_native_package_producer", SCRIPTS / "produce_native_packages.py"
)
producer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(producer)


def _archive(path, members):
    with tarfile.open(path, "w:gz") as archive:
        for name, data, mode, kind in members:
            member = tarfile.TarInfo(name)
            member.mode = mode
            member.type = kind
            member.size = len(data) if kind == tarfile.REGTYPE else 0
            if kind == tarfile.SYMTYPE:
                member.linkname = "../../outside"
            archive.addfile(member, io.BytesIO(data) if member.isfile() else None)
    raw = path.read_bytes()
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "integrity": "sha512-" + base64.b64encode(hashlib.sha512(raw).digest()).decode(),
        "size": len(raw),
        "maximum_bytes": len(raw),
    }


@pytest.fixture
def assembly(tmp_path, monkeypatch):
    recipe = tmp_path / "recipe"
    work = tmp_path / "work"
    work.mkdir()
    archives = work / "archives"
    archives.mkdir()
    code = tmp_path / "codex-artifact"
    code.mkdir()
    (code / "codex").write_bytes(b"synthetic reproduced GNU binary")
    template = json.loads((SCRIPTS.parents[1] / producer.MANIFEST).read_text())
    expected = tmp_path / "expected"
    expected.mkdir()
    for record in template["archives"]:
        package = {"name": record["name"], "version": record["version"]}
        files = {"package.json": (json.dumps(package).encode(), 0o644)}
        if record["id"] == "claude-wrapper":
            package["scripts"] = {"postinstall": "node install.cjs"}
            files["package.json"] = (json.dumps(package).encode(), 0o644)
            files["install.cjs"] = (b"original install hook", 0o644)
            files["sdk-tools.d.ts"] = (b"original public SDK types", 0o644)
        elif record["id"] == "claude-platform":
            files["claude"] = (b"synthetic unmodified official native", 0o751)
        elif record["id"] == "codex-wrapper":
            files["bin/codex.js"] = (b"original wrapper", 0o755)
        else:
            files["vendor/x86_64-unknown-linux-musl/bin/codex"] = (b"original main", 0o755)
            files["vendor/x86_64-unknown-linux-musl/voice/manifest.json"] = (
                b"original voice resources",
                0o751,
            )
            files["vendor/x86_64-unknown-linux-musl/bin/code-mode-host"] = (
                b"original additional native host",
                0o755,
            )
        members = [
            ("package/" + name, data, mode, tarfile.REGTYPE) for name, (data, mode) in files.items()
        ]
        record.update(_archive(archives / (record["id"] + ".tgz"), members))
        destination = expected / record["destination"]
        destination.mkdir(parents=True)
        for name, (data, mode) in files.items():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o755 if mode & 0o111 else 0o644)
    claude = template["claude"]
    original = expected / "claude" / claude["native_path"]
    installed = expected / "claude" / claude["installed_path"]
    installed.parent.mkdir()
    installed.write_bytes(original.read_bytes())
    installed.chmod(0o755)
    claude["native_sha256"] = producer.digest(original)
    codex = template["codex"]
    codex["binary_sha256"] = producer.digest(code / "codex")
    (expected / "codex" / codex["main_path"]).write_bytes((code / "codex").read_bytes())
    for path in (
        "Dockerfile.native-package-artifacts",
        "deploy/runtime-clis/toolchains.json",
        "deploy/runtime-clis/runtime-inputs.json",
        "deploy/runtime-clis/debian-system-inputs.json",
        "scripts/runtime_clis/install_system_artifacts.py",
        "deploy/runtime-clis/codex/series.json",
        "scripts/runtime_clis/produce_native_packages.py",
    ):
        target = recipe / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("synthetic exact recipe")
    for name in ("runtime_artifacts", "produce_codex_artifacts"):
        (recipe / "scripts/runtime_clis" / (name + ".py")).write_bytes(
            (SCRIPTS / (name + ".py")).read_bytes()
        )
    monkeypatch.setattr(producer, "RECIPE", recipe)
    real_helper = producer.helper
    artifacts = real_helper("runtime_artifacts")
    for name in ("claude", "codex"):
        template[name]["tree_sha256"] = artifacts.tree_digest(artifacts.inventory(expected / name))
    codex["series_sha256"] = producer.digest(recipe / "deploy/runtime-clis/codex/series.json")
    manifest = recipe / producer.MANIFEST
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps(template))
    (code / "provenance.json").write_text(
        json.dumps(
            {
                "binary_sha256": codex["binary_sha256"],
                "source_tree_sha256": codex["source_tree_sha256"],
                "series_sha256": codex["series_sha256"],
                "default_features": True,
                "target": "x86_64-unknown-linux-gnu",
            }
        )
    )
    guards = []

    def helper(name):
        module = real_helper(name)
        if name == "produce_codex_artifacts":
            module.network_none = lambda: guards.append("actual guard seam")
        return module

    monkeypatch.setattr(producer, "helper", helper)
    actual_identity = producer.identity
    monkeypatch.setattr(producer, "identity", lambda declaration: None)
    monkeypatch.setattr(producer, "ROOT", work)
    monkeypatch.setattr(producer, "CODEX", code)
    monkeypatch.setattr(producer, "OUTPUT", tmp_path / "artifacts/native-packages")
    monkeypatch.setattr(producer, "NODE", tmp_path / "official-node")
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        root = kwargs["cwd"]
        source = root / claude["native_path"]
        target = root / claude["installed_path"]
        target.parent.mkdir()
        os.link(source, target)

    monkeypatch.setattr(producer.subprocess, "run", run)
    producer.write_json(work / "seed.json", {"status": "NONFINAL", "recipe": producer.pins()})
    return SimpleNamespace(
        template=template,
        manifest=manifest,
        code=code,
        expected=expected,
        recipe=recipe,
        work=work,
        artifacts=artifacts,
        calls=calls,
        guards=guards,
        run=run,
        identity=actual_identity,
    )


def test_complete_assembly_retains_original_assets_and_hook_with_independent_modes(assembly):
    producer.build()
    assert len(assembly.calls) == 1
    argv, options = assembly.calls[0]
    assert argv == [str(producer.NODE / "bin/node"), "install.cjs"]
    assert options["check"] is True and options["stdin"] == subprocess.DEVNULL
    assert options["env"] == {
        "HOME": os.environ["HOME"],
        "PATH": f"{producer.NODE}/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
    }
    assert len(assembly.guards) == 2
    for name in ("claude", "codex"):
        output = producer.OUTPUT / "components" / name
        assert assembly.artifacts.inventory(output, closed=True) == assembly.artifacts.inventory(
            assembly.expected / name
        )
    native = assembly.template["claude"]
    output = producer.OUTPUT / "components/claude"
    assert (output / native["installed_path"]).stat().st_ino != (
        output / native["native_path"]
    ).stat().st_ino
    voice = (
        producer.OUTPUT
        / "components/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/voice/manifest.json"
    )
    assert voice.read_bytes() == b"original voice resources"
    assert voice.stat().st_mode & 0o777 == 0o755
    metadata = json.loads((producer.OUTPUT / "provenance.json").read_text())
    assert str(assembly.work.parent) not in json.dumps(metadata)
    assert set(metadata["archives"]) == set(producer.DESTINATIONS)


@pytest.mark.parametrize(
    "change", ["missing-archive", "binary", "feature", "source", "series", "recipe"]
)
def test_seed_or_artifact_drift_refuses_before_original_hook(assembly, change):
    if change == "missing-archive":
        next((assembly.work / "archives").iterdir()).unlink()
    elif change == "binary":
        (assembly.code / "codex").write_bytes(b"unreviewed substitute")
    elif change in {"feature", "source", "series"}:
        path = assembly.code / "provenance.json"
        data = json.loads(path.read_text())
        key = {
            "feature": "default_features",
            "source": "source_tree_sha256",
            "series": "series_sha256",
        }[change]
        data[key] = False if change == "feature" else "0" * 64
        path.write_text(json.dumps(data))
    else:
        (assembly.recipe / "Dockerfile.native-package-artifacts").write_text("changed recipe")
    with pytest.raises(ValueError):
        producer.build()
    assert not assembly.calls
    assert not producer.OUTPUT.exists()


def test_rebuilt_codex_binary_drift_from_reviewed_pin_is_recorded_not_refused(assembly):
    # A non-reproducible local compile: new bytes, consistent with the producer's own
    # provenance, but not the reviewed pin. It publishes and records both digests.
    (assembly.code / "codex").write_bytes(b"locally rebuilt codex bytes")
    rebuilt = producer.digest(assembly.code / "codex")
    path = assembly.code / "provenance.json"
    data = json.loads(path.read_text())
    data["binary_sha256"] = rebuilt
    path.write_text(json.dumps(data))
    producer.build()
    metadata = json.loads((producer.OUTPUT / "provenance.json").read_text())
    assert metadata["codex_binary_sha256"] == rebuilt
    reviewed = json.loads(assembly.manifest.read_text())["codex"]
    assert metadata["reviewed_codex_binary_sha256"] == reviewed["binary_sha256"] != rebuilt
    assert metadata["components"]["codex"] != metadata["reviewed_components"]["codex"]
    assert metadata["components"]["claude"] == metadata["reviewed_components"]["claude"]


def test_original_hook_failure_or_recipe_mutation_cannot_publish(assembly, monkeypatch):
    def failure(argv, **kwargs):
        raise subprocess.CalledProcessError(23, argv)

    monkeypatch.setattr(producer.subprocess, "run", failure)
    with pytest.raises(subprocess.CalledProcessError):
        producer.build()
    assert not producer.OUTPUT.exists()

    def changed(argv, **kwargs):
        assembly.run(argv, **kwargs)
        (assembly.recipe / "Dockerfile.native-package-artifacts").write_text("drift during hook")

    monkeypatch.setattr(producer.subprocess, "run", changed)
    with pytest.raises(ValueError, match="recipe changed"):
        producer.build()
    assert not producer.OUTPUT.exists()


@pytest.mark.parametrize(
    "members",
    [
        [("package/../escape", b"x", 0o644, tarfile.REGTYPE)],
        [("/package/absolute", b"x", 0o644, tarfile.REGTYPE)],
        [("package/link", b"", 0o777, tarfile.SYMTYPE)],
        [("package/repeated", b"x", 0o644, tarfile.REGTYPE)] * 2,
        [
            ("package/file", b"x", 0o644, tarfile.REGTYPE),
            ("package/file/child", b"", 0o755, tarfile.DIRTYPE),
        ],
    ],
)
def test_unsafe_archive_is_refused_before_any_extraction(tmp_path, members):
    source = tmp_path / "archive.tgz"
    record = _archive(source, members)
    with pytest.raises(ValueError):
        producer.extract(record, source, tmp_path / "output")
    assert not (tmp_path / "output").exists()
    assert not (tmp_path / "escape").exists()


def test_npm_input_integrity_and_response_identity_fail_before_publication(tmp_path, monkeypatch):
    raw = b"complete synthetic input"
    url = "https://registry.npmjs.org/fixture/-/fixture-1.0.0.tgz"
    record = {
        "url": url,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size": len(raw),
        "maximum_bytes": len(raw),
        "integrity": "sha512-" + base64.b64encode(hashlib.sha512(raw).digest()).decode(),
    }

    class Response(io.BytesIO):
        status = 200

        def geturl(self):
            return url

    monkeypatch.setattr(producer, "helper", lambda name: SimpleNamespace())
    response = Response(raw)
    opener = SimpleNamespace(open=lambda *args, **kwargs: response)
    producer.download(record, tmp_path / "healthy", opener=opener)
    assert (tmp_path / "healthy").read_bytes() == raw
    for status in (204, 302, 399):
        response = Response(raw)
        response.status = status
        with pytest.raises(ValueError):
            producer.download(record, tmp_path / "refused", opener=opener)
        assert response.closed
        assert not (tmp_path / "refused").exists()
    response = Response(b"altered bytes")
    with pytest.raises(ValueError):
        producer.download(record, tmp_path / "changed", opener=opener)
    assert not (tmp_path / "changed").exists()
    response = Response(raw)
    bad_integrity = {**record, "integrity": "sha512-" + base64.b64encode(b"wrong").decode()}
    with pytest.raises(ValueError, match="npm integrity"):
        producer.download(bad_integrity, tmp_path / "bad-integrity", opener=opener)
    assert not (tmp_path / "bad-integrity").exists()

    def forbidden_request(*args, **kwargs):
        raise AssertionError("unreviewed URL reached the wire")

    for foreign in (
        "http://registry.npmjs.org/package.tgz",
        "https://registry.npmjs.org@127.0.0.1/package.tgz",
        "https://registry.npmjs.org:443/package.tgz",
        url + "?token=synthetic",
    ):
        with pytest.raises(ValueError, match="invalid official"):
            producer.download(
                {**record, "url": foreign},
                tmp_path / "foreign",
                opener=SimpleNamespace(open=forbidden_request),
            )
        assert not (tmp_path / "foreign").exists()


def test_declared_target_set_and_original_paths_cannot_be_redirected(assembly):
    data = assembly.template
    data["codex"]["main_path"] = "../outside"
    assembly.manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="layout"):
        producer.declarations()
    data["codex"]["main_path"] = (
        "node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex"
    )
    data["archives"].pop()
    assembly.manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="complete"):
        producer.declarations()


def test_identity_checks_complete_node_payload_not_only_executable_and_version(
    assembly, monkeypatch
):
    node = producer.NODE
    (node / "bin").mkdir(parents=True)
    (node / "bin/node").write_bytes(b"synthetic Node executable")
    (node / "bin/node").chmod(0o755)
    npm = node / "lib/node_modules/npm"
    npm.mkdir(parents=True)
    (npm / "package.json").write_text('{"version":"10.9.9"}')
    (npm / "index.js").write_text("original complete toolchain payload")
    for path in [node, *node.rglob("*")]:
        if path.is_dir():
            path.chmod(0o755)
        elif path.name != "node":
            path.chmod(0o644)
    binary = producer.digest(node / "bin/node")
    (assembly.recipe / "deploy/runtime-clis/toolchains.json").write_text(
        json.dumps({"node": {"binary_sha256": binary}})
    )
    (assembly.recipe / "deploy/runtime-clis/runtime-inputs.json").write_text(
        json.dumps(
            {
                "components": {
                    "node": {
                        "tree_sha256": assembly.artifacts.tree_digest(
                            assembly.artifacts.inventory(node, closed=True)
                        )
                    }
                }
            }
        )
    )
    declaration = {**assembly.template, "node": {**assembly.template["node"], "sha256": binary}}
    home = assembly.work / "empty-owned-home"
    home.mkdir()
    literal_home = os.environ["HOME"]

    class Paths:
        def __new__(cls, value):
            # Model an isolated backing without changing HOME or reading its
            # actual host directory. No child or target runtime executes here.
            return home if value == literal_home else Path(value)

        @staticmethod
        def cwd():
            return producer.ROOT

    monkeypatch.setattr(producer, "Path", Paths)
    monkeypatch.setattr(producer.platform, "python_version", lambda: "3.12.13")
    monkeypatch.setattr(producer.platform, "libc_ver", lambda: ("glibc", "2.41"))
    monkeypatch.setattr(producer.platform, "machine", lambda: "x86_64")
    # The fixture retains the actual identity function before replacing it
    # for synthetic assembly; this control executes that original function.
    assembly.identity(declaration)
    (npm / "index.js").write_text("mutated other file, same Node and npm version")
    with pytest.raises(ValueError, match="complete official Node"):
        assembly.identity(declaration)
