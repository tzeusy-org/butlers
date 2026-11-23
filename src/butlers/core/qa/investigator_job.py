"""The per-attempt QA investigator Job template (bu-ejow5p.11, RFC 0015 isolation boundary).

One pure function, :func:`build_investigator_job`, renders the Kubernetes Job a QA investigation
or review follow-up runs in. It is the single source of the pod spec: the dispatcher launches it,
the chart's ValidatingAdmissionPolicy pins the same invariants, and the isolation verifier probes
exactly this template with only the container command swapped.

What the pod boundary gives, and what this template spends it on:

- a fixed non-root UID (the daemon runs as UID 0), no privilege escalation, every capability
  dropped, a read-only root filesystem and the runtime-default seccomp profile;
- no service-account token, no service links, no host PID/IPC/network namespaces;
- the only writable state is an ``emptyDir`` HOME and ``/tmp`` plus the exchange claim, mounted at
  ``subPath attempts/<attempt_uuid>`` so the pod sees its own attempt directory and nothing else;
- no secret, projected, hostPath or ``valueFrom`` source anywhere, and an environment of plain
  non-secret values (the model path is a proxy URL, never a key);
- a kernel-enforced deadline (``activeDeadlineSeconds``), ``backoffLimit`` 0 and a deterministic
  Job name, so a retry of ``create`` adopts the existing Job instead of launching a second one.

Nothing here talks to a cluster or reads a credential; the output is plain JSON-safe data.
"""

from __future__ import annotations

import copy
import re
import uuid
from collections.abc import Sequence
from typing import Any, Final

#: Component label value shared with the chart (selectors, NetworkPolicies, the admission policy).
INVESTIGATOR_COMPONENT: Final[str] = "qa-investigator"
INVESTIGATOR_SERVICE_ACCOUNT: Final[str] = "qa-investigator"
#: Fixed non-root UID/GID of the investigator process. The daemon is UID 0.
INVESTIGATOR_UID: Final[int] = 10001
CONTAINER_NAME: Final[str] = "investigator"
#: Where the attempt directory is mounted, and the working directory of the agent.
WORKSPACE_MOUNT: Final[str] = "/workspace"
HOME_MOUNT: Final[str] = "/home/investigator"
TMP_MOUNT: Final[str] = "/tmp"  # noqa: S108 - an emptyDir inside the pod, not the host's /tmp
EXCHANGE_SUBPATH_PREFIX: Final[str] = "attempts/"
#: What the chart's admission policy accepts for the exchange ``subPath``.
EXCHANGE_SUBPATH_PATTERN: Final[str] = (
    r"^attempts/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
#: Limits every investigator pod carries (the admission policy requires cpu, memory and
#: ephemeral-storage limits). Overridable per call; never absent.
DEFAULT_RESOURCES: Final[dict[str, dict[str, str]]] = {
    "requests": {"cpu": "250m", "memory": "512Mi", "ephemeral-storage": "256Mi"},
    "limits": {"cpu": "2", "memory": "4Gi", "ephemeral-storage": "2Gi"},
}
SCRATCH_SIZE_LIMIT: Final[str] = "1Gi"
MIN_DEADLINE_S: Final[int] = 60
MAX_DEADLINE_S: Final[int] = 4 * 60 * 60
ATTEMPT_LABEL: Final[str] = "butlers.io/qa-attempt"
CONTAINMENT_LABEL: Final[str] = "butlers.io/qa-containment"
#: The only environment names the template sets; all values are non-secret configuration.
ALLOWED_ENV_NAMES: Final[frozenset[str]] = frozenset(
    {"HOME", "TMPDIR", "PATH", "ANTHROPIC_BASE_URL", "GIT_TERMINAL_PROMPT", "LANG"}
)
_JOB_NAME_PREFIX: Final[str] = "qa-"
_IMAGE_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
_SAFE_ENV_VALUE = re.compile(r"^[ -~]{0,2048}$")


class InvestigatorJobError(ValueError):
    """The requested Job cannot be built without weakening the boundary."""


def job_name(attempt_id: uuid.UUID) -> str:
    """Deterministic Job name for an attempt: ``qa-<uuid>`` (well under the 63-char limit)."""
    return f"{_JOB_NAME_PREFIX}{attempt_id}"


def exchange_subpath(attempt_id: uuid.UUID) -> str:
    return f"{EXCHANGE_SUBPATH_PREFIX}{attempt_id}"


def _env(name: str, value: str) -> dict[str, str]:
    if name not in ALLOWED_ENV_NAMES:
        raise InvestigatorJobError(f"environment name {name!r} is not allowed in the investigator")
    if not _SAFE_ENV_VALUE.fullmatch(value):
        raise InvestigatorJobError(f"environment value for {name} is not printable ASCII")
    return {"name": name, "value": value}


#: Every investigator container starts under the egress gate (butlers.core.qa.egress_gate): the
#: CNI programs a NetworkPolicy after the pod starts, so the real command runs only once the
#: cluster's own API server has been refused several times in a row. ``exec`` keeps PID 1.
EGRESS_GATE_MODULE = "butlers.core.qa.egress_gate"


def gated_command(command: Sequence[str]) -> list[str]:
    return ["python", "-I", "-m", EGRESS_GATE_MODULE, "--", *command]


def build_investigator_job(
    *,
    attempt_id: uuid.UUID,
    release: str,
    namespace: str,
    image: str,
    exchange_claim: str,
    command: Sequence[str],
    deadline_s: int,
    model_proxy_url: str | None = None,
    containment: str = "pod",
    resources: dict[str, dict[str, str]] | None = None,
    working_dir: str = WORKSPACE_MOUNT,
) -> dict[str, Any]:
    """Render the Job manifest for one attempt (initial investigation or review follow-up).

    ``command`` is the container command: the agent CLI in production, the pinned probe in the
    isolation verifier. Everything else is fixed by this template. ``image`` must be the exact
    image the daemon runs, by digest (the admission policy compares it verbatim);
    ``model_proxy_url`` is the credential-injecting proxy the investigator reaches, never a key.
    """
    if not command:
        raise InvestigatorJobError("a container command is required")
    if working_dir != WORKSPACE_MOUNT and not working_dir.startswith(WORKSPACE_MOUNT + "/"):
        raise InvestigatorJobError("working_dir must be inside the workspace mount")
    if not MIN_DEADLINE_S <= deadline_s <= MAX_DEADLINE_S:
        raise InvestigatorJobError(
            f"deadline_s must be between {MIN_DEADLINE_S} and {MAX_DEADLINE_S} seconds"
        )
    if not _IMAGE_DIGEST.search(image):
        raise InvestigatorJobError("image must be pinned by a sha256 digest")

    name = job_name(attempt_id)
    labels = {
        "app.kubernetes.io/component": INVESTIGATOR_COMPONENT,
        "app.kubernetes.io/instance": release,
        "app.kubernetes.io/part-of": "butlers",
        ATTEMPT_LABEL: str(attempt_id),
        CONTAINMENT_LABEL: containment,
    }
    env = [
        _env("HOME", HOME_MOUNT),
        _env("TMPDIR", TMP_MOUNT),
        _env("PATH", "/usr/local/bin:/usr/bin:/bin"),
        _env("LANG", "C.UTF-8"),
        _env("GIT_TERMINAL_PROMPT", "0"),
    ]
    if model_proxy_url is not None:
        env.append(_env("ANTHROPIC_BASE_URL", model_proxy_url))

    container: dict[str, Any] = {
        "name": CONTAINER_NAME,
        "image": image,
        "command": gated_command(command),
        "workingDir": working_dir,
        "env": env,
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "runAsNonRoot": True,
            "runAsUser": INVESTIGATOR_UID,
            "runAsGroup": INVESTIGATOR_UID,
            "capabilities": {"drop": ["ALL"]},
        },
        "volumeMounts": [
            {"name": "home", "mountPath": HOME_MOUNT},
            {"name": "tmp", "mountPath": TMP_MOUNT},
            {
                "name": "exchange",
                "mountPath": WORKSPACE_MOUNT,
                "subPath": exchange_subpath(attempt_id),
            },
        ],
    }
    container["resources"] = copy.deepcopy(resources or DEFAULT_RESOURCES)

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "namespace": namespace, "labels": dict(labels)},
        "spec": {
            "backoffLimit": 0,
            "completions": 1,
            "parallelism": 1,
            "activeDeadlineSeconds": deadline_s,
            "template": {
                "metadata": {"labels": dict(labels)},
                "spec": {
                    "restartPolicy": "Never",
                    "serviceAccountName": INVESTIGATOR_SERVICE_ACCOUNT,
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "hostPID": False,
                    "hostIPC": False,
                    "hostNetwork": False,
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": INVESTIGATOR_UID,
                        "runAsGroup": INVESTIGATOR_UID,
                        "fsGroup": INVESTIGATOR_UID,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [container],
                    "volumes": [
                        {"name": "home", "emptyDir": {"sizeLimit": SCRATCH_SIZE_LIMIT}},
                        {"name": "tmp", "emptyDir": {"sizeLimit": SCRATCH_SIZE_LIMIT}},
                        {
                            "name": "exchange",
                            "persistentVolumeClaim": {"claimName": exchange_claim},
                        },
                    ],
                },
            },
        },
    }
