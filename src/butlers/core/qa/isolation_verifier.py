"""Pure planning for the QA isolation verifier (bu-ejow5p.11).

``scripts/verify_qa_isolation.py`` applies what this module renders. Nothing here touches a
cluster. The probe Job is ``build_investigator_job()`` unchanged except for its container
command, so the verifier tests the exact template production uses. Each inverse control renders
that template with exactly one hardening removed; the probe check that guards the hardening must
go RED for it and PASS again for the restored template, otherwise the probe proves nothing.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from butlers.core.qa.investigator_job import (
    CONTAINER_NAME,
    INVESTIGATOR_COMPONENT,
    build_investigator_job,
)

PROBE_MODULE: Final[str] = "butlers.core.qa.isolation_probe"
#: Checks that must hold for every investigator pod (the negatives and the baseline control).
BASELINE_CHECKS: Final[tuple[str, ...]] = (
    "uid_not_root",
    "no_foreign_processes",
    "no_service_account_token",
    "root_is_read_only",
    "app_is_read_only",
    "workspace_writable",
)


def probe_command(
    *,
    refused_tcp: Sequence[str] = (),
    reachable_tcp: Sequence[str] = (),
    forbidden_path: Sequence[str] = (),
    forbidden_env: Sequence[str] = (),
    readable_path: Sequence[str] = (),
) -> list[str]:
    """The pinned probe command: ``python -I -m`` the probe module with its targets."""
    command = ["python", "-I", "-m", PROBE_MODULE]
    for flag, values in (
        ("--refused-tcp", refused_tcp),
        ("--reachable-tcp", reachable_tcp),
        ("--forbidden-path", forbidden_path),
        ("--forbidden-env", forbidden_env),
        ("--readable-path", readable_path),
    ):
        for value in values:
            command += [flag, value]
    return command


def build_probe_job(attempt_id: uuid.UUID, **job_args: Any) -> dict[str, Any]:
    """The production template with the probe as its command (``command`` must be passed).

    The containment label is ``probe`` so the daemon's orphan sweep and Job count, which select
    ``pod``, never touch a verifier's Job.
    """
    job_args.setdefault("containment", "probe")
    return build_investigator_job(attempt_id=attempt_id, **job_args)


def _pod(job: dict[str, Any]) -> dict[str, Any]:
    return job["spec"]["template"]["spec"]


def _container(job: dict[str, Any]) -> dict[str, Any]:
    return _pod(job)["containers"][0]


def _automount_on(job: dict[str, Any]) -> None:
    _pod(job)["automountServiceAccountToken"] = True


def _run_as_root(job: dict[str, Any]) -> None:
    _pod(job)["securityContext"].update(runAsUser=0, runAsNonRoot=False)
    _container(job)["securityContext"].update(runAsUser=0, runAsNonRoot=False)


def _writable_root(job: dict[str, Any]) -> None:
    _container(job)["securityContext"]["readOnlyRootFilesystem"] = False


def _host_pid(job: dict[str, Any]) -> None:
    _pod(job)["hostPID"] = True


def _no_tracker_egress_exclusion(job: dict[str, Any]) -> None:
    """Drop the component label the tracker-egress policy excludes by, so it applies again."""
    for labels in (job["metadata"]["labels"], job["spec"]["template"]["metadata"]["labels"]):
        labels["app.kubernetes.io/component"] = "qa-investigator-unexcluded"


@dataclass(frozen=True)
class InverseControl:
    """One hardening removed, and the probe check that must catch it if admission does not."""

    name: str
    mutate: Callable[[dict[str, Any]], None]
    #: The probe check that must go RED when the Job is admitted.
    expect: str


INVERSE_CONTROLS: Final[tuple[InverseControl, ...]] = (
    InverseControl("automount_on", _automount_on, "no_service_account_token"),
    InverseControl("run_as_root", _run_as_root, "uid_not_root"),
    InverseControl("writable_root", _writable_root, "root_is_read_only"),
    InverseControl("host_pid", _host_pid, "no_foreign_processes"),
    InverseControl(
        "no_tracker_egress_exclusion",
        _no_tracker_egress_exclusion,
        "tcp_refused:tracker",
    ),
)


def render_inverse(job: Mapping[str, Any], control: InverseControl) -> dict[str, Any]:
    """A deep copy of ``job`` with exactly ``control``'s hardening removed."""
    mutated = copy.deepcopy(dict(job))
    control.mutate(mutated)
    return mutated


@dataclass(frozen=True)
class ControlVerdict:
    """RED when the hardening is removed (caught by a layer), PASS once restored."""

    name: str
    red_when_removed: bool
    green_when_restored: bool
    #: ``"admission"`` (the Job was refused), ``"probe"`` (admitted, the named check failed) or
    #: ``None`` (nothing caught it: the control proves nothing).
    caught_by: str | None = None

    @property
    def ok(self) -> bool:
        return self.red_when_removed and self.green_when_restored


def _outcome(receipt: Mapping[str, Any] | None, check: str) -> str:
    if receipt is None:
        return "no_receipt"
    for row in receipt.get("checks", []):
        if row.get("check") == check:
            return str(row.get("outcome"))
    return "missing"


def judge_control(
    control: InverseControl,
    removed: Mapping[str, Any] | None,
    restored: Mapping[str, Any] | None,
    *,
    removed_refused: bool = False,
) -> ControlVerdict:
    """Score one inverse control; a missing check or receipt is never a catch.

    ``removed_refused`` is true when admission refused the mutated Job: under the default Deny
    action that is the expected catch for every control, and it counts as RED. When the Job was
    admitted (Audit mode, or a hole in the policy), the probe's named check must fail instead.
    The restored template must always produce a real receipt that passes the named check.
    """
    if removed_refused:
        caught_by: str | None = "admission"
    elif removed is not None and _outcome(removed, control.expect) == "fail":
        caught_by = "probe"
    else:
        caught_by = None
    green = restored is not None and _outcome(restored, control.expect) == "pass"
    return ControlVerdict(control.name, caught_by is not None, green, caught_by)


def baseline_verdict(receipt: Mapping[str, Any] | None) -> list[str]:
    """Names of baseline checks that did not pass on the unmodified template (empty = good)."""
    if receipt is None:
        return list(BASELINE_CHECKS)
    return [check for check in BASELINE_CHECKS if _outcome(receipt, check) != "pass"]


def canary_pod(*, name: str, namespace: str, image: str, canary_secret: str) -> dict[str, Any]:
    """A throwaway publisher stand-in: a synthetic canary file, env canary and a listener.

    The canary values are generated by the verifier and mounted from a Secret it creates and
    deletes itself; no real credential is ever used.
    """
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {"app.kubernetes.io/component": "qa-isolation-canary"},
        },
        "spec": {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "containers": [
                {
                    "name": "canary",
                    "image": image,
                    "command": [
                        "python",
                        "-I",
                        "-c",
                        "import socket,time;s=socket.socket();s.bind(('0.0.0.0',9));"
                        "s.listen(8);time.sleep(900)",
                    ],
                    "env": [
                        {
                            "name": "QA_ISOLATION_CANARY",
                            "valueFrom": {"secretKeyRef": {"name": canary_secret, "key": "value"}},
                        }
                    ],
                    "volumeMounts": [{"name": "canary", "mountPath": "/canary", "readOnly": True}],
                }
            ],
            "volumes": [{"name": "canary", "secret": {"secretName": canary_secret}}],
        },
    }


__all__ = [
    "BASELINE_CHECKS",
    "CONTAINER_NAME",
    "INVERSE_CONTROLS",
    "INVESTIGATOR_COMPONENT",
    "ControlVerdict",
    "InverseControl",
    "baseline_verdict",
    "build_probe_job",
    "canary_pod",
    "judge_control",
    "probe_command",
    "render_inverse",
]
