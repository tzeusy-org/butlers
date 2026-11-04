"""Read-only dev split/cohost admission using the exact Helm render and live carriers.

This observes deployment/pod truth, not an atomic ownership lock. Operators
must still hold competing automation and stop Compose/native competitors.
Private render, API payloads and tool errors never enter the public receipt.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

import yaml

_INSTANCE = "app.kubernetes.io/instance"
_COMPONENT = "app.kubernetes.io/component"
_ROLES = ("butlers-up", "butlers-control")
_COUNTERS = ("replicas", "readyReplicas", "updatedReplicas", "availableReplicas")
_UP = ["/app/scripts/dev_entrypoint.sh", "butlers/up", "sh", "-c"]
_CONTROL = ["/app/scripts/dev_entrypoint.sh", "butlers/control", "sh", "-c"]


class Refused(ValueError):
    """Fixed, content-blind refusal category."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise Refused(reason)


def _read(command: list[str]) -> bytes:
    result = subprocess.run(command, capture_output=True, timeout=30, check=False)
    _require(result.returncode == 0, "query-or-render-failed")
    _require(len(result.stdout) <= 8 * 1024 * 1024, "oversized-response")
    return result.stdout


def _mode(spec: dict[str, Any], role: str) -> str:
    containers = spec["containers"]
    matches = [item for item in containers if item.get("name") == role]
    _require(
        len(matches) == 1 and sum(item.get("name") in _ROLES for item in containers) == 1,
        "unknown-runtime-container",
    )
    command = matches[0].get("command")
    if role == "butlers-up":
        if command == [*_UP, "uv sync --dev && exec uv run butlers up"]:
            return "cohost"
        if command == [*_UP, "uv sync --dev && exec uv run butlers up --exclude switchboard"]:
            return "domains"
    elif command == [
        *_CONTROL,
        "uv sync --dev && exec uv run butlers control --profile KEY_ABSENT_STAGE "
        "--config roster/switchboard --session-host 0.0.0.0",
    ]:
        return "control"
    raise Refused("unknown-runtime-command")


def _carrier(deployment: dict[str, Any], role: str, release: str) -> str:
    _require(deployment["kind"] == "Deployment", "unknown-runtime-carrier")
    _require(deployment["metadata"]["name"] == role, "wrong-runtime-carrier")
    _require(deployment["metadata"]["labels"][_INSTANCE] == release, "wrong-release")
    selector = deployment["spec"]["selector"]["matchLabels"]
    _require(selector == {_INSTANCE: release, _COMPONENT: role}, "unknown-runtime-selector")
    template = deployment["spec"]["template"]
    _require(
        all(template["metadata"]["labels"].get(key) == value for key, value in selector.items()),
        "runtime-selector-mismatch",
    )
    return _mode(template["spec"], role)


def _observed(deployment: dict[str, Any]) -> bool:
    """Return proven quiescence; absent counters mean zero only after observed scale0."""
    metadata, spec, status = deployment["metadata"], deployment["spec"], deployment["status"]
    _require(not metadata.get("deletionTimestamp"), "deleting-runtime-deployment")
    generation, observed = metadata["generation"], status["observedGeneration"]
    _require(
        type(generation) is int
        and generation > 0
        and type(observed) is int
        and observed >= generation,
        "unobserved-runtime-generation",
    )
    desired = spec["replicas"]
    _require(type(desired) is int and desired in (0, 1), "invalid-runtime-replicas")
    values = []
    for name in _COUNTERS:
        value = status.get(name, 0 if desired == 0 else None)
        _require(type(value) is int and value >= 0, "invalid-runtime-status")
        values.append(value)
    _require(all(value <= values[0] for value in values[1:]), "inconsistent-runtime-status")
    unavailable = status.get("unavailableReplicas", 0)
    _require(type(unavailable) is int and unavailable >= 0, "invalid-runtime-status")
    terminating = status.get("terminatingReplicas", 0)
    _require(type(terminating) is int and terminating >= 0, "invalid-runtime-status")
    _require(terminating == 0, "terminating-runtime-replicas")
    return desired == 0 and not any([*values, unavailable, terminating])


def check(namespace: str, release: str, helm_args: list[str]) -> str:
    # Include the namespace explicitly; reject a caller override to another DB host's fleet.
    for index, argument in enumerate(helm_args):
        if argument in ("-n", "--namespace"):
            _require(
                index + 1 < len(helm_args) and helm_args[index + 1] == namespace,
                "namespace-mismatch",
            )
        elif argument.startswith("--namespace="):
            _require(argument == "--namespace=" + namespace, "namespace-mismatch")
    rendered = list(
        yaml.safe_load_all(
            _read(
                [
                    "helm",
                    "template",
                    release,
                    ".",
                    "--namespace",
                    namespace,
                    *helm_args,
                ]
            )
        )
    )
    desired = {}
    for document in rendered:
        if not document or document.get("kind") != "Deployment":
            continue
        role = document["metadata"]["name"]
        runtime_containers = {
            item.get("name") for item in document["spec"]["template"]["spec"].get("containers", [])
        } & set(_ROLES)
        _require(not runtime_containers or role in _ROLES, "unknown-rendered-runtime-carrier")
        if role in _ROLES:
            _require(role not in desired, "duplicate-runtime-carrier")
            desired[role] = _carrier(document, role, release)
    _require("butlers-up" in desired, "missing-rendered-runtime")
    target = desired["butlers-up"]
    _require(
        (target == "cohost" and "butlers-control" not in desired)
        or (target == "domains" and desired.get("butlers-control") == "control"),
        "mixed-rendered-topology",
    )

    live, modes, stopped = {}, {}, {}
    for role in _ROLES:
        payload = _read(
            [
                "kubectl",
                "-n",
                namespace,
                "get",
                "deployment",
                role,
                "--ignore-not-found",
                "-o",
                "json",
            ]
        )
        if not payload.strip():
            live[role], stopped[role] = None, True  # Successful confirmed absence.
            continue
        deployment = json.loads(payload)
        modes[role] = _carrier(deployment, role, release)
        stopped[role] = _observed(deployment)
        live[role] = deployment

    # The actual rendered release label selects old ReplicaSet and terminating
    # pods too, rather than only the current Deployment/ReplicaSet's pods.
    release_value = next(
        document["spec"]["selector"]["matchLabels"][_INSTANCE]
        for document in rendered
        if document
        and document.get("kind") == "Deployment"
        and document["metadata"]["name"] == "butlers-up"
    )
    pods_response = json.loads(
        _read(
            [
                "kubectl",
                "-n",
                namespace,
                "get",
                "pods",
                "-l",
                _INSTANCE + "=" + release_value,
                "-o",
                "json",
            ]
        )
    )
    _require(pods_response.get("kind") in ("List", "PodList"), "invalid-runtime-pod-list")
    _require(isinstance(pods_response.get("items"), list), "invalid-runtime-pod-list")
    pods = {role: [] for role in _ROLES}
    pod_modes = {}
    for pod in pods_response["items"]:
        labels = pod["metadata"].get("labels", {})
        role = labels.get(_COMPONENT)
        if role not in _ROLES:
            _require(
                not any(item.get("name") in _ROLES for item in pod["spec"]["containers"]),
                "unclassified-runtime-pod",
            )
            continue
        _require(labels.get(_INSTANCE) == release_value, "wrong-pod-release")
        _require(pod.get("kind") == "Pod", "invalid-runtime-pod")
        _require(
            pod["status"]["phase"] in ("Pending", "Running", "Succeeded", "Failed"),
            "unknown-runtime-pod-phase",
        )
        _require(not pod["metadata"].get("deletionTimestamp"), "terminating-runtime-pod")
        pod_modes.setdefault(role, set()).add(_mode(pod["spec"], role))
        pods[role].append(pod)

    if all(item is None for item in live.values()) and not any(pods.values()):
        return "fresh"
    for role in _ROLES:
        _require(len(pods[role]) <= 1, "overlapping-runtime-pods")
        if live[role] is None:
            _require(not pods[role], "orphan-runtime-pod")
        elif live[role]["status"].get("replicas", 0) > 0:
            _require(bool(pods[role]), "missing-observed-runtime-pod")
    same = (
        modes.get("butlers-up") == target
        and all(values == {modes.get(role)} for role, values in pod_modes.items())
        and (
            (target == "cohost" and stopped["butlers-control"] and not pods["butlers-control"])
            or (target == "domains" and modes.get("butlers-control") == "control")
        )
    )
    if same:
        return "same"
    _require(all(stopped.values()) and not any(pods.values()), "stop-both-runtime-roles-first")
    return "transition-stopped"


def main() -> int:
    try:
        _require(len(sys.argv) >= 4 and all(sys.argv[1:4]), "missing-deploy-arguments")
        namespace, _tag, release, *arguments = sys.argv[1:]
        if namespace != "butlers-dev":
            return 0
        if arguments[:1] == ["--"]:
            arguments.pop(0)
        outcome = check(namespace, release, arguments)
        print("dev topology preflight: PASS (" + outcome + ")")
        return 0
    except Exception as exc:
        reason = str(exc) if isinstance(exc, Refused) else "unreadable-runtime-state"
        print("dev topology preflight: STOP (" + reason + ")", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
