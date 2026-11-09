"""Public-input and closure boundaries; these tests never compile a vendor CLI."""

import hashlib
import importlib.util
import io
import json
import stat
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "scripts/runtime_clis/produce_codex_artifacts.py"
SPEC = importlib.util.spec_from_file_location("_test_codex_artifact_producer", SOURCE)
producer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(producer)


def archive(path, entries):
    with zipfile.ZipFile(path, "w") as target:
        for name, mode, content in entries:
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            target.writestr(info, content)


def test_complete_compiler_wheel_preserves_executable_and_metadata(tmp_path):
    wheel = tmp_path / "compiler.whl"
    entries = [
        ("cmake/data/bin/cmake", stat.S_IFREG | 0o755, b"compiler"),
        ("cmake/data/share/module.cmake", stat.S_IFREG | 0o644, b"module"),
        ("cmake.dist-info/LICENSE", stat.S_IFREG | 0o644, b"license"),
    ]
    archive(wheel, entries)
    output = tmp_path / "output"
    producer.extract_cmake(wheel, output)
    assert {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()} == {
        entry[0] for entry in entries
    }
    for name, mode, content in entries:
        assert (output / name).read_bytes() == content
        assert stat.S_IMODE((output / name).stat().st_mode) == stat.S_IMODE(mode)
    with pytest.raises(ValueError, match="already exists"):
        producer.extract_cmake(wheel, output)


@pytest.mark.parametrize(
    ("name", "mode"),
    [
        ("../outside", stat.S_IFREG | 0o644),
        ("/outside", stat.S_IFREG | 0o644),
        ("a\\outside", stat.S_IFREG | 0o644),
        ("a\nfile", stat.S_IFREG | 0o644),
        ("link", stat.S_IFLNK | 0o777),
        ("setid", stat.S_IFREG | 0o4755),
        ("device", stat.S_IFCHR | 0o644),
    ],
)
def test_compiler_archive_refusals_publish_nothing(tmp_path, name, mode):
    wheel = tmp_path / "compiler.whl"
    archive(wheel, [("healthy", stat.S_IFREG | 0o644, b"healthy"), (name, mode, b"bad")])
    with pytest.raises(ValueError, match="unsafe"):
        producer.extract_cmake(wheel, tmp_path / "output")
    assert sorted(path.name for path in tmp_path.iterdir()) == ["compiler.whl"]


def test_duplicate_archive_population_is_refused_before_publication(tmp_path):
    wheel = tmp_path / "compiler.whl"
    with pytest.warns(UserWarning, match="Duplicate"):
        archive(
            wheel, [("same", stat.S_IFREG | 0o644, b"one"), ("same", stat.S_IFREG | 0o644, b"two")]
        )
    with pytest.raises(ValueError, match="population"):
        producer.extract_cmake(wheel, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_bounded_hash_verified_public_fetch_is_atomic(tmp_path):
    payload = b"complete pinned public source"
    item = {
        "url": "https://codeload.github.com/owner/repo/tar.gz/fixed",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
        "maximum_bytes": len(payload),
    }
    response = io.BytesIO(payload)
    with patch.object(producer.urllib.request, "build_opener") as opener:
        opener.return_value.open.return_value = response
        producer.fetch(item, tmp_path / "source")
    assert (tmp_path / "source").read_bytes() == payload
    assert response.closed
    assert [p.name for p in tmp_path.iterdir()] == ["source"]
    for change in ({"sha256": "0" * 64}, {"maximum_bytes": 1}, {"size": 1}):
        with patch.object(producer.urllib.request, "build_opener") as opener:
            opener.return_value.open.return_value = io.BytesIO(payload)
            with pytest.raises(ValueError):
                producer.fetch({**item, **change}, tmp_path / "refused")
        assert [p.name for p in tmp_path.iterdir()] == ["source"]


@pytest.mark.parametrize(
    "url",
    [
        "http://codeload.github.com/archive",
        "https://foreign.invalid/a",
        "https://user:password@files.pythonhosted.org/a",
        "https://files.pythonhosted.org/a?credential=synthetic",
        "https://files.pythonhosted.org/a#fragment",
    ],
)
def test_foreign_or_credential_bearing_input_never_opens(tmp_path, url):
    with patch.object(producer.urllib.request, "build_opener") as opener:
        with pytest.raises(ValueError, match="invalid public"):
            producer.fetch({"url": url}, tmp_path / "output")
        opener.assert_not_called()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("status", range(300, 400))
def test_each_redirect_is_closed_before_dispatch(status):
    class Response:
        closed = False

        def close(self):
            self.closed = True

    response = Response()
    response.status = status
    with pytest.raises(ValueError, match="redirects"):
        producer.RefuseRedirects().https_response(object(), response)
    assert response.closed


def test_source_inventory_retains_links_modes_and_actual_content(tmp_path):
    (tmp_path / "payload").write_bytes(b"original")
    (tmp_path / "payload").chmod(0o755)
    (tmp_path / "relative").symlink_to("./payload")
    before = producer.inventory(tmp_path)
    assert next(x for x in before if x["path"] == "relative")["target"] == "./payload"
    assert next(x for x in before if x["path"] == "payload")["mode"] == 0o755
    (tmp_path / "payload").write_bytes(b"changed")
    assert producer.inventory(tmp_path) != before


def test_compiler_supplement_keeps_original_profiles_separate():
    root = SOURCE.parents[2]
    supplemental = json.loads((root / "deploy/runtime-clis/codex/compiler-inputs.json").read_text())
    predecessor = root / supplemental["predecessor"]["path"]
    assert producer.digest(predecessor) == supplemental["predecessor"]["sha256"]
    assert set(supplemental["profiles"]) == {"compiler"}
    assert {x["package"] for x in supplemental["debs"]} == {
        "libssl-dev",
        "libssl3t64",
        "openssl-provider-legacy",
    }
    assert {x["version"] for x in supplemental["debs"]} == {"3.5.7-1~deb13u3"}


def test_compiler_environment_excludes_ambient_transport_and_loader_overrides():
    declared = {
        key: "declared"
        for key in (
            "HOME",
            "SOURCE_DATE_EPOCH",
            "ZERO_AR_DATE",
            "RUSTFLAGS",
            "CFLAGS",
            "CXXFLAGS",
            "STABLE_GIT_COMMIT",
            "CARGO_HOME",
            "CARGO_TARGET_DIR",
        )
    }
    ambient = {
        "HTTPS_PROXY": "host-only",
        "LD_PRELOAD": "host-only",
        "RUSTC_WRAPPER": "host-only",
        "CARGO_REGISTRIES_CRATES_IO_TOKEN": "host-only",
    }
    with patch.dict(producer.os.environ, {**declared, **ambient}, clear=True):
        public = producer.environment(offline=False)
        offline = producer.environment(offline=True)
    assert all(public[key] == value for key, value in declared.items())
    assert not set(ambient) & set(public)
    assert public["CARGO_NET_OFFLINE"] == "false"
    assert offline == {**public, "CARGO_NET_OFFLINE": "true"}


def test_network_guard_has_healthy_and_real_external_route_controls():
    values = {
        "/proc/net/dev": "header\nheader\n lo: 0\n",
        "/proc/net/route": "header\n",
        "/proc/net/ipv6_route": "zero zero lo\n",
    }
    with patch.object(producer.Path, "read_text", lambda path: values[str(path)]):
        producer.network_none()
        for path, bad in (
            ("/proc/net/dev", "header\nheader\n lo: 0\n eth0: 0\n"),
            ("/proc/net/route", "header\neth0 route\n"),
            ("/proc/net/ipv6_route", "zero zero eth0\n"),
        ):
            previous = values[path]
            values[path] = bad
            with pytest.raises(ValueError, match="networkless"):
                producer.network_none()
            values[path] = previous
        producer.network_none()
