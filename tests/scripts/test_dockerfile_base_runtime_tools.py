"""Contracts for runtime CLI tools shipped in the base container image."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _dockerfile_base_text() -> str:
    return Path("Dockerfile.base").read_text(encoding="utf-8")


def _compose_script_text() -> str:
    return Path("scripts/compose.sh").read_text(encoding="utf-8")


def _non_stage_copy_sources() -> tuple[str, ...]:
    """Return every Dockerfile.base COPY source not supplied by another stage."""
    sources: list[str] = []
    for raw_line in _dockerfile_base_text().splitlines():
        line = raw_line.strip()
        if not line.startswith("COPY ") or "--from=" in line:
            continue
        tokens = shlex.split(line)
        assert tokens[0] == "COPY"
        positional = [token for token in tokens[1:] if not token.startswith("--")]
        assert len(positional) >= 2
        sources.extend(positional[:-1])
    return tuple(sources)


def _base_input_fingerprint(inputs: tuple[str, ...], *, cwd: Path, env: dict[str, str]) -> str:
    """Run the exact no-Docker helper through a deliberately minimal environment."""
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; shift; butlers_base_image_input_fingerprint "$@"',
            "base-image-input-fingerprint",
            str(Path.cwd() / "scripts/base-image-input-fingerprint.sh"),
            *inputs,
        ],
        check=True,
        capture_output=True,
        cwd=cwd,
        env=env,
        text=True,
    )
    return result.stdout.strip()


def test_base_image_installs_uv_git_and_gh_for_qa_runtime(tmp_path: Path, monkeypatch) -> None:
    text = _dockerfile_base_text()
    assert "git" in text
    assert "python -m pip install --no-cache-dir uv" in text
    assert "uv --version" in text
    assert "gh" in text

    # REQ-testing-048: project/lock packaging choice; these structural and
    # installed witnesses do not claim either application image was built.
    project = tomllib.loads(Path("pyproject.toml").read_text())
    assert project["tool"]["uv"].get("sources", {}).get("torch") == {"index": "pytorch-cpu"}
    index = next(
        index for index in project["tool"]["uv"]["index"] if index["name"] == "pytorch-cpu"
    )
    assert index["explicit"] is True
    assert index["url"] == "https://download.pytorch.org/whl/cpu"
    packages = tomllib.loads(Path("uv.lock").read_text())["package"]
    assert not any(package["name"].startswith("nvidia-") for package in packages)
    assert not any(package["name"] in {"pgvector", "qrcode"} for package in packages)
    for name in ("Dockerfile", "Dockerfile.meeting-prep-route-a"):
        app = Path(name).read_text()
        assert "--frozen" in app and "--extra whatsapp" not in app
        assert "UV_TORCH_BACKEND" not in app
        assert "COPY --from=go-builder /out/whatsapp-bridge" in app
    assert "github.com/skip2/go-qrcode" in Path("whatsapp-bridge/cmd/bridge/main.go").read_text()
    import torch

    assert torch.version.cuda is None
    assert not torch.cuda.is_available()
    assert int(torch.tensor([2, 3], device="cpu").sum()) == 5

    # Actual local archive conformance, not a mocked application-image build.
    import hashlib
    import importlib.util
    import io
    import json
    import tarfile

    spec = importlib.util.spec_from_file_location(
        "image_diagnostic", Path("scripts/ci_image_size_diagnostic.py")
    )
    diagnostic = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(diagnostic)
    layer_archive = io.BytesIO()
    with tarfile.open(fileobj=layer_archive, mode="w") as output:
        member = tarfile.TarInfo("probe")
        member.size = len(b"fixture")
        output.addfile(member, io.BytesIO(b"fixture"))
    layer = layer_archive.getvalue()
    labels = {"org.butlers.route-a.cache-kind": "uv-cache"}
    config = json.dumps(
        {
            "config": {"Labels": labels},
            "rootfs": {"diff_ids": ["sha256:" + hashlib.sha256(layer).hexdigest()]},
        }
    ).encode()
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as output:
        for name, body in (
            ("layer/layer.tar", layer),
            ("config.json", config),
            (
                "manifest.json",
                json.dumps([{"Config": "config.json", "Layers": ["layer/layer.tar"]}]).encode(),
            ),
        ):
            member = tarfile.TarInfo(name)
            member.size = len(body)
            output.addfile(member, io.BytesIO(body))
    encoded = archive.getvalue()
    uri = diagnostic.write_oci_layout(io.BytesIO(encoded), tmp_path / "positive", labels)
    assert "@sha256:" in uri
    index = json.loads((tmp_path / "positive/index.json").read_text())
    digest = index["manifests"][0]["digest"].split(":")[1]
    sealed = (tmp_path / "positive/blobs/sha256" / digest).read_bytes()
    assert hashlib.sha256(sealed).hexdigest() == digest
    assert json.loads(sealed)["config"]["digest"] == "sha256:" + hashlib.sha256(config).hexdigest()
    with pytest.raises(diagnostic.DiagnosticRefusal, match="label_mismatch"):
        diagnostic.write_oci_layout(
            io.BytesIO(encoded), tmp_path / "wrong-label", {next(iter(labels)): "go-modules"}
        )
    with pytest.raises(diagnostic.DiagnosticRefusal, match="content_mismatch"):
        diagnostic.write_oci_layout(
            io.BytesIO(encoded.replace(b"fixture", b"mutated", 1)), tmp_path / "wrong-layer", labels
        )
    # The diagnostic refuses local Docker execution before its first build.
    with pytest.raises(diagnostic.DiagnosticRefusal, match="hosted_exact_source"):
        diagnostic.run_diagnostic(Path.cwd(), Path.cwd(), tmp_path / "no-local-docker", "not-a-sha")
    # The complete source-owned recipe fixture has the same pinned e7 bytes.
    # Ordinary unit checkouts need no history/network. This software comparator
    # is not a historical input, Docker/compiler or trained-model witness.
    original = Path(
        "tests/fixtures/route_a_9ff_offline_recipes/Dockerfile.meeting-prep-route-a"
    ).read_bytes()
    adapted = diagnostic.baseline_compatibility_recipe(original)
    assert hashlib.sha256(original).hexdigest() == diagnostic.BASE_ROUTE_RECIPE_SHA256
    prefix = (
        b"ARG ROUTE_A_GO_DEPS_IMAGE\nARG ROUTE_A_UV_CACHE_IMAGE\n\n"
        b"FROM ${ROUTE_A_GO_DEPS_IMAGE} AS route-a-go-deps\n"
        b"FROM ${ROUTE_A_UV_CACHE_IMAGE} AS route-a-uv-cache\n\n"
    )
    restored = adapted.replace(prefix, b"", 1)
    restored = restored.replace(
        b"COPY --from=route-a-go-deps ", b"COPY --from=${ROUTE_A_GO_DEPS_IMAGE} "
    )
    restored = restored.replace(
        b"COPY --from=route-a-uv-cache ", b"COPY --from=${ROUTE_A_UV_CACHE_IMAGE} "
    )
    assert restored == original
    assert b"COPY --from=${" not in adapted
    assert b"uv sync --offline --frozen --no-dev --extra whatsapp" in adapted
    assert b"ENV UV_TORCH_BACKEND=cpu" in adapted
    with pytest.raises(diagnostic.DiagnosticRefusal, match="baseline_recipe_mismatch"):
        diagnostic.baseline_compatibility_recipe(original + b"\n")

    # A self-contained miniature Git tree exercises the exact production byte,
    # mode and extra-input checks without requiring history in a shallow shard.
    # It is explicitly not the historical1690-input/image comparison: that
    # remains in the unchanged hosted diagnostic's actual pinned e7 checkout.
    before = tmp_path / "immutable-before"
    before.mkdir()
    for relative in (
        "Dockerfile.base",
        "scripts/runtime_cli_sandbox_init.c",
        "scripts/generate_runtime_cli_sandbox_manifest.py",
        "whatsapp-bridge/go.mod",
        "whatsapp-bridge/go.sum",
    ):
        destination = before / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(Path(relative).read_bytes())
    (before / "Dockerfile.meeting-prep-route-a").write_bytes(original)
    (before / "pyproject.toml").write_text("# miniature software fixture project\n")
    lock = before / "uv.lock"
    lock.write_text("# miniature software fixture lock\n")
    (before / "src").mkdir()
    (before / "src/payload.py").write_text("# miniature software fixture source\n")
    link = before / "src/alias.py"
    link.symlink_to("payload.py")
    monkeypatch.setenv("GIT_DIR", str(before / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(before))
    monkeypatch.setenv("GIT_INDEX_FILE", str(before / ".git/index"))
    subprocess.run(["git", "init", "-q", str(before)], check=True)
    subprocess.run(["git", "add", "--all"], check=True)
    tree = subprocess.check_output(["git", "write-tree"], text=True).strip()
    # The module instance is private to this node. Only this software fixture's
    # source selector changes; production BASE_SOURCE and CLI remain fixed e7.
    monkeypatch.setattr(diagnostic, "BASE_SOURCE", tree)
    inputs = diagnostic.baseline_build_inputs(before)
    assert inputs["source"] == diagnostic.BASE_SOURCE
    assert inputs["input_files"] > 0 and inputs["all_original_git_blob_bytes_equal"] is True
    assert inputs["literal_recipe_sha256"] == diagnostic.BASE_ROUTE_RECIPE_SHA256
    for shared_base_input in (
        "Dockerfile.base",
        "scripts/runtime_cli_sandbox_init.c",
        "scripts/generate_runtime_cli_sandbox_manifest.py",
    ):
        assert (Path.cwd() / shared_base_input).read_bytes() == (
            before / shared_base_input
        ).read_bytes()
    sys_path = str(Path.cwd() / "scripts")
    monkeypatch.syspath_prepend(sys_path)
    from run_meeting_prep_route_a_evidence import SafetyError, validate_sealed_build_inputs

    # The current launcher remains strict: it refuses the original variable COPY.
    with pytest.raises(SafetyError):
        validate_sealed_build_inputs(before)
    original_lock = lock.read_bytes()
    lock.write_bytes(lock.read_bytes() + b"\n")
    with pytest.raises(diagnostic.DiagnosticRefusal, match="baseline_input_mismatch"):
        diagnostic.baseline_build_inputs(before)
    lock.write_bytes(original_lock)
    mode_source = before / "scripts/runtime_cli_sandbox_init.c"
    original_mode = mode_source.stat().st_mode
    mode_source.chmod(0o755)
    with pytest.raises(diagnostic.DiagnosticRefusal, match="baseline_input_mode_mismatch"):
        diagnostic.baseline_build_inputs(before)
    mode_source.chmod(original_mode)
    link.unlink()
    link.symlink_to("different.py")
    with pytest.raises(diagnostic.DiagnosticRefusal, match="baseline_input_mismatch"):
        diagnostic.baseline_build_inputs(before)
    link.unlink()
    link.symlink_to("payload.py")
    (before / "src/extra-untracked.py").write_text("# not an original input\n")
    with pytest.raises(diagnostic.DiagnosticRefusal, match="baseline_inputs_untracked"):
        diagnostic.baseline_build_inputs(before)


def test_compose_base_freshness_uses_pinned_dockerfile_not_live_npm_latest() -> None:
    dockerfile_text = _dockerfile_base_text()
    compose_text = _compose_script_text()
    runtime_cli_packages = [
        "@anthropic-ai/claude-code",
        "@google/gemini-cli",
        "@openai/codex",
        "opencode-ai",
    ]

    for package in runtime_cli_packages:
        assert re.search(rf"{re.escape(package)}@\d+\.\d+\.\d+", dockerfile_text)

    assert "registry.npmjs.org/${pkg}/latest" not in compose_text
    assert "CLI_PKGS=" not in compose_text


def test_codex_cli_pin_supports_gpt_6_sol_and_luna() -> None:
    text = _dockerfile_base_text()
    match = re.search(r"@openai/codex@(\d+)\.(\d+)\.(\d+)\s+\\", text)

    assert match is not None
    assert tuple(map(int, match.groups())) >= (0, 159, 2), (
        "gpt-6-sol and gpt-6-luna require Codex CLI 0.159.2 or newer"
    )


def test_base_image_ships_the_pinned_dashboard_cli_sandbox_toolchain() -> None:
    """REQ-core-credentials-002: the image, not the host, owns the sandbox contract."""
    text = _dockerfile_base_text()

    assert re.search(r"\bbubblewrap=0\.12\.0-1~deb13u1\b", text)
    assert "dpkg-query" in text
    assert "bubblewrap" in text
    assert "runtime_cli_sandbox_init.c" in text
    assert "/usr/local/libexec/butlers/runtime-cli-sandbox-init" in text
    assert "61000-61999" in text


def test_sandbox_init_builder_creates_its_declared_output_directory() -> None:
    """The exact-image toolchain cannot rely on an implicit compiler output path."""
    text = _dockerfile_base_text()

    assert text.index("RUN mkdir -p /out") < text.index(
        "gcc -O2 -Wall -Wextra -Werror -o /out/runtime-cli-sandbox-init"
    )


def test_base_image_generates_the_exact_runtime_input_manifest() -> None:
    """REQ-core-credentials-002: production resolver input is image-owned and immutable."""
    text = _dockerfile_base_text()

    assert "scripts/generate_runtime_cli_sandbox_manifest.py" in text
    assert "runtime-cli-sandbox-inputs.json" in text
    assert "--output /usr/local/share/butlers/runtime-cli-sandbox-inputs.json" in text
    assert "chmod 0444 /usr/local/share/butlers/runtime-cli-sandbox-inputs.json" in text
    shim_copy = (
        "COPY --from=runtime-cli-sandbox-init-builder /out/runtime-cli-sandbox-init "
        "/usr/local/libexec/butlers/runtime-cli-sandbox-init"
    )
    generator_copy = (
        "COPY scripts/generate_runtime_cli_sandbox_manifest.py "
        "/tmp/generate_runtime_cli_sandbox_manifest.py"
    )
    assert text.count(shim_copy) == 1
    assert (
        text.index(shim_copy)
        < text.index("chmod 0755 /usr/local/libexec/butlers/runtime-cli-sandbox-init")
        < text.index(generator_copy)
    )


def test_base_image_freshness_fingerprints_each_local_copy_input_without_dotenv(
    tmp_path: Path,
) -> None:
    """REQ-core-credentials-002: copied sandbox helpers cannot inherit a stale base image."""
    docker_sources = _non_stage_copy_sources()
    expected_inputs = (
        "Dockerfile.base",
        "scripts/runtime_cli_sandbox_init.c",
        "scripts/generate_runtime_cli_sandbox_manifest.py",
    )
    assert ("Dockerfile.base", *docker_sources) == expected_inputs

    for relative_path in expected_inputs:
        source = Path(relative_path)
        destination = tmp_path / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())

    fingerprint_env = {"PATH": os.defpath, "UNRELATED_DEPLOYMENT_VALUE": "first"}
    first = _base_input_fingerprint(expected_inputs, cwd=tmp_path, env=fingerprint_env)
    (tmp_path / ".env.dev").write_text("POSTGRES_PASSWORD=first-synthetic-value\n")
    second = _base_input_fingerprint(
        expected_inputs,
        cwd=tmp_path,
        env={"PATH": os.defpath, "UNRELATED_DEPLOYMENT_VALUE": "second"},
    )
    assert second == first

    for relative_path in expected_inputs:
        target = tmp_path / relative_path
        original = target.read_bytes()
        target.write_bytes(original + b"\n# input mutation\n")
        assert (
            _base_input_fingerprint(expected_inputs, cwd=tmp_path, env=fingerprint_env) != first
        ), relative_path
        target.write_bytes(original)


def test_compose_base_freshness_uses_the_input_receipt_and_rebuilds_legacy_images() -> None:
    """The canonical launcher reads and writes the separate path-bound input label."""
    text = _compose_script_text()

    inputs_match = re.search(
        r"BASE_IMAGE_BUILD_INPUTS=\(\n(?P<inputs>(?:\s+[^\n]+\n)+)\)",
        text,
    )
    assert inputs_match is not None
    configured_inputs = tuple(
        line.strip() for line in inputs_match.group("inputs").splitlines() if line.strip()
    )
    assert configured_inputs == ("Dockerfile.base", *_non_stage_copy_sources())
    assert 'source "${SCRIPT_DIR}/base-image-input-fingerprint.sh"' in text
    assert '"${BASE_IMAGE_BUILD_INPUTS[@]}"' in text
    assert '"butlers.base.dockerfile_sha"' in text
    assert '"butlers.base.input_sha"' in text
    assert '--label "butlers.base.dockerfile_sha=${BASE_DOCKERFILE_SHA}"' in text
    assert '--label "butlers.base.input_sha=${BASE_INPUT_SHA}"' in text
    assert '[ -z "$BASE_IMAGE_DOCKERFILE_SHA" ] || [ -z "$BASE_IMAGE_INPUT_SHA" ]' in text
    assert '[ "$BASE_IMAGE_INPUT_SHA" != "$BASE_INPUT_SHA" ]' in text
