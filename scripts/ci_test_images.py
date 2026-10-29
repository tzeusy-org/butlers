"""Preload the existing test images by public immutable content, without credentials.

Docker's ordinary local image store is advisory: a matching alias is reused only
after its content ID/platform is checked. The public Docker Hub mirror is tried
once, then the original registry once. No daemon/registry configuration changes,
generic retries, container starts, or changes to Testcontainers/Ryuk policy.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import signal
import subprocess
import sys
import time

# Independently read Docker Hub's public tag/platform index and the exact public
# mirror manifest/config on 2026-10-09. Pins retain the existing fixture aliases.
IMAGES = {
    "postgres": {
        "alias": "pgvector/pgvector:pg17",
        "repository": "pgvector/pgvector",
        "manifest": "sha256:cd4ccfdaf62cbfeb5c6ee19ef0dd663406d4ed740c320b324d016a90e85385b3",
        "config": "sha256:151aa1c2e849e7828ed8aef2183ae15f692cbe887d2f28921d8b44b26e154d7e",
        "pg_major": "17",
    },
    "ryuk": {
        "alias": "testcontainers/ryuk:0.8.1",
        "repository": "testcontainers/ryuk",
        "manifest": "sha256:ce323122ac8fe22e87a63651903286d478c857c44961aa3a484c2596c53cd1d0",
        "config": "sha256:e1c72b0438b5e617d269397a7d6e8be66c9cc4c1ed01d4e480f20234eb09b507",
        "pg_major": None,
    },
    "owner-browser": {
        "alias": "postgres:16-alpine",
        "repository": "library/postgres",
        "manifest": "sha256:1a66d744c1b459e13b05a8fca341da84cb63383e99ce262210efee5a319d4551",
        "config": "sha256:81bd698b4594e751a3269e4dcd3e03a4a0ec0daf7b72e7aa1abd43cce9887542",
        "pg_major": "16",
    },
}
TOTAL_SECONDS = 360
CLEANUP_SECONDS = 3


class AcquisitionError(Exception):
    """Only fixed source-owned failure categories may leave this module."""


def run(command: list[str], timeout: float) -> subprocess.CompletedProcess:
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    finally:
        # Signal only the process group this invocation owns, including its
        # descendants, even when the direct parent has already returned. A
        # DEVNULL descendant cannot keep communicate() waiting for the group.
        # Raw Docker output never becomes a retained diagnostic.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        finally:
            # Parent completion is not group completion: a descendant may
            # ignore TERM after the parent closes both output pipes.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                # An escaped pipe holder cannot extend this own-child deadline.
                process.stdout.close()
                process.stderr.close()
                process.wait(timeout=1)
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _unique_object(pairs: list[tuple]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate image field")
        value[key] = item
    return value


def matching_image(result: subprocess.CompletedProcess, image: dict) -> bool:
    if result.returncode != 0:
        return False
    try:
        values = json.loads(result.stdout, object_pairs_hook=_unique_object)
        if type(values) is not list or len(values) != 1 or type(values[0]) is not dict:
            return False
        actual = values[0]
        environment = actual.get("Config", {}).get("Env", [])
        if type(environment) is not list or any(type(item) is not str for item in environment):
            return False
        return (
            actual.get("Id") == image["config"]
            and actual.get("Os") == "linux"
            and actual.get("Architecture") == "amd64"
            and (
                image["pg_major"] is None
                or [item for item in environment if item.startswith("PG_MAJOR=")]
                == ["PG_MAJOR=" + image["pg_major"]]
            )
        )
    except (ValueError, TypeError, AttributeError):
        return False


def prepare(names: list[str], *, execute=run, now=time.monotonic) -> dict:
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "amd64"}:
        raise AcquisitionError("unsupported-runner-platform")
    if not names or len(names) != len(set(names)) or any(name not in IMAGES for name in names):
        raise AcquisitionError("invalid-required-image-set")
    deadline = now() + TOTAL_SECONDS
    rows = []

    def invoke(*args: str) -> subprocess.CompletedProcess:
        remaining = deadline - now() - CLEANUP_SECONDS
        if remaining <= 0:
            raise AcquisitionError("acquisition-deadline")
        return execute(["docker", "image", *args], min(120, remaining))

    for name in names:
        image = IMAGES[name]
        row = {"image": name, "alias": image["alias"], "state": "unknown", "attempted": []}
        rows.append(row)
        try:
            if matching_image(invoke("inspect", image["alias"]), image):
                row["state"] = "compatible-local"
                continue
            # Each path names the SAME immutable manifest; neither a tag change
            # nor a successful command can substitute for actual content checks.
            for endpoint in ("mirror.gcr.io/", "docker.io/"):
                source = endpoint + image["repository"] + "@" + image["manifest"]
                row["attempted"].append(
                    "public-mirror" if endpoint.startswith("mirror") else "upstream"
                )
                result = invoke("pull", "--platform=linux/amd64", source)
                if result.returncode != 0:
                    continue
                if not matching_image(invoke("inspect", source), image):
                    raise AcquisitionError("pulled-content-incompatible")
                if invoke("tag", source, image["alias"]).returncode != 0:
                    raise AcquisitionError("alias-install-failed")
                if not matching_image(invoke("inspect", image["alias"]), image):
                    raise AcquisitionError("installed-alias-incompatible")
                row["state"] = (
                    "verified-mirror" if endpoint.startswith("mirror") else "verified-upstream"
                )
                break
            else:
                raise AcquisitionError("required-image-unavailable")
        except (OSError, subprocess.SubprocessError, AcquisitionError) as error:
            kind = (
                str(error)
                if isinstance(error, AcquisitionError)
                else "docker-command-unavailable-or-timeout"
            )
            row.update(state="failed", failure_kind=kind)
            raise AcquisitionError(json.dumps({"complete": False, "images": rows})) from None
    return {"complete": True, "images": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image", action="append", choices=("postgres", "owner-browser"), required=True
    )
    parser.add_argument("--ryuk", action="store_true", help="preload; never enables/disables Ryuk")
    args = parser.parse_args()
    try:
        result = prepare([*args.image, *(["ryuk"] if args.ryuk else [])])
        print(json.dumps(result, sort_keys=True))
        return 0
    except AcquisitionError as error:
        # Only the fixed diagnostic constructed above, never underlying Docker
        # exception text, credential config, progress or HTTP error arguments.
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
