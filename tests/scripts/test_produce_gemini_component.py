"""Complete-graph Gemini producer controls; no npm, fetch or native execution."""

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts/runtime_clis"
SPEC = importlib.util.spec_from_file_location(
    "_test_gemini_component_builder", SCRIPTS / "produce_gemini_component.py"
)
producer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(producer)


def test_sdk_outputs_bind_whole_population_archive_and_declared_source(tmp_path, monkeypatch):
    recipe = tmp_path / "recipe"
    declaration = recipe / "deploy/runtime-clis/gemini/artifact-inputs.json"
    declaration.parent.mkdir(parents=True)
    sdk = tmp_path / "sdk"
    sdk.mkdir()
    records = []
    for name, vendor in producer.SDK_VENDORS.items():
        root = sdk / vendor
        root.mkdir()
        archive = root / (vendor + ".tgz")
        archive.write_bytes(vendor.encode())
        record = {
            "name": name,
            "path": "vendor/" + archive.name,
            "sha256": producer.digest(archive),
            "source_tree_sha256": "source-" + vendor,
            "source_series_sha256": "series-" + vendor,
        }
        records.append(record)
        producer.write_json(
            root / "provenance.json",
            {
                "schema": "butlers.npm-artifact/v1",
                "vendor": vendor,
                "archive_sha256": record["sha256"],
                "source_tree_sha256": record["source_tree_sha256"],
                "recipe": {
                    f"deploy/runtime-clis/{vendor}/series.json": record["source_series_sha256"]
                },
            },
        )
    producer.write_json(declaration, {"artifacts": records})
    monkeypatch.setattr(producer, "RECIPE", recipe)
    monkeypatch.setattr(producer, "SDK", sdk)
    assert set(producer.sdk_inputs()) == set(producer.SDK_VENDORS)
    target = sdk / "js-genai"
    archive = target / "js-genai.tgz"
    archive.write_bytes(b"changed")
    with pytest.raises(ValueError, match="identity"):
        producer.sdk_inputs()
    archive.write_bytes(b"js-genai")
    (target / "foreign").write_bytes(b"not part of complete output")
    with pytest.raises(ValueError, match="complete"):
        producer.sdk_inputs()
    (target / "foreign").unlink()
    archive.unlink()
    archive.symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="regular"):
        producer.sdk_inputs()


@pytest.fixture
def build_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(producer, "RECIPE", SCRIPTS.parents[1])
    modules = {
        name: producer.helper(name)
        for name in ("produce_npm_artifact", "produce_codex_artifacts", "runtime_artifacts")
    }
    root = tmp_path / "builder"
    root.mkdir()
    template = tmp_path / "original-source"
    template.mkdir()
    producer.write_json(template / "package.json", {"version": "0.66.0"})
    producer.write_json(
        template / "package-lock.json",
        {
            "packages": {
                "node_modules/node-pty": {"version": "1.0.0"},
                "node_modules/nan": {"version": "2.23.0"},
            }
        },
    )
    (template / "CONTRIBUTING.md").write_bytes(b"original documentation")
    (template / "source.ts").write_bytes(b"original transport and tools")
    (root / "source.tgz").write_bytes(b"whole source archive")
    shutil.copytree(template, root / "source")
    for name in ("npm-cache", "browser-cache"):
        (root / name).mkdir()
    for name in ("npm-user.npmrc", "npm-global.npmrc"):
        (root / name).write_bytes(b"")
    current_recipe = {"builder": "unchanged"}
    sdk = tmp_path / "sdk.tgz"
    sdk.write_bytes(b"complete separately produced SDK")
    inputs = {"sdk": sdk}
    calls = []
    transport = modules["produce_codex_artifacts"]
    monkeypatch.setattr(transport, "network_none", lambda: calls.append("guard"))
    monkeypatch.setattr(producer, "ROOT", root)
    monkeypatch.setattr(producer, "OUTPUT", tmp_path / "output")
    monkeypatch.setattr(producer, "helper", lambda name: modules[name])
    series = {
        "version": "0.66.0",
        "commit": "pinned-commit",
        "source_tree_sha256": "whole-source",
        "archive": {"sha256": producer.digest(root / "source.tgz")},
    }
    monkeypatch.setattr(producer, "identity", lambda: series)
    monkeypatch.setattr(producer, "pins", lambda: current_recipe.copy())
    monkeypatch.setattr(producer, "sdk_inputs", lambda: inputs)

    def prepare(_series, *, offline):
        assert offline
        calls.append("prepare")
        shutil.copytree(template, root / "source")

    def compile(_series, *, offline):
        assert offline
        calls.append("compile")
        source = root / "source"
        bundle = source / "bundle"
        bundle.mkdir()
        (bundle / "gemini.js").write_bytes(b"full compiled transport/auth/tools")
        graph = source / "node_modules/native-platform"
        graph.mkdir(parents=True)
        (graph / "asset").write_bytes(b"native asset")
        (graph / "asset").chmod(0o751)
        for name, version in (("node-pty", "1.0.0"), ("nan", "2.23.0")):
            directory = source / "node_modules" / name
            directory.mkdir()
            producer.write_json(directory / "package.json", {"name": name, "version": version})
        native = source / "node_modules/node-pty/build/Release/pty.node"
        native.parent.mkdir(parents=True)
        native.write_bytes(b"synthetic native payload; never executed")
        (source / "node_modules/nan/nan.h").write_bytes(b"synthetic header")
        for relative in producer.DOC_LINKS:
            link = source / relative
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(source / "CONTRIBUTING.md")

    monkeypatch.setattr(producer, "prepare", prepare)
    monkeypatch.setattr(producer, "build_commands", compile)

    def native_load(argv, *, offline, cwd, commit):
        assert offline and cwd == root / "source" and commit == "pinned-commit"
        assert argv[:3] == [str(producer.NODE / "bin/node"), "--input-type=commonjs", "-e"]
        assert 'require("node-pty")' in argv[3] and "pty.native.fork" in argv[3]

    monkeypatch.setattr(producer, "command", native_load)
    producer.write_json(
        root / "seed.json",
        {
            "status": "NONFINAL",
            "recipe": current_recipe.copy(),
            "sdk": {name: producer.digest(path) for name, path in inputs.items()},
            "source": transport.inventory(root / "source"),
            "source_archive_sha256": producer.digest(root / "source.tgz"),
            "npm_cache": [],
            "browser_cache": [],
            "configuration": {
                name: producer.digest(root / name)
                for name in ("npm-user.npmrc", "npm-global.npmrc")
            },
        },
    )
    return root, current_recipe, calls, compile


def test_complete_component_preserves_graph_assets_and_documentation(build_fixture):
    root, _recipe, calls, _compile = build_fixture
    producer.build()
    assert calls == ["guard", "prepare", "compile", "guard"]
    output = producer.OUTPUT / "components/gemini"
    assert (output / "source.ts").read_bytes() == b"original transport and tools"
    asset = output / "node_modules/native-platform/asset"
    assert asset.read_bytes() == b"native asset"
    assert asset.stat().st_mode & 0o777 == 0o755
    assert asset.stat().st_ino != (root / "source/node_modules/native-platform/asset").stat().st_ino
    for relative, target in producer.DOC_LINKS.items():
        assert (output / relative).readlink() == Path(target)
        assert (output / relative).read_bytes() == b"original documentation"
    provenance = json.loads((producer.OUTPUT / "provenance.json").read_bytes())
    artifacts = producer.helper("runtime_artifacts")
    assert provenance["tree_sha256"] == artifacts.tree_digest(
        artifacts.inventory(output, closed=True)
    )
    assert str(root) not in json.dumps(provenance)
    assert provenance["git_commit"] == "pinned-commit"


@pytest.mark.parametrize(
    "changed", ["source.tgz", "npm-user.npmrc", "browser-cache/payload", "configuration omission"]
)
def test_closed_seed_drift_refuses_before_fresh_install(build_fixture, changed):
    root, _recipe, calls, _compile = build_fixture
    if changed == "configuration omission":
        seed = json.loads((root / "seed.json").read_bytes())
        del seed["configuration"]["npm-user.npmrc"]
        producer.write_json(root / "seed.json", seed)
    else:
        (root / changed).write_bytes(b"changed input")
    with pytest.raises(ValueError, match="preparation changed"):
        producer.build()
    assert calls == ["guard"]
    assert not producer.OUTPUT.exists()
    assert (root / "source/source.ts").exists()


def test_final_recipe_change_cannot_publish_after_complete_build(build_fixture, monkeypatch):
    _root, recipe, calls, compile = build_fixture

    def changed(series, *, offline):
        compile(series, offline=offline)
        recipe["builder"] = "changed during build"

    monkeypatch.setattr(producer, "build_commands", changed)
    with pytest.raises(ValueError, match="recipe changed"):
        producer.build()
    assert calls == ["guard", "prepare", "compile", "guard"]
    assert not producer.OUTPUT.exists()
    assert not list(producer.OUTPUT.parent.glob("gemini-*"))


def test_missing_or_changed_generated_doc_link_cannot_escape_component(build_fixture, monkeypatch):
    root, _recipe, _calls, compile = build_fixture

    def changed(series, *, offline):
        compile(series, offline=offline)
        link = root / "source/bundle/docs/CONTRIBUTING.md"
        link.unlink()
        link.symlink_to("/foreign/CONTRIBUTING.md")

    monkeypatch.setattr(producer, "build_commands", changed)
    with pytest.raises(ValueError, match="documentation link"):
        producer.build()
    assert not producer.OUTPUT.exists()


def test_original_source_mutation_by_lifecycle_is_not_accepted(build_fixture, monkeypatch):
    root, _recipe, _calls, compile = build_fixture

    def changed(series, *, offline):
        compile(series, offline=offline)
        (root / "source/source.ts").write_bytes(b"removed original tools")

    monkeypatch.setattr(producer, "build_commands", changed)
    with pytest.raises(ValueError, match="source changed"):
        producer.build()
    assert not producer.OUTPUT.exists()


def test_original_build_commands_keep_lifecycles_offline_and_pinned_commit(monkeypatch):
    monkeypatch.setattr(producer, "RECIPE", SCRIPTS.parents[1])
    commands = []
    monkeypatch.setattr(producer, "command", lambda argv, **kwargs: commands.append((argv, kwargs)))
    producer.build_commands({"commit": "exact-commit"}, offline=True)
    assert len(commands) == 2
    assert commands[0][0][-3:] == ["ci", "--foreground-scripts", "--offline"]
    assert commands[1][0][-2:] == ["run", "build"]
    assert all(item[1]["offline"] and item[1]["commit"] == "exact-commit" for item in commands)
    assert all("--ignore-scripts" not in item[0] and "--omit" not in item[0] for item in commands)


def test_copied_component_is_checked_before_publication(build_fixture, monkeypatch):
    _root, _recipe, _calls, _compile = build_fixture
    artifacts = producer.helper("runtime_artifacts")
    original = artifacts._copy

    def incomplete(source, destination, entries):
        original(source, destination, entries)
        (destination / "node_modules/native-platform/asset").unlink()

    monkeypatch.setattr(artifacts, "_copy", incomplete)
    with pytest.raises(ValueError, match="copied Gemini component"):
        producer.build()
    assert not producer.OUTPUT.exists()


def test_real_command_environment_binds_commit_without_ambient_passthrough(monkeypatch):
    monkeypatch.setattr(producer, "RECIPE", SCRIPTS.parents[1])
    monkeypatch.setenv("GIT_COMMIT", "forged-ambient-commit")
    monkeypatch.setenv("HTTPS_PROXY", "synthetic-foreign-proxy")
    commands = []
    monkeypatch.setattr(
        producer.subprocess, "run", lambda argv, **kwargs: commands.append((argv, kwargs))
    )
    producer.command(
        ["owned-build-command"], offline=True, cwd=producer.ROOT, commit="exact-commit"
    )
    assert len(commands) == 1
    environment = commands[0][1]["env"]
    assert environment["GIT_COMMIT"] == "exact-commit"
    assert environment["npm_config_offline"] == "true"
    assert "HTTPS_PROXY" not in environment
    assert commands[0][1]["check"] is True


@pytest.mark.parametrize("drift", ["package", "version", "native", "header", "link"])
def test_optional_native_hook_failure_cannot_publish_complete_component(
    build_fixture, monkeypatch, drift
):
    root, _recipe, _calls, compile = build_fixture

    def incomplete(series, *, offline):
        compile(series, offline=offline)
        source = root / "source/node_modules"
        if drift == "package":
            shutil.rmtree(source / "node-pty")
        elif drift == "version":
            producer.write_json(source / "nan/package.json", {"name": "nan", "version": "0.0.0"})
        elif drift == "native":
            (source / "node-pty/build/Release/pty.node").unlink()
        elif drift == "header":
            (source / "nan/nan.h").unlink()
        else:
            package = source / "node-pty/package.json"
            data = package.read_bytes()
            package.unlink()
            target = root / "outside-package.json"
            target.write_bytes(data)
            package.symlink_to(target)

    monkeypatch.setattr(producer, "build_commands", incomplete)
    refusal = "target native|unsafe runtime relative path" if drift == "link" else "target native"
    with pytest.raises(ValueError, match=refusal):
        producer.build()
    assert not producer.OUTPUT.exists()


def test_lifecycle_commands_use_the_existing_pinned_node_headers(tmp_path, monkeypatch):
    import types

    monkeypatch.setattr(producer, "NODE", tmp_path / "pinned-node")
    environment = {"HOME": "/home/orca", "npm_config_offline": "true"}
    monkeypatch.setattr(
        producer,
        "helper",
        lambda name: types.SimpleNamespace(environment=lambda **kwargs: environment),
    )
    calls = []
    monkeypatch.setattr(
        producer.subprocess, "run", lambda argv, **kwargs: calls.append((argv, kwargs))
    )
    producer.command(
        ["npm", "ci", "--foreground-scripts", "--offline"],
        offline=True,
        cwd=tmp_path,
        commit="preserved",
    )
    assert calls[0][0] == ["npm", "ci", "--foreground-scripts", "--offline"]
    assert calls[0][1]["env"].get("npm_config_nodedir") == str(tmp_path / "pinned-node")
    assert calls[0][1]["env"]["GIT_COMMIT"] == "preserved"
    assert calls[0][1]["env"]["npm_config_offline"] == "true"
