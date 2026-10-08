"""Hosted-only, nonpublishing before/current builds of both application recipes.

Route A uses immutable local OCI inputs with its exact cache labels and offline
Dockerfile. This proves that build recipe, not the separate RepoDigest launcher
admission or its browser/DB lifecycle. No provider/model invocation is performed.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import subprocess
import tarfile
import time
from pathlib import Path

BASE_SOURCE = "e7b7812a3fa65c80f3f070d38ee43f7fa6474881"
BASE_ROUTE_RECIPE_SHA256 = "9badf1251a274a7e8526100b34fcd517aecdf640713aae157467f1f9ecc4e85f"
DIAGNOSTIC_SECONDS = 1800

RUNTIME_PROBE = """
import importlib.metadata, importlib.util, json, os, sys, types
import torch
from butlers.modules.memory.embedding import EmbeddingEngine
assert importlib.util.find_spec('sentence_transformers') is not None
observed=[]
class OfflineModel:
    def __init__(self, name): observed.append(name)
    def encode(self, values, **kwargs):
        observed.append(values)
        return torch.ones((len(values),384)) if isinstance(values,list) else torch.ones(384)
sys.modules['sentence_transformers']=types.SimpleNamespace(SentenceTransformer=OfflineModel)
engine=EmbeddingEngine()
assert engine.dimension==384 and len(engine.embed(None))==384
assert len(engine.embed_batch(['','probe']))==2 and observed[1]==' '
assert torch.tensor([2,3],device='cpu').sum().item()==5
assert os.environ['GIT_SHA']==sys.argv[1]
names=[d.metadata['Name'].lower() for d in importlib.metadata.distributions()]
print(json.dumps({'cpu_tensor':True,'cuda_build_absent':torch.version.cuda is None,
 'cuda_available':torch.cuda.is_available(),'image_git_sha_equal':True,
 'nvidia_count':sum(n.startswith('nvidia-') for n in names),
 'embedding_engine_offline_fixture':True,'embedding_dimension':engine.dimension}))
"""


class DiagnosticRefusal(RuntimeError):
    """Closed diagnostic category; build output and credentials are never exported."""


def digest_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def baseline_compatibility_recipe(original: bytes) -> bytes:
    """Adapt only the pinned before recipe's unsupported COPY representation.

    Keep every original instruction and input. Named stages bind the same
    constructor-fixed cache arguments; the exact inverse must recover the
    immutable original. The generated Dockerfile lives outside its checkout.
    This does not make the literal historical recipe a successful build.
    """
    if hashlib.sha256(original).hexdigest() != BASE_ROUTE_RECIPE_SHA256:
        raise DiagnosticRefusal("baseline_recipe_mismatch")
    source = original.decode("utf-8")
    marker = "FROM ${ROUTE_A_GO_IMAGE} AS go-builder\n"
    prefix = (
        "ARG ROUTE_A_GO_DEPS_IMAGE\n"
        "ARG ROUTE_A_UV_CACHE_IMAGE\n\n"
        "FROM ${ROUTE_A_GO_DEPS_IMAGE} AS route-a-go-deps\n"
        "FROM ${ROUTE_A_UV_CACHE_IMAGE} AS route-a-uv-cache\n\n"
    )
    replacements = (
        ("COPY --from=${ROUTE_A_GO_DEPS_IMAGE} ", "COPY --from=route-a-go-deps ", 2),
        ("COPY --from=${ROUTE_A_UV_CACHE_IMAGE} ", "COPY --from=route-a-uv-cache ", 1),
    )
    if source.count(marker) != 1:
        raise DiagnosticRefusal("baseline_recipe_representation")
    adapted = source.replace(marker, prefix + marker, 1)
    for before, after, count in replacements:
        if source.count(before) != count or after in source:
            raise DiagnosticRefusal("baseline_recipe_representation")
        adapted = adapted.replace(before, after)
    restored = adapted.replace(prefix, "", 1)
    for before, after, _ in replacements:
        restored = restored.replace(after, before)
    if restored.encode("utf-8") != original:
        raise DiagnosticRefusal("baseline_recipe_representation")
    return adapted.encode("utf-8")


def baseline_build_inputs(checkout: Path) -> dict:
    """Bind every original application/cache COPY input to the pinned Git tree.

    This is a historical diagnostic snapshot check, not launcher admission.
    The current launcher still validates the complete current recipe family.
    No generated Dockerfile or current source is written into this checkout.
    """
    paths = (
        "Dockerfile",
        "Dockerfile.base",
        "Dockerfile.meeting-prep-route-a",
        ".dockerignore",
        "pyproject.toml",
        "uv.lock",
        "src",
        "scripts",
        "alembic",
        "roster",
        "whatsapp-bridge",
        "pricing.toml",
        "model_catalog_defaults.toml",
    )
    listing = subprocess.check_output(
        ["git", "ls-tree", "-rz", BASE_SOURCE, "--", *paths], cwd=checkout
    )
    aggregate = hashlib.sha256()
    count = 0
    for entry in listing.split(b"\0"):
        if not entry:
            continue
        metadata, relative = entry.split(b"\t", 1)
        mode, kind, expected = metadata.split()
        source = checkout / os.fsdecode(relative)
        if kind != b"blob" or mode not in (b"100644", b"100755", b"120000"):
            raise DiagnosticRefusal("baseline_input_kind")
        if mode == b"120000":
            if not source.is_symlink():
                raise DiagnosticRefusal("baseline_input_mismatch")
            body = os.fsencode(os.readlink(source))
        else:
            if source.is_symlink() or not source.is_file():
                raise DiagnosticRefusal("baseline_input_mismatch")
            if bool(source.stat().st_mode & 0o111) != (mode == b"100755"):
                raise DiagnosticRefusal("baseline_input_mode_mismatch")
            body = source.read_bytes()
        # Git's object identity verifies actual bytes against immutable history;
        # SHA256 separately binds the complete ordered diagnostic input set.
        blob = hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest()
        if blob.encode() != expected:
            raise DiagnosticRefusal("baseline_input_mismatch")
        aggregate.update(relative + b"\0" + hashlib.sha256(body).digest())
        count += 1
    if not count:
        raise DiagnosticRefusal("baseline_inputs_absent")
    # Ignored/untracked material must not silently enter an original COPY tree.
    for options in (
        ("--others", "--exclude-standard"),
        ("--others", "--ignored", "--exclude-standard"),
    ):
        if subprocess.check_output(["git", "ls-files", "-z", *options, "--", *paths], cwd=checkout):
            raise DiagnosticRefusal("baseline_inputs_untracked")
    tree = subprocess.check_output(
        ["git", "rev-parse", BASE_SOURCE + "^{tree}"], cwd=checkout, text=True
    ).strip()
    return {
        "source": BASE_SOURCE,
        "tree": tree,
        "copy_input_paths": list(paths),
        "input_files": count,
        "actual_input_sha256": aggregate.hexdigest(),
        "all_original_git_blob_bytes_equal": True,
        "literal_recipe_sha256": digest_file(checkout / "Dockerfile.meeting-prep-route-a"),
        "project_sha256": digest_file(checkout / "pyproject.toml"),
        "lock_sha256": digest_file(checkout / "uv.lock"),
        "go_module_sha256": digest_file(checkout / "whatsapp-bridge/go.mod"),
        "go_sum_sha256": digest_file(checkout / "whatsapp-bridge/go.sum"),
    }


def write_oci_layout(archive, destination: Path, labels: dict[str, str]) -> str:
    """Convert actual docker-save bytes without changing config/filesystem content.

    Validate each uncompressed layer against the original config's diff ID and
    the actual sealed cache labels. OCI references use the resulting immutable
    manifest digest, never a mutable tag or invented RepoDigest.
    """
    blobs = destination / "blobs/sha256"
    blobs.mkdir(parents=True)
    entries: dict[str, tuple[str, int]] = {}
    with tarfile.open(fileobj=archive, mode="r|") as source:
        for member in source:
            if member.isdir():
                continue
            if (
                not member.isfile()
                or Path(member.name).is_absolute()
                or ".." in Path(member.name).parts
            ):
                raise DiagnosticRefusal("invalid_image_archive")
            stream = source.extractfile(member)
            if stream is None:
                raise DiagnosticRefusal("missing_image_member")
            temporary = blobs / "incoming"
            hashed = hashlib.sha256()
            with temporary.open("wb") as output:
                while chunk := stream.read(1024 * 1024):
                    hashed.update(chunk)
                    output.write(chunk)
            value = hashed.hexdigest()
            temporary.replace(blobs / value)
            entries[member.name] = (value, member.size)
    manifest = json.loads((blobs / entries["manifest.json"][0]).read_bytes())
    if len(manifest) != 1:
        raise DiagnosticRefusal("ambiguous_image_archive")
    original = manifest[0]
    config_digest, config_size = entries[original["Config"]]
    config = json.loads((blobs / config_digest).read_bytes())
    actual_labels = config.get("config", {}).get("Labels") or {}
    if any(actual_labels.get(key) != value for key, value in labels.items()):
        raise DiagnosticRefusal("cache_input_label_mismatch")
    diffs = config["rootfs"]["diff_ids"]
    if len(diffs) != len(original["Layers"]):
        raise DiagnosticRefusal("layer_count_mismatch")
    layers = []
    for name, expected_diff in zip(original["Layers"], diffs, strict=True):
        value, size = entries[name]
        path = blobs / value
        with path.open("rb") as check:
            compressed = check.read(2) == b"\x1f\x8b"
        if compressed:
            with gzip.open(path, "rb") as stream:
                actual_diff = hashlib.file_digest(stream, "sha256").hexdigest()
        else:
            actual_diff = value
        if expected_diff != "sha256:" + actual_diff:
            raise DiagnosticRefusal("layer_content_mismatch")
        layers.append(
            {
                "mediaType": "application/vnd.oci.image.layer.v1.tar"
                + ("+gzip" if compressed else ""),
                "digest": "sha256:" + value,
                "size": size,
            }
        )
    sealed = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {
            "mediaType": "application/vnd.oci.image.config.v1+json",
            "digest": "sha256:" + config_digest,
            "size": config_size,
        },
        "layers": layers,
    }
    body = json.dumps(sealed, sort_keys=True).encode()
    value = hashlib.sha256(body).hexdigest()
    (blobs / value).write_bytes(body)
    (destination / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}\n')
    (destination / "index.json").write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "manifests": [
                    {
                        "mediaType": sealed["mediaType"],
                        "digest": "sha256:" + value,
                        "size": len(body),
                    }
                ],
            }
        )
        + "\n"
    )
    return f"oci-layout://{destination}@sha256:{value}"


def run_diagnostic(current: Path, baseline: Path, output: Path, source_sha: str) -> dict:
    if os.environ.get("GITHUB_ACTIONS") != "true" or not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise DiagnosticRefusal("hosted_exact_source_required")
    from prove_route_a_offline_builds import closed_build_failure
    from run_meeting_prep_route_a_evidence import (
        SafetyError,
        dependency_cache_contracts,
        validate_sealed_build_inputs,
    )

    started = time.monotonic()
    deadline = started + DIAGNOSTIC_SECONDS
    images: list[str] = []
    builder = "ci-images-" + source_sha[:12]
    builder_created = False
    output.mkdir(parents=True, exist_ok=False)
    receipt = {
        "source": source_sha,
        "baseline": BASE_SOURCE,
        "status": "incomplete",
        "images": {},
        "stages": [],
        "launcher_admission": (
            "NOT RUN; local OCI build inputs do not establish RepoDigest launcher admission"
        ),
        "wall_clock_gain_claimed": False,
        "trained_model_provider_invocation": False,
    }
    stage = "source_validation"

    def command(
        args: list[str], *, cwd: Path = current, capture: bool = False, bound: int = 600
    ) -> str:
        remaining = int(deadline - time.monotonic())
        if remaining <= 0:
            raise DiagnosticRefusal("diagnostic_deadline")
        receipt["active_stage"] = stage
        (output / "image-size-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        done = subprocess.run(
            ["timeout", "--signal=TERM", "--kill-after=15s", str(min(bound, remaining)), *args],
            cwd=cwd,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            text=True,
        )
        return done.stdout if capture else ""

    def tag(name: str) -> str:
        value = f"ci-image-proof:{source_sha[:12]}-{name}"
        images.append(value)
        return value

    def seal(image: str, name: str, labels: dict[str, str]) -> str:
        remaining = min(180, int(deadline - time.monotonic()))
        if remaining <= 0:
            raise DiagnosticRefusal("diagnostic_deadline")
        with subprocess.Popen(
            [
                "timeout",
                "--signal=TERM",
                "--kill-after=15s",
                str(remaining),
                "docker",
                "image",
                "save",
                image,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ) as process:
            try:
                result = write_oci_layout(process.stdout, output / name, labels)
            finally:
                if process.stdout:
                    process.stdout.close()
                process.wait(timeout=remaining + 20)
            if process.returncode != 0:
                raise DiagnosticRefusal("image_export_failed")
        manifest = json.loads((output / name / "index.json").read_text())["manifests"][0]
        body = json.loads(
            (output / name / "blobs/sha256" / manifest["digest"].split(":")[1]).read_bytes()
        )
        actual_id = json.loads(
            command(["docker", "image", "inspect", "--format", "{{json .Id}}", image], capture=True)
        )
        if actual_id != body["config"]["digest"]:
            raise DiagnosticRefusal("actual_image_identity_mismatch")
        return result

    try:
        for checkout, expected in ((current, source_sha), (baseline, BASE_SOURCE)):
            if (
                command(["git", "rev-parse", "HEAD"], cwd=checkout, capture=True).strip()
                != expected
            ):
                raise DiagnosticRefusal("source_mismatch")
            if command(
                ["git", "status", "--porcelain", "--untracked-files=no"], cwd=checkout, capture=True
            ).strip():
                raise DiagnosticRefusal("dirty_source")
        validate_sealed_build_inputs(current)
        original = (baseline / "Dockerfile.meeting-prep-route-a").read_bytes()
        adapted = baseline_compatibility_recipe(original)
        receipt["baseline_inputs"] = baseline_build_inputs(baseline)
        adapted_recipe = output / "before-route-a-compatibility.Dockerfile"
        adapted_recipe.write_bytes(adapted)
        receipt["baseline_representation"] = {
            "literal_recipe_sha256": hashlib.sha256(original).hexdigest(),
            "adapted_recipe_sha256": hashlib.sha256(adapted).hexdigest(),
            "exact_inverse_restores_original": True,
            "generated_outside_before_checkout": not adapted_recipe.is_relative_to(baseline),
            "literal_historical_success_claimed": False,
            "changes": "global cache ARGs, two fixed input stages, three COPY source tokens",
        }
        if adapted_recipe.is_relative_to(baseline):
            raise DiagnosticRefusal("baseline_adapter_location")
        shared_base_inputs = (
            "Dockerfile.base",
            "scripts/runtime_cli_sandbox_init.c",
            "scripts/generate_runtime_cli_sandbox_manifest.py",
        )
        for relative in shared_base_inputs:
            if (current / relative).read_bytes() != (baseline / relative).read_bytes():
                raise DiagnosticRefusal("incompatible_base_comparison")
        receipt["shared_base_copied_inputs"] = {
            relative: digest_file(current / relative) for relative in shared_base_inputs
        }
        go = re.search(
            r"^FROM (golang:[^ ]+@sha256:[0-9a-f]{64})", (current / "Dockerfile").read_text(), re.M
        )
        if go is None:
            raise DiagnosticRefusal("missing_pinned_go")
        baseline_go = re.search(
            r"^FROM (golang:[^ ]+@sha256:[0-9a-f]{64})",
            (baseline / "Dockerfile").read_text(),
            re.M,
        )
        if baseline_go is None or baseline_go[1] != go[1]:
            raise DiagnosticRefusal("incompatible_go_comparison")
        stage = "base_build"
        command(
            ["docker", "build", "-f", "Dockerfile.base", "-t", "butlers-base:latest", "."],
            bound=900,
        )
        base_input = seal("butlers-base:latest", "base-oci", {})
        stage = "go_input"
        command(["docker", "pull", go[1]])
        go_input = seal(go[1], "go-oci", {})
        command(["docker", "buildx", "create", "--name", builder, "--driver", "docker-container"])
        builder_created = True
        for variant, checkout in (("before", baseline), ("current", current)):
            sha = BASE_SOURCE if variant == "before" else source_sha
            stage = variant + "_normal_build"
            normal = tag(variant + "-normal")
            command(
                ["docker", "build", "--build-arg", "GIT_SHA=" + sha, "-t", normal, "."],
                cwd=checkout,
                bound=900,
            )
            size = json.loads(
                command(
                    ["docker", "image", "inspect", "--format", "{{json .Size}}", normal],
                    capture=True,
                )
            )
            if type(size) is not int or size <= 0:
                raise DiagnosticRefusal("invalid_image_size")
            receipt["images"][variant] = {
                "normal": {"source": sha, "bytes": size, "build": "passed", "runtime": "NOT RUN"}
            }
            contracts = dependency_cache_contracts(checkout)
            cache_inputs = {}
            for kind, key in (("go", "ROUTE_A_GO_DEPS_IMAGE"), ("uv", "ROUTE_A_UV_CACHE_IMAGE")):
                stage = variant + "_" + kind + "_cache"
                recipe = output / f"{variant}-{kind}.Dockerfile"
                if kind == "go":
                    recipe.write_text(
                        f"FROM {go[1]}\nWORKDIR /build\nCOPY whatsapp-bridge/ ./\n"
                        "RUN go mod download && CGO_ENABLED=0 GOOS=linux go build "
                        "-o /tmp/proof-bridge ./cmd/bridge\n"
                    )
                else:
                    extra = (
                        " --extra whatsapp"
                        if "--extra whatsapp"
                        in (checkout / "Dockerfile.meeting-prep-route-a").read_text()
                        else ""
                    )
                    recipe.write_text(
                        "FROM butlers-base:latest\nCOPY pyproject.toml uv.lock ./\n"
                        "COPY src/ src/\nRUN uv sync --frozen --no-dev" + extra + "\n"
                    )
                cache = tag(variant + "-" + kind + "-cache")
                labels = contracts[key]
                args = ["docker", "build", "-f", str(recipe), "-t", cache]
                for key_name, value in labels.items():
                    args += ["--label", key_name + "=" + value]
                command(args + ["."], cwd=checkout, bound=900)
                cache_inputs[kind] = seal(cache, variant + "-" + kind + "-oci", labels)
            stage = variant + "_route_a_offline_build"
            route = tag(variant + "-route-a")
            args = [
                "docker",
                "buildx",
                "build",
                "--builder",
                builder,
                "--load",
                "--network=none",
                "--pull=false",
                "-f",
                "Dockerfile.meeting-prep-route-a",
                "-t",
                route,
                "--build-arg",
                "GIT_SHA=" + sha,
            ]
            inputs = {
                "ROUTE_A_BASE_IMAGE": ("diag-base", base_input),
                "ROUTE_A_GO_IMAGE": ("diag-go", go_input),
                "ROUTE_A_GO_DEPS_IMAGE": ("diag-go-cache", cache_inputs["go"]),
                "ROUTE_A_UV_CACHE_IMAGE": ("diag-uv-cache", cache_inputs["uv"]),
            }
            for argument, (alias, sealed) in inputs.items():
                args += [
                    "--build-arg",
                    argument + "=" + alias,
                    "--build-context",
                    alias + "=" + sealed,
                ]
            if variant == "before":
                stage = "before_literal_route_a_compiler_negative"
                remaining = min(120, int(deadline - time.monotonic()))
                if remaining <= 0:
                    raise DiagnosticRefusal("diagnostic_deadline")
                # Real compiler output stays in process memory. Export only the
                # public source-defined category, never stderr or image values.
                negative = subprocess.run(
                    [
                        "timeout",
                        "--signal=TERM",
                        "--kill-after=15s",
                        str(remaining),
                        *args,
                        ".",
                    ],
                    cwd=checkout,
                    capture_output=True,
                    check=False,
                )
                category = closed_build_failure(negative.stderr, negative.stdout)
                receipt["baseline_representation"]["literal_compiler_observation"] = {
                    "category": category,
                    "returncode_nonzero": negative.returncode != 0,
                }
                if negative.returncode == 0 or category != "unsupported_copy_from_variable":
                    raise DiagnosticRefusal("baseline_compiler_negative_unpositioned")
                receipt["baseline_representation"]["literal_compiler_negative"] = {
                    "category": category,
                    "returncode_nonzero": True,
                    "actual_same_original_source_and_sealed_inputs": True,
                }
                # Same e7 build context, source, lock, caches and args. Only -f
                # names the separately generated, exactly reversible recipe.
                args[args.index("-f") + 1] = str(adapted_recipe)
                stage = "before_adapted_route_a_offline_build"
            command(args + ["."], cwd=checkout, bound=900)
            for kind, image in (("normal", normal), ("route_a", route)):
                stage = variant + "_" + kind + "_runtime"
                raw = command(
                    ["docker", "image", "inspect", "--format", "{{json .Size}}", image],
                    capture=True,
                )
                size = json.loads(raw)
                if type(size) is not int or size <= 0:
                    raise DiagnosticRefusal("invalid_image_size")
                probe = json.loads(
                    command(
                        [
                            "docker",
                            "run",
                            "--rm",
                            "--network=none",
                            "--entrypoint",
                            "/app/.venv/bin/python",
                            image,
                            "-c",
                            RUNTIME_PROBE,
                            sha,
                        ],
                        capture=True,
                        bound=120,
                    )
                )
                if variant == "current" and (
                    probe["cuda_build_absent"] is not True
                    or probe["cuda_available"] is not False
                    or probe["nvidia_count"] != 0
                ):
                    raise DiagnosticRefusal("current_not_cpu_only")
                command(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--network=none",
                        "--entrypoint",
                        "/usr/local/bin/whatsapp-bridge",
                        image,
                        "--help",
                    ],
                    bound=20,
                )
                command(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--network=none",
                        "--entrypoint",
                        "psql",
                        image,
                        "--version",
                    ],
                    bound=20,
                )
                receipt["images"][variant][kind] = {
                    "source": sha,
                    "bytes": size,
                    "recipe_basis": (
                        "representation_adapted_original"
                        if variant == "before" and kind == "route_a"
                        else "literal_recipe"
                    ),
                    "runtime": probe,
                    "go_bridge_help": True,
                    "postgres_client": True,
                }
            receipt["stages"].append(
                {
                    "variant": variant,
                    "actual_offline_inputs": {
                        name: uri.split("@sha256:")[1] for name, (_, uri) in inputs.items()
                    },
                    "sealed_labels_checked": True,
                    "normal_build": "passed",
                    "route_a_offline_build": "passed",
                }
            )
            command(["docker", "image", "rm", "-f", *images], bound=60)
            images.clear()
            for kind in ("go", "uv"):
                import shutil

                shutil.rmtree(output / (variant + "-" + kind + "-oci"))
        receipt["status"] = "passed"
    except (
        DiagnosticRefusal,
        SafetyError,
        subprocess.SubprocessError,
        OSError,
        ValueError,
        KeyError,
    ) as error:
        receipt.update(status="failed", failed_stage=stage, error_category=type(error).__name__)
        if isinstance(error, subprocess.CalledProcessError):
            receipt["command_returncode"] = error.returncode
    finally:
        cleanup = subprocess.run(
            ["timeout", "--kill-after=15s", "60", "docker", "buildx", "rm", "--force", builder],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        receipt["builder_cleanup_returncode"] = cleanup
        if builder_created and cleanup != 0:
            receipt["status"] = "failed"
            receipt["cleanup_failed"] = True
        if images:
            subprocess.run(
                ["timeout", "--kill-after=15s", "60", "docker", "image", "rm", "-f", *images],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        receipt["diagnostic_elapsed_s"] = time.monotonic() - started
        (output / "image-size-receipt.json").write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n"
        )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    result = run_diagnostic(
        args.current.resolve(), args.baseline.resolve(), args.output.resolve(), args.source_sha
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "failed_stage": result.get("failed_stage"),
                "wall_clock_gain_claimed": False,
            }
        )
    )
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
