"""SDK builder boundaries without invoking npm, fetching, or vendor operations."""

import importlib.util
import json
import types
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts/runtime_clis"
SPEC = importlib.util.spec_from_file_location(
    "_test_npm_artifact_producer", SCRIPTS / "produce_npm_artifact.py"
)
producer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(producer)


def test_private_build_helper_preserves_source_population_and_never_imports_unknown(tmp_path):
    helper = tmp_path / "scripts/runtime_clis"
    helper.mkdir(parents=True)
    source = helper / "produce_codex_artifacts.py"
    source.write_text("value = 42\n")
    with patch.object(producer, "RECIPE", tmp_path):
        assert producer.private_helper("produce_codex_artifacts").value == 42
        with pytest.raises(ValueError, match="undeclared"):
            producer.private_helper("foreign")
        assert sorted(p.name for p in helper.iterdir()) == [source.name]
        source.unlink()
        source.symlink_to(SCRIPTS / "produce_codex_artifacts.py")
        with pytest.raises(ValueError, match="regular"):
            producer.private_helper("produce_codex_artifacts")


def test_child_environment_keeps_literal_home_and_excludes_ambient_authority():
    with patch.dict(
        producer.os.environ,
        {
            "HOME": "/owned-home",
            "HTTPS_PROXY": "foreign",
            "NODE_OPTIONS": "foreign",
            "NPM_TOKEN": "synthetic",
        },
        clear=True,
    ):
        result = producer.environment(offline=True)
    assert result == {
        "HOME": "/owned-home",
        "PATH": "/opt/node/bin:/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "TZ": "UTC",
        "SOURCE_DATE_EPOCH": "0",
        "PUPPETEER_CACHE_DIR": "/opt/js-build/browser-cache",
        "npm_config_offline": "true",
    }
    argv = producer.npm("ci", "--offline", "--foreground-scripts")
    assert argv[-3:] == ["ci", "--offline", "--foreground-scripts"]
    assert "--ignore-scripts" not in argv
    assert "--registry=https://registry.npmjs.org" in argv
    assert "--userconfig" in argv and "--globalconfig" in argv


def test_cache_links_are_contained_but_source_fixture_links_remain_literal(tmp_path):
    with patch.object(producer, "RECIPE", SCRIPTS.parents[1]):
        helper = producer.private_helper("produce_codex_artifacts")
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "payload").write_bytes(b"asset")
    (cache / "inside").symlink_to("payload")
    assert len(producer.cache_inventory(cache, helper)) == 2
    (cache / "inside").unlink()
    (cache / "inside").symlink_to("../foreign")
    (tmp_path / "foreign").write_bytes(b"foreign")
    with pytest.raises(ValueError, match="escapes"):
        producer.cache_inventory(cache, helper)
    literal = helper.inventory(cache)
    producer.source_identity(cache, literal, helper)
    (cache / "inside").unlink()
    (cache / "inside").symlink_to("missing")
    with pytest.raises(ValueError, match="unreadable"):
        producer.cache_inventory(cache, helper)
    producer.source_identity(cache, helper.inventory(cache), helper)
    (cache / "payload").write_bytes(b"changed")
    with pytest.raises(ValueError, match="source changed"):
        producer.source_identity(cache, literal, helper)


@pytest.fixture
def build_fixture(tmp_path, monkeypatch):
    root = tmp_path / "build"
    root.mkdir()
    monkeypatch.setattr(producer, "ROOT", root)
    monkeypatch.setattr(producer, "OUTPUT", tmp_path / "artifacts")
    original_digest = producer.digest
    monkeypatch.setattr(
        producer,
        "digest",
        lambda path: "node-binary" if path == Path("/opt/node/bin/node") else original_digest(path),
    )
    helper = types.ModuleType("_synthetic_build_helper")
    helper.inventory = lambda path: (
        [{"path": "source", "sha256": "source"}] if path.name == "source" else []
    )
    helper.network_none = lambda: None
    recipe = {"recipe": "bound"}
    artifact = {
        "path": "vendor/google-auth-library-10.9.0.tgz",
        "sha256": producer.hashlib.sha256(b"packed").hexdigest(),
    }
    series = {
        "archive": {"sha256": producer.hashlib.sha256(b"archive").hexdigest()},
        "source_tree_sha256": "tree",
    }
    monkeypatch.setattr(producer, "identity", lambda vendor: (series, artifact, helper))
    monkeypatch.setattr(producer, "pins", lambda vendor: recipe.copy())
    (root / "source.tgz").write_bytes(b"archive")
    (root / "source").mkdir()
    for name in ("npm-cache", "browser-cache"):
        (root / name).mkdir()
    for name in ("npm-user.npmrc", "npm-global.npmrc"):
        (root / name).write_bytes(b"")
    producer.write_json(
        root / "seed.json",
        {
            "status": "NONFINAL",
            "vendor": "google-auth-library",
            "recipe": recipe,
            "source": helper.inventory(root / "source"),
            "source_archive_sha256": series["archive"]["sha256"],
            "npm_cache": [],
            "browser_cache": [],
            "configuration": {
                name: producer.digest(root / name)
                for name in ("npm-user.npmrc", "npm-global.npmrc")
            },
        },
    )

    def prepare(vendor, *, offline):
        assert offline
        (root / "source").mkdir()
        (root / "source/package.json").write_text(
            json.dumps({"name": "google-auth-library", "version": "10.9.0"})
        )
        (root / "source/package-lock.json").write_bytes(b"complete-lock")

    monkeypatch.setattr(producer, "prepare", prepare)
    calls = []

    def command(argv, *, offline, cwd):
        assert offline and cwd == root / "source"
        calls.append(argv)
        if "pack" in argv:
            (root / "packed/google-auth-library-10.9.0.tgz").write_bytes(b"packed")

    monkeypatch.setattr(producer, "command", command)
    return root, tmp_path / "artifacts", recipe, artifact, calls


def test_offline_build_publishes_exact_package_and_content_only_provenance(build_fixture):
    root, output, _, _, calls = build_fixture
    producer.build("google-auth-library")
    assert len(calls) == 2 and "ci" in calls[0] and "pack" in calls[1]
    assert (output / "google-auth-library/google-auth-library-10.9.0.tgz").read_bytes() == b"packed"
    metadata = json.loads((output / "google-auth-library/provenance.json").read_text())
    assert metadata["package_lock_sha256"] == producer.digest(root / "source/package-lock.json")
    assert metadata["lifecycle_scripts"] == "unchanged"
    assert not {"started", "elapsed", "npm_cache", "browser_cache"} & metadata.keys()


@pytest.mark.parametrize(
    "failure", ["archive", "configuration", "pack-digest", "recipe-install", "recipe-pack"]
)
def test_changed_inputs_or_lifecycle_outputs_never_publish(build_fixture, monkeypatch, failure):
    root, output, recipe, artifact, _ = build_fixture
    original = producer.command
    if failure == "archive":
        (root / "source.tgz").write_bytes(b"changed")
    if failure == "configuration":
        (root / "npm-user.npmrc").write_bytes(b"changed")
    if failure == "pack-digest":
        artifact["sha256"] = "0" * 64

    def command(argv, **kwargs):
        original(argv, **kwargs)
        if (failure == "recipe-install" and "ci" in argv) or (
            failure == "recipe-pack" and "pack" in argv
        ):
            recipe["recipe"] = "changed"

    monkeypatch.setattr(producer, "command", command)
    with pytest.raises(ValueError):
        producer.build("google-auth-library")
    assert not output.exists()
