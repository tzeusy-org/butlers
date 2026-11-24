"""Synthetic complete graph orchestration; no public acquisition or package code."""

import base64
import copy
import hashlib
import importlib.util
import io
import json
import os
import signal
import subprocess
import sys
import tarfile
import time
import zipfile
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts/runtime_clis"


def load(name):
    spec = importlib.util.spec_from_file_location("_test_plugin_" + name, SCRIPTS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


producer = load("produce_default_plugin_artifact")
artifacts = load("runtime_artifacts")
native = load("produce_native_packages")


def archive(data):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as stream:
        for name, payload, mode in data:
            member = tarfile.TarInfo("package/" + name)
            member.size = len(payload)
            member.mode = mode
            stream.addfile(member, io.BytesIO(payload))
    return output.getvalue()


def bun_zip(*, unsafe=False):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as stream:
        # Reverse order deliberately positions a member before its directory.
        member = zipfile.ZipInfo("../outside" if unsafe else "bun-linux-x64/bun")
        member.external_attr = 0o100755 << 16
        stream.writestr(member, b"synthetic Bun executable")
        directory = zipfile.ZipInfo("bun-linux-x64/")
        directory.external_attr = 0o40755 << 16
        stream.writestr(directory, b"")
    return output.getvalue()


class Commands:
    def __init__(self, fixture):
        self.fixture = fixture
        self.calls = []
        self.failure = None

    def run(self, argv, *, cwd, env, capture=False, timeout=1800):
        self.calls.append((argv, env))
        assert "--ignore-scripts" not in argv and not any("--omit" in arg for arg in argv)
        assert set(env) == {"HOME", "PATH", "LANG", "TZ", "SOURCE_DATE_EPOCH", "npm_config_offline"}
        if "ci" in argv:
            assert env["npm_config_offline"] == str("--offline" in argv).lower()
            self.fixture.install(cwd)
            cache = Path(argv[argv.index("--cache") + 1])
            cache.mkdir(parents=True, exist_ok=True)
            (cache / "public-bytes").write_bytes(b"synthetic public npm cache")
            return ""
        if "--version" in argv:
            return "1.3.14\n" if "bun" in argv[0] else "v22.23.4\n"
        runtime = "node" if "--input-type=module" in argv else "bun"
        names = self.fixture.manifest["imports"][runtime]
        marker = (
            "ALL_NINE_NODE_EXPORT_FAMILIES_IMPORTED"
            if runtime == "node"
            else "ALL_TEN_ORIGINAL_EXPORT_FAMILIES_IMPORTED"
        )
        if self.failure == "exit":
            raise subprocess.CalledProcessError(23, argv)
        records = [json.dumps({"module": name, "exports": ["originalExport"]}) for name in names]
        if self.failure == "missing-family":
            records.pop()
        if self.failure == "wrong-family":
            records[0] = json.dumps({"module": "foreign", "exports": ["originalExport"]})
        if self.failure != "missing-marker":
            records.append(marker)
        return "\n".join(records) + "\n"


@pytest.fixture
def graph(tmp_path, monkeypatch):
    recipe = tmp_path / "recipe"
    root = tmp_path / "work"
    root.mkdir()
    source = recipe / producer.DECLARATION
    source.mkdir(parents=True)
    for name in ("package.json", "package-lock.json", "build-inputs.json"):
        (source / name).write_bytes((SCRIPTS.parents[1] / producer.DECLARATION / name).read_bytes())
    manifest = json.loads((source / "build-inputs.json").read_bytes())
    lock = json.loads((source / "package-lock.json").read_bytes())
    payloads = {}
    musl_archive = tmp_path / "original-musl.tgz"
    for item in manifest["supplements"]:
        raw = archive(
            [
                (
                    "package.json",
                    json.dumps({"name": item["package"], "version": item["version"]}).encode(),
                    0o644,
                ),
                ("complete-original-native", b"non-target data never executed", 0o751),
            ]
        )
        item.update(
            archive_sha256=hashlib.sha256(raw).hexdigest(),
            archive_bytes=len(raw),
            archive_integrity="sha512-" + base64.b64encode(hashlib.sha512(raw).digest()).decode(),
        )
        lock["packages"][item["lock_path"]]["integrity"] = item["archive_integrity"]
        payloads[item["archive_url"]] = raw
        if item["lock_path"] in producer.PRESENT_SUPPLEMENTS:
            musl_archive.write_bytes(raw)
    (source / "package-lock.json").write_text(json.dumps(lock))
    manifest["package_lock_sha256"] = producer.digest(source / "package-lock.json")
    raw_bun = bun_zip()
    manifest["bun"].update(
        size=len(raw_bun),
        sha256=hashlib.sha256(raw_bun).hexdigest(),
        binary_sha256=hashlib.sha256(b"synthetic Bun executable").hexdigest(),
    )
    payloads[manifest["bun"]["url"]] = raw_bun
    for record in manifest["metadata"]:
        node = lock["packages"]["node_modules/" + record["name"]]
        raw = json.dumps(
            {
                "name": record["name"],
                "version": record["version"],
                "dist": {"integrity": node["integrity"], "tarball": node["resolved"]},
            }
        ).encode()
        record.update(size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        payloads[record["url"]] = raw
    guard_calls = []

    def install(directory):
        absent = {item["lock_path"] for item in manifest["supplements"]}
        for name, record in lock["packages"].items():
            if not name or name in absent:
                continue
            package = directory / name
            package.mkdir(parents=True)
            (package / "package.json").write_text(
                json.dumps(
                    {"name": name.rsplit("node_modules/", 1)[1], "version": record["version"]}
                )
            )
        present = next(
            item
            for item in manifest["supplements"]
            if item["lock_path"] in producer.PRESENT_SUPPLEMENTS
        )
        native.extract(
            producer.supplemental_record(present), musl_archive, directory / present["lock_path"]
        )
        # A DFS walk misorders these two, unlike the contract's literal sort.
        assets = directory / "node_modules/@opencode-ai/plugin/lib/helpers"
        assets.mkdir(parents=True)
        (assets / "map.js").write_text("original source map")
        (assets.parent / "helpers-generated.js").write_text("original generated asset")
        binary = assets / "executable"
        binary.write_bytes(b"original executable data")
        binary.chmod(0o751)
        (assets / "relative-link").symlink_to("map.js")

    value = SimpleNamespace(
        recipe=recipe,
        root=root,
        output=tmp_path / "artifacts/seed",
        manifest=manifest,
        lock=lock,
        payloads=payloads,
        install=install,
        guard_calls=guard_calls,
    )
    value.commands = Commands(value)
    monkeypatch.setattr(producer, "RECIPE", recipe)
    monkeypatch.setattr(producer, "ROOT", root)
    monkeypatch.setattr(producer, "OUTPUT", value.output)
    monkeypatch.setattr(producer, "declarations", lambda: (manifest, lock))
    monkeypatch.setattr(producer, "identity", lambda _manifest: None)
    monkeypatch.setattr(producer, "pins", lambda: {"synthetic-recipe": "fixed"})
    monkeypatch.setattr(
        producer,
        "helper",
        lambda name: {
            "runtime_artifacts": artifacts,
            "produce_native_packages": native,
            "produce_codex_artifacts": SimpleNamespace(
                network_none=lambda: guard_calls.append(True)
            ),
        }[name],
    )

    def fetch(record, output, **_kwargs):
        output.write_bytes(payloads[record["url"]])
        output.chmod(0o644)

    monkeypatch.setattr(producer, "fetch_tool_input", fetch)
    monkeypatch.setattr(native, "download", fetch)
    return value


def test_complete_phases_preserve_graph_modes_links_order_and_all_imports(graph):
    producer.public_seed(graph.commands)
    assert not graph.output.exists()
    before = artifacts.inventory(graph.root / "inputs", closed=True)
    producer.build(graph.commands)
    seed = json.loads((graph.output / "seed.json").read_bytes())
    assert set(seed) == {
        "schema",
        "package",
        "version",
        "node",
        "npm",
        "package_lock_sha256",
        "entries",
        "provenance",
    }
    assert seed["entries"] == sorted(seed["entries"], key=lambda item: item["path"])
    assert seed["entries"] == sorted(
        [
            entry
            for entry in artifacts.inventory(graph.output, closed=True)
            if entry["path"] != "seed.json"
        ],
        key=lambda entry: entry["path"],
    )
    assert len(seed["provenance"]["imports"]["node"]) == 9
    assert len(seed["provenance"]["imports"]["bun"]) == 10
    assert len([path for path in graph.output.rglob("package.json")]) == 142
    assert (
        graph.output / "node_modules/@opencode-ai/plugin/lib/helpers/executable"
    ).stat().st_mode & 0o777 == 0o755
    assert (
        os.readlink(graph.output / "node_modules/@opencode-ai/plugin/lib/helpers/relative-link")
        == "map.js"
    )
    assert before == artifacts.inventory(graph.root / "inputs", closed=True)
    assert len(graph.guard_calls) == 2
    assert ["--offline" in argv for argv, _env in graph.commands.calls if "ci" in argv] == [
        False,
        True,
    ]
    assert producer.provenance(graph.manifest, {"source": "content"}, {}) == producer.provenance(
        copy.deepcopy(graph.manifest), {"source": "content"}, {}
    )
    with pytest.raises(ValueError, match="new plugin artifact"):
        producer.build(graph.commands)


@pytest.mark.parametrize("failure", ["exit", "missing-marker", "missing-family", "wrong-family"])
def test_import_failures_never_publish(graph, failure):
    producer.public_seed(graph.commands)
    graph.commands.failure = failure
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        producer.build(graph.commands)
    assert not graph.output.exists()
    assert list(graph.output.parent.iterdir()) == []


@pytest.mark.parametrize("change", ["cache", "archive", "package-lock", "recipe", "network"])
def test_input_and_route_refusals_precede_package_commands(graph, monkeypatch, change):
    producer.public_seed(graph.commands)
    graph.commands.calls.clear()
    if change == "recipe":
        monkeypatch.setattr(producer, "pins", lambda: {"synthetic-recipe": "changed"})
    elif change == "network":

        def denied():
            raise ValueError("actual networkless builder layer required")

        original = producer.helper
        monkeypatch.setattr(
            producer,
            "helper",
            lambda name: (
                SimpleNamespace(network_none=denied)
                if name == "produce_codex_artifacts"
                else original(name)
            ),
        )
    else:
        paths = {
            "cache": graph.root / "inputs/npm-cache/public-bytes",
            "archive": graph.root / "inputs/archives/00.tgz",
            "package-lock": graph.root / "inputs/ordinary/package-lock.json",
        }
        paths[change].write_bytes(b"changed input")
    with pytest.raises(ValueError):
        producer.build(graph.commands)
    assert graph.commands.calls == []
    assert not graph.output.exists()


def test_late_recipe_change_refuses_atomic_publication(graph, monkeypatch):
    producer.public_seed(graph.commands)
    original = producer.import_families

    def changed(*args):
        result = original(*args)
        monkeypatch.setattr(producer, "pins", lambda: {"synthetic-recipe": "changed"})
        return result

    monkeypatch.setattr(producer, "import_families", changed)
    with pytest.raises(ValueError, match="changed during build"):
        producer.build(graph.commands)
    assert not graph.output.exists()


def test_original_lock_roots_and_every_optional_record_are_required(monkeypatch, tmp_path):
    recipe = SCRIPTS.parents[1]
    monkeypatch.setattr(producer, "RECIPE", recipe)
    monkeypatch.setattr(producer, "helper", lambda _name: artifacts)
    manifest, lock = producer.declarations()
    assert len(lock["packages"]) == 142 and len(manifest["supplements"]) == 12
    project = tmp_path / "project"
    project.mkdir()
    for name in ("package.json", "package-lock.json"):
        (project / name).write_bytes((recipe / producer.DECLARATION / name).read_bytes())
    with pytest.raises(ValueError, match="population"):
        producer.package_population(project, lock, manifest, complete=False)


@pytest.mark.parametrize("change", ["missing-package", "wrong-version", "root-entry"])
def test_ordinary_population_is_exact_before_supplements(graph, monkeypatch, change):
    original = graph.install

    def altered(directory):
        original(directory)
        package = directory / "node_modules/@opencode-ai/plugin/package.json"
        if change == "root-entry":
            (directory / "foreign").write_bytes(b"unexpected root payload")
        elif change == "missing-package":
            package.unlink()
        else:
            value = json.loads(package.read_bytes())
            value["version"] = "0.0.0"
            package.write_text(json.dumps(value))

    monkeypatch.setattr(graph, "install", altered)
    with pytest.raises((ValueError, FileNotFoundError)):
        producer.public_seed(graph.commands)
    assert not (graph.root / "inputs").exists() and not graph.output.exists()


def test_supplement_identity_refusal_keeps_output_absent(graph, monkeypatch):
    producer.public_seed(graph.commands)
    original = native.extract

    def changed(record, archive, output):
        original(record, archive, output)
        if record["name"] == "@opentui/core-linux-x64-musl":
            return
        package = json.loads((output / "package.json").read_bytes())
        package["version"] = "0.0.0"
        (output / "package.json").write_text(json.dumps(package))

    monkeypatch.setattr(native, "extract", changed)
    with pytest.raises(ValueError, match="original full lock"):
        producer.build(graph.commands)
    assert not graph.output.exists()


def test_ordinary_population_diagnostic_binds_only_public_lock_paths(graph):
    directory = graph.root / "project"
    producer.project(directory)
    graph.install(directory)
    item = graph.manifest["supplements"][0]
    additional = directory / item["lock_path"]
    additional.mkdir(parents=True)
    (additional / "package.json").write_text(
        json.dumps({"name": item["package"], "version": item["version"]})
    )
    with pytest.raises(ValueError, match="population differs: ") as caught:
        producer.package_population(directory, graph.lock, graph.manifest, complete=False)
    diagnostic = json.loads(str(caught.value).split(": ", 1)[1])
    expected = {
        record["lock_path"]
        for record in graph.manifest["supplements"]
        if record["lock_path"] not in producer.PRESENT_SUPPLEMENTS
    }
    assert diagnostic == {
        "absent": sorted(expected - {item["lock_path"]}),
        "expected_absent": sorted(expected),
    }
    assert str(graph.root) not in str(caught.value)


@pytest.mark.parametrize("change", ["missing", "wrong-payload", "extra-payload"])
def test_exact_ordinary_musl_payload_is_preserved_and_required(graph, monkeypatch, change):
    original = graph.install

    def changed(directory):
        original(directory)
        musl = directory / producer.PRESENT_SUPPLEMENTS[0]
        if change == "missing":
            import shutil

            shutil.rmtree(musl)
        elif change == "wrong-payload":
            (musl / "complete-original-native").write_bytes(b"wrong platform payload")
        else:
            (musl / "foreign").write_bytes(b"unexpected payload")

    monkeypatch.setattr(graph, "install", changed)
    with pytest.raises(ValueError):
        producer.public_seed(graph.commands)
    assert not (graph.root / "inputs").exists() and not graph.output.exists()


def test_present_verification_never_replaces_the_ordinary_payload(graph):
    directory = graph.root / "project"
    producer.project(directory)
    graph.install(directory)
    archives = graph.root / "archives"
    archives.mkdir()
    for index, record in enumerate(graph.manifest["supplements"]):
        (archives / f"{index:02d}.tgz").write_bytes(graph.payloads[record["archive_url"]])
    installed = directory / producer.PRESENT_SUPPLEMENTS[0]
    before = artifacts.inventory(installed, closed=True)
    identities = {
        path.relative_to(installed).as_posix(): path.stat().st_ino for path in installed.rglob("*")
    }
    producer.package_population(directory, graph.lock, graph.manifest, complete=False)
    producer.preserve_present_supplements(directory, archives, graph.manifest)
    assert before == artifacts.inventory(installed, closed=True)
    assert identities == {
        path.relative_to(installed).as_posix(): path.stat().st_ino for path in installed.rglob("*")
    }


@pytest.mark.parametrize("field", ["dependencies", "population", "authority", "integrity"])
def test_committed_declarations_drift_is_refused(tmp_path, monkeypatch, field):
    root = SCRIPTS.parents[1]
    source = tmp_path / producer.DECLARATION
    source.mkdir(parents=True)
    for name in ("package.json", "package-lock.json", "build-inputs.json"):
        (source / name).write_bytes((root / producer.DECLARATION / name).read_bytes())
    monkeypatch.setattr(producer, "RECIPE", tmp_path)
    monkeypatch.setattr(producer, "helper", lambda _name: artifacts)
    for name in ("toolchains.json", "runtime-inputs.json"):
        (tmp_path / "deploy/runtime-clis" / name).write_bytes(
            (root / "deploy/runtime-clis" / name).read_bytes()
        )
    package = json.loads((source / "package.json").read_bytes())
    lock = json.loads((source / "package-lock.json").read_bytes())
    if field == "dependencies":
        package["dependencies"].pop("@opentui/solid")
        (source / "package.json").write_text(json.dumps(package))
    else:
        path = next(name for name in lock["packages"] if name)
        if field == "population":
            del lock["packages"][path]
        elif field == "authority":
            lock["packages"][path]["resolved"] = "https://foreign.example/payload.tgz"
        else:
            lock["packages"][path]["integrity"] = "sha1-unsupported"
        (source / "package-lock.json").write_text(json.dumps(lock))
    # Keep top-level hashes coherent: the real inner policy must reject drift.
    manifest = json.loads((source / "build-inputs.json").read_bytes())
    manifest["package_json_sha256"] = producer.digest(source / "package.json")
    manifest["package_lock_sha256"] = producer.digest(source / "package-lock.json")
    (source / "build-inputs.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        producer.declarations()


class Response(io.BytesIO):
    def __init__(self, body, status, url, location=None):
        super().__init__(body)
        self.status = status
        self.url = url
        self.headers = Message()
        if location is not None:
            self.headers["Location"] = location

    def geturl(self):
        return self.url


def test_exact_bun_single_asset_transition_and_npm_direct_response(tmp_path):
    body = b"original public payload"
    record = {
        "url": "https://github.com/oven-sh/bun/releases/download/pinned/bun.zip",
        "size": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
    }
    target = "https://release-assets.githubusercontent.com/original?synthetic-signature=private"
    responses = [Response(b"", 302, record["url"], target), Response(body, 200, target)]
    seen = []

    def open_response(url, timeout):
        seen.append(url)
        return responses[len(seen) - 1]

    producer.fetch_tool_input(
        record, tmp_path / "bun", bun=True, opener=SimpleNamespace(open=open_response)
    )
    assert (tmp_path / "bun").read_bytes() == body and seen == [record["url"], target]
    assert all(response.closed for response in responses)
    record["url"] = "https://registry.npmjs.org/package/pinned"
    response = Response(body, 200, record["url"])
    producer.fetch_tool_input(
        record, tmp_path / "npm", opener=SimpleNamespace(open=lambda *_args, **_kwargs: response)
    )
    assert response.closed and (tmp_path / "npm").read_bytes() == body


@pytest.mark.parametrize(
    "target",
    [
        "http://release-assets.githubusercontent.com/file",
        "https://foreign.example/file",
        "https://release-assets.githubusercontent.com:443/file",
        "https://127.0.0.1/file",
        "https://release-assets.githubusercontent.com/file#fragment",
    ],
)
def test_bun_bad_transition_is_closed_and_sanitized(tmp_path, target):
    record = {
        "url": "https://github.com/oven-sh/bun/releases/download/pinned/bun.zip",
        "size": 3,
        "sha256": "f" * 64,
    }
    response = Response(b"", 302, record["url"], target)
    with pytest.raises(ValueError, match="^pinned public tool input acquisition failed$"):
        producer.fetch_tool_input(
            record,
            tmp_path / "absent",
            bun=True,
            opener=SimpleNamespace(open=lambda *_args, **_kwargs: response),
        )
    assert response.closed and not (tmp_path / "absent").exists()


def test_npm_redirect_and_second_bun_hop_and_exception_text_are_refused(tmp_path):
    record = {"url": "https://registry.npmjs.org/original", "size": 3, "sha256": "f" * 64}
    response = Response(b"", 302, record["url"], "https://registry.npmjs.org/other")
    with pytest.raises(ValueError):
        producer.fetch_tool_input(
            record,
            tmp_path / "npm",
            opener=SimpleNamespace(open=lambda *_args, **_kwargs: response),
        )
    assert response.closed
    record["url"] = "https://github.com/oven-sh/bun/releases/download/pinned/bun.zip"
    responses = [
        Response(b"", 302, record["url"], "https://release-assets.githubusercontent.com/one"),
        Response(
            b"",
            307,
            "https://release-assets.githubusercontent.com/one",
            "https://release-assets.githubusercontent.com/two",
        ),
    ]
    with pytest.raises(ValueError):
        producer.fetch_tool_input(
            record,
            tmp_path / "bun",
            bun=True,
            opener=SimpleNamespace(open=lambda *_args, **_kwargs: responses.pop(0)),
        )

    def signed_error(*_args, **_kwargs):
        raise OSError("https://release-assets.githubusercontent.com/?private-signature=synthetic")

    with pytest.raises(ValueError) as caught:
        producer.fetch_tool_input(
            record, tmp_path / "error", bun=True, opener=SimpleNamespace(open=signed_error)
        )
    assert caught.value.__suppress_context__ and "private-signature" not in str(caught.value)


def test_every_npm_3xx_is_refused_before_a_second_open(tmp_path):
    record = {"url": "https://registry.npmjs.org/original", "size": 3, "sha256": "f" * 64}
    responses = []
    for status in range(300, 400):
        response = Response(b"", status, record["url"], "https://registry.npmjs.org/other")
        responses.append(response)
        opened = []

        def open_response(url, timeout, *, opened=opened, response=response):
            opened.append(url)
            return response

        with pytest.raises(ValueError):
            producer.fetch_tool_input(
                record, tmp_path / str(status), opener=SimpleNamespace(open=open_response)
            )
        assert opened == [record["url"]] and response.closed
        assert not (tmp_path / str(status)).exists()
    assert len(responses) == 100


@pytest.mark.parametrize("wrong", ["size", "digest"])
def test_failed_tool_identity_leaves_no_partial_output(tmp_path, wrong):
    body = b"complete bytes"
    record = {
        "url": "https://registry.npmjs.org/original",
        "size": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
    }
    record[wrong if wrong == "size" else "sha256"] = 1 if wrong == "size" else "f" * 64
    response = Response(body, 200, record["url"])
    with pytest.raises(ValueError):
        producer.fetch_tool_input(
            record,
            tmp_path / "output",
            opener=SimpleNamespace(open=lambda *_args, **_kwargs: response),
        )
    assert response.closed and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("unsafe", [False, True])
def test_complete_bun_zip_order_modes_and_prewrite_refusal(tmp_path, unsafe):
    data = bun_zip(unsafe=unsafe)
    path = tmp_path / "archive.zip"
    path.write_bytes(data)
    record = {
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "binary_sha256": hashlib.sha256(b"synthetic Bun executable").hexdigest(),
    }
    output = tmp_path / "complete"
    if unsafe:
        with pytest.raises(ValueError, match="unsafe"):
            producer.extract_bun(path, output, record)
        assert not output.exists() and not (tmp_path / "outside").exists()
    else:
        producer.extract_bun(path, output, record)
        assert (output / "bun-linux-x64/bun").stat().st_mode & 0o777 == 0o755
        assert len(list(output.rglob("*"))) == 2


@pytest.mark.parametrize("number", [signal.SIGINT, signal.SIGTERM])
@pytest.mark.parametrize("window", ["running", "acquiring-handle", "inside-subprocess-wait"])
def test_owned_command_settles_on_owner_signal(tmp_path, number, window):
    child_pid = tmp_path / "child.pid"
    driver = tmp_path / "owner.py"
    # Pid files are published by rename: a reader must never see an empty file.
    record = (
        "def record(pid):\n"
        f" t=m.Path({str(child_pid) + '.owner'!r});t.write_text(str(pid));os.replace(t,{str(child_pid)!r})\n"
    )
    critical = {
        "running": "",
        "acquiring-handle": (
            "original=m.subprocess.Popen\n"
            "def launch(*args,**kwargs):\n"
            " child=original(*args,**kwargs)\n"
            " record(child.pid)\n"
            f" os.kill(os.getpid(),{number})\n"
            " return child\n"
            "m.subprocess.Popen=launch\n"
        ),
        # The signal lands right after subprocess's non-blocking waitpid-lock acquire
        # returns, before its try/finally owns the lock (bu-kiuh8v.34): a handler that
        # raised there leaked the lock and the settling wait() blocked forever.
        "inside-subprocess-wait": (
            "original=m.subprocess.Popen\n"
            "class Fire:\n"
            " def __init__(s,inner):s.inner=inner;s.fired=False\n"
            " def acquire(s,blocking=True,timeout=-1):\n"
            "  got=s.inner.acquire(blocking,timeout)\n"
            "  if got and not blocking and not s.fired:\n"
            f"   s.fired=True;os.kill(os.getpid(),{number})\n"
            "  return got\n"
            " def release(s):s.inner.release()\n"
            " def __enter__(s):s.acquire();return s\n"
            " def __exit__(s,*a):s.release()\n"
            "def launch(*args,**kwargs):\n"
            " child=original(*args,**kwargs)\n"
            " record(child.pid)\n"
            " child._waitpid_lock=Fire(child._waitpid_lock)\n"
            " return child\n"
            "m.subprocess.Popen=launch\n"
        ),
    }[window]
    driver.write_text(
        "import importlib.util,os,signal,sys\n"
        f"s=importlib.util.spec_from_file_location('_private_signal_probe',{str(SCRIPTS / 'produce_default_plugin_artifact.py')!r});m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\n"
        + record
        + critical
        + "try:\n"
        " with m.owner_signals():\n"
        f"  m.Operations().run([sys.executable,'-c',\"import os,time,pathlib;p=pathlib.Path({str(child_pid) + '.child'!r});p.write_text(str(os.getpid()));os.replace(p,{str(child_pid)!r});time.sleep(90)\"],cwd=m.Path({str(tmp_path)!r}),env={{'PATH':'/usr/bin:/bin'}})\n"
        "except m.OwnerSignal as e:\n"
        " signal.signal(e.number,signal.SIG_DFL);os.kill(os.getpid(),e.number)\n"
    )
    owner = subprocess.Popen([sys.executable, "-I", "-B", str(driver)], start_new_session=True)
    owned = None
    try:
        deadline = time.monotonic() + 10
        if window == "running":
            while not child_pid.exists() and time.monotonic() < deadline:
                assert owner.poll() is None
                time.sleep(0.01)
            owned = int(child_pid.read_text())
            os.kill(owner.pid, number)
        assert owner.wait(timeout=10) == -number
        if child_pid.exists():
            owned = int(child_pid.read_text())
            assert not Path("/proc", str(owned)).exists()
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait()
        if owned is not None:
            try:
                os.killpg(owned, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_declarations_and_provenance_ignore_unrelated_runtime_output_maps(tmp_path, monkeypatch):
    import shutil

    source = SCRIPTS.parents[1]
    recipe = tmp_path / "recipe"
    monkeypatch.setattr(producer, "RECIPE", source)
    for name in producer.pins():
        output = recipe / name
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, output)
    unrelated = recipe / "deploy/runtime-clis/runtime-inputs.json"
    original = json.loads((source / "deploy/runtime-clis/runtime-inputs.json").read_bytes())
    producer.write_json(unrelated, original)
    monkeypatch.setattr(producer, "RECIPE", recipe)
    first = producer.declarations()
    before = producer.provenance(first[0], producer.pins(), {})
    original["components"]["opencode"]["tree_sha256"] = "a" * 64
    producer.write_json(unrelated, original)
    assert producer.declarations() == first
    assert producer.provenance(first[0], producer.pins(), {}) == before
    assert "deploy/runtime-clis/runtime-inputs.json" not in producer.pins()
    assert producer.DECLARATION + "/build-inputs.json" in producer.pins()
    unrelated.unlink()
    assert producer.declarations() == first
    assert producer.provenance(first[0], producer.pins(), {}) == before


def test_node_identity_binds_complete_declared_tree_with_healthy_companion(tmp_path, monkeypatch):
    monkeypatch.setattr(producer, "RECIPE", SCRIPTS.parents[1])
    node = tmp_path / "node"
    binary = node / "bin/node"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"fixed official binary stand-in")
    binary.chmod(0o755)
    npm = node / "lib/node_modules/npm/package.json"
    npm.parent.mkdir(parents=True)
    npm.write_text(json.dumps({"version": "10.9.9"}))
    asset = node / "share/full-native-asset"
    asset.parent.mkdir()
    asset.write_bytes(b"complete original asset")
    for path in [node, *node.rglob("*")]:
        if path.is_dir():
            path.chmod(0o755)
        elif path != binary:
            path.chmod(0o644)
    declaration = {
        "node": {
            "binary_sha256": producer.digest(binary),
            "tree_sha256": artifacts.tree_digest(artifacts.inventory(node, closed=True)),
        },
        "npm": "10.9.9",
    }
    monkeypatch.setattr(producer, "NODE", node)
    producer.validate_node(declaration)
    asset.write_bytes(b"wrong ancillary asset, same executable")
    with pytest.raises(ValueError, match="complete official Node"):
        producer.validate_node(declaration)
    asset.write_bytes(b"complete original asset")
    producer.validate_node(declaration)
