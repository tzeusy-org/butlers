#!/usr/bin/env python3
"""Read-only D3 source admission for the session host's credential composition.

Inspect only schema and variable NAMES, never mount contents or secret values.
This guard does not prove an image, a deployed pod or a kernel network policy.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

_PREFIX = [
    "/usr/bin/env",
    "-i",
    "PATH=/usr/local/bin:/usr/bin:/bin",
    "HOME=/tmp",
    "/app/.venv/bin/python",
    "-m",
    "butlers.session_host",
]


def compose_errors(document: dict) -> list[str]:
    errors = []
    for name in ("session-host", "session-egress"):
        service = document.get("services", {}).get(name, {})
        if not service or service.get("entrypoint", [])[:7] != _PREFIX:
            errors.append(f"{name}: environment_not_closed")
        for key in ("env_file", "environment", "secrets", "devices", "volumes_from", "extra_hosts"):
            if service.get(key):
                errors.append(f"{name}: ambient_authority")
        if service.get("privileged") or service.get("pid") or service.get("network_mode"):
            errors.append(f"{name}: isolation_bypass")
        allowed = (
            {"/etc/session-host/host.json", "/run/session-proofs", "/run/session-tls"}
            if name == "session-host"
            else {"/etc/session-host/gateway.json"}
        )
        mounted = set()
        for volume in service.get("volumes", []):
            # The committed overlay uses only bounded, explicit read-only binds.
            if type(volume) is not str or not volume.endswith(":ro"):
                errors.append(f"{name}: unreviewed_mount")
                continue
            target = volume.rsplit(":", 2)[-2]
            mounted.add(target)
            if target not in allowed:
                errors.append(f"{name}: unreviewed_mount")
        if mounted != allowed:
            errors.append(f"{name}: mount_set_changed")
    host = document.get("services", {}).get("session-host", {})
    ordinary = ["/tmp:rw,nosuid,nodev,size=256m"]
    private_auth = ordinary + ["/run/session-credentials:rw,nosuid,nodev,noexec,size=1m,mode=0700"]
    if host.get("read_only") is not True or host.get("tmpfs") not in (ordinary, private_auth):
        errors.append("session-host: staging_volume_unavailable")
    isolated = document.get("networks", {}).get("session-isolated", {})
    if (
        set(host.get("networks", {})) != {"session-isolated"}
        or not isolated.get("internal")
        or isolated.get("driver_opts", {}).get("com.docker.network.bridge.gateway_mode_ipv4")
        != "isolated"
    ):
        errors.append("session-host: direct_network_route")
    return errors


def kubernetes_errors(documents: list[dict]) -> list[str]:
    errors = []
    seen = set()
    for document in documents:
        if document.get("kind") != "Deployment":
            continue
        name = document.get("metadata", {}).get("name")
        if name not in {"session-host", "session-egress"}:
            continue
        seen.add(name)
        pod = document["spec"]["template"]["spec"]
        if (
            pod.get("automountServiceAccountToken") is not False
            or pod.get("enableServiceLinks") is not False
        ):
            errors.append(f"{name}: ambient_platform_authority")
        for container in pod.get("containers", []) + pod.get("initContainers", []):
            if container.get("command", [])[:7] != _PREFIX:
                errors.append(f"{name}: environment_not_closed")
            if container.get("env") or container.get("envFrom"):
                errors.append(f"{name}: ambient_authority")
        for volume in pod.get("volumes", []):
            if "secret" in volume and not (name == "session-host" and volume["name"] == "tls"):
                errors.append(f"{name}: unreviewed_secret_mount")
            if any(kind in volume for kind in ("hostPath", "persistentVolumeClaim", "projected")):
                errors.append(f"{name}: unreviewed_mount")
        if name == "session-host":
            container = next(
                (row for row in pod.get("containers", []) if row.get("name") == name), {}
            )
            mounts = container.get("volumeMounts", [])
            volume = next((row for row in pod.get("volumes", []) if row.get("name") == "stage"), {})
            if (
                {"name": "stage", "mountPath": "/tmp"} not in mounts
                or volume.get("emptyDir") != {"medium": "Memory", "sizeLimit": "256Mi"}
                or container.get("securityContext", {}).get("readOnlyRootFilesystem") is not True
            ):
                errors.append("session-host: staging_volume_unavailable")
    if seen != {"session-host", "session-egress"}:
        errors.append("session-host: deployment_missing")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose", type=Path, default=Path("docker-compose.session-host.yml"))
    parser.add_argument("--rendered-kubernetes", type=Path)
    args = parser.parse_args()
    errors = compose_errors(yaml.safe_load(args.compose.read_text()))
    if args.rendered_kubernetes:
        errors += kubernetes_errors(
            [d for d in yaml.safe_load_all(args.rendered_kubernetes.read_text()) if d]
        )
    for error in errors:
        print(error)  # Closed reasons only, never supplied values or paths.
    if not errors:
        print("session-host credential composition: PASS (source only)")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
