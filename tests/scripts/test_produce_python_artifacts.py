"""Producer orchestration with synthetic inputs; no installed-profile proof."""

from __future__ import annotations

import ctypes
import hashlib
import importlib.util
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/runtime_clis/produce_python_artifacts.py"
spec = importlib.util.spec_from_file_location("_tested_python_producer", SCRIPT)
producer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(producer)
LOCK = ROOT / "deploy/runtime-clis/python-artifacts/build-lock.toml"


def git(project, *args):
    return subprocess.run(
        ["git", "-C", str(project), *args], check=True, capture_output=True, timeout=10
    ).stdout


def checkout(root):
    project = root / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[build-system]\nrequires=["hatchling"]\nbuild-backend="hatchling.build"\n'
        '[project]\nname="butlers"\nversion="0.1.0"\n'
    )
    (project / "uv.lock").write_text("version=1\n")
    (project / "src/butlers").mkdir(parents=True)
    (project / "src/butlers/__init__.py").write_text("# synthetic package\n")
    (project / "src/butlers/data.json").write_text('{"real_data":true}\n')
    (project / "src/butlers/py.typed").touch()
    for name in producer.HELPERS:
        target = project / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    git(project, "init", "-q")
    git(project, "add", ".")
    git(
        project,
        "-c",
        "user.name=Synthetic",
        "-c",
        "user.email=synthetic@example.invalid",
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "-qm",
        "Synthetic packaging inputs",
    )
    return project


class SyntheticOperations:
    """Record real orchestration; do not simulate native install conformance."""

    uv = Path("/synthetic/uv")
    environment = producer.isolated_environment("/synthetic/empty-home")

    def __init__(self, output):
        self.output = output
        self.commands = []
        self.requests = []
        self.fail_profile = None
        self.mutate = None

    def target(self):
        return {"target": producer.TARGET, "test_only": "synthetic"}

    def fetch(self, artifact, wheelhouse):
        path = wheelhouse / producer.public_artifact(artifact)[0]
        path.write_bytes(b"synthetic complete input")
        return path

    def run(self, argv, project, **kwargs):
        self.commands.append((argv, project, kwargs))
        if "--output-file" in argv:
            Path(argv[argv.index("--output-file") + 1]).write_text("# synthetic export\n")
        if "sync" in argv:
            cache = Path(argv[argv.index("--cache-dir") + 1])
            (cache / "executable").write_bytes(b"owned executable cache bytes")
            (cache / "executable").chmod(0o755)
        return ""

    def packaging(self, wheel, artifact):
        return None

    def select(self, export, packaging):
        return [], {"synthetic-runtime": "1.0"}

    def offline_profile(self, request):
        if self.output.exists():
            raise AssertionError("published before all profile checks")
        self.requests.append(request)
        if len(self.requests) == self.fail_profile:
            raise RuntimeError("positioned synthetic profile failure")
        if self.mutate:
            self.mutate(request)
        return {"status": "PASS", "variant": request["variant"], "profile": request["profile"]}


def command_owner(work: Path) -> None:
    operations = producer.Operations.__new__(producer.Operations)
    operations.work, operations.sequence, operations.nested = work, 0, False
    operations.environment = producer.isolated_environment(os.environ["HOME"])
    child = (
        "import os,sys,time; from pathlib import Path; "
        "Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(20)"
    )
    operations.run([sys.executable, "-I", "-c", child, str(work / "child.pid")], work, timeout=25)


def cancellation_driver(work: Path, signum: int) -> dict:
    # This private driver owns orphan adoption only for its own inert command.
    # A deliberately old owner can fail; still reap its actual child before exit.
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
        raise RuntimeError("cannot supervise the synthetic cancellation control")
    child = None
    with (work / "owner.log").open("wb") as log:
        owner = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-B",
                str(Path(__file__).resolve()),
                "--command-owner",
                str(work),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 5
            while not (work / "child.pid").exists():
                if owner.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("cancellation owner did not reach its command")
                time.sleep(0.01)
            child = int((work / "child.pid").read_text())
            if os.getpgid(child) != child:
                raise AssertionError("command is not in its owned session")
            os.kill(owner.pid, signum)
            status = owner.wait(timeout=8)
            survived = Path(f"/proc/{child}").exists()
            path = work / "command-01.json"
            result = {
                "owner_exit": status,
                "child_survived": survived,
                "receipt": json.loads(path.read_text()) if path.exists() else None,
            }
        finally:
            if owner.poll() is None:
                owner.kill()
                owner.wait(timeout=5)
            if child is not None:
                try:
                    os.killpg(child, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    os.waitpid(child, 0)
                except ChildProcessError:
                    pass
            if Path(f"/proc/{owner.pid}").exists() or (
                child is not None and Path(f"/proc/{child}").exists()
            ):
                raise AssertionError("owned cancellation driver did not settle")
    return result


class ProducerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = checkout(self.root)
        self.work, self.output = self.root / "work", self.root / "output"
        self.ops = SyntheticOperations(self.output)

    def seed(self):
        return producer.public_seed(self.project, self.work, self.output, LOCK, self.ops)

    def verify(self):
        return producer.offline_verify(self.project, self.work, self.output, LOCK, self.ops)

    def test_snapshot_includes_tracked_dirty_data_and_excludes_ambient(self):
        data = self.project / "src/butlers/data.json"
        data.write_text('{"changed":true}')
        (self.project / ".env").write_text("not copied")
        (self.project / ".venv").mkdir()
        (self.project / "src/butlers/untracked.py").write_text("not copied")
        declared = producer.snapshot(self.project, self.output)
        self.assertEqual(producer.source_manifest(self.output), declared)
        self.assertEqual((self.output / "src/butlers/data.json").read_bytes(), data.read_bytes())
        self.assertTrue((self.output / "src/butlers/py.typed").is_file())
        self.assertTrue(all((self.output / name).is_file() for name in producer.HELPERS))
        self.assertFalse((self.output / ".env").exists())
        self.assertFalse((self.output / ".git").exists())
        self.assertFalse((self.output / "src/butlers/untracked.py").exists())
        data.write_text("later source mutation")
        self.assertNotEqual((self.output / "src/butlers/data.json").read_bytes(), data.read_bytes())

    def test_closed_snapshot_four_profile_flow_does_not_create_import_cache(self):
        closed = self.root / "closed-source"
        producer.snapshot(self.project, closed)
        self.project = closed
        before = producer.source_manifest(closed)
        error = None
        with patch.object(sys, "dont_write_bytecode", False):
            try:
                self.seed()
                result = self.verify()
            except ValueError as cause:
                error = str(cause)
            self.assertFalse(sys.dont_write_bytecode)
        self.assertIsNone(error, f"ordinary closed source orchestration refused: {error}")
        self.assertEqual(result["status"], "VERIFIED_FOUR_PROFILES")
        self.assertEqual(len(self.ops.requests), 4)
        self.assertTrue(self.output.is_dir())
        self.assertEqual(producer.source_manifest(closed), before)
        self.assertFalse(list(closed.rglob("__pycache__")))

    def test_snapshot_refuses_tampering_missing_extra_and_mode_drift(self):
        producer.snapshot(self.project, self.output)
        target = self.output / "src/butlers/data.json"
        original, mode = target.read_bytes(), target.stat().st_mode & 0o777
        for mutation in (
            lambda: target.write_bytes(b"changed"),
            lambda: target.unlink(),
            lambda: target.chmod(mode ^ 0o100),
            lambda: (self.output / "unknown").write_text("extra"),
        ):
            with self.subTest(mutation=mutation):
                mutation()
                with self.assertRaises((ValueError, FileNotFoundError)):
                    producer.source_manifest(self.output)
                (self.output / "unknown").unlink(missing_ok=True)
                target.write_bytes(original)
                target.chmod(mode)
        self.assertEqual(producer.source_manifest(self.output)["schema"], producer.SOURCE_SCHEMA)

    def test_refuses_untracked_required_input_hooks_links_and_arbitrary_directory(self):
        required = self.project / producer.HELPERS[0]
        git(self.project, "rm", "--cached", producer.HELPERS[0])
        with self.assertRaisesRegex(ValueError, "incomplete"):
            producer.source_manifest(self.project)
        git(self.project, "add", producer.HELPERS[0])
        config = self.project / "pyproject.toml"
        original = config.read_text()
        config.write_text(original + "[tool.hatch.build.hooks.custom]\n")
        with self.assertRaisesRegex(ValueError, "hooks"):
            producer.source_manifest(self.project)
        config.write_text(original)
        data = required.read_bytes()
        required.unlink()
        required.symlink_to(ROOT / producer.HELPERS[0])
        with self.assertRaisesRegex(ValueError, "links"):
            producer.source_manifest(self.project)
        required.unlink()
        required.write_bytes(data)
        arbitrary = self.root / "arbitrary"
        shutil.copytree(self.project, arbitrary, ignore=shutil.ignore_patterns(".git"))
        with self.assertRaises(subprocess.CalledProcessError):
            producer.source_manifest(arbitrary)
        self.assertEqual(producer.source_manifest(self.project)["schema"], producer.SOURCE_SCHEMA)

    def test_tracked_sensitive_packaging_path_refuses_instead_of_copying(self):
        sensitive = self.project / "src/butlers/.env"
        sensitive.write_text("synthetic excluded input")
        git(self.project, "add", str(sensitive))
        with self.assertRaisesRegex(ValueError, "ambient or credential"):
            producer.snapshot(self.project, self.output)
        self.assertFalse(self.output.exists())
        git(self.project, "rm", "--cached", str(sensitive))
        self.assertEqual(
            producer.snapshot(self.project, self.output)["schema"], producer.SOURCE_SCHEMA
        )
        self.assertFalse((self.output / "src/butlers/.env").exists())

    def test_source_path_population_and_identity_are_closed(self):
        producer.snapshot(self.project, self.output)
        manifest = self.output / "source-inputs.json"
        original = json.loads(manifest.read_text())
        for name in ("../outside", "/absolute", "src//double", "src/./dot", "src\\backslash"):
            with self.subTest(name=name):
                value = json.loads(json.dumps(original))
                value["inputs"][0]["path"] = name
                producer.write_json(manifest, value)
                with self.assertRaises((ValueError, FileNotFoundError)):
                    producer.source_manifest(self.output)
        producer.write_json(manifest, original)
        self.assertEqual(producer.source_manifest(self.output), original)

    def test_exact_backend_lock_and_constraint_inventory(self):
        backend = producer.backend_lock(LOCK)
        self.assertEqual(len(backend["packages"]), 9)
        self.assertEqual(
            {p["name"] for p in backend["packages"]},
            {
                "editables",
                "hatchling",
                "packaging",
                "pathspec",
                "pluggy",
                "setuptools",
                "tomlkit",
                "trove-classifiers",
                "wheel",
            },
        )
        modified = self.root / "changed.toml"
        modified.write_text(LOCK.read_text().replace('version = "0.6"', 'version = "0.7"'))
        with self.assertRaisesRegex(ValueError, "nine-wheel"):
            producer.backend_lock(modified)

    def test_seed_is_nonfinal_full_four_export_profiles_and_no_index_sync(self):
        receipt = self.seed()
        self.assertEqual(receipt["status"], "NONFINAL")
        self.assertFalse(self.output.exists())
        self.assertEqual(set(receipt["versions"]), {f"{v}/{p}" for v, p in producer.PROFILES})
        exports = [command for command, _, _ in self.ops.commands if "export" in command]
        self.assertEqual(len(exports), 4)
        self.assertEqual(sum("--extra" in command for command in exports), 2)
        self.assertEqual(sum("--group" in command for command in exports), 2)
        for command, _, _ in self.ops.commands:
            self.assertIn("--frozen", command)
            if "export" in command:
                self.assertIn("--offline", command)
            self.assertNotIn("--build-constraints", command)
        self.assertIn("--no-index", self.ops.commands[-1][0])
        constraints = (self.work / "seed/build-constraints.txt").read_text()
        self.assertIn("hatchling==1.33.0\n", constraints)
        self.assertIn("editables==0.6\n", constraints)

    def test_only_public_seed_primes_url_cache_and_cold_sync_stays_offline(self):
        self.seed()
        syncs = [command for command, _, _ in self.ops.commands if "sync" in command]
        self.assertEqual(len(syncs), 1)
        self.assertNotIn("--offline", syncs[0])
        cold = producer.sync_argv(
            self.ops, self.project, self.root / "cache", self.root / "wheels", "production", []
        )
        self.assertIn("--offline", cold)
        for command in (syncs[0], cold):
            for flag in ("--frozen", "--no-index", "--find-links", "--no-python-downloads"):
                self.assertIn(flag, command)

    def test_seed_exports_use_uv_pylock_filenames(self):
        self.seed()
        exports = [command for command, _, _ in self.ops.commands if "export" in command]
        self.assertEqual(
            [Path(command[command.index("--output-file") + 1]).name for command in exports],
            [f"pylock.{variant}-{profile}.toml" for variant, profile in producer.PROFILES],
        )

    def test_all_four_positive_receipts_before_atomic_independent_publication(self):
        self.seed()
        receipt = self.verify()
        self.assertEqual(receipt["status"], "VERIFIED_FOUR_PROFILES")
        self.assertEqual(
            [(r["variant"], r["profile"]) for r in self.ops.requests], list(producer.PROFILES)
        )
        for variant in ("default", "live-listener"):
            context = self.output / variant
            extras = [] if variant == "default" else [variant]
            producer.helper(self.project, "python_entrypoint").verify_build(
                context, self.project, "production", extras
            )
            target = context / "uv-cache/executable"
            source = self.work / "seed/uv-cache/executable"
            self.assertNotEqual(target.stat().st_ino, source.stat().st_ino)
            self.assertEqual(target.stat().st_mode & 0o777, 0o755)
            self.assertEqual(target.read_bytes(), source.read_bytes())
        (self.work / "seed/uv-cache/executable").write_bytes(b"later mutable seed")
        self.assertEqual(
            (self.output / "default/uv-cache/executable").read_bytes(),
            b"owned executable cache bytes",
        )

    def test_each_failed_profile_leaves_no_final_output(self):
        self.seed()
        for index in range(1, 5):
            with self.subTest(profile=index):
                self.ops.requests = []
                self.ops.fail_profile = index
                with self.assertRaisesRegex(RuntimeError, "positioned"):
                    self.verify()
                self.assertFalse(self.output.exists())
                self.assertEqual(len(self.ops.requests), index)
                shutil.rmtree(self.work / "verification")
                self.assertFalse(list(self.root.glob("python-final-*")))
        self.ops.fail_profile = None
        self.assertEqual(self.verify()["status"], "VERIFIED_FOUR_PROFILES")

    def test_seed_material_and_source_drift_refuse_before_profile(self):
        self.seed()
        target = self.work / "seed/uv-cache/executable"
        target.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "drift"):
            self.verify()
        self.assertEqual(self.ops.requests, [])
        self.assertFalse(self.output.exists())

    def test_mutation_during_profiles_prevents_publication(self):
        self.seed()
        self.ops.mutate = lambda request: (self.work / "seed/build-constraints.txt").write_text(
            "drift"
        )
        with self.assertRaisesRegex(ValueError, "changed"):
            self.verify()
        self.assertFalse(self.output.exists())
        self.assertEqual(len(self.ops.requests), 4)

    def test_existing_output_is_never_replaced(self):
        self.output.mkdir()
        sentinel = self.output / "sentinel"
        sentinel.write_text("preserved")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.seed()
        self.assertEqual(sentinel.read_text(), "preserved")
        self.assertEqual(self.ops.commands, [])

    def test_actual_child_refuses_route_before_target_or_any_command(self):
        request = self.root / "request.json"
        request.write_text("not read before route check")
        with (
            patch.object(producer, "check_network", side_effect=ValueError("external route")),
            patch.object(producer, "Operations") as operations,
        ):
            with self.assertRaisesRegex(ValueError, "external route"):
                producer.verify_child(request)
            operations.assert_not_called()

    def test_network_parsers_require_only_loopback_and_no_gateway(self):
        good = {
            "/proc/net/dev": "header\nheader\n lo: 0 0\n",
            "/proc/net/route": "header\n",
            "/proc/net/ipv6_route": "",
        }

        def read(path):
            return current[str(path)]

        for filename, suffix in (
            ("/proc/net/dev", " eth0: 1 1\n"),
            ("/proc/net/route", "eth0 00000000 01010101 0\n"),
            ("/proc/net/ipv6_route", "0 " * 9 + "eth0\n"),
        ):
            current = dict(good)
            current[filename] += suffix
            with self.subTest(filename=filename), patch.object(Path, "read_text", read):
                with self.assertRaisesRegex(ValueError, "loopback|external"):
                    producer.check_network()
        current = good
        with patch.object(Path, "read_text", read):
            producer.check_network()

    def test_each_actual_child_uses_fresh_project_cache_and_real_seal_contract(self):
        self.seed()
        context = self.root / "context"
        producer.helper(self.project, "close_python_artifacts").close_context(
            project=self.project,
            wheelhouse=self.work / "seed/wheelhouse",
            cache=self.work / "seed/uv-cache",
            constraints=self.work / "seed/build-constraints.txt",
            output=context,
            extras=[],
        )
        parent = self.root / "children"
        parent.mkdir()
        calls = []

        class ChildOperations:
            uv = Path("/synthetic/uv")
            environment = producer.isolated_environment("/synthetic/empty-home")

            def __init__(self, work, uv=None, nested=False):
                self.work = work

            def target(self):
                return {"test_only": "synthetic target"}

            def run(self, argv, project, **kwargs):
                environment = Path(kwargs["environment"]["UV_PROJECT_ENVIRONMENT"])
                environment.mkdir()
                calls.append((argv, project, environment))

        environment_helper = producer.helper(self.project, "python_environment")
        actual_helper = producer.helper

        def load(project, name):
            return (
                environment_helper if name == "python_environment" else actual_helper(project, name)
            )

        inventory = {"distributions": [{"name": "butlers", "version": "0.1.0"}]}
        for profile in ("production", "development"):
            request = {
                "variant": "default",
                "profile": profile,
                "source": str(self.project),
                "source_manifest": producer.source_manifest(self.project),
                "context": str(context),
                "uv": "/synthetic/uv",
                "runtime": {"test_only": "synthetic target"},
                "versions": {"butlers": "0.1.0"},
                "receipt": str(parent / f"{profile}-receipt.json"),
            }
            request_path = parent / f"{profile}-request.json"
            producer.write_json(request_path, request)
            with (
                patch.object(producer, "check_network") as route,
                patch.object(producer, "Operations", ChildOperations),
                patch.object(producer, "helper", load),
                patch.object(environment_helper, "inventory", return_value=inventory),
            ):
                producer.verify_child(request_path)
                route.assert_called_once()
            receipt = json.loads(Path(request["receipt"]).read_text())
            self.assertEqual(receipt["status"], "PASS")
            self.assertEqual(receipt["versions"], request["versions"])
            manifest = parent / f"default-{profile}/environment/.butlers-environment.json"
            self.assertEqual(json.loads(manifest.read_text())["environment"], inventory)
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0][1], calls[1][1])
        self.assertNotEqual(calls[0][2], calls[1][2])
        first_cache = Path(calls[0][0][calls[0][0].index("--cache-dir") + 1])
        second_cache = Path(calls[1][0][calls[1][0].index("--cache-dir") + 1])
        self.assertNotEqual(first_cache, second_cache)
        self.assertNotEqual(
            (first_cache / "executable").stat().st_ino, (second_cache / "executable").stat().st_ino
        )

    def test_material_binding_covers_directory_link_contents_and_refuses_escape_cycle(self):
        material = self.root / "material"
        (material / "payload").mkdir(parents=True)
        content = material / "payload/data"
        content.write_text("first")
        (material / "alias").symlink_to("payload", target_is_directory=True)
        first = producer.material_identity(material)
        content.write_text("second")
        self.assertNotEqual(producer.material_identity(material), first)
        (material / "alias").unlink()
        (material / "alias").symlink_to(self.project, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "leaves"):
            producer.material_identity(material)
        (material / "alias").unlink()
        (material / "payload/cycle").symlink_to("..", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "cycle"):
            producer.material_identity(material)

    def test_real_command_failure_and_timeout_have_waited_terminal_receipts(self):
        work = self.root / "commands"
        work.mkdir()
        operations = producer.Operations.__new__(producer.Operations)
        operations.work = work
        operations.sequence = 0
        operations.nested = False
        operations.environment = producer.isolated_environment(str(self.root))
        previous_handler = signal.getsignal(signal.SIGTERM)
        with self.assertRaisesRegex(RuntimeError, "command failed"):
            operations.run([sys.executable, "-I", "-c", "raise SystemExit(7)"], self.root)
        with self.assertRaises(subprocess.TimeoutExpired):
            operations.run(
                [sys.executable, "-I", "-c", "import time; time.sleep(5)"], self.root, timeout=0.05
            )
        self.assertEqual(
            operations.run([sys.executable, "-I", "-c", "print('positive')"], self.root).strip(),
            "positive",
        )
        receipts = [json.loads(path.read_text()) for path in sorted(work.glob("*.json"))]
        self.assertEqual([r["exit"] for r in receipts], [7, -9, 0])
        self.assertTrue(all(r["parent_waited"] for r in receipts))
        self.assertTrue(
            all(r["group_settlement"] == {"running": [], "zombies": []} for r in receipts)
        )
        self.assertTrue(receipts[1]["timed_out_or_cancelled"])
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous_handler)
        self.assertTrue(all(not Path(f"/proc/{r['pid']}").exists() for r in receipts))

    def _assert_command_cancellation(self, signum):
        work = self.root / "cancellation"
        work.mkdir()
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                str(Path(__file__).resolve()),
                "--cancellation-driver",
                str(work),
                str(signum),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        result = json.loads(completed.stdout)
        self.assertFalse(result["child_survived"], result)
        self.assertEqual(result["owner_exit"], -signum)
        self.assertTrue(result["receipt"]["parent_waited"])
        self.assertTrue(result["receipt"]["timed_out_or_cancelled"])
        self.assertEqual(result["receipt"]["group_settlement"], {"running": [], "zombies": []})

    @unittest.skipUnless(sys.platform == "linux", "producer command supervision uses Linux /proc")
    def test_sigterm_owner_settles_command_before_preserving_signal_exit(self):
        self._assert_command_cancellation(signal.SIGTERM)

    @unittest.skipUnless(sys.platform == "linux", "producer command supervision uses Linux /proc")
    def test_sigint_owner_keeps_its_existing_settled_signal_exit(self):
        self._assert_command_cancellation(signal.SIGINT)

    def test_target_accepts_pinned_official_uv_spelling_and_refuses_other_inputs(self):
        operations = producer.Operations.__new__(producer.Operations)
        operations.work = self.root
        operations.uv = self.root / "synthetic-uv"
        operations.uv.write_bytes(b"synthetic target-identity fixture")
        with (
            patch.object(producer.platform, "python_version", return_value="3.12.13"),
            patch.object(producer.platform, "system", return_value="Linux"),
            patch.object(producer.platform, "machine", return_value="x86_64"),
            patch.object(producer.platform, "libc_ver", return_value=("glibc", "2.41")),
            patch.object(
                operations, "run", return_value="uv 0.12.22 (x86_64-unknown-linux-gnu)\n"
            ) as run,
        ):
            error = None
            try:
                result = operations.target()
            except ValueError as cause:
                error = str(cause)
            self.assertIsNone(error, f"pinned published uv identity refused: {error}")
            self.assertEqual(result["target"], producer.TARGET)
            self.assertEqual(result["uv_sha256"], producer.digest(operations.uv))
            run.assert_called_once_with([str(operations.uv), "--version"], self.root, timeout=15)
            run.return_value = "uv 0.12.22\n"
            self.assertEqual(operations.target()["uv_sha256"], result["uv_sha256"])
            for text in (
                "uv 0.12.21 (x86_64-unknown-linux-gnu)",
                "uv 0.12.22 (aarch64-unknown-linux-gnu)",
                "uv 0.12.22 extra",
                "uv 0.12.22 (x86_64-unknown-linux-gnu) extra",
            ):
                run.return_value = text
                with self.assertRaisesRegex(ValueError, "uv version mismatch"):
                    operations.target()

    def test_clean_environment_preserves_home_but_not_ambient_settings(self):
        with patch.dict(os.environ, {"HTTP_PROXY": "synthetic-proxy", "UV_INDEX": "not-used"}):
            env = producer.isolated_environment("/owned/empty-home")
        self.assertEqual(env["HOME"], "/owned/empty-home")
        self.assertNotIn("HTTP_PROXY", env)
        self.assertNotIn("UV_INDEX", env)
        self.assertEqual(env["UV_DEFAULT_INDEX"], "https://pypi.org/simple")

    def test_public_artifact_authority_and_path_refusals_have_healthy_companion(self):
        base = {
            "url": "https://files.pythonhosted.org/pkg/safe.whl",
            "size": 4,
            "hashes": {"sha256": hashlib.sha256(b"data").hexdigest()},
        }
        self.assertEqual(producer.public_artifact(base)[0], "safe.whl")
        for url in (
            "http://files.pythonhosted.org/pkg/safe.whl",
            "https://not-public.invalid/safe.whl",
            "https://u:p@files.pythonhosted.org/safe.whl",
            "https://files.pythonhosted.org/pkg/%2fescape.whl",
            "https://files.pythonhosted.org/safe.whl?credential=unused",
            "https://files.pythonhosted.org:444/safe.whl",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                producer.public_artifact({**base, "url": url})
        with self.assertRaisesRegex(ValueError, "redirect"):
            producer.NoRedirect().redirect_request(None, None, 302, "", {}, base["url"])

    def test_actual_downloader_binds_full_bytes_size_hash_and_ignores_proxy(self):
        item = {
            "url": "https://files.pythonhosted.org/pkg/safe.whl",
            "size": 4,
            "hashes": {"sha256": hashlib.sha256(b"data").hexdigest()},
        }
        wheelhouse = self.root / "wheels"
        wheelhouse.mkdir()

        class Response(io.BytesIO):
            status = 200

            def geturl(self):
                return item["url"]

        class Opener:
            def open(self, url, timeout):
                return Response(payload)

        for payload in (b"wrong", b"bad", b"data"):
            with (
                self.subTest(length=len(payload)),
                patch.object(
                    producer.urllib.request, "build_opener", return_value=Opener()
                ) as opener,
            ):
                if payload == b"data":
                    result = producer.download(item, wheelhouse)
                    self.assertEqual(result.read_bytes(), payload)
                    self.assertEqual(result.stat().st_mode & 0o777, 0o644)
                else:
                    with self.assertRaises(ValueError):
                        producer.download(item, wheelhouse)
                    self.assertEqual(list(wheelhouse.iterdir()), [])
                self.assertEqual(opener.call_args.args[0].proxies, {})


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--command-owner":
        command_owner(Path(sys.argv[2]))
    elif len(sys.argv) > 1 and sys.argv[1] == "--cancellation-driver":
        print(json.dumps(cancellation_driver(Path(sys.argv[2]), int(sys.argv[3]))))
    else:
        unittest.main()
