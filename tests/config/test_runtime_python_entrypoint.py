"""Installed-only role execution and closed offline image-input provenance.

Synthetic tests use disposable minimal venvs, never project installation
or actual images. They also run under the normal pytest owning population.
"""

from __future__ import annotations

import importlib.util
import json
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "scripts/runtime_clis/python_entrypoint.py"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ENTRY = load("_test_image_entrypoint", SOURCE)
ENVIRONMENT = load("_test_environment_seal", SOURCE.with_name("python_environment.py"))


class InstalledStartup(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        source = self.project / "src/butlers"
        source.mkdir(parents=True)
        (source / "__init__.py").write_text("VALUE = 'original'\n")
        (source / "synthetic.py").write_text(
            "import json, sys, butlers; print(json.dumps([butlers.VALUE, *sys.argv[1:]]))\n"
        )
        (self.project / "pyproject.toml").write_text('[project]\nname="butlers"\n')
        (self.project / "uv.lock").write_text("synthetic-owned-lock\n")
        self.environment = self.root / "venv"
        subprocess.run(
            [sys.executable, "-m", "venv", "--without-pip", str(self.environment)],
            check=True,
            capture_output=True,
            timeout=15,
        )
        self.site = next(self.environment.glob("lib/python*/site-packages"))
        (self.site / "butlers.pth").write_text(str(self.project / "src") + "\n")
        metadata = self.site / "butlers-0.1.dist-info"
        metadata.mkdir()
        (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: butlers\nVersion: 0.1\n")
        (metadata / "RECORD").write_text("butlers-0.1.dist-info/METADATA,,\n")
        (self.environment / "bin/butlers").write_text(
            "import json, sys, butlers; print(json.dumps([butlers.VALUE, *sys.argv[1:]])); "
            "sys.exit(23 if '--exit-23' in sys.argv else 0)\n"
        )
        self.manifest = self.root / "environment.json"
        self.manifest.write_text(
            json.dumps(
                ENVIRONMENT.seal(
                    self.environment,
                    self.project,
                    ["dev"],
                    ["live-listener"],
                )
            )
        )
        # Run the actual dispatcher with test-only function parameters. Production
        # CLI has fixed /app targets and does not accept these as child flags.
        self.launcher = self.root / "launch.py"
        self.launcher.write_text(
            "import functools, importlib.util, pathlib, sys\n"
            f"spec=importlib.util.spec_from_file_location('entry', {str(SOURCE)!r})\n"
            "entry=importlib.util.module_from_spec(spec); spec.loader.exec_module(entry)\n"
            f"entry.run=functools.partial(entry.run, environment=pathlib.Path({str(self.environment)!r}), "
            f"project=pathlib.Path({str(self.project)!r}), manifest=pathlib.Path({str(self.manifest)!r}))\n"
            "entry.main()\n"
        )

    def launch(self, *arguments):
        return subprocess.run(
            [sys.executable, "-I", str(self.launcher), *arguments],
            capture_output=True,
            text=True,
            timeout=10,
            env={"PATH": str(self.root / "no-command-fallback")},
        )

    def test_console_and_python_roles_preserve_arguments_without_uv(self):
        console = self.launch(
            "butlers", "control", "--profile", "KEY_ABSENT_STAGE", "--project", "payload"
        )
        self.assertEqual(console.returncode, 0, console.stderr)
        self.assertEqual(
            json.loads(console.stdout),
            ["original", "control", "--profile", "KEY_ABSENT_STAGE", "--project", "payload"],
        )
        # Execute the actual composed session-host payload through the same seal,
        # with no uv/command fallback available in this synthetic environment.
        service = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"][
            "session-host"
        ]
        self.assertEqual(
            service["entrypoint"][:3],
            ["/usr/local/bin/python", "-I", "/app/scripts/runtime_clis/python_entrypoint.py"],
        )
        arguments = [*service["entrypoint"][3:], *service["command"]]
        self.assertEqual(arguments[:2], ["butlers", "session-host"])
        session_host = self.launch(*arguments)
        self.assertEqual(session_host.returncode, 0, session_host.stderr)
        self.assertEqual(json.loads(session_host.stdout), ["original", *arguments[1:]])
        seal = self.manifest.read_bytes()
        self.manifest.unlink()
        try:
            refused_session_host = self.launch(*arguments)
            self.assertEqual(refused_session_host.returncode, 78)
            self.assertEqual(refused_session_host.stdout, "")
        finally:
            self.manifest.write_bytes(seal)
        module = self.launch("python", "-m", "butlers.synthetic", "--host", "0.0.0.0")
        self.assertEqual(module.returncode, 0, module.stderr)
        self.assertEqual(json.loads(module.stdout), ["original", "--host", "0.0.0.0"])
        script = self.root / "gate.py"
        script.write_text("print('script-role')\n")
        self.assertEqual(self.launch("python", str(script)).stdout.strip(), "script-role")
        self.assertEqual(self.launch("butlers", "--exit-23").returncode, 23)
        refused = self.launch("uv", "sync", "--dev")
        self.assertEqual(refused.returncode, 78)
        self.assertEqual(refused.stdout, "")
        self.assertIn("Offline Python startup refused", refused.stderr)
        child = self.launch(
            "python",
            "-c",
            "import os,json; print(json.dumps([os.environ['VIRTUAL_ENV'], os.environ['PATH'].split(os.pathsep)[0]]))",
        )
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(
            json.loads(child.stdout), [str(self.environment), str(self.environment / "bin")]
        )

    def test_source_hotreload_works_but_project_lock_and_metadata_drift_refuse(self):
        (self.project / "src/butlers/__init__.py").write_text("VALUE = 'mounted'\n")
        self.assertEqual(json.loads(self.launch("butlers").stdout), ["mounted"])
        for path in [
            self.project / "uv.lock",
            self.project / "pyproject.toml",
            self.site / "butlers-0.1.dist-info/METADATA",
        ]:
            before = path.read_bytes()
            with self.subTest(path=path.name):
                try:
                    path.write_bytes(before + b"changed\n")
                    failed = self.launch("butlers")
                    self.assertEqual(failed.returncode, 78)
                    self.assertEqual(failed.stdout, "")
                finally:
                    path.write_bytes(before)
        self.assertEqual(self.launch("butlers").returncode, 0)

    def test_missing_manifest_wrong_import_and_symlink_environment_refuse(self):
        before = self.manifest.read_bytes()
        self.manifest.unlink()
        self.assertEqual(self.launch("butlers").returncode, 78)
        self.manifest.write_bytes(before)
        (self.site / "butlers.pth").write_text(str(self.root / "foreign") + "\n")
        self.assertEqual(self.launch("butlers").returncode, 78)
        with self.assertRaises(ValueError):
            link = self.root / "linked-venv"
            link.symlink_to(self.environment, target_is_directory=True)
            ENTRY.run(["butlers"], environment=link, project=self.project, manifest=self.manifest)

    def test_exec_preserves_pid_and_delivers_term_to_role(self):
        (self.environment / "bin/butlers").write_text(
            "import os, time; print(os.getpid(), flush=True); time.sleep(30)\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-I", str(self.launcher), "butlers"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(int(process.stdout.readline()), process.pid)
            process.send_signal(signal.SIGTERM)
            process.communicate(timeout=5)
            self.assertEqual(process.returncode, -signal.SIGTERM)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)


class ArtifactInputs(unittest.TestCase):
    def test_closed_artifacts_accept_and_refuse_positioned_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            for name in ["pyproject.toml", "uv.lock"]:
                (project / name).write_text(name)
            artifacts = root / "artifacts"
            (artifacts / "wheelhouse").mkdir(parents=True)
            (artifacts / "wheelhouse/synthetic.whl").write_bytes(
                b"input-bytes-not-an-installed-wheel"
            )
            (artifacts / "build-constraints.txt").write_text("hatchling==1.27.0\n")
            provenance = {
                "schema": "butlers.python-artifacts/v1",
                "target": {"python": "3.12.13", "platform": "linux-amd64", "libc": "glibc2.41"},
                "project": {
                    name: ENTRY._digest(project / name) for name in ["pyproject.toml", "uv.lock"]
                },
                "profiles": {
                    "production": {"groups": [], "extras": ["live-listener"]},
                    "development": {"groups": ["dev"], "extras": ["live-listener"]},
                },
                "inputs": [
                    {"path": name, "sha256": ENTRY._digest(artifacts / name)}
                    for name in ["wheelhouse/synthetic.whl", "build-constraints.txt"]
                ],
            }
            manifest = artifacts / "provenance.json"
            encoded = json.dumps(provenance)
            manifest.write_text(encoded)
            for profile in ["production", "development"]:
                ENTRY.verify_build(artifacts, project, profile, ["live-listener"])
            for mutation in [
                "target",
                "project",
                "extras",
                "unknown",
                "missing",
                "changed",
                "foreign",
                "symlink",
                "duplicate",
                "traversal",
            ]:
                with self.subTest(mutation=mutation):
                    value = json.loads(encoded)
                    changed = None
                    if mutation == "target":
                        value["target"]["python"] = "3.13.0"
                    if mutation == "project":
                        value["project"]["uv.lock"] = "0" * 64
                    if mutation == "extras":
                        value["profiles"]["production"]["extras"] = []
                    if mutation == "unknown":
                        value["unknown"] = True
                    if mutation == "missing":
                        changed = artifacts / "wheelhouse/synthetic.whl"
                        changed.unlink()
                    if mutation == "changed":
                        (artifacts / "wheelhouse/synthetic.whl").write_bytes(b"changed")
                    if mutation in {"foreign", "symlink"}:
                        changed = artifacts / "foreign"
                        if mutation == "foreign":
                            changed.write_text("undeclared")
                        else:
                            changed.symlink_to(project / "uv.lock")
                    if mutation == "duplicate":
                        value["inputs"].append(value["inputs"][0])
                    if mutation == "traversal":
                        value["inputs"][0]["path"] = "wheelhouse/../../foreign"
                    manifest.write_text(json.dumps(value))
                    try:
                        with self.assertRaises(ValueError):
                            ENTRY.verify_build(artifacts, project, "production", ["live-listener"])
                    finally:
                        if changed is not None and (changed.exists() or changed.is_symlink()):
                            changed.unlink()
                        (artifacts / "wheelhouse/synthetic.whl").write_bytes(
                            b"input-bytes-not-an-installed-wheel"
                        )
                        manifest.write_text(encoded)
            ENTRY.verify_build(artifacts, project, "production", ["live-listener"])


if __name__ == "__main__":
    unittest.main()
