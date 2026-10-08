# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml>=6"]
# ///
"""Hosted, nonpublishing proof of Route A's three offline build recipes.

OCI inputs are sealed from actual BuildKit output, with complete blob validation.
This is recipe evidence; it does not bypass or claim RepoDigest launcher admission.
Only closed failure categories, public source/input hashes and numeric exits leave
this process. Docker output is inspected in memory, never printed or persisted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

from run_meeting_prep_route_a_evidence import (
    dependency_cache_contracts,
    validate_ambient_environment,
    validate_sealed_build_inputs,
)

ROOT = Path(__file__).resolve().parents[1]
BASE_SOURCE = "9ff27ce876c15ce21271be6246372a49fe05d56e"
PROOF_SECONDS = 1800
RECIPES = {
    "application": "Dockerfile.meeting-prep-route-a",
    "frontend": "frontend/Dockerfile.meeting-prep-evidence",
    "browser": "frontend/Dockerfile.meeting-prep-browser",
}
DIAGNOSTIC_WINDOW_BYTES = 65536


class ProofRefusal(RuntimeError):
    """Fixed public failure category, never subprocess arguments or output."""


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def seal_oci(archive: Path, destination: Path, expected_labels: dict[str, str]) -> dict:
    """Validate native OCI output without inventing a RepoDigest or rewriting it."""
    destination.mkdir()
    with tarfile.open(archive) as contents:
        for member in contents:
            name = Path(member.name)
            if name.is_absolute() or ".." in name.parts or not (member.isfile() or member.isdir()):
                raise ProofRefusal("invalid_oci_archive")
            contents.extract(member, destination, filter="data")
    if json.loads((destination / "oci-layout").read_text()) != {"imageLayoutVersion": "1.0.0"}:
        raise ProofRefusal("invalid_oci_layout")
    index = json.loads((destination / "index.json").read_text())
    manifests = index.get("manifests", [])
    if len(manifests) != 1:
        raise ProofRefusal("ambiguous_oci_input")

    def read_blob(descriptor: dict) -> bytes:
        value = descriptor.get("digest", "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
            raise ProofRefusal("invalid_oci_digest")
        path = destination / "blobs/sha256" / value[7:]
        if path.is_symlink() or not path.is_file():
            raise ProofRefusal("missing_oci_blob")
        if path.stat().st_size != descriptor.get("size") or digest(path) != value[7:]:
            raise ProofRefusal("oci_blob_mismatch")
        return path.read_bytes() if descriptor.get("mediaType", "").endswith("+json") else b""

    manifest = json.loads(read_blob(manifests[0]))
    config = json.loads(read_blob(manifest["config"]))
    for layer in manifest["layers"]:
        read_blob(layer)
    labels = config.get("config", {}).get("Labels") or {}
    if any(labels.get(k) != v for k, v in expected_labels.items()):
        raise ProofRefusal("sealed_cache_label_mismatch")
    archive.unlink()
    return {
        "manifest_digest": manifests[0]["digest"],
        "config_digest": manifest["config"]["digest"],
        "expected_labels": expected_labels,
        "all_blob_sizes_and_digests_verified": True,
        "layer_count": len(manifest["layers"]),
    }


def _diagnostic_text(stdout: bytes, stderr: bytes) -> str:
    """Inspect bounded stream windows without returning any original bytes."""
    half = DIAGNOSTIC_WINDOW_BYTES // 2
    windows = [
        stream if len(stream) <= 2 * half else stream[:half] + b"\n" + stream[-half:]
        for stream in (stdout, stderr)
    ]
    return b"\n".join(windows).decode("utf-8", errors="replace").lower()


def closed_build_failure(stderr: bytes, stdout: bytes = b"") -> str:
    """Match only fixed builder classes; arbitrary bytes never enter a receipt."""
    text = _diagnostic_text(stdout, stderr)
    if any(p in text for p in ("invalid reference format", "invalid from flag value")):
        return "invalid_copy_or_image_reference"
    if any(p in text for p in ("base name", "blank", "requires either one or three arguments")):
        return "invalid_or_missing_stage_input"
    return "unclassified_builder_failure"


def closed_builder_diagnostic(stdout: bytes, stderr: bytes, recipe: Path | None = None) -> dict:
    """Export fixed observations and positions within the known public recipe.

    Phrase observations do not establish an underlying builder cause. Unknown
    failures stay unknown, and no context/registry phrase satisfies a COPY red.
    """
    text = _diagnostic_text(stdout, stderr)
    indicators = {
        "invalid_reference_format": "invalid reference format" in text,
        "invalid_from_flag": "invalid from flag value" in text,
        "stage_name_parse_failure": "failed to parse stage name" in text,
        "repository_requires_lowercase": bool(
            re.search(r"repository name[^\n]*must be lowercase", text)
        ),
        "blank_base_name": "base name" in text and "blank" in text,
        "stage_argument_arity": "requires either one or three arguments" in text,
        "dockerfile_parse_failure": "dockerfile parse error" in text,
        "oci_layout_mentioned": "oci-layout" in text or "oci layout" in text,
        "source_metadata_resolution_failure": "failed to resolve source metadata" in text,
        "dockerfile_read_failure": "failed to read dockerfile" in text,
        "blob_not_found": bool(
            re.search(r"(?:blob|digest)[^\n]*(?:not found|does not exist)", text)
        ),
        "builder_connection_failure": any(
            p in text
            for p in ("cannot connect to the docker daemon", "failed to dial", "connection refused")
        ),
        "permission_denied": "permission denied" in text,
    }
    result = {
        "schema": 1,
        "stdout_bytes": len(stdout),
        "stderr_bytes": len(stderr),
        "window_bytes_per_stream": DIAGNOSTIC_WINDOW_BYTES,
        "complete_streams_examined": len(stdout) <= DIAGNOSTIC_WINDOW_BYTES
        and len(stderr) <= DIAGNOSTIC_WINDOW_BYTES,
        "indicators": indicators,
        "recipe_line_numbers": [],
        "recipe_instructions": [],
    }
    if recipe is not None:
        lines = recipe.read_text().splitlines()
        names = "(?:dockerfile|" + re.escape(recipe.name.lower()) + ")"
        positions = sorted(
            {
                int(value)
                for value in re.findall(r"(?m)^\s*" + names + r":([0-9]+)(?::|\s*$)", text)
                if 1 <= int(value) <= len(lines)
            }
        )
        known = {"ARG", "FROM", "COPY", "RUN", "ENV", "WORKDIR", "ENTRYPOINT", "CMD", "LABEL"}
        result["recipe_sha256"] = digest(recipe)
        result["recipe_line_numbers"] = positions
        for position in positions:
            parts = lines[position - 1].split(maxsplit=1)
            word = parts[0] if parts else ""
            result["recipe_instructions"].append(word if word in known else "OTHER")
    return result


def run_proof(output: Path, source: str) -> int:
    if os.environ.get("GITHUB_ACTIONS") != "true" or not re.fullmatch(r"[0-9a-f]{40}", source):
        raise ProofRefusal("hosted_exact_source_required")
    if not output.is_absolute() or output.is_symlink() or output.exists():
        raise ProofRefusal("fresh_absolute_output_required")
    output.mkdir(parents=True)
    deadline = time.monotonic() + PROOF_SECONDS
    receipt: dict = {
        "schema": 1,
        "source": source,
        "historical_recipe_source": BASE_SOURCE,
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "status": "incomplete",
        "inputs": {},
        "controls": [],
        "images": {},
        "cleanup": "not_run",
        "launcher_RepoDigest_admission": "NOT RUN; actual local OCI recipe proof only",
        "full_launcher_runtime_browser_SQL_provider_model": "NOT RUN",
        "historical_37754481671_cause": "UNKNOWN",
        "gain_claimed": False,
    }
    receipt_file = output / "offline-build-receipt.json"
    stage = "source_validation"
    tags: list[str] = []
    builder = "route-a-proof-" + source[:12] + "-" + str(os.getpid())
    created = False

    def save() -> None:
        receipt["active_stage"] = stage
        receipt_file.write_text(json.dumps(receipt, indent=2) + "\n")

    def execute(
        args: list[str], *, cwd: Path = ROOT, bound: int = 900
    ) -> subprocess.CompletedProcess:
        remaining = int(deadline - time.monotonic())
        if remaining <= 0:
            raise ProofRefusal("proof_deadline")
        save()
        return subprocess.run(
            ["timeout", "--signal=TERM", "--kill-after=15s", str(min(bound, remaining)), *args],
            cwd=cwd,
            capture_output=True,
            check=False,
        )

    def required(args: list[str], **kwargs) -> bytes:
        done = execute(args, **kwargs)
        if done.returncode:
            receipt["failed_exit"] = done.returncode
            receipt["failure_kind"] = closed_build_failure(done.stderr, done.stdout)
            recipe = Path(args[args.index("-f") + 1]) if "buildx" in args and "-f" in args else None
            receipt["builder_diagnostic"] = closed_builder_diagnostic(
                done.stdout, done.stderr, recipe
            )
            raise ProofRefusal("required_build_step_failed")
        return done.stdout

    def build(recipe: Path, context: Path, args: list[str], *, output_args: list[str]) -> list[str]:
        return [
            "docker",
            "buildx",
            "build",
            "--builder",
            builder,
            "--provenance=false",
            # Plain diagnostics are captured in memory, never printed/retained.
            "--progress=plain",
            "-f",
            str(recipe),
            *args,
            *output_args,
            str(context),
        ]

    try:
        validate_ambient_environment(os.environ)
        if required(["git", "rev-parse", "HEAD"], bound=10).decode().strip() != source:
            raise ProofRefusal("source_mismatch")
        if required(["git", "status", "--porcelain", "--untracked-files=no"], bound=10).strip():
            raise ProofRefusal("dirty_source")
        validate_sealed_build_inputs(ROOT)
        if ROOT.is_symlink() or (ROOT / "frontend").is_symlink():
            raise ProofRefusal("symlinked_build_context")
        historical = ROOT / "tests/fixtures/route_a_9ff_offline_recipes"
        history = json.loads((historical / "source-manifest.json").read_text())
        if history["source"] != BASE_SOURCE or set(history["recipes"]) != set(RECIPES.values()):
            raise ProofRefusal("historical_recipe_identity_unavailable")
        for relative, expected in history["recipes"].items():
            if digest(historical / relative) != expected:
                raise ProofRefusal("historical_recipe_mutated")
        receipt["historical_recipe_sha256"] = history["recipes"]
        receipt["source_files"] = {
            path: digest(ROOT / path)
            for path in [
                *RECIPES.values(),
                "Dockerfile.base",
                "Dockerfile",
                "uv.lock",
                "frontend/package-lock.json",
                ".dockerignore",
                "frontend/.dockerignore",
            ]
        }
        receipt["builder_version_sha256"] = hashlib.sha256(
            required(["docker", "buildx", "version"], bound=10)
        ).hexdigest()
        required(
            ["docker", "buildx", "create", "--name", builder, "--driver", "docker-container"],
            bound=120,
        )
        created = True
        with tempfile.TemporaryDirectory(prefix="route-a-offline-proof-") as directory:
            work = Path(directory)
            sealed: dict[str, tuple[str, dict]] = {}

            def prepare(name: str, text: str, context: Path, labels: dict[str, str]) -> str:
                nonlocal stage
                stage = "prepare_" + name
                recipe = work / (name + ".Dockerfile")
                recipe.write_text(text)
                archive = work / (name + ".tar")
                label_args = [
                    v for k, value in labels.items() for v in ("--label", k + "=" + value)
                ]
                required(
                    build(
                        recipe,
                        context,
                        label_args,
                        output_args=["--output", "type=oci,dest=" + str(archive)],
                    )
                )
                layout = work / (name + "-oci")
                identity = seal_oci(archive, layout, labels)
                reference = "oci-layout://" + str(layout) + "@" + identity["manifest_digest"]
                sealed[name] = (reference, identity)
                receipt["inputs"][name] = identity
                save()
                return reference

            # No public image is needed for this actual parser/resolver control.
            control = work / "control"
            control.mkdir()
            (control / "proof-sentinel").write_text("Route A public synthetic build sentinel\n")
            control_input = prepare(
                "control", "FROM scratch\nCOPY proof-sentinel /proof-sentinel\n", control, {}
            )
            control_args = [
                "--network=none",
                "--pull=false",
                "--build-arg",
                "ROUTE_A_BASE_IMAGE=proof-input",
                "--build-arg",
                "ROUTE_A_GO_DEPS_IMAGE=proof-input",
                "--build-context",
                "proof-input=" + control_input,
            ]
            old = (
                "ARG ROUTE_A_BASE_IMAGE\nFROM ${ROUTE_A_BASE_IMAGE}\nARG ROUTE_A_GO_DEPS_IMAGE\n"
                "COPY --from=${ROUTE_A_GO_DEPS_IMAGE} /proof-sentinel /proof-sentinel\n"
            )
            valid = (
                "ARG ROUTE_A_BASE_IMAGE\nARG ROUTE_A_GO_DEPS_IMAGE\n"
                "FROM ${ROUTE_A_GO_DEPS_IMAGE} AS route-a-go-deps\nFROM ${ROUTE_A_BASE_IMAGE}\n"
                "COPY --from=route-a-go-deps /proof-sentinel /proof-sentinel\n"
            )
            cases = [
                ("old_variable_copy", old, control_args, False, "invalid_copy_or_image_reference"),
                ("valid_alias", valid, control_args, True, "none"),
                (
                    "blank_stage",
                    valid.replace("AS route-a-go-deps", "AS "),
                    control_args,
                    False,
                    "invalid_or_missing_stage_input",
                ),
                (
                    "missing_input",
                    valid,
                    [*control_args[:4], *control_args[6:]],
                    False,
                    "invalid_or_missing_stage_input",
                ),
                (
                    "invalid_input",
                    valid,
                    [
                        v.replace("=proof-input", "=not a valid image")
                        if v.startswith("ROUTE_A_GO_DEPS_IMAGE=")
                        else v
                        for v in control_args
                    ],
                    False,
                    "invalid_copy_or_image_reference",
                ),
            ]
            for name, text, args, positive, expected_failure in cases:
                stage = "control_" + name
                recipe = work / (name + ".Dockerfile")
                recipe.write_text(text)
                tag = "route-a-offline-proof:" + source[:12] + "-" + name
                done = execute(
                    build(recipe, control, args, output_args=["--load", "-t", tag]), bound=90
                )
                if done.returncode == 0:
                    tags.append(tag)
                kind = (
                    "none"
                    if done.returncode == 0
                    else closed_build_failure(done.stderr, done.stdout)
                )
                receipt["controls"].append(
                    {
                        "name": name,
                        "recipe_sha256": digest(recipe),
                        "exit": done.returncode,
                        "expected_positive": positive,
                        "failure_kind": kind,
                        "expected_failure_kind": expected_failure,
                        "builder_diagnostic": closed_builder_diagnostic(
                            done.stdout, done.stderr, recipe
                        ),
                        "input_digest": sealed["control"][1]["manifest_digest"],
                        "network": "none",
                    }
                )
                if (done.returncode == 0) != positive or kind != expected_failure:
                    raise ProofRefusal("control_not_positioned")
                if positive:
                    required(["docker", "image", "inspect", "--format", "{{.Id}}", tag], bound=10)
            contracts = dependency_cache_contracts(ROOT)
            go = re.search(
                r"^FROM (golang:[^ ]+@sha256:[0-9a-f]{64})", (ROOT / "Dockerfile").read_text(), re.M
            )
            if not go:
                raise ProofRefusal("pinned_go_unavailable")
            lock = json.loads((ROOT / "frontend/package-lock.json").read_text())
            version = lock["packages"]["node_modules/@playwright/test"]["version"]
            if not re.fullmatch(r"\d+\.\d+\.\d+", version):
                raise ProofRefusal("locked_playwright_version_unavailable")
            base_recipe = work / "base.Dockerfile"
            base_recipe.write_bytes((ROOT / "Dockerfile.base").read_bytes())
            stage = "prepare_base"
            base_archive = work / "base.tar"
            required(
                build(
                    base_recipe,
                    ROOT,
                    [],
                    output_args=["--output", "type=oci,dest=" + str(base_archive)],
                )
            )
            base_layout = work / "base-oci"
            base_identity = seal_oci(base_archive, base_layout, {})
            base_ref = "oci-layout://" + str(base_layout) + "@" + base_identity["manifest_digest"]
            receipt["inputs"]["base"] = base_identity
            go_ref = prepare("go", "FROM " + go[1] + "\n", ROOT, {})
            node_ref = prepare("node", "FROM node:22-bookworm-slim\n", ROOT, {})
            browser_ref = prepare(
                "playwright", "FROM mcr.microsoft.com/playwright:v" + version + "-noble\n", ROOT, {}
            )
            go_cache = prepare(
                "go_cache",
                "FROM " + go[1] + "\nWORKDIR /build\nCOPY whatsapp-bridge/ ./\n"
                "RUN go mod download && CGO_ENABLED=0 GOOS=linux go build "
                "-o /tmp/proof-bridge ./cmd/bridge\n",
                ROOT,
                contracts["ROUTE_A_GO_DEPS_IMAGE"],
            )

            # Dependencies are materialized once before the offline commands.
            def cache_with_base(
                name: str, base: str, text: str, context: Path, labels: dict
            ) -> str:
                nonlocal stage
                stage = "prepare_" + name
                recipe = work / (name + ".Dockerfile")
                recipe.write_text("FROM materialized-base\n" + text)
                archive = work / (name + ".tar")
                args = ["--build-context", "materialized-base=" + base]
                args += [v for k, value in labels.items() for v in ("--label", k + "=" + value)]
                required(
                    build(
                        recipe,
                        context,
                        args,
                        output_args=["--output", "type=oci,dest=" + str(archive)],
                    )
                )
                layout = work / (name + "-oci")
                identity = seal_oci(archive, layout, labels)
                receipt["inputs"][name] = identity
                return "oci-layout://" + str(layout) + "@" + identity["manifest_digest"]

            uv_cache = cache_with_base(
                "uv_cache",
                base_ref,
                "WORKDIR /app\nENV UV_TORCH_BACKEND=cpu\nCOPY pyproject.toml uv.lock ./\n"
                "COPY src/ src/\nRUN uv sync --frozen --no-dev --extra whatsapp\n",
                ROOT,
                contracts["ROUTE_A_UV_CACHE_IMAGE"],
            )
            npm_cache = cache_with_base(
                "npm_cache",
                node_ref,
                "WORKDIR /app\nCOPY package.json package-lock.json ./\n"
                "RUN npm ci --cache=/root/.npm\n",
                ROOT / "frontend",
                contracts["ROUTE_A_NPM_CACHE_IMAGE"],
            )
            inputs = {
                "ROUTE_A_BASE_IMAGE": base_ref,
                "ROUTE_A_GO_IMAGE": go_ref,
                "ROUTE_A_NODE_IMAGE": node_ref,
                "ROUTE_A_PLAYWRIGHT_IMAGE": browser_ref,
                "ROUTE_A_GO_DEPS_IMAGE": go_cache,
                "ROUTE_A_UV_CACHE_IMAGE": uv_cache,
                "ROUTE_A_NPM_CACHE_IMAGE": npm_cache,
            }
            receipt["same_controlled_inputs"] = {
                key: reference.split("@")[-1] for key, reference in inputs.items()
            }
            # Run every immutable old recipe with the exact same sealed inputs.
            # Their current context/locks are explicit; these are old-recipe
            # representation reds, not a historical baseline-success claim.
            for name, relative in RECIPES.items():
                stage = "old_recipe_" + name
                args = ["--network=none", "--pull=false", "--build-arg", "GIT_SHA=" + BASE_SOURCE]
                for key, reference in inputs.items():
                    alias = "input-" + key.lower().replace("_", "-")
                    args += [
                        "--build-arg",
                        key + "=" + alias,
                        "--build-context",
                        alias + "=" + reference,
                    ]
                context = ROOT if name == "application" else ROOT / "frontend"
                done = execute(
                    build(historical / relative, context, args, output_args=[]), bound=90
                )
                kind = closed_build_failure(done.stderr, done.stdout)
                receipt["controls"].append(
                    {
                        "name": "immutable_old_" + name,
                        "exit": done.returncode,
                        "recipe_sha256": history["recipes"][relative],
                        "failure_kind": kind,
                        "builder_diagnostic": closed_builder_diagnostic(
                            done.stdout, done.stderr, historical / relative
                        ),
                        "same_sealed_input_digests": receipt["same_controlled_inputs"],
                        "network": "none",
                        "expected_positive": False,
                    }
                )
                if done.returncode == 0 or kind != "invalid_copy_or_image_reference":
                    raise ProofRefusal("historical_variable_copy_red_not_positioned")
            for name, relative in RECIPES.items():
                stage = "offline_" + name
                args = ["--network=none", "--pull=false", "--build-arg", "GIT_SHA=" + source]
                for key, reference in inputs.items():
                    alias = "input-" + key.lower().replace("_", "-")
                    args += [
                        "--build-arg",
                        key + "=" + alias,
                        "--build-context",
                        alias + "=" + reference,
                    ]
                tag = "route-a-offline-proof:" + source[:12] + "-" + name
                tags.append(tag)
                context = ROOT if name == "application" else ROOT / "frontend"
                required(build(ROOT / relative, context, args, output_args=["--load", "-t", tag]))
                image_id = (
                    required(["docker", "image", "inspect", "--format", "{{.Id}}", tag], bound=10)
                    .decode()
                    .strip()
                )
                if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
                    raise ProofRefusal("actual_image_identity_unavailable")
                receipt["images"][name] = {
                    "image_id": image_id,
                    "recipe_sha256": digest(ROOT / relative),
                    "source": source,
                    "network": "none",
                    "pull": False,
                    "build": "passed",
                    "input_digests": {k: v.split("@")[-1] for k, v in inputs.items()},
                }
                save()
                required(["docker", "image", "rm", "-f", tag], bound=60)
                tags.remove(tag)
            receipt["status"] = "passed"
    except Exception as exc:  # A failed proof exports no arbitrary exception arguments or bytes.
        receipt["status"] = "failed"
        receipt["failed_stage"] = stage
        receipt["refusal"] = str(exc) if isinstance(exc, ProofRefusal) else "proof_unavailable"
    finally:
        stage = "cleanup"
        cleanup = []
        if tags:
            cleanup.append(
                subprocess.run(
                    ["timeout", "--kill-after=15s", "60", "docker", "image", "rm", "-f", *tags],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).returncode
            )
        if created:
            cleanup.append(
                subprocess.run(
                    [
                        "timeout",
                        "--kill-after=15s",
                        "60",
                        "docker",
                        "buildx",
                        "rm",
                        "--force",
                        builder,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).returncode
            )
        receipt["cleanup_exits"] = cleanup
        receipt["cleanup"] = "passed" if all(code == 0 for code in cleanup) else "failed"
        if receipt["cleanup"] != "passed":
            receipt["status"] = "failed"
        save()
    return 0 if receipt["status"] == "passed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", required=True)
    args = parser.parse_args()
    try:
        return run_proof(args.output, args.source)
    except ProofRefusal:
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
