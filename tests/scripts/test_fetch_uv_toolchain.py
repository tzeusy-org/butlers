"""Bounded synthetic HTTP/ZIP controls; never execute or install the payload."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import stat
import tempfile
import unittest
import urllib.request
import urllib.response
import warnings
import zipfile
from email.message import Message
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/runtime_clis/fetch_uv_toolchain.py"
spec = importlib.util.spec_from_file_location("_uv_acquisition_under_test", SCRIPT)
assert spec is not None and spec.loader is not None
fetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch)
BINARY = b"synthetic executable bytes; never run"
PAYLOAD = {
    name: (BINARY if name == fetch.BINARY else f"retained:{name}".encode()) for name in fetch.FILES
}


def wheel(*, omit=(), extra=(), modes=None, change=None, payload=None):
    entries = [
        (name, (payload or {}).get(name, content))
        for name, content in sorted(PAYLOAD.items())
        if name not in omit
    ]
    entries += list(extra)
    result = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in entries:
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                mode = stat.S_IFREG | (0o755 if name in fetch.EXECUTABLES else 0o644)
                info.external_attr = (modes or {}).get(name, mode) << 16
                if change:
                    change(info)
                archive.writestr(info, content)
    return result.getvalue()


class Response(io.BytesIO):
    status = 200

    def __init__(self, body, *, length=None, status=200, url=None):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body)) if length is None else length}
        self.status = status
        self.url = url or fetch.PINNED["url"]

    def geturl(self):
        return self.url


class Opener:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def open(self, url, timeout):
        self.calls.append((url, timeout))
        return self.response


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manifest = self.root / "toolchains.json"
        self.output = self.root / "uv"
        self.body = wheel()
        self.pin = {
            **fetch.PINNED,
            "sha256": hashlib.sha256(self.body).hexdigest(),
            "binary_sha256": hashlib.sha256(BINARY).hexdigest(),
        }
        self.write_manifest()
        self.patch = patch.object(fetch, "PINNED", self.pin)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def write_manifest(self, uv=None):
        self.manifest.write_text(json.dumps({"uv": uv or self.pin, "unrelated": "ignored"}))

    def prepare(self, body=None, **response):
        reply = Response(self.body if body is None else body, **response)
        opener = Opener(reply)
        try:
            return fetch.prepare(self.manifest, self.output, opener=opener)
        finally:
            self.assertTrue(reply.closed)
            self.assertEqual(opener.calls, [(self.pin["url"], fetch.READ_TIMEOUT)])

    def bind_body(self, body):
        self.pin["sha256"] = hashlib.sha256(body).hexdigest()
        self.write_manifest()
        return body

    def assert_absent_and_clean(self):
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob(".uv-toolchain-*")))

    def test_complete_payload_archive_uvx_metadata_licenses_and_canonical_modes(self):
        value = self.prepare()
        self.assertEqual(value["schema"], "butlers.uv-toolchain/v1")
        self.assertEqual({entry["path"] for entry in value["payload"]}, fetch.FILES)
        self.assertEqual((self.output / self.pin["filename"]).read_bytes(), self.body)
        self.assertEqual(value["binary"]["sha256"], hashlib.sha256(BINARY).hexdigest())
        for entry in value["payload"]:
            path = self.output / "wheel" / entry["path"]
            self.assertEqual(path.read_bytes(), PAYLOAD[entry["path"]])
            self.assertEqual(
                path.stat().st_mode & 0o777, 0o755 if entry["path"] in fetch.EXECUTABLES else 0o644
            )
            self.assertEqual(entry["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertTrue(
            all(
                path.stat().st_mode & 0o777 == 0o755
                for path in self.output.rglob("*")
                if path.is_dir()
            )
        )
        self.assertEqual((self.output / "provenance.json").stat().st_mode & 0o777, 0o644)
        text = (self.output / "provenance.json").read_text()
        self.assertNotIn(str(self.root), text)
        self.assertNotIn("timestamp", text)
        self.assertFalse(list(self.root.glob(".uv-toolchain-*")))

    def test_archive_hash_and_binary_hash_have_separate_refusals(self):
        changed = wheel(payload={"uv/py.typed": b"changed"})
        with self.assertRaisesRegex(ValueError, "archive hash"):
            self.prepare(changed)
        self.assert_absent_and_clean()
        self.pin["binary_sha256"] = "0" * 64
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "executable hash"):
            self.prepare()
        self.assert_absent_and_clean()

    def test_broken_zip_has_no_partial_final_output(self):
        body = self.bind_body(b"not a ZIP")
        with self.assertRaises(zipfile.BadZipFile):
            self.prepare(body)
        self.assert_absent_and_clean()

    def test_path_duplicate_missing_and_unknown_population_refusals(self):
        for bad in (
            "../escape",
            "/absolute",
            "uv//double",
            "uv/./dot",
            "uv\\slash",
            "uv/control\n",
        ):
            with self.subTest(path=bad):
                body = self.bind_body(wheel(extra=((bad, b"x"),)))
                with self.assertRaisesRegex(ValueError, "unsafe"):
                    self.prepare(body)
                self.assert_absent_and_clean()
        for body, message in (
            (wheel(extra=(("uv/py.typed", b"x"),)), "duplicate"),
            (wheel(omit=("uv-0.12.22.dist-info/licenses/LICENSE-MIT",)), "missing"),
            (wheel(extra=(("uv/unknown", b"x"),)), "population"),
        ):
            with self.subTest(message=message):
                self.bind_body(body)
                with self.assertRaisesRegex(ValueError, message):
                    self.prepare(body)
                self.assert_absent_and_clean()

    def test_links_special_privileged_and_wrong_executable_modes_refuse(self):
        for mode in (
            stat.S_IFLNK | 0o777,
            stat.S_IFIFO | 0o644,
            stat.S_IFCHR | 0o644,
            stat.S_IFREG | 0o4755,
            stat.S_IFREG | 0o777,
            0o644,
        ):
            with self.subTest(mode=mode):
                body = self.bind_body(wheel(modes={"uv/__init__.py": mode}))
                with self.assertRaisesRegex(ValueError, "type, mode"):
                    self.prepare(body)
                self.assert_absent_and_clean()
        body = self.bind_body(wheel(modes={fetch.BINARY: stat.S_IFREG | 0o644}))
        with self.assertRaisesRegex(ValueError, "mode"):
            self.prepare(body)
        self.assert_absent_and_clean()

    def test_entry_count_and_total_expanded_bounds_refuse_before_extraction(self):
        for bound, value in (("MAX_ENTRIES", 11), ("MAX_EXPANDED", 1)):
            with self.subTest(bound=bound), patch.object(fetch, bound, value):
                with self.assertRaisesRegex(ValueError, "bound"):
                    self.prepare()
                self.assert_absent_and_clean()

    def test_archive_header_actual_bytes_and_transfer_deadline_are_bounded(self):
        with patch.object(fetch, "MAX_ARCHIVE", len(self.body) - 1):
            with self.assertRaisesRegex(ValueError, "length exceeds"):
                self.prepare()
        with patch.object(fetch, "MAX_ARCHIVE", len(self.body) - 1):
            with self.assertRaisesRegex(ValueError, "transfer bound"):
                self.prepare(length="1")
        with self.assertRaisesRegex(ValueError, "length mismatch"):
            self.prepare(length=str(len(self.body) + 1))
        with patch.object(fetch.time, "monotonic", side_effect=[0, fetch.TRANSFER_TIMEOUT + 1]):
            with self.assertRaisesRegex(ValueError, "transfer bound"):
                self.prepare()
        self.assert_absent_and_clean()

    def test_all_3xx_close_before_urllib_can_follow(self):
        calls, replies = [], []

        class SyntheticHTTPS(urllib.request.HTTPSHandler):
            def https_open(self, request):
                calls.append(request.full_url)
                headers = Message()
                headers["Location"] = "https://not-contacted.invalid/"
                reply = urllib.response.addinfourl(
                    io.BytesIO(b"redirect"),
                    headers,
                    request.full_url,
                    code,
                )
                replies.append(reply)
                return reply

        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), SyntheticHTTPS(), fetch.NoRedirect()
        )
        for code in range(300, 400):
            with self.subTest(status=code):
                with self.assertRaisesRegex(ValueError, "redirect"):
                    fetch.prepare(self.manifest, self.output, opener=opener)
                self.assertTrue(replies[-1].closed)
                self.assert_absent_and_clean()
        self.assertEqual(calls, [self.pin["url"]] * 100)

    def test_proxy_discovery_disabled_and_response_identity_closed(self):
        reply = Response(self.body)
        with patch.object(
            fetch.urllib.request, "build_opener", return_value=Opener(reply)
        ) as build:
            fetch.prepare(self.manifest, self.output)
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsInstance(build.call_args.args[1], fetch.NoRedirect)
        other = self.root / "other"
        with self.assertRaisesRegex(ValueError, "identity"):
            fetch.prepare(
                self.manifest,
                other,
                opener=Opener(Response(self.body, url="https://not-contacted.invalid/")),
            )
        self.assertFalse(other.exists())

    def test_exact_manifest_refuses_identity_changes_ignores_other_components(self):
        self.assertEqual(fetch.read_manifest(self.manifest), self.pin)
        for key, value in (
            ("version", "other"),
            ("url", self.pin["url"] + "?unused"),
            ("platform", "other"),
            ("sha256", "0" * 64),
            ("extra", "unknown"),
        ):
            with self.subTest(key=key):
                self.write_manifest({**self.pin, key: value})
                with self.assertRaisesRegex(ValueError, "identity"):
                    fetch.read_manifest(self.manifest)
        self.write_manifest()
        expected = self.prepare()
        other = self.root / "same-content"
        self.manifest.write_text(
            json.dumps({"unrelated": {"different": True}, "uv": self.pin}, sort_keys=True, indent=4)
        )
        actual = fetch.prepare(self.manifest, other, opener=Opener(Response(self.body)))
        self.assertEqual(actual, expected)
        self.assertNotEqual(
            (self.output / "wheel" / fetch.BINARY).stat().st_ino,
            (other / "wheel" / fetch.BINARY).stat().st_ino,
        )

    def test_existing_output_symlink_parent_and_concurrent_output_are_preserved(self):
        self.output.mkdir()
        sentinel = self.output / "sentinel"
        sentinel.write_text("preserved")
        with self.assertRaisesRegex(ValueError, "absent"):
            fetch.prepare(self.manifest, self.output)
        self.assertEqual(sentinel.read_text(), "preserved")
        alias = self.root / "alias"
        alias.symlink_to(self.output, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "real path"):
            fetch.prepare(self.manifest, alias / "new")
        stage = self.root / "stage"
        stage.mkdir()
        with self.assertRaisesRegex(ValueError, "already exists"):
            fetch.publish(stage, self.output)
        self.assertTrue(stage.is_dir())
        self.assertEqual(sentinel.read_text(), "preserved")

    def test_docker_named_source_same_inputs_network_boundary_and_final_only(self):
        text = (ROOT / "Dockerfile.python-artifacts").read_text()
        self.assertIn(
            "python:3.12.13-slim-trixie@sha256:c2d8472b831337ab296a8ce652e1ba786e9e3034fc445dc58b50a7f5251f0003",
            text,
        )
        self.assertIn("COPY --from=python-source / /inputs/source", text)
        self.assertIn("--phase public-seed --project /inputs/source --work /work/python", text)
        self.assertIn("--phase offline-verify --project /inputs/source --work /work/python", text)
        self.assertEqual(
            text.count("--output /artifacts/python --backend-lock /inputs/build-lock.toml"), 2
        )
        for phase, network in (("public-seed", "default"), ("offline-verify", "none")):
            command = next(
                command for command in text.split("\nRUN ") if f"--phase {phase}" in command
            )
            self.assertTrue(
                command.startswith(f"--network={network} --mount=type=tmpfs,target=/root")
            )
            self.assertIn('test "$HOME" = /root', command)
        self.assertNotIn("ENV HOME", text)
        self.assertNotIn("--build-constraints", text)
        self.assertTrue(
            text.endswith("FROM scratch AS artifacts\nCOPY --from=verified /artifacts/python/ /\n")
        )


if __name__ == "__main__":
    unittest.main()
