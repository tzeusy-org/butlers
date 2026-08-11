"""Contracts for runtime CLI tools shipped in the base container image."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
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


def test_base_image_installs_uv_git_and_gh_for_qa_runtime() -> None:
    text = _dockerfile_base_text()
    assert "git" in text
    assert "python -m pip install --no-cache-dir uv" in text
    assert "uv --version" in text
    assert "gh" in text


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


def test_codex_cli_pin_supports_gpt_5_6_luna() -> None:
    text = _dockerfile_base_text()
    match = re.search(r"@openai/codex@(\d+)\.(\d+)\.(\d+)\s+\\", text)

    assert match is not None
    assert tuple(map(int, match.groups())) >= (0, 144, 1), (
        "gpt-5.6-luna requires Codex CLI 0.144.1 or newer"
    )


def test_base_image_ships_the_pinned_dashboard_cli_sandbox_toolchain() -> None:
    """REQ-core-credentials-002: the image, not the host, owns the sandbox contract."""
    text = _dockerfile_base_text()

    assert re.search(r"\bbubblewrap=0\.11\.0-2\+deb13u1\b", text)
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
