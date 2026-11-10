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
    assert metadata["lifecycle_scripts"] == "all original hooks executed"
    assert metadata["npm_child_umask"] == "0027"
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


def test_npm_children_reproduce_modes_without_changing_parent_umask(tmp_path, monkeypatch):
    import os
    import stat
    import sys

    monkeypatch.setenv("HOME", str(tmp_path))
    before = os.umask(0o022)
    try:
        producer.command(
            [sys.executable, "-I", "-B", "-c", "from pathlib import Path;Path('child').touch()"],
            offline=True,
            cwd=tmp_path,
        )
        assert stat.S_IMODE((tmp_path / "child").stat().st_mode) == 0o640
        (tmp_path / "parent").touch()
        assert stat.S_IMODE((tmp_path / "parent").stat().st_mode) == 0o644
        assert os.umask(0o022) == 0o022
    finally:
        os.umask(before)


@pytest.fixture
def dependency_fixture(tmp_path, monkeypatch):
    import base64
    import copy
    import difflib
    import io
    import tarfile

    root = tmp_path / "build"
    root.mkdir()
    recipe = tmp_path / "recipe/deploy/runtime-clis/npm-artifacts"
    (recipe / "patches").mkdir(parents=True)
    monkeypatch.setattr(producer, "ROOT", root)
    monkeypatch.setattr(producer, "RECIPE", tmp_path / "recipe")
    monkeypatch.setenv("HOME", str(tmp_path))
    original = b"const cache = false;\n"
    patched = b"const cache = true;\n"
    release = b"synthetic public release bytes"
    contents = {
        "package/LICENSE": b"license",
        "package/dist/index.js": b"export {}",
        "package/postinstall.js": original,
        "package/checksums.json": json.dumps(
            {
                "v0.2.9": {
                    "test-server_Linux_x86_64.tar.gz": producer.hashlib.sha256(release).hexdigest()
                }
            }
        ).encode(),
        "package/package.json": b'{"name":"test-server-sdk","version":"0.2.9"}',
        "package/dist/index.d.ts": b"export {}",
    }
    dependencies = root / "dependencies"
    dependencies.mkdir()
    archive = dependencies / "test-server-sdk-0.2.9.tgz"
    members = []
    with tarfile.open(archive, "w:gz") as tar:
        for name, data in contents.items():
            item = tarfile.TarInfo(name)
            item.size = len(data)
            item.mode = 0o640
            item.mtime = 499162500
            members.append(item)
            tar.addfile(item, io.BytesIO(data))
    patch_file = recipe / "patches/change.patch"
    patch_file.write_text(
        "".join(
            difflib.unified_diff(
                original.decode().splitlines(keepends=True),
                patched.decode().splitlines(keepends=True),
                fromfile="a/postinstall.js",
                tofile="b/postinstall.js",
                n=0,
            )
        )
    )
    derived = tmp_path / "expected.tar"
    with tarfile.open(derived, "w", format=tarfile.USTAR_FORMAT) as tar:
        for member in members:
            data = patched if member.name == "package/postinstall.js" else contents[member.name]
            item = copy.copy(member)
            item.size = len(data)
            tar.addfile(item, io.BytesIO(data))
    (dependencies / "test-server_Linux_x86_64.tar.gz").write_bytes(release)

    def record(path):
        return {
            "size": path.stat().st_size,
            "sha256": producer.digest(path),
            "integrity": "sha512-"
            + base64.b64encode(producer.hashlib.sha512(path.read_bytes()).digest()).decode(),
        }

    inputs = {
        "package": "test-server-sdk",
        "version": "0.2.9",
        "lock_path": "node_modules/test-server-sdk",
        "original_archive": {
            **record(archive),
            "url": "https://registry.npmjs.org/test-server-sdk/-/test-server-sdk-0.2.9.tgz",
        },
        "release_archive": record(dependencies / "test-server_Linux_x86_64.tar.gz"),
        "members": [
            {
                "path": member.name,
                "size": member.size,
                "mode": member.mode,
                "sha256": producer.hashlib.sha256(contents[member.name]).hexdigest(),
            }
            for member in members
        ],
        "patch": {
            "path": "patches/change.patch",
            "sha256": producer.digest(patch_file),
            "before_sha256": producer.hashlib.sha256(original).hexdigest(),
            "after_sha256": producer.hashlib.sha256(patched).hexdigest(),
        },
        "checksums_sha256": producer.hashlib.sha256(contents["package/checksums.json"]).hexdigest(),
        "derived_archive": {**record(derived), "filename": "test-server-sdk-0.2.9-cache.tar"},
    }
    source = root / "source"
    source.mkdir()
    lock = {
        "name": "unchanged-sdk",
        "packages": {
            "": {"devDependencies": {"test-server-sdk": "0.2.9"}},
            "node_modules/test-server-sdk": {
                "version": "0.2.9",
                "resolved": inputs["original_archive"]["url"],
                "integrity": inputs["original_archive"]["integrity"],
                "dev": True,
                "hasInstallScript": True,
            },
            "node_modules/other": {"version": "1.0.0", "optional": True},
        },
    }
    text = json.dumps(lock, indent=2) + "\n"
    (source / "package-lock.json").write_text(text)
    resolved = "file:../dependencies/test-server-sdk-0.2.9-cache.tar"
    overlay = text.replace(inputs["original_archive"]["url"], resolved).replace(
        inputs["original_archive"]["integrity"], inputs["derived_archive"]["integrity"]
    )
    inputs["lock"] = {
        "before_sha256": producer.hashlib.sha256(text.encode()).hexdigest(),
        "after_sha256": producer.hashlib.sha256(overlay.encode()).hexdigest(),
        "resolved": resolved,
    }
    monkeypatch.setattr(producer, "build_inputs", lambda: inputs)

    # Existing exact original inputs are provided data; the public branch still
    # takes its normal acquisition path and the real patch executable runs.
    def fetch(record, output, **kwargs):
        assert output.is_file()
        producer.verify_archive(output, record)

    monkeypatch.setattr(producer, "fetch_dependency", fetch)
    return root, inputs, derived, lock


def test_declared_dependency_reproduces_all_members_and_only_two_lock_fields(dependency_fixture):
    import tarfile

    root, inputs, expected, original_lock = dependency_fixture
    # Public acquisition is separately exercised; use the complete supplied
    # inputs here and retain the real patch subprocess and deterministic tar.
    (root / "dependencies" / expected.name).write_bytes(expected.read_bytes())
    # The actual declared filename differs from this fixture's expectation filename.
    (root / "dependencies" / expected.name).rename(
        root / "dependencies" / inputs["derived_archive"]["filename"]
    )
    producer.dependency_inputs(offline=True)
    derived = root / "dependencies" / inputs["derived_archive"]["filename"]
    assert derived.read_bytes() == expected.read_bytes()
    with tarfile.open(derived) as archive:
        assert len(archive.getmembers()) == 6
        assert {member.mode for member in archive.getmembers()} == {0o640}
        assert archive.extractfile("package/postinstall.js").read() == b"const cache = true;\n"
        assert archive.extractfile("package/LICENSE").read() == b"license"
    producer.overlay_lock(inputs)
    changed = json.loads((root / "source/package-lock.json").read_bytes())
    expected_lock = producer.copy.deepcopy(original_lock)
    expected_lock["packages"][inputs["lock_path"]].update(
        resolved=inputs["lock"]["resolved"], integrity=inputs["derived_archive"]["integrity"]
    )
    assert changed == expected_lock
    assert changed["packages"][""] == original_lock["packages"][""]
    assert (
        changed["packages"]["node_modules/other"] == original_lock["packages"]["node_modules/other"]
    )


@pytest.mark.parametrize(
    "failure", ["original", "release", "patch", "member", "hook", "checksum", "derived", "foreign"]
)
def test_dependency_input_drift_refuses_before_lock_or_output(dependency_fixture, failure):
    root, inputs, expected, _ = dependency_fixture
    target = root / "dependencies" / inputs["derived_archive"]["filename"]
    target.write_bytes(expected.read_bytes())
    before = (root / "source/package-lock.json").read_bytes()
    if failure in {"original", "release", "derived"}:
        name = {
            "original": "test-server-sdk-0.2.9.tgz",
            "release": "test-server_Linux_x86_64.tar.gz",
            "derived": target.name,
        }[failure]
        (root / "dependencies" / name).write_bytes(b"wrong")
    elif failure == "patch":
        inputs["patch"]["sha256"] = "0" * 64
    elif failure == "member":
        inputs["members"][0]["mode"] = 0o644
    elif failure == "hook":
        inputs["patch"]["after_sha256"] = "0" * 64
    elif failure == "checksum":
        inputs["release_archive"]["sha256"] = "0" * 64
    else:
        (root / "dependencies/foreign").write_bytes(b"foreign")
    with pytest.raises(ValueError):
        producer.dependency_inputs(offline=True)
    assert (root / "source/package-lock.json").read_bytes() == before
    assert not (root / "artifacts").exists()


def test_overlay_lock_refuses_wrong_original_or_undeclared_extra_change(dependency_fixture):
    root, inputs, _, _ = dependency_fixture
    path = root / "source/package-lock.json"
    original = path.read_bytes()
    path.write_bytes(original + b" ")
    with pytest.raises(ValueError, match="original SDK"):
        producer.overlay_lock(inputs)
    assert path.read_bytes() == original + b" "
    path.write_bytes(original)
    inputs["lock"]["after_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="overlay"):
        producer.overlay_lock(inputs)
    assert path.read_bytes() == original


class DependencyResponse:
    def __init__(self, status, url, data=b"", location=None):
        from email.message import Message

        self.status, self.url, self.data, self.closed = status, url, data, False
        self.headers = Message()
        if location is not None:
            self.headers.add_header("Location", location)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def read(self, size):
        data, self.data = self.data[:size], self.data[size:]
        return data

    def geturl(self):
        return self.url


class DependencyOpener:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def open(self, url, *, timeout):
        self.calls.append(url)
        return next(self.responses)


def test_public_dependency_refuses_every_3xx_and_closes_without_output(tmp_path):
    url = "https://registry.npmjs.org/test-server-sdk/-/test-server-sdk-0.2.9.tgz"
    record = {"url": url, "size": 5, "sha256": producer.hashlib.sha256(b"bytes").hexdigest()}
    for status in range(300, 400):
        response = DependencyResponse(status, url, location="https://foreign.invalid/forbidden")
        opener = DependencyOpener([response])
        with pytest.raises(ValueError, match="acquisition failed"):
            producer.fetch_dependency(record, tmp_path / "output", opener=opener)
        assert response.closed and opener.calls == [url]
        assert list(tmp_path.iterdir()) == []
    healthy = DependencyResponse(200, url, b"bytes")
    producer.fetch_dependency(record, tmp_path / "output", opener=DependencyOpener([healthy]))
    assert healthy.closed and (tmp_path / "output").read_bytes() == b"bytes"


def test_release_fetch_has_one_exact_asset_transition_and_sanitizes_errors(tmp_path):
    url = "https://github.com/google/test-server/releases/download/v0.2.9/archive.tar.gz"
    record = {"url": url, "size": 5, "sha256": producer.hashlib.sha256(b"bytes").hexdigest()}
    signed = "https://release-assets.githubusercontent.com/asset?signature=synthetic-private-marker"
    first = DependencyResponse(302, url, location=signed)
    second = DependencyResponse(200, signed, b"bytes")
    opener = DependencyOpener([first, second])
    producer.fetch_dependency(record, tmp_path / "healthy", release=True, opener=opener)
    assert first.closed and second.closed and opener.calls == [url, signed]
    assert (tmp_path / "healthy").read_bytes() == b"bytes"
    for location in [
        "http://release-assets.githubusercontent.com/asset",
        "https://github.com/other",
        "https://release-assets.githubusercontent.com:443/asset",
        "https://127.0.0.1/asset",
        "https://foreign.invalid/asset",
        "https://release-assets.githubusercontent.com/asset#fragment",
    ]:
        response = DependencyResponse(302, url, location=location)
        with pytest.raises(ValueError, match="acquisition failed") as error:
            producer.fetch_dependency(
                record, tmp_path / "bad", release=True, opener=DependencyOpener([response])
            )
        assert response.closed and location not in str(error.value)
        assert not (tmp_path / "bad").exists()

    class FailingOpener:
        def open(self, url, **kwargs):
            raise RuntimeError(signed)

    with pytest.raises(ValueError) as error:
        producer.fetch_dependency(record, tmp_path / "bad", release=True, opener=FailingOpener())
    assert signed not in str(error.value) and error.value.__suppress_context__


def test_explicit_cache_path_is_passed_without_ambient_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr(producer, "ROOT", tmp_path)
    monkeypatch.setenv("HOME", "/literal-owner-home")
    assert "BUTLERS_TEST_SERVER_ARCHIVE" not in producer.environment(offline=True)
    (tmp_path / "dependencies").mkdir()
    archive = tmp_path / "dependencies/test-server_Linux_x86_64.tar.gz"
    archive.write_bytes(b"synthetic")
    environment = producer.environment(offline=True)
    assert environment["HOME"] == "/literal-owner-home"
    assert environment["BUTLERS_TEST_SERVER_ARCHIVE"] == str(archive)
