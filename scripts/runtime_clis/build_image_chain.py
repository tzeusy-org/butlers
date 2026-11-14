#!/usr/bin/env python3
"""Build local application images from closed contexts and a real immutable base.

The disposable registry is bound only to loopback. Nothing is deployed or pushed
to an external registry, and no caller Docker credentials/configuration are used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import types
import urllib.request
import uuid
from pathlib import Path

BASE_INPUTS = (
    "Dockerfile.base",
    "scripts/runtime_clis/install_system_artifacts.py",
    "deploy/runtime-clis/debian-system-inputs.json",
    "scripts/runtime_cli_sandbox_init.c",
    "scripts/runtime_clis/runtime_artifacts.py",
    "deploy/runtime-clis/runtime-inputs.json",
    "scripts/generate_runtime_cli_sandbox_manifest.py",
)
REGISTRY_INPUT = "deploy/runtime-clis/image-registry-inputs.json"
OWNER_LABEL = "butlers.image-chain.owner"


def shared():
    """Load the fixed sibling without writing bytecode or forking package identity."""
    path = Path(__file__).with_name("produce_image_contexts.py")
    if path.is_symlink() or not path.is_file():
        raise ValueError("image context helper must be a regular sibling")
    name = "_image_chain_context_helpers"
    if name in sys.modules:
        raise ValueError("private image helper name already occupied")
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    try:
        exec(compile(path.read_bytes(), str(path), "exec", dont_inherit=True), module.__dict__)
    finally:
        del sys.modules[name]
    return module


def regular(root: Path, name: str) -> Path:
    path = root / name
    if any(parent.is_symlink() for parent in path.parents if parent.is_relative_to(root)):
        raise ValueError("image input ancestors must be real directories")
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
        raise ValueError("image input must be a contained regular file")
    return path


def contexts_identity(source: Path, contexts: Path, identity: dict, helpers) -> None:
    expected = regular(contexts, "source-inputs.json")
    verification = json.loads(regular(contexts, "verification.json").read_bytes())
    if (
        json.loads(expected.read_bytes()) != identity
        or verification.get("schema") != "butlers.image-contexts/v1"
        or verification.get("status") != "CLOSED_CONTEXTS"
        or verification.get("source_inputs_sha256") != helpers.digest(expected)
        or verification.get("runtime_inputs_sha256")
        != helpers.digest(source / "deploy/runtime-clis/runtime-inputs.json")
        or verification.get("producers") != [build.name for build in helpers.builds()]
    ):
        raise ValueError("closed contexts do not match the exact current source")
    for name in ("system", "runtime", "python/default", "python/live-listener", "go"):
        path = contexts / name
        if not path.is_dir() or path.resolve() != path.absolute():
            raise ValueError("required named context must be a real directory")
    if (
        regular(contexts, "runtime/inputs.json").read_bytes()
        != regular(source, "deploy/runtime-clis/runtime-inputs.json").read_bytes()
    ):
        raise ValueError("runtime context differs from its exact source manifest")


def loaded_identity_matches(image: dict, config: str, manifest: str | None) -> bool:
    """The loaded image is exactly what Buildx built, under either Docker image store.

    The classic store reports the config digest as Id. Docker 29's containerd store
    reports the loaded manifest digest as Id and repeats it in Descriptor, so a
    present Descriptor must name exactly the manifest Buildx produced (bu-kiuh8v.38).
    """
    descriptor = image.get("Descriptor")
    if descriptor is not None and (
        type(descriptor) is not dict or manifest is None or descriptor.get("digest") != manifest
    ):
        return False
    return image.get("Id") == config or (manifest is not None and image.get("Id") == manifest)


_EVIDENCE_SHAPES = {
    "digest": r"sha256:[0-9a-f]{64}",
    "media": r"application/[a-z0-9.+-]{1,120}",
    "reference": r"[a-z0-9./:_-]{1,200}@sha256:[0-9a-f]{64}",
    "platform": r"[a-z0-9]{1,16}",
}


def _shaped(value, shape: str):
    """Pass a value through only when it has the expected public shape."""
    if value is None or (shape == "size" and type(value) is int):
        return value
    if isinstance(value, str) and re.fullmatch(_EVIDENCE_SHAPES.get(shape, r"(?!)"), value):
        return value
    return "<unexpected " + type(value).__name__ + ">"


def _descriptor_evidence(descriptor):
    if not isinstance(descriptor, dict):
        return _shaped(descriptor, "digest")
    return {
        "mediaType": _shaped(descriptor.get("mediaType"), "media"),
        "digest": _shaped(descriptor.get("digest"), "digest"),
        "size": _shaped(descriptor.get("size"), "size"),
    }


def identity_evidence(built: dict, inspected) -> str:
    """Digest-only account of what Buildx and the image store reported.

    Image identities are public content addresses; every other field is dropped,
    and anything not shaped like one is replaced, so no configuration leaks.
    """
    images = inspected if isinstance(inspected, list) else [inspected]
    evidence = {
        "buildx": {
            "keys": sorted(str(key) for key in built),
            "containerimage.config.digest": _shaped(
                built.get("containerimage.config.digest"), "digest"
            ),
            "containerimage.digest": _shaped(built.get("containerimage.digest"), "digest"),
            "containerimage.descriptor": _descriptor_evidence(
                built.get("containerimage.descriptor")
            ),
        },
        "inspect": [
            {
                "Id": _shaped(image.get("Id"), "digest"),
                "Descriptor": _descriptor_evidence(image.get("Descriptor")),
                "RepoDigests": [
                    _shaped(item, "reference") for item in image.get("RepoDigests") or []
                ],
                "Os": _shaped(image.get("Os"), "platform"),
                "Architecture": _shaped(image.get("Architecture"), "platform"),
                "Variant": _shaped(image.get("Variant"), "platform"),
            }
            if isinstance(image, dict)
            else "<unexpected " + type(image).__name__ + ">"
            for image in images
        ],
    }
    return json.dumps(evidence, sort_keys=True)


def local_manifest(reference: str) -> str:
    """Read only the owned loopback registry; redirects and proxies are disabled."""
    match = re.fullmatch(r"127\.0\.0\.1:([0-9]+)/butlers-base@sha256:([0-9a-f]{64})", reference)
    if not match or not 0 < int(match[1]) < 65536:
        raise ValueError("manifest reader requires the owned loopback base reference")

    class NoRedirect(urllib.request.HTTPErrorProcessor):
        def http_response(self, request, response):
            if response.status != 200:
                response.close()
                raise ValueError("owned registry did not return its exact manifest")
            return response

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(
        "http://127.0.0.1:" + match[1] + "/v2/butlers-base/manifests/sha256:" + match[2],
        headers={
            "Accept": (
                "application/vnd.oci.image.manifest.v1+json,"
                "application/vnd.oci.image.index.v1+json,"
                "application/vnd.docker.distribution.manifest.v2+json"
            )
        },
    )
    with opener.open(request, timeout=20) as response:
        payload = response.read(4 * 1024 * 1024 + 1)
        if (
            len(payload) > 4 * 1024 * 1024
            or hashlib.sha256(payload).hexdigest() != match[2]
            or response.headers.get("Docker-Content-Digest") != "sha256:" + match[2]
        ):
            raise ValueError("owned registry manifest digest differs from Buildx output")
    manifest = json.loads(payload)
    if manifest.get("schemaVersion") != 2:
        raise ValueError("owned registry did not publish an OCI/distribution manifest")
    return "sha256:" + match[2]


class Registry:
    def __init__(self, commands, source: Path, image: str):
        self.commands = commands
        self.source = source
        self.image = image
        self.owner = uuid.uuid4().hex
        self.name = "butlers-image-chain-" + self.owner

    def start(self) -> str:
        self.commands.run(
            [
                "docker",
                "run",
                "--detach",
                "--platform",
                "linux/amd64",
                "--name",
                self.name,
                "--label",
                OWNER_LABEL + "=" + self.owner,
                "--publish",
                "127.0.0.1::5000",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                self.image,
            ],
            cwd=self.source,
            timeout=180,
        )
        value = json.loads(self.commands.run(["docker", "inspect", self.name], cwd=self.source))
        if len(value) != 1 or value[0]["Config"]["Labels"].get(OWNER_LABEL) != self.owner:
            raise ValueError("registry container ownership differs")
        bindings = value[0]["NetworkSettings"]["Ports"].get("5000/tcp")
        if (
            not isinstance(bindings, list)
            or len(bindings) != 1
            or bindings[0].get("HostIp") != "127.0.0.1"
            or not re.fullmatch(r"[0-9]+", bindings[0].get("HostPort", ""))
            or not 0 < int(bindings[0]["HostPort"]) < 65536
        ):
            raise ValueError("registry must have exactly one loopback binding")
        return "127.0.0.1:" + bindings[0]["HostPort"] + "/butlers-base"

    def cleanup(self) -> None:
        """Defer repeat signals until inspect/remove has settled, never detach it."""
        boundary = self.commands.boundary
        with boundary.command_signals():

            def run(argv):
                child = subprocess.Popen(
                    argv,
                    cwd=self.source,
                    env=self.commands.environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                )
                try:
                    output, _ = child.communicate(timeout=30)
                finally:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    child.wait()
                if child.returncode:
                    raise RuntimeError("owned registry cleanup command failed")
                return output.decode("utf-8")

            identifier = run(
                [
                    "docker",
                    "ps",
                    "--all",
                    "--quiet",
                    "--no-trunc",
                    "--filter",
                    "name=^/" + self.name + "$",
                ]
            ).strip()
            if not identifier:
                return
            if not re.fullmatch(r"[0-9a-f]{64}", identifier):
                raise ValueError("ambiguous owned registry container")
            value = json.loads(run(["docker", "inspect", identifier]))
            if (
                len(value) != 1
                or value[0].get("Id") != identifier
                or value[0].get("Name") != "/" + self.name
                or value[0]["Config"]["Labels"].get(OWNER_LABEL) != self.owner
            ):
                raise ValueError("refusing to remove a foreign registry container")
            run(["docker", "rm", "--force", "--volumes", identifier])


def produce(project: Path, contexts: Path, work: Path, output: Path, revision: str) -> dict:
    helpers = shared()
    helpers.checked_paths(project, work, output)
    if (
        contexts.absolute() != contexts.resolve()
        or not contexts.is_dir()
        or any(
            contexts == path or contexts.is_relative_to(path) or path.is_relative_to(contexts)
            for path in (project, work, output)
        )
        or not re.fullmatch(r"[0-9a-f]{40}", revision)
    ):
        raise ValueError("contexts must be real/disjoint and revision must be a full Git SHA")
    work.mkdir(mode=0o755)
    boundary = "scripts/runtime_clis/produce_default_plugin_artifact.py"
    if (
        regular(project, boundary).read_bytes()
        != Path(__file__).with_name("produce_default_plugin_artifact.py").read_bytes()
    ):
        raise ValueError("command boundary must match the declared fixed sibling")
    commands = helpers.Commands(project, work)
    try:
        with commands.boundary.owner_signals():
            return build(project, contexts, work, output, revision, helpers, commands)
    except commands.boundary.OwnerSignal as interrupted:
        signal.signal(interrupted.number, signal.SIG_DFL)
        os.kill(os.getpid(), interrupted.number)
        raise SystemExit(128 + interrupted.number) from None


def build(project, contexts, work, output, revision, helpers, commands) -> dict:
    if commands.run(["git", "rev-parse", "HEAD"], cwd=project).strip() != revision:
        raise ValueError("image revision must name the actual checkout HEAD")
    source = work / "source"
    identity = helpers.freeze(project, source, commands)
    contexts_identity(source, contexts, identity, helpers)
    declaration = json.loads(regular(source, REGISTRY_INPUT).read_bytes())
    image = declaration.get("image", "")
    if (
        declaration.get("platform") != "linux/amd64"
        or not re.fullmatch(r"docker\.io/library/registry@sha256:[0-9a-f]{64}", image)
        or declaration.get("manifest", {}).get("sha256") != image.split("@")[-1]
    ):
        raise ValueError("registry requires the exact declared public amd64 image")
    fingerprint = commands.run(
        [
            "bash",
            "-c",
            'source "$1"; shift; butlers_base_image_input_fingerprint "$@"',
            "base-fingerprint",
            str(source / "scripts/base-image-input-fingerprint.sh"),
            *BASE_INPUTS,
        ],
        cwd=source,
    ).strip()
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ValueError("base fingerprint helper did not return its exact digest")
    registry = Registry(commands, source, image)
    with tempfile.TemporaryDirectory(prefix="image-chain-", dir=output.parent) as temporary:
        stage = Path(temporary) / "output"
        stage.mkdir(mode=0o755)
        try:
            repository = registry.start()
            base_metadata = stage / "base.json"
            commands.run(
                [
                    "docker",
                    "buildx",
                    "build",
                    "--builder",
                    "default",
                    "--platform",
                    "linux/amd64",
                    "--progress",
                    "plain",
                    "--file",
                    str(source / "Dockerfile.base"),
                    "--build-context",
                    "system-artifacts=" + str(contexts / "system"),
                    "--build-context",
                    "runtime-artifacts=" + str(contexts / "runtime"),
                    "--label",
                    "butlers.base.dockerfile_sha=" + helpers.digest(source / "Dockerfile.base"),
                    "--label",
                    "butlers.base.input_sha=" + fingerprint,
                    "--metadata-file",
                    str(base_metadata),
                    "--output",
                    "type=image,name=" + repository + ",push=true,registry.insecure=true",
                    str(source),
                ],
                cwd=source,
                timeout=3600,
            )
            base_digest = json.loads(regular(stage, "base.json").read_bytes()).get(
                "containerimage.digest"
            )
            if not isinstance(base_digest, str) or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", base_digest
            ):
                raise ValueError("Buildx did not return a base manifest digest")
            reference = repository + "@" + base_digest
            local_manifest(reference)
            variants = {}
            for profile in ("default", "live-listener"):
                for target in ("production", "development"):
                    name = profile + "-" + target
                    tag = "butlers-app:" + revision
                    if name != "default-production":
                        tag += "-" + name
                    metadata = stage / (name + ".json")
                    commands.run(
                        [
                            "docker",
                            "buildx",
                            "build",
                            "--builder",
                            "default",
                            "--platform",
                            "linux/amd64",
                            "--progress",
                            "plain",
                            "--file",
                            str(source / "Dockerfile"),
                            "--target",
                            target,
                            # No attestations: with them Docker 29 loads an OCI index and Buildx
                            # reports no config digest, so nothing binds the loaded image id
                            # (bu-kiuh8v.38, dispatch 43610085269). Nothing consumes them.
                            "--provenance=false",
                            "--sbom=false",
                            "--load",
                            "--tag",
                            tag,
                            "--build-context",
                            "python-artifacts=" + str(contexts / "python" / profile),
                            "--build-context",
                            "go-artifacts=" + str(contexts / "go"),
                            "--build-arg",
                            "BUTLERS_BASE_IMAGE=" + reference,
                            "--build-arg",
                            "GIT_SHA=" + revision,
                            "--build-arg",
                            "EXTRAS=" + ("live-listener" if profile == "live-listener" else ""),
                            "--metadata-file",
                            str(metadata),
                            str(source),
                        ],
                        cwd=source,
                        timeout=3600,
                    )
                    built = json.loads(regular(stage, metadata.name).read_bytes())
                    config = built.get("containerimage.config.digest")
                    manifest = built.get("containerimage.digest")
                    inspected = json.loads(
                        commands.run(["docker", "image", "inspect", tag], cwd=source)
                    )
                    if (
                        not isinstance(config, str)
                        or not re.fullmatch(r"sha256:[0-9a-f]{64}", config)
                        or not (
                            manifest is None
                            or (
                                isinstance(manifest, str)
                                and re.fullmatch(r"sha256:[0-9a-f]{64}", manifest)
                            )
                        )
                        or len(inspected) != 1
                        or not loaded_identity_matches(inspected[0], config, manifest)
                        or inspected[0].get("Architecture") != "amd64"
                        or inspected[0].get("Os") != "linux"
                    ):
                        raise ValueError(
                            "loaded application identity differs from Buildx output: "
                            + identity_evidence(built, inspected)
                        )
                    variants[name] = {"tag": tag, "image_id": config}
            helpers.verify_frozen(source, identity)
            contexts_identity(source, contexts, identity, helpers)
        finally:
            pending = sys.exception()
            try:
                registry.cleanup()
            except commands.boundary.OwnerSignal:
                # A repeat signal must not replace the original cancellation
                # status after the entire cleanup transaction has settled.
                if not isinstance(pending, commands.boundary.OwnerSignal):
                    raise
        result = {
            "schema": "butlers.image-chain/v1",
            "status": "BUILT_LOCAL_IMAGES",
            "revision": revision,
            "base_manifest": base_digest,
            "base_input_sha256": fingerprint,
            "variants": variants,
            "source_inputs_sha256": helpers.digest(contexts / "source-inputs.json"),
            "scope": (
                "Local image construction only; cold runtime/native/CNI conformance is separate."
            ),
        }
        helpers.write_json(stage / "verification.json", result)
        stage.rename(output)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("project", "contexts", "work", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            produce(
                args.project.absolute(),
                args.contexts.absolute(),
                args.work.absolute(),
                args.output.absolute(),
                args.revision,
            )
        )
    )


if __name__ == "__main__":
    main()
