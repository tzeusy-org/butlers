"""Build-only installed image export contracts; no runtime execution."""

import hashlib
import importlib.util
import json
import os
import struct
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

pytestmark = pytest.mark.unit


def test_nightly_exports_installed_files_before_userns_policy() -> None:
    job = yaml.safe_load(Path(".github/workflows/nightly.yml").read_text())["jobs"][
        "exact-image-sandbox"
    ]
    names = [step.get("name") for step in job["steps"]]
    assert "Export installed runtime image files" in names
    assert names.index("Build butlers-app exact image") < names.index(
        "Export installed runtime image files"
    )
    assert names.index("Export installed runtime image files") < names.index(
        "Allow unprivileged user namespaces for Bubblewrap (runner host setting)"
    )


def module():
    spec = importlib.util.spec_from_file_location(
        "installed_image_export", "scripts/export_runtime_image_closure.py"
    )
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def elf_bytes():
    raw = bytearray(512)
    raw[:16] = b"\x7fELF\x02\x01\x01" + b"\0" * 9
    struct.pack_into("<HHIQQQIHHHHHH", raw, 16, 3, 62, 1, 0, 64, 0, 0, 64, 56, 3, 0, 0, 0)
    interpreter = b"/lib64/ld-linux-x86-64.so.2\0"
    struct.pack_into("<IIQQQQQQ", raw, 64, 1, 5, 0, 0, 0, 512, 512, 4096)
    struct.pack_into("<IIQQQQQQ", raw, 120, 3, 4, 240, 0, 0, len(interpreter), len(interpreter), 1)
    struct.pack_into("<IIQQQQQQ", raw, 176, 2, 4, 288, 0, 0, 64, 64, 8)
    raw[240 : 240 + len(interpreter)] = interpreter
    for index, item in enumerate(((1, 1), (5, 400), (10, 11), (0, 0))):
        struct.pack_into("<qQ", raw, 288 + 16 * index, *item)
    raw[400:411] = b"\0libc.so.6\0"
    return bytes(raw)


@pytest.fixture
def image(tmp_path):
    helper = module()
    root = tmp_path / "image"
    root.mkdir()
    package = helper.PACKAGES[0]
    payload = package + "/bin/opencode.exe"
    library = "/usr/lib/x86_64-linux-gnu/libc.so.6"
    loader = "/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"
    for name, raw in {
        payload: elf_bytes(),
        helper.SHIM: elf_bytes(),
        library: elf_bytes(),
        loader: elf_bytes(),
        helper.CA: b"public synthetic CA bundle\n",
        helper.VERSIONS: b"v22.1.0\n+-- @openai/codex@0.159.2\n`-- opencode-ai@1.17.7\n",
        package + "/package.json": json.dumps(
            {"name": "opencode-ai", "version": "1.17.7", "bin": {"opencode": "./bin/opencode.exe"}}
        ).encode(),
    }.items():
        path = root / name.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        path.chmod(0o755 if raw.startswith(b"\x7fELF") else 0o444)
    alias = root / "lib64"
    alias.mkdir()
    (alias / "ld-linux-x86-64.so.2").symlink_to("../usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2")
    os.link(root / payload.lstrip("/"), root / "native-hardlink")

    def binding(source, destination=None):
        return {"source": source, "destination": destination or source}

    manifest = {
        "version": 3,
        "shim": {
            "name": "runtime-cli-sandbox-init",
            "executable": helper.SHIM,
            "readonly_inputs": [
                binding(helper.SHIM),
                binding(loader, "/lib64/ld-linux-x86-64.so.2"),
                binding(library),
            ],
        },
        "providers": {
            "opencode-openai": {
                "binary": "opencode",
                "executable": payload,
                "readonly_inputs": [
                    binding(payload),
                    binding(package),
                    binding(helper.CA),
                    binding("/etc/resolv.conf"),
                ],
            }
        },
    }
    manifest_path = root / helper.V3.lstrip("/")
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(manifest))
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    identity = {
        "version": 1,
        "checkout_sha": "a" * 40,
        "checkout_tree": "b" * 40,
        "workflow_sha": "c" * 40,
        "run_id": 17,
        "run_attempt": 1,
        "job_id": 23,
        "job_name": "exact-image-sandbox",
        "repository": "test/repository",
        "image_id": "sha256:" + "d" * 64,
        "base_input_sha": "e" * 64,
        "exporter_sha256": "f" * 64,
        "generator_sha256": "0" * 64,
        "versions": dict(helper.PINS),
    }
    return SimpleNamespace(
        helper=helper,
        root=root,
        output=output,
        identity=identity,
        manifest=manifest,
        manifest_path=manifest_path,
        payload=payload,
        package=package,
        library=library,
    )


def run(image):
    return image.helper.export_files(image.root, image.output, image.identity)


def change_manifest(image):
    image.manifest_path.write_text(json.dumps(image.manifest))


def test_real_bytes_metadata_and_elf_facts_are_preserved_without_runtime(image):
    report = run(image)
    image.helper.verify_export(image.output, image.identity)
    records = {entry["source"]: entry for entry in report["files"]}
    payload = records[image.payload]
    assert payload["original"]["nlink"] == 2
    assert "original_hardlinked" in payload["findings"]
    assert payload["elf"] == {
        "bits": 64,
        "endian": "little",
        "machine": 62,
        "type": 3,
        "interpreter": "/lib64/ld-linux-x86-64.so.2",
        "needed": ["libc.so.6"],
    }
    for entry in report["files"]:
        original = image.root / entry["source"].lstrip("/")
        copied = image.output / entry["copy"]
        assert copied.read_bytes() == original.read_bytes()
        assert hashlib.sha256(copied.read_bytes()).hexdigest() == entry["sha256"]
        assert copied.stat().st_nlink == 1
        assert copied.stat().st_mode & 0o777 == 0o600
    loader = records["/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"]
    assert loader["aliases"][0]["target"] == "../usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"
    assert {entry["reason"] for entry in report["omitted"]} == {
        "resolver_contents_excluded",
        "directory_not_exported",
    }
    assert not any(entry["source"] == "/etc/resolv.conf" for entry in report["files"])
    assert json.loads((image.output / "manifest.json").read_bytes()) == report
    assert report["total_bytes"] == sum(entry["original"]["size"] for entry in report["files"])


@pytest.mark.parametrize(
    "bad",
    [
        "/etc/passwd",
        "/home/runner/auth.json",
        "/usr/lib/../secrets.so",
        "/usr/lib//libtest.so",
        "/usr/lib/private.txt",
        "relative.so",
    ],
)
def test_binding_bad_paths_refuse_before_any_export(image, bad):
    image.manifest["shim"]["readonly_inputs"].append({"source": bad, "destination": bad})
    change_manifest(image)
    with pytest.raises(image.helper.Refused):
        run(image)
    assert not (image.output / "manifest.json").exists()


@pytest.mark.parametrize("kind", ["symlink", "fifo", "directory", "parent_symlink"])
def test_canonical_source_metadata_refuses(image, kind):
    target = image.root / image.library.lstrip("/")
    target.unlink()
    if kind == "symlink":
        target.symlink_to(os.path.relpath(image.root / image.payload.lstrip("/"), target.parent))
    elif kind == "fifo":
        os.mkfifo(target)
    elif kind == "directory":
        target.mkdir()
    else:
        parent = target.parent
        moved = parent.with_name("moved")
        parent.rename(moved)
        parent.symlink_to(moved, target_is_directory=True)
    with pytest.raises((image.helper.Refused, OSError)):
        run(image)
    assert not (image.output / "manifest.json").exists()


@pytest.mark.parametrize(
    "which", ["version", "package", "payload", "versions", "duplicate", "identity"]
)
def test_installed_identity_or_manifest_drift_refuses(image, which):
    if which == "version":
        image.manifest["version"] = 2
        change_manifest(image)
    elif which == "package":
        (image.root / (image.package + "/package.json").lstrip("/")).chmod(0o644)
        (image.root / (image.package + "/package.json").lstrip("/")).write_text(
            '{"name":"opencode-ai","version":"wrong"}'
        )
    elif which == "payload":
        image.manifest["providers"]["opencode-openai"]["executable"] = "/usr/bin/opencode"
        change_manifest(image)
    elif which == "versions":
        (image.root / image.helper.VERSIONS.lstrip("/")).chmod(0o644)
        (image.root / image.helper.VERSIONS.lstrip("/")).write_text(
            "opencode-ai@1.17.6\n@openai/codex@0.159.2\n"
        )
    elif which == "duplicate":
        image.manifest_path.write_text('{"version":3,"version":3}')
    else:
        image.identity["job_id"] = True
    with pytest.raises(image.helper.Refused):
        run(image)
    assert not (image.output / "manifest.json").exists()


@pytest.mark.parametrize("cap", ["MAX_FILES", "MAX_FILE", "MAX_TOTAL", "SECONDS"])
def test_live_export_caps_refuse_without_success_manifest(image, monkeypatch, cap):
    monkeypatch.setattr(image.helper, cap, 1 if cap != "SECONDS" else -1)
    with pytest.raises(image.helper.Refused):
        run(image)
    assert not (image.output / "manifest.json").exists()


def test_source_changes_during_copy_refuse(image, monkeypatch):
    original_read = image.helper.os.read
    changed = False

    def mutate(fd, count):
        nonlocal changed
        raw = original_read(fd, count)
        if (
            (os.fstat(fd).st_dev, os.fstat(fd).st_ino)
            == (
                (image.root / image.payload.lstrip("/")).stat().st_dev,
                (image.root / image.payload.lstrip("/")).stat().st_ino,
            )
            and raw
            and not changed
        ):
            changed = True
            destination = image.root / image.payload.lstrip("/")
            with destination.open("ab") as stream:
                stream.write(b"drift")
        return raw

    monkeypatch.setattr(image.helper.os, "read", mutate)
    with pytest.raises(image.helper.Refused, match="source_drift"):
        run(image)
    assert changed and not (image.output / "manifest.json").exists()


def test_nonempty_or_unsafe_output_refuses(image):
    (image.output / "old").touch()
    with pytest.raises(image.helper.Refused, match="output_not_empty"):
        run(image)
    (image.output / "old").unlink()
    image.output.chmod(0o755)
    with pytest.raises(image.helper.Refused, match="output_owner"):
        run(image)


def test_selected_native_bytes_must_actually_be_elf(image):
    (image.root / image.payload.lstrip("/")).write_bytes(b"#!/bin/node\n")
    with pytest.raises(image.helper.Refused, match="executable_elf"):
        run(image)


def test_malformed_elf_is_not_guessed_static(image):
    (image.root / image.payload.lstrip("/")).write_bytes(b"\x7fELF\x02\x01")
    with pytest.raises(image.helper.Refused, match="elf_header"):
        run(image)


@pytest.mark.parametrize(
    "tamper",
    ["extra_file", "bytes", "extra_key", "nested_key", "job", "copy_path", "count", "nonfinite"],
)
def test_host_verifier_refuses_modified_or_extra_artifacts(image, tamper):
    report = run(image)
    path = image.output / "manifest.json"
    if tamper == "extra_file":
        (image.output / "files/extra.bin").write_bytes(b"not allowed")
    elif tamper == "bytes":
        (image.output / report["files"][0]["copy"]).write_bytes(b"changed")
    elif tamper == "extra_key":
        report["unapproved"] = "data"
    elif tamper == "nested_key":
        report["files"][0]["original"]["unapproved"] = 1
    elif tamper == "job":
        report["identity"]["job_id"] += 1
    elif tamper == "copy_path":
        report["files"][0]["copy"] = "../outside"
    elif tamper == "count":
        report["total_bytes"] += 1
    else:
        path.write_text('{"unapproved":NaN}')
    if tamper != "nonfinite":
        path.write_text(json.dumps(report))
    # Separate immutable intent; mutating the report identity cannot mutate it.
    intended = dict(image.identity, job_id=23)
    with pytest.raises(image.helper.Refused):
        image.helper.verify_export(image.output, intended)


def test_binding_cannot_relabel_different_bytes_as_loader(image):
    target = image.root / "lib64/ld-linux-x86-64.so.2"
    target.unlink()
    target.symlink_to("../usr/lib/x86_64-linux-gnu/libc.so.6")
    with pytest.raises(image.helper.Refused, match="binding_identity"):
        run(image)


def test_host_identity_pins_provider_job_checkout_and_exact_build_fingerprint(image, monkeypatch):
    helper = image.helper
    actual_checkout = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    actual_tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], text=True).strip()
    original_command = helper._command
    values = {
        "GITHUB_SHA": actual_checkout,
        "GITHUB_WORKFLOW_SHA": "c" * 40,
        "GITHUB_RUN_ID": "17",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_JOB": "exact-image-sandbox",
        "GITHUB_REPOSITORY": "test/repository",
        "GH_TOKEN": "synthetic-test-only",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    fingerprint = subprocess.run(
        [
            "bash",
            "-c",
            (
                "source scripts/base-image-input-fingerprint.sh; "
                "butlers_base_image_input_fingerprint Dockerfile.base "
                "scripts/runtime_clis/install_system_artifacts.py "
                "deploy/runtime-clis/debian-system-inputs.json "
                "scripts/runtime_cli_sandbox_init.c "
                "scripts/runtime_clis/runtime_artifacts.py "
                "deploy/runtime-clis/runtime-inputs.json "
                "scripts/generate_runtime_cli_sandbox_manifest.py"
            ),
        ],
        capture_output=True,
        check=True,
        text=True,
        env={"PATH": os.defpath},
    ).stdout.strip()
    commands = []

    def command(args):
        commands.append(args)
        if args[0] == "git":
            return original_command(args)
        return "sha256:" + "d" * 64 + "\n" + fingerprint

    monkeypatch.setattr(helper, "_command", command)
    jobs = [
        {
            "id": 23,
            "name": "exact-image-sandbox",
            "head_sha": actual_checkout,
            "status": "in_progress",
        }
    ]

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, limit):
            return json.dumps({"jobs": jobs}).encode()[:limit]

    requests = []

    def response(request, timeout):
        requests.append(request.full_url)
        assert timeout == 10
        return Response()

    monkeypatch.setattr(helper.urllib.request, "urlopen", response)
    result = helper.prepare_identity()
    assert result["checkout_sha"] == actual_checkout
    assert result["base_input_sha"] == fingerprint
    assert result["job_id"] == 23 and result["checkout_tree"] == actual_tree
    assert result["exporter_sha256"] == hashlib.sha256(Path(helper.SCRIPT).read_bytes()).hexdigest()
    assert commands[-1][:3] == ["docker", "image", "inspect"]
    assert requests == [
        "https://api.github.com/repos/test/repository/actions/runs/17/attempts/1/jobs?per_page=100&page=1"
    ]
    jobs[0]["head_sha"] = "f" * 40
    with pytest.raises(helper.Refused, match="provider_job_binding"):
        helper.prepare_identity()
    jobs[0]["head_sha"] = actual_checkout
    jobs.append(dict(jobs[0]))
    with pytest.raises(helper.Refused, match="provider_job_binding"):
        helper.prepare_identity()


def test_workflow_export_is_fixed_bounded_and_uploads_only_verified_files():
    workflow = yaml.safe_load(Path(".github/workflows/nightly.yml").read_text())
    job = workflow["jobs"]["exact-image-sandbox"]
    assert job["permissions"] == {"contents": "read", "actions": "read"}
    assert workflow["permissions"] == {"contents": "read"}
    export = next(
        step for step in job["steps"] if step.get("name") == "Export installed runtime image files"
    )
    upload = next(
        step for step in job["steps"] if step.get("name") == "Upload installed runtime image files"
    )
    script = export["run"]
    for fixed in (
        "--pull never",
        "--network none --read-only --cap-drop ALL",
        "--security-opt no-new-privileges --pids-limit 16 --memory 512m --cpus 1",
        "--ulimit nofile=64:64",
        "--ulimit fsize=268435456:268435456",
        '--user "$(id -u):$(id -g)"',
        "--entrypoint /usr/local/bin/python3 --workdir /app",
        "120s docker start",
        'docker rm --force "${owned_id}"',
        "scripts.export_runtime_image_closure verify",
    ):
        assert fixed in script
    assert script.rindex("cleanup_export\n") < script.index(
        "scripts.export_runtime_image_closure verify"
    )
    assert "--privileged" not in script and "docker.sock" not in script and "--env" not in script
    assert export["timeout-minutes"] == 4
    assert "if" not in upload and upload["with"]["if-no-files-found"] == "error"
    paths = upload["with"]["path"].splitlines()
    assert (
        len(paths) == 2
        and paths[0].endswith("/payload/manifest.json")
        and paths[1].endswith("/payload/files/*.bin")
    )
    assert "opencode run" not in script and "codex " not in script


def test_existing_disallowed_file_is_never_copied(image):
    source = "/home/synthetic/unsupported.txt"
    path = image.root / source.lstrip("/")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"synthetic public refusal control")
    image.manifest["shim"]["readonly_inputs"].append({"source": source, "destination": source})
    change_manifest(image)
    with pytest.raises(image.helper.Refused, match="path_allowlist"):
        run(image)
    assert not (image.output / "manifest.json").exists()


def test_exact_aggregate_limit_still_exports_and_verifies(image, monkeypatch):
    total = sum(
        path.stat().st_size
        for path in image.root.rglob("*")
        if path.is_file() and not path.is_symlink() and path.name != "native-hardlink"
    )
    monkeypatch.setattr(image.helper, "MAX_TOTAL", total)
    report = run(image)
    assert report["total_bytes"] == total
    image.helper.verify_export(image.output, image.identity)


@pytest.mark.parametrize("removal_exit", [0, 1])
def test_owned_container_cleanup_propagates_removal_failure(tmp_path, removal_exit):
    job = yaml.safe_load(Path(".github/workflows/nightly.yml").read_text())["jobs"][
        "exact-image-sandbox"
    ]
    script = next(
        step["run"]
        for step in job["steps"]
        if step.get("name") == "Export installed runtime image files"
    )
    start = script.index("cleanup_export() {")
    cleanup = script[start : script.index("\n}\n", start) + 2]
    cid = "d" * 64
    (tmp_path / "container.cid").write_text(cid)
    program = "\n".join(
        [
            "set -euo pipefail",
            cleanup,
            'timeout() { printf "%s\\n" "$*" > "$EXPORT_WORK/observed"; return "$REMOVAL_EXIT"; }',
            "if cleanup_export; then exit 0; else exit 7; fi",
        ]
    )
    result = subprocess.run(
        ["bash", "-c", program],
        env={"PATH": os.defpath, "EXPORT_WORK": str(tmp_path), "REMOVAL_EXIT": str(removal_exit)},
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == (0 if removal_exit == 0 else 7)
    assert (tmp_path / "container.cid").exists() == bool(removal_exit)
    assert (
        tmp_path / "observed"
    ).read_text().strip() == f"--signal=TERM --kill-after=5s 20s docker rm --force {cid}"


@pytest.mark.parametrize("change", ["none", "checkout", "exporter", "generator"])
def test_real_main_checks_image_checkout_and_actual_source_bytes(
    image, monkeypatch, capsys, change
):
    # The only filesystem seam maps the fixed image paths into this real-file fixture.
    # No parser, identity verifier, exporter or artifact verifier is replaced.
    helper = image.helper
    for name, key in ((helper.SCRIPT, "exporter_sha256"), (helper.GENERATOR, "generator_sha256")):
        raw = Path(name).read_bytes()
        destination = image.root / "app" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        image.identity[key] = hashlib.sha256(raw).hexdigest()
    monkeypatch.setenv(
        "GIT_SHA", image.identity["checkout_sha"] if change != "checkout" else "e" * 40
    )
    if change in ("exporter", "generator"):
        name = helper.SCRIPT if change == "exporter" else helper.GENERATOR
        with (image.root / "app" / name).open("ab") as stream:
            stream.write(b"\n# source-drift-control\n")

    def image_path(*parts):
        if parts == ("/export",):
            return image.output
        if parts == ("/",):
            return image.root
        if parts and parts[0] == "/app":
            return image.root.joinpath("app", *parts[1:])
        return Path(*parts)

    monkeypatch.setattr(helper, "Path", image_path)
    try:
        result = helper.main(
            ["export", "--output", "/export", "--identity", json.dumps(image.identity)]
        )
    finally:
        helper.signal.alarm(0)
    captured = capsys.readouterr()
    if change == "none":
        assert result == 0 and captured.out == "installed-image-export: PASS\n"
        helper.verify_export(image.output, image.identity)
    else:
        assert result == 1 and captured.err == "installed-image-export: REFUSED\n"
        assert not list(image.output.iterdir())
    assert "source-drift-control" not in captured.out + captured.err


def test_unchanged_generator_produces_the_v3_document_that_is_exported(image, monkeypatch):
    spec = importlib.util.spec_from_file_location("unchanged_v3_generator", image.helper.GENERATOR)
    generator = importlib.util.module_from_spec(spec)
    import sys

    monkeypatch.setitem(sys.modules, spec.name, generator)
    spec.loader.exec_module(generator)
    original = image.manifest
    (image.root / image.package.lstrip("/")).parent.joinpath("@openai/codex").mkdir(parents=True)

    def package_directory(path):
        # Existing build IO seam: use real fixture directory metadata. No claim
        # that the unprivileged fixture has image-root UID ownership.
        assert (image.root / str(path).lstrip("/")).is_dir()
        return path

    monkeypatch.setattr(generator, "_safe_regular_or_directory", package_directory)

    def bindings(items):
        return tuple(
            generator.RuntimeInputBinding(Path(item["source"]), Path(item["destination"]))
            for item in items
        )

    generated = generator.build_manifest(
        npm_root=lambda: Path(image.package).parent,
        resolve_command=lambda binary: Path(
            image.payload
            if binary == "opencode"
            else "/usr/lib/node_modules/@openai/codex/bin/codex.js"
        ),
        runtime_closure=lambda executable, package: (
            bindings(original["providers"]["opencode-openai"]["readonly_inputs"])
            if package.name == "opencode-ai"
            else ()
        ),
        shim_path=Path(image.helper.SHIM),
        resolve_shim=lambda path: path,
        shim_runtime_closure=lambda executable: bindings(original["shim"]["readonly_inputs"]),
    )
    assert generated["version"] == 3
    assert set(generated["providers"]) == {"codex", "opencode-openai", "opencode-go"}
    generator.write_manifest(image.manifest_path, generated)
    built_bytes = image.manifest_path.read_bytes()
    report = run(image)
    record = next(item for item in report["files"] if item["source"] == image.helper.V3)
    assert (image.output / record["copy"]).read_bytes() == built_bytes
    assert json.loads(built_bytes) == generated
    image.helper.verify_export(image.output, image.identity)


def test_installed_npm_unicode_tree_format_and_dockerfile_pins_are_accepted(image):
    path = image.root / image.helper.VERSIONS.lstrip("/")
    path.chmod(0o644)
    path.write_text(
        "v22.21.1\n/usr/lib\n"
        "├── @anthropic-ai/claude-code@2.1.309\n├── @google/gemini-cli@0.66.0\n"
        "├── @openai/codex@0.159.2\n└── opencode-ai@1.17.7\n\n"
        "bubblewrap=0.12.0-1~deb13u1\n"
    )
    dockerfile = Path("Dockerfile.base").read_text()
    versions = json.loads(Path("deploy/runtime-clis/runtime-inputs.json").read_text())["versions"]
    assert versions["codex"] == image.helper.PINS["codex"]
    assert versions["opencode"] == image.helper.PINS["opencode"]
    assert "--mount=from=runtime-artifacts" in dockerfile
    assert "npm list -g --depth=0" in dockerfile
    report = run(image)
    record = next(item for item in report["files"] if item["source"] == image.helper.VERSIONS)
    assert (image.output / record["copy"]).read_bytes() == path.read_bytes()
    image.helper.verify_export(image.output, image.identity)
