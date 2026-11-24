"""Complete synthetic Node/uv data fixtures; never execute downloaded tool binaries."""

import hashlib
import importlib.util
import io
import json
import os
import stat
import tarfile
import urllib.request
import zipfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts/runtime_clis"
SPEC = importlib.util.spec_from_file_location(
    "_test_toolchain_components", SCRIPTS / "produce_toolchain_components.py"
)
producer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(producer)


class Response(io.BytesIO):
    status = 200

    def __init__(self, content, url):
        super().__init__(content)
        self.url = url
        self.headers = {"Content-Length": str(len(content))}

    def geturl(self):
        return self.url


class Opener:
    def __init__(self, content, url):
        self.content, self.url = content, url
        self.responses = []

    def open(self, url, timeout):
        assert url == self.url and timeout > 0
        response = Response(self.content, url)
        self.responses.append(response)
        return response


def json_file(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    path.chmod(0o644)


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    original_helper = producer.helper
    artifacts = original_helper("runtime_artifacts")
    uv = original_helper("fetch_uv_toolchain")
    monkeypatch.setattr(
        producer,
        "helper",
        lambda name: {"runtime_artifacts": artifacts, "fetch_uv_toolchain": uv}[name],
    )
    source = tmp_path / "source"
    source.mkdir()
    files = {
        "CHANGELOG.md": b"complete changelog",
        "LICENSE": b"complete license",
        "README.md": b"complete readme",
        "bin/node": b"synthetic node executable",
        "include/node.h": b"all headers",
        "lib/node_modules/npm/bin/npm-cli.js": b"npm cli",
        "share/man/node.1": b"all manuals",
    }
    for name, data in files.items():
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o755 if name.startswith("bin/") or name.endswith(".js") else 0o644)
    (source / "bin/npm").symlink_to("../lib/node_modules/npm/bin/npm-cli.js")
    for directory in [source, *[p for p in source.rglob("*") if p.is_dir()]]:
        directory.chmod(0o755)
    node_archive = tmp_path / "node.tar.xz"
    with tarfile.open(node_archive, "w:xz") as archive:
        archive.add(source, arcname=producer.NODE_ROOT, recursive=False)
        for path in sorted(source.rglob("*")):
            archive.add(
                path,
                arcname=producer.NODE_ROOT + "/" + path.relative_to(source).as_posix(),
                recursive=False,
            )
    node = {
        **producer.NODE,
        "sha256": producer.digest(node_archive),
        "binary_sha256": producer.digest(source / "bin/node"),
    }
    monkeypatch.setattr(producer, "NODE", node)
    wheel = tmp_path / "uv.whl"
    wheel_bytes = {name: (b"synthetic " + name.encode()) for name in uv.FILES}
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, content in reversed(sorted(wheel_bytes.items())):
            item = zipfile.ZipInfo(name)
            item.external_attr = (stat.S_IFREG | (0o755 if name in uv.EXECUTABLES else 0o644)) << 16
            archive.writestr(item, content)
    uv.PINNED = {
        **uv.PINNED,
        "sha256": producer.digest(wheel),
        "binary_sha256": hashlib.sha256(wheel_bytes[uv.BINARY]).hexdigest(),
    }
    uv_component = tmp_path / "uv-component"
    uv_component.mkdir(mode=0o755)
    uv_component.chmod(0o755)
    for name in ("uv", "uvx"):
        path = uv_component / name
        path.write_bytes(wheel_bytes[str(Path(uv.BINARY).with_name(name))])
        path.chmod(0o755)
    tools = tmp_path / "toolchains.json"
    json_file(tools, {"node": node, "npm": {"version": "10.9.9"}, "uv": uv.PINNED})
    inputs = tmp_path / "runtime-inputs.json"
    json_file(
        inputs,
        {
            "target": artifacts.TARGET,
            "versions": artifacts.VERSIONS,
            "components": {
                "node": {
                    "tree_sha256": artifacts.tree_digest(artifacts.inventory(source, closed=True))
                },
                "uv": {
                    "tree_sha256": artifacts.tree_digest(
                        artifacts.inventory(uv_component, closed=True)
                    )
                },
            },
            "provenance": {"unrelated": "old"},
        },
    )
    return {
        "root": tmp_path,
        "node": node_archive,
        "wheel": wheel,
        "tools": tools,
        "inputs": inputs,
        "source": source,
        "artifacts": artifacts,
        "uv": uv,
    }


def produce(fixture, output):
    return producer.produce(
        fixture["tools"],
        fixture["inputs"],
        output,
        node_opener=Opener(fixture["node"].read_bytes(), producer.NODE["url"]),
        uv_opener=Opener(fixture["wheel"].read_bytes(), fixture["uv"].PINNED["url"]),
    )


def test_complete_produce_verify_preserves_both_components_and_full_inputs(fixture):
    output = fixture["root"] / "output"
    before = {
        str(p): producer.digest(p)
        for p in (fixture["tools"], fixture["inputs"], fixture["node"], fixture["wheel"])
    }
    old_umask = os.umask(0o027)
    try:
        result = produce(fixture, output)
    finally:
        os.umask(old_umask)
    assert producer.verify(fixture["tools"], fixture["inputs"], output) == result
    with zipfile.ZipFile(fixture["wheel"]) as archive:
        assert archive.namelist() != sorted(archive.namelist())
    assert {p.name for p in (output / "components/node").iterdir()} == producer.NODE_TOP
    assert (
        output / "components/node/bin/npm"
    ).readlink().as_posix() == "../lib/node_modules/npm/bin/npm-cli.js"
    assert {p.name for p in (output / "components/uv").iterdir()} == {"uv", "uvx"}
    assert len([p for p in (output / "inputs/uv/wheel").rglob("*") if p.is_file()]) == 12
    assert (
        producer.digest(output / "inputs/node" / Path(producer.NODE["url"]).name)
        == before[str(fixture["node"])]
    )
    assert (output / "components/uv/uv").stat().st_ino != (
        output / "inputs/uv/wheel" / fixture["uv"].BINARY
    ).stat().st_ino
    assert not {"started", "elapsed", "cwd", "native_success"} & result.keys()
    assert before == {path: producer.digest(Path(path)) for path in before}
    # Unrelated CLI receipt/tree changes cannot alter these component bytes or metadata.
    value = json.loads(fixture["inputs"].read_text())
    value["components"]["claude"] = {"tree_sha256": "0" * 64}
    value["provenance"] = {"new": "receipt"}
    json_file(fixture["inputs"], value)
    assert producer.verify(fixture["tools"], fixture["inputs"], output) == result
    with pytest.raises(ValueError, match="absent"):
        produce(fixture, output)


@pytest.mark.parametrize(
    "drift",
    [
        "extra",
        "missing",
        "mode",
        "payload",
        "metadata",
        "metadata-bytes",
        "alias",
        "expectation",
        "toolchains",
    ],
)
def test_verify_refuses_closed_context_or_declared_input_drift(fixture, drift):
    output = fixture["root"] / "output"
    produce(fixture, output)
    if drift == "extra":
        (output / "inputs/uv/extra").write_bytes(b"extra")
    elif drift == "missing":
        (output / "components/uv/uvx").unlink()
    elif drift == "mode":
        (output / "components/node/bin/node").chmod(0o644)
    elif drift == "payload":
        (output / "inputs/uv/wheel/uv/__init__.py").write_bytes(b"changed")
    elif drift == "metadata":
        (output / "provenance.json").chmod(0o600)
    elif drift == "metadata-bytes":
        path = output / "provenance.json"
        path.write_text(json.dumps(json.loads(path.read_text())))
    elif drift == "alias":
        source = output / "inputs/uv/wheel" / fixture["uv"].BINARY
        target = output / "components/uv/uv"
        target.unlink()
        os.link(source, target)
    elif drift == "expectation":
        value = json.loads(fixture["inputs"].read_text())
        value["components"]["node"]["tree_sha256"] = "0" * 64
        json_file(fixture["inputs"], value)
    elif drift == "toolchains":
        value = json.loads(fixture["tools"].read_text())
        value["foreign"] = True
        json_file(fixture["tools"], value)
    with pytest.raises(ValueError):
        producer.verify(fixture["tools"], fixture["inputs"], output)


def changed_archive(fixture, *, name, kind=tarfile.REGTYPE, link="", mode=0o644):
    path = fixture["root"] / "bad.tar.xz"
    with tarfile.open(fixture["node"], "r:xz") as original, tarfile.open(path, "w:xz") as target:
        for item in original:
            stream = original.extractfile(item) if item.isfile() else None
            with stream if stream is not None else __import__("contextlib").nullcontext():
                target.addfile(item, stream)
        item = tarfile.TarInfo(name)
        item.type = kind
        item.linkname = link
        item.mode = mode
        item.size = 3 if kind == tarfile.REGTYPE else 0
        target.addfile(item, io.BytesIO(b"bad") if item.isfile() else None)
    return path


@pytest.mark.parametrize(
    ("name", "kind", "link", "mode"),
    [
        ("../escape", tarfile.REGTYPE, "", 0o644),
        ("/absolute", tarfile.REGTYPE, "", 0o644),
        (producer.NODE_ROOT + "/bin/node", tarfile.REGTYPE, "", 0o755),
        (producer.NODE_ROOT + "/device", tarfile.CHRTYPE, "", 0o644),
        (producer.NODE_ROOT + "/setid", tarfile.REGTYPE, "", 0o4755),
        (producer.NODE_ROOT + "/escape", tarfile.SYMTYPE, "../outside", 0o777),
        (producer.NODE_ROOT + "/dangling", tarfile.SYMTYPE, "no-file", 0o777),
        (producer.NODE_ROOT + "/cycle", tarfile.SYMTYPE, "cycle", 0o777),
        (producer.NODE_ROOT + "/bin/npm/alias", tarfile.REGTYPE, "", 0o644),
        (producer.NODE_ROOT + "/hard", tarfile.LNKTYPE, "../foreign", 0o644),
    ],
)
def test_invalid_archive_never_publishes(fixture, name, kind, link, mode):
    archive = changed_archive(fixture, name=name, kind=kind, link=link, mode=mode)
    output = fixture["root"] / "refused"
    with pytest.raises(ValueError):
        producer.extract_node(archive, output)
    assert not output.exists()
    assert not list(fixture["root"].glob(".node-component-*"))


def test_valid_hardlink_becomes_independent_regular_payload(fixture):
    archive = changed_archive(
        fixture,
        name=producer.NODE_ROOT + "/include/alias.h",
        kind=tarfile.LNKTYPE,
        link=producer.NODE_ROOT + "/include/node.h",
    )
    output = fixture["root"] / "hardlinks"
    producer.extract_node(archive, output)
    assert (output / "include/alias.h").read_bytes() == (output / "include/node.h").read_bytes()
    assert (output / "include/alias.h").stat().st_ino != (output / "include/node.h").stat().st_ino


def test_hash_failure_removes_only_owned_staging(fixture):
    original = fixture["node"].read_bytes()
    opener = Opener(original + b"changed", producer.NODE["url"])
    output = fixture["root"] / "refused"
    with pytest.raises(ValueError, match="bytes differ"):
        producer.produce(fixture["tools"], fixture["inputs"], output, node_opener=opener)
    assert all(response.closed for response in opener.responses)
    assert not output.exists() and not list(fixture["root"].glob(".toolchain-components-*"))


def test_every_redirect_refuses_and_closes_before_default_dispatch():
    request = urllib.request.Request(producer.NODE["url"])
    for status in range(300, 400):
        response = Response(b"foreign location", producer.NODE["url"])
        response.status = status
        with pytest.raises(ValueError, match="redirects"):
            producer.NoRedirect().https_response(request, response)
        assert response.closed


def test_private_helpers_do_not_write_source_cache_or_load_unknown_names(tmp_path, monkeypatch):
    entrypoint = tmp_path / "producer.py"
    entrypoint.write_text("# declared source fixture\n")
    sibling = tmp_path / "runtime_artifacts.py"
    sibling.write_bytes((SCRIPTS / "runtime_artifacts.py").read_bytes())
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "existing").write_bytes(b"preserved")
    monkeypatch.setattr(producer, "__file__", str(entrypoint))
    before = {
        p.relative_to(tmp_path).as_posix(): producer.digest(p)
        for p in tmp_path.rglob("*")
        if p.is_file()
    }
    assert producer.helper("runtime_artifacts").TARGET["platform"] == "linux-amd64"
    after = {
        p.relative_to(tmp_path).as_posix(): producer.digest(p)
        for p in tmp_path.rglob("*")
        if p.is_file()
    }
    assert after == before
    with pytest.raises(ValueError, match="undeclared"):
        producer.helper("foreign")
    sibling.unlink()
    sibling.symlink_to(SCRIPTS / "runtime_artifacts.py")
    with pytest.raises(ValueError, match="regular declared sibling"):
        producer.helper("runtime_artifacts")


def test_independent_hardlink_materialization_obeys_expanded_byte_bound(fixture, monkeypatch):
    archive = changed_archive(
        fixture,
        name=producer.NODE_ROOT + "/include/alias.h",
        kind=tarfile.LNKTYPE,
        link=producer.NODE_ROOT + "/include/node.h",
    )
    with tarfile.open(archive, "r:xz") as source:
        monkeypatch.setattr(producer, "MAX_EXPANDED", sum(item.size for item in source))
    output = fixture["root"] / "refused-expanded"
    with pytest.raises(ValueError, match="independent Node payload expansion"):
        producer.extract_node(archive, output)
    assert not output.exists()
