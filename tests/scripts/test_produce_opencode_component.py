"""Position full-workspace orchestration and input refusal without package execution."""

import copy
import importlib.util
import io
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts/runtime_clis"
SPEC = importlib.util.spec_from_file_location(
    "_test_opencode_component", SCRIPTS / "produce_opencode_component.py"
)
producer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(producer)


def test_actual_declarations_bind_complete_original_sources_and_four_root_seed(monkeypatch):
    monkeypatch.setattr(producer, "RECIPE", SCRIPTS.parents[1])
    manifest, series, models = producer.declarations()
    assert len(series["patches"]) == 11
    assert manifest["default_plugin"]["complete_lock_packages"] == 141
    assert len(models["logos"]) == 235
    assert models["catalogue"]["models"] == 8939
    for record in manifest["archives"]:
        assert record["version"] == "1.17.7"
        assert record["integrity"].startswith("sha512-")
    assert "deploy/runtime-clis/runtime-inputs.json" not in producer.pins()


def test_stock_commands_keep_lifecycles_ui_platform_installers_and_no_release(
    tmp_path, monkeypatch
):
    calls = []
    monkeypatch.setattr(producer, "ROOT", tmp_path)
    operations = SimpleNamespace(run=lambda argv, **kwargs: calls.append((argv, kwargs)) or "")
    producer.build_commands(operations)
    assert [argv[1:] for argv, _ in calls] == [
        ["install", "--frozen-lockfile"],
        ["run", "packages/opencode/script/build.ts", "--single"],
    ]
    for _argv, options in calls:
        environment = options["env"]
        assert environment["OPENCODE_VERSION"] == "1.17.7"
        assert environment["OPENCODE_CHANNEL"] == "latest"
        assert environment["MODELS_DEV_API_JSON"] == str(tmp_path / "models/api.json")
        assert environment["OPENCODE_MODELS_LOGOS"] == str(tmp_path / "models/logos")
        assert "OPENCODE_RELEASE" not in environment
        assert "NODE_OPTIONS" not in environment
        assert "BUTLERS_EGRESS_MODE" not in environment


def test_complete_lock_semantics_and_all_original_source_are_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(producer, "RECIPE", SCRIPTS.parents[1])
    transport = producer.helper("produce_codex_artifacts")
    root = tmp_path / "root"
    source = root / "source"
    source.mkdir(parents=True)
    (source / "wire.ts").write_bytes(b"original auth transport and tools")
    (source / "bun.lock").write_bytes(b"original JSONC spelling")
    expected = transport.inventory(source)
    lock = {"packages": {"native-package": ["original", "integrity"]}}
    monkeypatch.setattr(producer, "ROOT", root)
    monkeypatch.setattr(producer, "lock_identity", lambda _operations: lock)
    (source / "bun.lock").write_bytes(b"different JSONC spelling; same full semantic object")
    producer.source_identity(expected, copy.deepcopy(lock), None)
    with pytest.raises(ValueError, match="lock semantics"):
        producer.source_identity(expected, {"packages": {}}, None)
    (source / "wire.ts").write_bytes(b"changed auth transport")
    with pytest.raises(ValueError, match="original OpenCode source"):
        producer.source_identity(expected, lock, None)


@pytest.fixture
def model_inputs(tmp_path, monkeypatch):
    root = tmp_path / "root"
    logos = root / "models/logos"
    logos.mkdir(parents=True)
    (root / "models/api.json").write_text(json.dumps({"provider": {"models": {"model": {}}}}))
    (logos / "provider.svg").write_bytes(b"complete original logo")
    inputs = {
        "catalogue": {
            "sha256": producer.digest(root / "models/api.json"),
            "providers": 1,
            "models": 1,
        },
        "logos": [
            {"provider": "provider", "size": 22, "sha256": producer.digest(logos / "provider.svg")}
        ],
    }
    monkeypatch.setattr(producer, "ROOT", root)
    return root, inputs


@pytest.mark.parametrize(
    "drift", ["catalogue", "missing-logo", "foreign-logo", "logo-bytes", "logo-link"]
)
def test_entire_catalogue_and_logo_population_is_required(model_inputs, drift):
    root, inputs = model_inputs
    producer.models_identity(inputs)
    logos = root / "models/logos"
    if drift == "catalogue":
        inputs["catalogue"]["models"] = 2
    elif drift == "missing-logo":
        (logos / "provider.svg").unlink()
    elif drift == "foreign-logo":
        (logos / "foreign.svg").write_bytes(b"not declared")
    elif drift == "logo-bytes":
        (logos / "provider.svg").write_bytes(b"different logo")
    else:
        target = root / "original.svg"
        (logos / "provider.svg").rename(target)
        (logos / "provider.svg").symlink_to(target)
    with pytest.raises(ValueError, match="catalogue|logo"):
        producer.models_identity(inputs)


@pytest.fixture
def model_snapshot(tmp_path, monkeypatch):
    import gzip
    import hashlib
    import tarfile

    root = tmp_path / "work"
    root.mkdir()
    recipe = tmp_path / "recipe"
    path = recipe / producer.MODEL_SNAPSHOT
    path.parent.mkdir(parents=True)
    catalogue = json.dumps({"provider": {"models": {"model": {}}}}).encode()
    logo = b"<svg>whole original logo</svg>"
    models = {
        "catalogue": {"sha256": hashlib.sha256(catalogue).hexdigest(), "providers": 1, "models": 1},
        "logos": [
            {"provider": "provider", "sha256": hashlib.sha256(logo).hexdigest(), "size": len(logo)}
        ],
        "snapshot": {"path": "model-inputs.tar.gz", "format": "ustar+gzip", "members": 2},
    }
    payload = [("api.json", catalogue), ("logos/provider.svg", logo)]

    def pack(entries=payload, *, changed=None):
        with path.open("wb") as output:
            with gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0) as compressed:
                with tarfile.open(
                    fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT
                ) as archive:
                    for name, data in entries:
                        item = tarfile.TarInfo(name)
                        item.size = len(data)
                        item.mode = 0o644
                        if changed is not None:
                            changed(item)
                        archive.addfile(item, io.BytesIO(data))
        models["snapshot"].update(size=path.stat().st_size, sha256=producer.digest(path))

    pack()
    monkeypatch.setattr(producer, "ROOT", root)
    monkeypatch.setattr(producer, "RECIPE", recipe)
    return root, path, models, payload, pack


def test_model_snapshot_is_complete_atomic_and_transport_independent(model_snapshot, monkeypatch):
    import os
    import urllib.request

    root, path, models, payload, _pack = model_snapshot
    monkeypatch.setattr(
        urllib.request, "build_opener", lambda *_a, **_k: pytest.fail("model HTTP operation")
    )
    original = path.read_bytes()
    previous = os.umask(0o027)
    try:
        producer.materialize_models(models)
    finally:
        os.umask(previous)
    producer.models_identity(models)
    assert (root / "models").stat().st_mode & 0o7777 == 0o755
    assert (root / "models/logos").stat().st_mode & 0o7777 == 0o755
    for name, data in payload:
        assert (root / "models" / name).read_bytes() == data
        assert (root / "models" / name).stat().st_mode & 0o7777 == 0o644
    assert sorted(p.name for p in root.iterdir()) == ["models"]
    assert path.read_bytes() == original
    with pytest.raises(ValueError, match="snapshot input"):
        producer.materialize_models(models)


@pytest.mark.parametrize(
    "drift",
    [
        "archive",
        "missing",
        "extra",
        "duplicate",
        "order",
        "path",
        "link",
        "mode",
        "owner",
        "time",
        "payload",
        "population",
    ],
)
def test_model_snapshot_refuses_noncanonical_or_changed_data(model_snapshot, drift):
    import tarfile

    root, path, models, payload, pack = model_snapshot
    if drift == "archive":
        data = bytearray(path.read_bytes())
        data[4] = 1  # gzip timestamp changes while all decompressed payload bytes remain valid.
        path.write_bytes(data)
    elif drift == "missing":
        pack(payload[:1])
    elif drift == "extra":
        pack([*payload, ("undeclared", b"foreign")])
    elif drift == "duplicate":
        pack([payload[0], *payload])
    elif drift == "order":
        pack(list(reversed(payload)))
    elif drift == "path":
        pack([payload[0], ("../escape", b"foreign")])
    elif drift == "link":
        pack(changed=lambda item: setattr(item, "type", tarfile.SYMTYPE))
    elif drift == "mode":
        pack(changed=lambda item: setattr(item, "mode", 0o755))
    elif drift == "owner":
        pack(changed=lambda item: setattr(item, "uid", 1234))
    elif drift == "time":
        pack(changed=lambda item: setattr(item, "mtime", 1))
    elif drift == "payload":
        pack([payload[0], (payload[1][0], b"wrong original logo")])
    else:
        models["catalogue"]["models"] = 2
    with pytest.raises(ValueError, match="snapshot|population"):
        producer.materialize_models(models)
    assert list(root.iterdir()) == []
    assert not (root.parent / "escape").exists()


@pytest.fixture
def build_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(producer, "RECIPE", SCRIPTS.parents[1])
    artifacts = producer.helper("runtime_artifacts")
    transport = producer.helper("produce_codex_artifacts")
    root = tmp_path / "work"
    root.mkdir()
    template = tmp_path / "complete-source"
    template.mkdir()
    (template / "wire.ts").write_bytes(b"all auth/tool transports")
    (template / "bun.lock").write_bytes(b"all records")
    shutil.copytree(template, root / "source")
    (root / "source.tgz").write_bytes(b"exact archive")
    (root / "bun.zip").write_bytes(b"exact Bun")
    cache = root / "bun-cache"
    cache.mkdir()
    (cache / "complete").write_bytes(b"full graph")
    recipe = {"source": "current"}
    plugin = [{"full-plugin": "141-package graph"}]
    series = {"version": "1.17.7", "source_tree_sha256": "exact"}
    seed = {
        "status": "NONFINAL",
        "recipe": recipe,
        "source": transport.inventory(template),
        "lock": {"all": "3124 original packages"},
        "plugin": plugin,
        "inputs": {"bun-cache": transport.inventory(cache)},
        "source_archive_sha256": producer.digest(root / "source.tgz"),
        "bun_archive_sha256": producer.digest(root / "bun.zip"),
    }
    producer.write_json(root / "seed.json", seed)
    calls = []
    monkeypatch.setattr(producer, "ROOT", root)
    monkeypatch.setattr(producer, "identity", lambda _manifest: None)
    monkeypatch.setattr(producer, "declarations", lambda: ({}, series, {}))
    monkeypatch.setattr(producer, "pins", lambda: recipe.copy())
    monkeypatch.setattr(producer, "plugin_input", lambda _manifest: plugin.copy())
    monkeypatch.setattr(producer, "models_identity", lambda _models: calls.append("models"))
    monkeypatch.setattr(transport, "network_none", lambda: calls.append("guard"))
    monkeypatch.setattr(
        producer,
        "helper",
        lambda name: {"runtime_artifacts": artifacts, "produce_codex_artifacts": transport}[name],
    )

    def prepare(_series):
        calls.append("prepare")
        shutil.copytree(template, root / "source")

    monkeypatch.setattr(producer, "prepare", prepare)
    monkeypatch.setattr(
        producer, "build_commands", lambda _operations: calls.append("full-stock-build")
    )
    monkeypatch.setattr(producer, "source_identity", lambda *_args: calls.append("source-lock"))
    monkeypatch.setattr(producer, "assemble", lambda *_args: calls.append("assemble"))
    return root, recipe, seed, calls


def test_offline_build_requires_preparation_then_fresh_full_build(build_inputs):
    _root, _recipe, _seed, calls = build_inputs
    producer.build(None)
    assert calls == ["guard", "models", "prepare", "full-stock-build", "source-lock", "assemble"]


@pytest.mark.parametrize("drift", ["recipe", "cache", "archive", "bun", "plugin"])
def test_closed_preparation_drift_refuses_before_source_or_build(build_inputs, drift, monkeypatch):
    root, recipe, _seed, calls = build_inputs
    if drift == "recipe":
        recipe["source"] = "changed"
    elif drift == "cache":
        (root / "bun-cache/foreign").write_bytes(b"unreviewed package")
    elif drift == "archive":
        (root / "source.tgz").write_bytes(b"wrong source")
    elif drift == "bun":
        (root / "bun.zip").write_bytes(b"wrong Bun")
    else:
        monkeypatch.setattr(producer, "plugin_input", lambda _manifest: [])
    with pytest.raises(ValueError, match="closed OpenCode"):
        producer.build(None)
    assert calls == ["guard"]
    assert (root / "source/wire.ts").read_bytes() == b"all auth/tool transports"


@pytest.fixture
def plugin_seed(tmp_path, monkeypatch):
    monkeypatch.setattr(producer, "RECIPE", SCRIPTS.parents[1])
    artifacts = producer.helper("runtime_artifacts")
    plugin = producer.helper("produce_default_plugin_artifact")
    declaration, lock = plugin.declarations()
    seedroot = tmp_path / "seed"
    seedroot.mkdir(mode=0o755)
    for name in ("package.json", "package-lock.json"):
        shutil.copyfile(SCRIPTS.parents[1] / plugin.DECLARATION / name, seedroot / name)
    for relative, record in lock["packages"].items():
        if not relative:
            continue
        directory = seedroot / relative
        directory.mkdir(parents=True)
        (directory / "package.json").write_text(
            json.dumps(
                {
                    "name": relative.rsplit("node_modules/", 1)[1],
                    "version": record["version"],
                }
            )
        )
    for path in [seedroot, *seedroot.rglob("*")]:
        path.chmod(0o755 if path.is_dir() else 0o644)
    entries = sorted(artifacts.inventory(seedroot, closed=True), key=lambda item: item["path"])
    manifest = {
        "version": "1.17.7",
        "default_plugin": {"entries_sha256": artifacts.tree_digest(entries)},
    }
    metadata = {
        "schema": "butlers.opencode-default-plugin/v1",
        "package": "@opencode-ai/plugin",
        "version": "1.17.7",
        "node": "v22.23.4",
        "npm": "10.9.9",
        "package_lock_sha256": declaration["package_lock_sha256"],
        "entries": entries,
        "provenance": {
            "schema": "butlers.default-plugin-provenance/v1",
            "recipe": plugin.pins(),
            "public_inputs": declaration,
        },
    }
    producer.write_json(seedroot / "seed.json", metadata)
    monkeypatch.setattr(producer, "PLUGIN", seedroot)
    return seedroot, manifest, metadata


@pytest.mark.parametrize(
    "drift", ["payload", "foreign", "metadata", "recipe", "mode", "lock-package"]
)
def test_real_seed_validation_requires_full_graph_recipe_modes_and_eight_fields(plugin_seed, drift):
    seedroot, manifest, metadata = plugin_seed
    assert producer.plugin_input(manifest)
    package = seedroot / "node_modules/@opencode-ai/plugin/package.json"
    if drift == "payload":
        package.write_bytes(b"wrong bytes")
    elif drift == "foreign":
        (seedroot / "foreign").write_bytes(b"not declared")
    elif drift == "metadata":
        metadata["ninth_field"] = "not admitted"
        producer.write_json(seedroot / "seed.json", metadata)
    elif drift == "recipe":
        metadata["provenance"]["recipe"]["undeclared-source"] = "wrong"
        producer.write_json(seedroot / "seed.json", metadata)
    elif drift == "mode":
        package.chmod(0o600)
    else:
        # Even a re-described payload cannot omit a locked package.
        shutil.rmtree(seedroot / "node_modules/@opencode-ai/plugin")
        artifacts = producer.helper("runtime_artifacts")
        entries = sorted(
            (item for item in artifacts.inventory(seedroot) if item["path"] != "seed.json"),
            key=lambda item: item["path"],
        )
        metadata["entries"] = entries
        manifest["default_plugin"]["entries_sha256"] = artifacts.tree_digest(entries)
        producer.write_json(seedroot / "seed.json", metadata)
    with pytest.raises(ValueError, match="seed differs|mode|lock|population|package"):
        producer.plugin_input(manifest)


def test_default_plugin_docker_output_matches_named_context_input():
    root = SCRIPTS.parents[1]
    output = (root / "Dockerfile.default-plugin-artifacts").read_text()
    caller = (root / "Dockerfile.opencode-artifacts").read_text()
    assert "COPY --from=producer /artifacts/default-plugin/ /" in output
    assert "COPY --from=default-plugin-artifacts / /inputs/default-plugin/" in caller


@pytest.fixture
def installed_package(tmp_path, monkeypatch):
    import base64
    import hashlib
    import tarfile

    monkeypatch.setattr(producer, "RECIPE", SCRIPTS.parents[1])
    artifacts = producer.helper("runtime_artifacts")
    native = producer.helper("produce_native_packages")
    root = tmp_path / "work"
    archives = root / "archives"
    archives.mkdir(parents=True)
    package = {
        "name": "opencode-ai",
        "version": "1.17.7",
        "bin": {"opencode": "./bin/opencode.exe"},
        "optionalDependencies": {"opencode-linux-x64": "1.17.7", "retained-platform": "1.17.7"},
    }
    records = []
    for name, files in (
        (
            "opencode-ai",
            {
                "package.json": json.dumps(package).encode(),
                "bin/opencode.exe": b"original published launcher",
                "postinstall.mjs": b"original postinstall",
                "LICENSE": b"whole license",
            },
        ),
        (
            "opencode-linux-x64",
            {
                "package.json": json.dumps(
                    {"name": "opencode-linux-x64", "version": "1.17.7"}
                ).encode(),
                "bin/opencode": b"original native target",
                "native-data": b"retained native assets",
            },
        ),
    ):
        archive = archives / (name + ".tgz")
        with tarfile.open(archive, "w:gz") as stream:
            for relative, data in files.items():
                member = tarfile.TarInfo("package/" + relative)
                member.mode = 0o755 if relative.startswith("bin/") else 0o644
                member.size = len(data)
                stream.addfile(member, io.BytesIO(data))
        records.append(
            {
                "name": name,
                "version": "1.17.7",
                "size": archive.stat().st_size,
                "sha256": producer.digest(archive),
                "integrity": "sha512-"
                + base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode(),
            }
        )
    binary = root / "source/packages/opencode/dist/opencode-linux-x64/bin/opencode"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"full compiled UI tools and auth")
    binary.chmod(0o755)
    source_asset = root / "source/native-graph-data"
    source_asset.write_bytes(b"complete graph")
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "seed.json").write_bytes(b"complete validated metadata")
    (plugin / "native-plugin-data").write_bytes(b"complete plugin asset")
    plugin_entries = artifacts.inventory(plugin)
    recipe = {"source": "bound"}
    manifest = {"archives": records, "version": "1.17.7"}
    seed = {"recipe": recipe, "plugin": plugin_entries, "lock": {"all": "original lock"}}
    series = {"source_tree_sha256": "source", "archive": {"sha256": "archive"}}
    calls = []
    transport = producer.helper("produce_codex_artifacts")
    monkeypatch.setattr(transport, "network_none", lambda: calls.append("guard"))
    monkeypatch.setattr(
        producer,
        "helper",
        lambda name: {
            "runtime_artifacts": artifacts,
            "produce_native_packages": native,
            "produce_codex_artifacts": transport,
        }[name],
    )
    monkeypatch.setattr(producer, "ROOT", root)
    monkeypatch.setattr(producer, "OUTPUT", tmp_path / "output")
    monkeypatch.setattr(producer, "PLUGIN", plugin)
    monkeypatch.setattr(producer, "pins", lambda: recipe.copy())
    monkeypatch.setattr(producer, "plugin_input", lambda _manifest: plugin_entries.copy())

    def postinstall(_operations, argv, *, cwd):
        calls.append("original-postinstall")
        assert argv[1:] == ["postinstall.mjs"]
        assert (cwd / "postinstall.mjs").read_bytes() == b"original postinstall"
        launcher = cwd / "bin/opencode.exe"
        launcher.unlink()
        launcher.hardlink_to(cwd / producer.BIN)

    monkeypatch.setattr(producer, "command", postinstall)
    return manifest, series, seed, calls, artifacts, recipe


def test_complete_original_package_and_graph_are_independently_copied(installed_package):
    import os

    manifest, series, seed, calls, artifacts, _recipe = installed_package
    old_umask = os.umask(0o027)
    try:
        producer.assemble(manifest, series, seed, None)
    finally:
        os.umask(old_umask)
    output = producer.OUTPUT / "components/opencode"
    entries = artifacts.inventory(output, closed=True)
    assert entries
    assert calls == ["original-postinstall", "guard"]
    assert (
        output / "node_modules/opencode-linux-x64/native-data"
    ).read_bytes() == b"retained native assets"
    assert (output / "source/native-graph-data").read_bytes() == b"complete graph"
    assert (output / "default-plugin/native-plugin-data").read_bytes() == b"complete plugin asset"
    main = output / producer.BIN
    launcher = output / "bin/opencode.exe"
    assert main.read_bytes() == launcher.read_bytes() == b"full compiled UI tools and auth"
    assert main.stat().st_ino != launcher.stat().st_ino
    original = producer.ROOT / "source/native-graph-data"
    assert original.stat().st_ino != (output / "source/native-graph-data").stat().st_ino
    original.write_bytes(b"later source mutation")
    assert (output / "source/native-graph-data").read_bytes() == b"complete graph"


def test_postinstall_may_only_change_declared_launcher_and_compiled_target(
    installed_package, monkeypatch
):
    manifest, series, seed, _calls, _artifacts, _recipe = installed_package
    original = producer.command

    def changed(operations, argv, *, cwd):
        original(operations, argv, cwd=cwd)
        (cwd / "LICENSE").write_bytes(b"dropped original data")

    monkeypatch.setattr(producer, "command", changed)
    with pytest.raises(ValueError, match="original installed OpenCode payload"):
        producer.assemble(manifest, series, seed, None)
    assert not producer.OUTPUT.exists()
