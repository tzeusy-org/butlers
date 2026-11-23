"""Complete-shaped synthetic trees test copying/planning, not actual CLI behavior."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "runtime_artifacts",
    ROOT / "scripts/runtime_clis/runtime_artifacts.py",
)
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


def file(path: Path, contents: bytes = b"synthetic complete graph member\n", mode: int = 0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents)
    path.chmod(mode)


def snapshot(root: Path):
    return [
        (
            str(p.relative_to(root)),
            p.lstat().st_mode,
            os.readlink(p) if p.is_symlink() else p.read_bytes() if p.is_file() else None,
        )
        for p in sorted(root.rglob("*"))
    ]


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.components = {name: self.base / "source" / name for name in runtime.DESTINATIONS}
        for path in self.components.values():
            file(path / "assets/complete-data", b"retained asset/model/plugin data\n")
        node = self.components["node"]
        file(node / "bin/node", mode=0o755)
        for name in ["npm", "npx"]:
            file(node / f"lib/node_modules/npm/bin/{name}-cli.js", mode=0o755)
            (node / "bin" / name).symlink_to(f"../lib/node_modules/npm/bin/{name}-cli.js")
        for name in ["uv", "uvx"]:
            file(self.components["uv"] / name, mode=0o755)
        for name in runtime.LAUNCHERS:
            # Preserve the genuine declared original package layout in a tiny tree.
            entry = {
                "claude": "bin/claude.exe",
                "codex": "bin/codex.js",
                "gemini": "bundle/gemini.js",
                "opencode": "bin/opencode.exe",
            }[name]
            file(self.components[name] / entry, mode=0o755)
        self.inputs = self.base / "reviewed.json"
        self.context = self.base / "closed"
        self.prefix = self.base / "prefix"
        self.prefix.mkdir()
        self.review()

    def review(self):
        value = {
            "schema": "butlers.runtime-inputs/v1",
            "target": runtime.TARGET,
            "versions": runtime.VERSIONS,
            "components": {
                name: {"tree_sha256": runtime.tree_digest(runtime.inventory(path))}
                for name, path in self.components.items()
            },
            "provenance": {"scope": "synthetic source test, not native proof"},
        }
        self.inputs.write_text(json.dumps(value))

    def close(self):
        runtime.close(self.inputs, self.components, self.context)

    def test_compatible_existing_directories_preserve_identity_and_permissions(self):
        self.close()
        paths = [
            self.prefix / p
            for p in [
                "usr",
                "usr/local",
                "usr/local/bin",
                "usr/local/lib",
                "usr/local/lib/node_modules",
            ]
        ]
        for p in paths:
            p.mkdir(exist_ok=True)
            p.chmod(0o770)
        before = {
            p: (p.stat().st_ino, stat.S_IMODE(p.stat().st_mode), p.stat().st_uid, p.stat().st_gid)
            for p in paths
        }
        error = None
        try:
            runtime.install(self.inputs, self.context, self.prefix)
        except ValueError as exc:
            error = str(exc)
        self.assertIsNone(error, "compatible real directories must be accepted")
        self.assertEqual(
            before,
            {
                p: (
                    p.stat().st_ino,
                    stat.S_IMODE(p.stat().st_mode),
                    p.stat().st_uid,
                    p.stat().st_gid,
                )
                for p in paths
            },
        )
        self.assertTrue((self.prefix / "usr/local/bin/claude").is_symlink())

    def test_planned_launcher_collision_refuses_before_any_prefix_write(self):
        file(self.components["node"] / "bin/claude", mode=0o755)
        self.review()
        before = snapshot(self.prefix)
        with self.assertRaises(ValueError):
            self.close()
            runtime.install(self.inputs, self.context, self.prefix)
        self.assertEqual(
            before, snapshot(self.prefix), "all launchers must be planned before copying"
        )

    def test_cross_component_file_collision_refuses_before_any_prefix_write(self):
        file(self.components["node"] / "bin/uv", mode=0o755)
        self.review()
        with self.assertRaises(ValueError):
            self.close()
            runtime.install(self.inputs, self.context, self.prefix)
        self.assertEqual([], snapshot(self.prefix))

    def test_new_implicit_parents_have_deterministic_modes(self):
        self.close()
        old = os.umask(0o077)
        try:
            runtime.install(self.inputs, self.context, self.prefix)
        finally:
            os.umask(old)
        for parent in [
            "usr",
            "usr/local/lib/node_modules/@anthropic-ai",
            "usr/local/lib/node_modules/@google",
        ]:
            self.assertEqual(stat.S_IMODE((self.prefix / parent).stat().st_mode), 0o755, parent)

    def test_manifest_bytes_are_bound_before_and_after_close(self):
        original = runtime._copy
        changed = False

        def mutate(*args):
            nonlocal changed
            original(*args)
            if not changed:
                changed = True
                self.inputs.write_bytes(self.inputs.read_bytes() + b"\n")

        with patch.object(runtime, "_copy", side_effect=mutate), self.assertRaises(ValueError):
            self.close()
        self.assertTrue(changed)
        self.assertFalse(self.context.exists())

    def test_closed_file_mode_drift_refuses_before_any_prefix_write(self):
        self.close()
        (self.context / "components/claude/assets/complete-data").chmod(0o600)
        with self.assertRaises(ValueError):
            runtime.install(self.inputs, self.context, self.prefix)
        self.assertEqual([], snapshot(self.prefix))

    def test_full_close_install_keeps_graph_links_assets_and_independent_bytes(self):
        source = self.components["gemini"]
        deep = source / ("node_modules/dependency/" * 20) / "data.json"
        file(deep, b"full nested package data")
        (source / "assets/model-link").symlink_to("./complete-data")
        self.review()
        original = {name: snapshot(path) for name, path in self.components.items()}
        self.close()
        self.assertEqual(original, {name: snapshot(path) for name, path in self.components.items()})
        file(source / "assets/complete-data", b"source changed after close")
        runtime.install(self.inputs, self.context, self.prefix)
        for name, destination in runtime.DESTINATIONS.items():
            closed = self.context / "components" / name
            installed = self.prefix / destination
            for entry in runtime.inventory(closed):
                actual = installed / entry["path"]
                if entry["type"] == "file":
                    self.assertEqual(actual.read_bytes(), (closed / entry["path"]).read_bytes())
                    self.assertNotEqual(
                        actual.stat().st_ino, (closed / entry["path"]).stat().st_ino
                    )
                elif entry["type"] == "link":
                    self.assertEqual(os.readlink(actual), entry["target"])
        installed = self.prefix / runtime.DESTINATIONS["gemini"] / "assets/complete-data"
        self.assertEqual(installed.read_bytes(), b"retained asset/model/plugin data\n")
        file(self.context / "components/gemini/assets/complete-data", b"context changed later")
        self.assertEqual(installed.read_bytes(), b"retained asset/model/plugin data\n")
        for name, target in runtime.LAUNCHERS.items():
            command = self.prefix / "usr/local/bin" / name
            self.assertEqual(os.readlink(command), target)
            self.assertTrue(command.resolve().is_relative_to(self.prefix))

    def test_tree_drift_from_reviewed_pin_is_recorded_and_install_binds_to_closed_tree(self):
        file(self.components["codex"] / "assets/complete-data", b"locally rebuilt bytes\n")
        self.close()  # The reviewed pin no longer matches codex: recorded, not refused.
        provenance = json.loads((self.context / "provenance.json").read_text())
        observed = runtime.tree_digest(runtime.inventory(self.components["codex"]))
        self.assertEqual(provenance["components"]["codex"]["tree_sha256"], observed)
        self.assertNotEqual(provenance["reviewed_components"]["codex"]["tree_sha256"], observed)
        self.assertNotIn("codex", provenance["matches_reviewed"])
        self.assertIn("claude", provenance["matches_reviewed"])
        # Tampering with the closed tree or its recorded digest still refuses install.
        recorded = (self.context / "provenance.json").read_bytes()
        forged = json.loads(recorded)
        forged["components"]["codex"]["tree_sha256"] = "0" * 64
        (self.context / "provenance.json").write_text(json.dumps(forged))
        with self.assertRaises(ValueError):
            runtime.install(self.inputs, self.context, self.prefix)
        self.assertEqual([], snapshot(self.prefix))
        (self.context / "provenance.json").write_bytes(recorded)
        runtime.install(self.inputs, self.context, self.prefix)

    def test_reviewed_manifest_rejects_wrong_shape_versions_and_digest(self):
        original = self.inputs.read_bytes()
        for change in [
            "schema",
            "target",
            "versions",
            "components",
            "digest",
            "provenance",
            "extra",
            "duplicate",
        ]:
            value = json.loads(original)
            if change == "schema":
                value["schema"] = "foreign"
            elif change == "target":
                value["target"]["python"] = "3.12.14"
            elif change == "versions":
                value["versions"]["npm"] = "11.0.0"
            elif change == "components":
                del value["components"]["claude"]
            elif change == "digest":
                value["components"]["claude"]["tree_sha256"] = "G" * 64
            elif change == "provenance":
                value["provenance"] = {}
            elif change == "extra":
                value["foreign"] = True
            data = json.dumps(value).encode()
            if change == "duplicate":
                data = data[:-1] + b',"schema":"butlers.runtime-inputs/v1"}'
            self.inputs.write_bytes(data)
            with self.assertRaises(ValueError, msg=change):
                self.close()
            self.assertFalse(self.context.exists())
        self.inputs.write_bytes(original)
        self.close()  # Genuine complete-shaped healthy companion.

    def test_source_mutation_during_close_refuses_without_publishing(self):
        original = runtime._copy
        changed = False

        def mutate(*args):
            nonlocal changed
            original(*args)
            if not changed:
                changed = True
                file(self.components["node"] / "assets/complete-data", b"changed while copying")

        with patch.object(runtime, "_copy", side_effect=mutate), self.assertRaises(ValueError):
            self.close()
        self.assertTrue(changed)
        self.assertFalse(self.context.exists())
        self.assertFalse(list(self.base.glob(".runtime-context-*")))

    def test_source_links_special_files_and_privilege_bits_are_refused(self):
        member = self.components["claude"] / "unsafe"
        file(self.base / "outside", b"owned outside control")
        for change in ["absolute", "escape", "dangling", "cycle", "fifo", "setid", "unsafe-name"]:
            if change == "absolute":
                member.symlink_to(self.base / "outside")
            elif change == "escape":
                member.symlink_to("../../outside")
            elif change == "dangling":
                member.symlink_to("missing")
            elif change == "cycle":
                member.symlink_to("unsafe")
            elif change == "fifo":
                os.mkfifo(member)
            elif change == "setid":
                file(member, mode=0o4755)
            else:
                member = member.with_name("bad\\name")
                file(member)
            try:
                with self.assertRaises(ValueError, msg=change):
                    self.close()
                self.assertFalse(self.context.exists())
            finally:
                member.unlink()
        self.assertEqual((self.base / "outside").read_bytes(), b"owned outside control")
        self.close()

    def test_missing_or_nonexecutable_original_entrypoint_is_not_a_complete_component(self):
        entry = self.components["codex"] / "bin/codex.js"
        for change in ["missing", "not-executable"]:
            if change == "missing":
                entry.unlink()
            else:
                file(entry, mode=0o644)
            self.review()  # Digest is valid: failure must be actual required layout.
            with self.assertRaises(ValueError, msg=change):
                self.close()
            self.assertFalse(self.context.exists())
        file(entry, mode=0o755)
        self.review()
        self.close()

    def test_closed_metadata_shape_and_bytes_must_match_the_reviewed_manifest(self):
        self.close()
        provenance = self.context / "provenance.json"
        inputs = self.context / "inputs.json"
        for change in [
            "provenance",
            "manifest-bytes",
            "extra-component",
            "extra-root",
            "metadata-link",
            "directory-mode",
            "file-bytes",
            "metadata-mode",
            "component-root-mode",
            "context-mode",
        ]:
            old_provenance, old_inputs = provenance.read_bytes(), inputs.read_bytes()
            extra = self.context / "foreign"
            changed = self.context / "components/claude/assets/complete-data"
            directory = self.context / "components/claude/assets"
            old_member = changed.read_bytes()
            if change == "provenance":
                value = json.loads(old_provenance)
                value["foreign"] = True
                provenance.write_text(json.dumps(value))
            elif change == "manifest-bytes":
                inputs.write_bytes(old_inputs + b"\n")
            elif change == "extra-component":
                extra = self.context / "components/foreign"
                extra.mkdir()
            elif change == "extra-root":
                file(extra)
            elif change == "metadata-link":
                inputs.unlink()
                inputs.symlink_to(self.inputs)
            elif change == "directory-mode":
                directory.chmod(0o700)
            elif change == "metadata-mode":
                inputs.chmod(0o600)
            elif change == "component-root-mode":
                (self.context / "components/claude").chmod(0o700)
            elif change == "context-mode":
                self.context.chmod(0o700)
            else:
                changed.write_bytes(b"different")
            try:
                with self.assertRaises((ValueError, OSError), msg=change):
                    runtime.install(self.inputs, self.context, self.prefix)
                self.assertEqual([], snapshot(self.prefix))
            finally:
                if inputs.is_symlink():
                    inputs.unlink()
                inputs.write_bytes(old_inputs)
                inputs.chmod(0o644)
                provenance.write_bytes(old_provenance)
                self.context.chmod(0o755)
                (self.context / "components/claude").chmod(0o755)
                directory.chmod(0o755)
                changed.write_bytes(old_member)
                if extra.is_dir():
                    extra.rmdir()
                elif extra.exists():
                    extra.unlink()
        runtime.install(self.inputs, self.context, self.prefix)

    def test_destination_links_and_late_file_conflicts_leave_prefix_unchanged(self):
        self.close()
        outside = self.base / "untouched"
        outside.mkdir()
        usr = self.prefix / "usr"
        usr.symlink_to(outside)
        before = snapshot(self.prefix)
        with self.assertRaises(ValueError):
            runtime.install(self.inputs, self.context, self.prefix)
        self.assertEqual(before, snapshot(self.prefix))
        self.assertEqual([], snapshot(outside))
        usr.unlink()
        late = self.prefix / runtime.DESTINATIONS["opencode"] / "bin/opencode.exe"
        file(late, b"existing output", mode=0o755)
        before = snapshot(self.prefix)
        with self.assertRaises(ValueError):
            runtime.install(self.inputs, self.context, self.prefix)
        self.assertEqual(before, snapshot(self.prefix))
        late.unlink()
        parent = late.parent
        parent.chmod(0o500)
        try:
            # Root can bypass DAC; the denied-parent position is specific to an
            # actual unprivileged caller, with the healthy directories above.
            if not os.access(parent, os.W_OK | os.X_OK):
                before = snapshot(self.prefix)
                with self.assertRaises(ValueError):
                    runtime.install(self.inputs, self.context, self.prefix)
                self.assertEqual(before, snapshot(self.prefix))
        finally:
            parent.chmod(0o755)

    def test_close_refuses_parent_links_output_overlap_and_preserves_existing_output(self):
        outside = self.base / "other"
        outside.mkdir()
        link = self.base / "parent-link"
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            runtime.close(self.inputs, self.components, link / "closed")
        self.assertEqual([], snapshot(outside))
        with self.assertRaises(ValueError):
            runtime.close(self.inputs, self.components, self.components["node"] / "closed")
        file(self.context, b"existing")
        with self.assertRaises(ValueError):
            self.close()
        self.assertEqual(self.context.read_bytes(), b"existing")

    def test_cli_requires_explicit_root_and_only_copies_owned_synthetic_prefix(self):
        script = ROOT / "scripts/runtime_clis/runtime_artifacts.py"
        args = [
            sys.executable,
            str(script),
            "close",
            "--manifest",
            str(self.inputs),
            "--output",
            str(self.context),
        ]
        for name, path in self.components.items():
            args.extend(["--" + name, str(path)])
        result = subprocess.run(args, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        install = [
            sys.executable,
            str(script),
            "install",
            "--manifest",
            str(self.inputs),
            "--artifacts",
            str(self.context),
        ]
        result = subprocess.run(install, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"--root", result.stderr)
        result = subprocess.run(
            [*install, "--root", str(self.prefix)], capture_output=True, timeout=10
        )
        self.assertEqual(result.returncode, 0)
        self.assertTrue((self.prefix / "usr/local/bin/claude").is_symlink())


if __name__ == "__main__":
    unittest.main()
