"""Rendered application roles retain argv and validate their installed environment.

These are source/stdlib-venv controls, not container or operational role runs.
Only fixed image paths are projected into a disposable fixture; the actual
entrypoint and log-wrapper control flow execute against nonoperational targets.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DISPATCH = ["/usr/local/bin/python", "-I", "/app/scripts/runtime_clis/python_entrypoint.py"]


def _fixture_module():
    spec = importlib.util.spec_from_file_location(
        "_role_installed_fixture", ROOT / "tests/config/test_runtime_python_entrypoint.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(shutil.which("helm"), "helm not installed")
class RenderedPythonRoles(unittest.TestCase):
    def setUp(self):
        self.installed = _fixture_module().InstalledStartup(methodName="runTest")
        self.installed.setUp()
        self.addCleanup(self.installed.doCleanups)
        self.fixture = self.installed
        rendered = subprocess.run(
            [
                "helm", "template", "butlers", str(ROOT / "deploy/helm/butlers"),
                "--namespace", "butlers-dev",
                "--set", "image.tag=synthetic", "--set", "frontendImage.tag=synthetic",
                "--set", "database.host=postgres.invalid", "--set", "egressPolicy.enabled=false",
                "--set", "egressProfiles.enabled=false",
                "--set", "egressProfiles.sites.database.namespace=postgres-dev",
                "--set", "egressProfiles.sites.otlp.cidr=198.51.100.9/32",
                "--set", "egressProfiles.sites.home-assistant.cidr=198.51.100.10/32",
                "--set", "egressProfiles.sites.offsite-backup.cidr=198.51.100.11/32",
            ],
            capture_output=True, text=True, timeout=30, check=False,
        )  # fmt: skip
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        self.pods = {
            doc["metadata"]["name"]: doc["spec"]["template"]["spec"]
            for doc in yaml.safe_load_all(rendered.stdout)
            if doc and doc["kind"] == "Deployment"
        }
        self.connectors = {
            name: pod["containers"][0]["command"]
            for name, pod in self.pods.items()
            if name.startswith("connector-")
        }
        health_init = self.pods["connector-google-health"]["initContainers"]
        oauth = next(role for role in health_init if role["name"] == "oauth-gate")
        waiter = next(role for role in health_init if role["name"] == "wait-for-health-routes")
        gateway_render = subprocess.run(
            [
                *rendered.args,
                "--set",
                "egressProfiles.enabled=true",
                "--set",
                "trackerEgress.deniedCidrs={192.0.2.1/32}",
                "--set",
                "connectors.telegram-user.enabled=false",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(gateway_render.returncode, 0, gateway_render.stderr)
        gateway_doc = next(
            doc
            for doc in yaml.safe_load_all(gateway_render.stdout)
            if doc and doc["kind"] == "Deployment" and doc["metadata"]["name"] == "egress-gateway"
        )
        gateway = gateway_doc["spec"]["template"]["spec"]["containers"][0]
        compose = yaml.safe_load((ROOT / "docker-compose.restore-drill.yml").read_text())
        executor = compose["services"]["restore-drill-executor"]
        self.direct = {
            "oauth": oauth["command"],
            "health-waiter": waiter["command"] + waiter["args"],
            "gateway": gateway["command"],
            "restore-executor": executor["entrypoint"] + executor["command"],
        }
        self.payload = "import json,sys; print(json.dumps(sys.argv[1:]))\n"
        for command in [*self.connectors.values(), *self.direct.values()]:
            # Both published old and current argv end in the same module/script.
            # Populate only these explicit targets; never execute application code.
            if "-m" in command:
                module = command[command.index("-m") + 1]
                target = self.fixture.project / "src" / (module.replace(".", "/") + ".py")
                target.parent.mkdir(parents=True, exist_ok=True)
                (target.parent / "__init__.py").touch()
                target.write_text(self.payload)
            else:
                script = next(value for value in command if value.startswith("scripts/"))
                target = self.fixture.project / script
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(self.payload)
        # Explicit image-path projection only, retaining bash/tee/logging/exec.
        self.wrapper = self.fixture.root / "dev_entrypoint.sh"
        self.wrapper.write_text(
            (ROOT / "scripts/dev_entrypoint.sh")
            .read_text()
            .replace("/app/logs", str(self.fixture.root / "logs"))
            .replace("/usr/local/bin/python", sys.executable)
            .replace("/app/scripts/runtime_clis/python_entrypoint.py", str(self.fixture.launcher))
        )

    def execute(self, command):
        if command[0] == "/app/scripts/dev_entrypoint.sh":
            projected = ["/bin/bash", str(self.wrapper), *command[1:]]
        else:
            self.assertEqual(command[:3], DISPATCH)
            projected = [sys.executable, "-I", str(self.fixture.launcher), *command[3:]]
        return subprocess.run(
            projected,
            cwd=self.fixture.project,
            capture_output=True,
            text=True,
            timeout=10,
            env={"PATH": "/usr/bin:/bin"},
        )

    def assert_healthy(self, command):
        result = self.execute(command)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        marker = (
            command.index("-m")
            if "-m" in command
            else next(i for i, value in enumerate(command) if value.startswith("scripts/"))
        )
        tail = command[marker + (2 if command[marker] == "-m" else 1) :]
        self.assertEqual(json.loads(result.stdout), tail)

    def test_all_rendered_connectors_use_actual_log_wrapper_and_dispatcher(self):
        values = yaml.safe_load((ROOT / "deploy/helm/butlers/values.yaml").read_text())
        expected = {
            f"connector-{name}"
            for name, value in values["connectors"].items()
            if value.get("enabled") is not False
        }
        self.assertEqual(set(self.connectors), expected)
        for name, command in self.connectors.items():
            with self.subTest(connector=name):
                self.assertEqual(command[:1], ["/app/scripts/dev_entrypoint.sh"])
                self.assert_healthy(command)

    def test_each_application_role_refuses_missing_or_drifted_seal(self):
        commands = {**self.connectors, **self.direct}
        for name, command in commands.items():
            with self.subTest(role=name):
                self.assert_healthy(command)
                seal = self.fixture.manifest.read_bytes()
                lock = self.fixture.project / "uv.lock"
                original_lock = lock.read_bytes()
                for mutation in ["missing", "lock-drift"]:
                    with self.subTest(mutation=mutation):
                        try:
                            if mutation == "missing":
                                self.fixture.manifest.unlink()
                            else:
                                lock.write_bytes(original_lock + b"changed\n")
                            result = self.execute(command)
                            self.assertEqual(result.returncode, 78)
                            category = (
                                "FileNotFoundError" if mutation == "missing" else "ValueError"
                            )
                            diagnostic = f"Offline Python startup refused ({category})\n"
                            if command[0] == "/app/scripts/dev_entrypoint.sh":
                                # The real log wrapper intentionally tees merged stderr.
                                self.assertEqual(result.stdout, diagnostic)
                                self.assertEqual(result.stderr, "")
                            else:
                                self.assertEqual(result.stdout, "")
                                self.assertEqual(result.stderr, diagnostic)
                        finally:
                            self.fixture.manifest.write_bytes(seal)
                            lock.write_bytes(original_lock)
                self.assert_healthy(command)

    def test_direct_roles_keep_explicit_installed_only_boundary(self):
        for name, command in self.direct.items():
            with self.subTest(role=name):
                self.assertEqual(command[:4], [*DISPATCH, "python"])
                self.assert_healthy(command)
        refused = self.fixture.launch("uv", "run", "python", "-m", "butlers.synthetic")
        self.assertEqual(refused.returncode, 78)
        self.assertEqual(refused.stdout, "")


if __name__ == "__main__":
    unittest.main()
