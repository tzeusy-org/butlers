"""Run one rendered investigator Job to a terminal pod over the Kubernetes API (bu-ejow5p.11).

``K8sJobProcess.run`` implements the runtime ``JobRunner`` protocol: create the Job (adopting an
existing identical one on a retried create), poll it to a terminal condition, then wait for its pod
to be terminal before reporting, so the caller never reads the exchange while the pod can still
write to it. It speaks plain HTTPS with the daemon's own service-account token (read per request,
so rotation is honoured) and holds no other credential. Every refusal is a closed category
(``JobLaunchRefused.category``) that carries no cluster text, prompt or token.

The admission policy, not this module, is the isolation boundary: this client can only ever ask for
a Job, and the policy denies any Job that is not the pinned investigator shape.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import httpx

from butlers.core.qa.egress_gate import EXIT_NOT_ENFORCED as EGRESS_GATE_EXIT_NOT_ENFORCED
from butlers.core.runtimes.base import JobLaunchRefused, JobOutcome

logger = logging.getLogger(__name__)

API_HOST: Final[str] = "https://kubernetes.default.svc"
_SA_DIR: Final[Path] = Path("/var/run/secrets/kubernetes.io/serviceaccount")
_JOB_NAME = re.compile(r"^qa-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
#: Slack after the Job's own deadline for the control plane to notice it and the pod to stop.
DEADLINE_GRACE_S: Final[float] = 60.0
#: How long a Job-terminal pod may linger before the outcome is reported as "pod not terminal".
POD_TERMINAL_WAIT_S: Final[float] = 60.0


class _ServiceAccountAuth(httpx.Auth):
    """Bearer token re-read from the projected file on every request."""

    def __init__(self, token_path: Path) -> None:
        self._path = token_path

    def auth_flow(self, request: httpx.Request):
        try:
            token = self._path.read_text(encoding="utf-8").strip()
        except OSError:
            raise JobLaunchRefused("api_unreachable") from None
        request.headers["Authorization"] = f"Bearer {token}"
        yield request


def _refusal_for(response: httpx.Response) -> JobLaunchRefused:
    """Map an API error to a closed category without echoing any of its body."""
    status = response.status_code
    if status == 409:
        return JobLaunchRefused("conflict")
    if status in (400, 422):
        return JobLaunchRefused("invalid")
    if status == 403:
        # Quota denials arrive as 403 "exceeded quota"; everything else is an RBAC/admission no.
        text = response.text.lower()
        return JobLaunchRefused("quota_exceeded" if "exceeded quota" in text else "forbidden")
    if status == 401:
        return JobLaunchRefused("forbidden")
    if status == 429:
        return JobLaunchRefused("quota_exceeded")
    return JobLaunchRefused("api_unreachable")


class K8sJobProcess:
    """A ``JobRunner`` over the Kubernetes batch/v1 and core/v1 APIs."""

    def __init__(
        self,
        *,
        namespace: str,
        client: httpx.AsyncClient,
        poll_interval_s: float = 2.0,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self._ns = namespace
        self._client = client
        self._poll = poll_interval_s
        self._sleep = sleep
        #: Jobs whose delete failed; the restart sweep (PR-D) collects what is left.
        self.undeleted: set[str] = set()

    @classmethod
    def in_cluster(cls, *, namespace: str | None = None) -> K8sJobProcess:
        """Client for the pod's own cluster using its projected token and CA bundle."""
        try:
            ns = namespace or (_SA_DIR / "namespace").read_text(encoding="utf-8").strip()
        except OSError:
            raise JobLaunchRefused("api_unreachable") from None
        client = httpx.AsyncClient(
            base_url=API_HOST,
            verify=str(_SA_DIR / "ca.crt"),
            auth=_ServiceAccountAuth(_SA_DIR / "token"),
            timeout=httpx.Timeout(15.0),
        )
        return cls(namespace=ns, client=client)

    @property
    def namespace(self) -> str:
        return self._ns

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- API helpers -----------------------------------------------------------------------

    def _jobs(self, name: str = "") -> str:
        return f"/apis/batch/v1/namespaces/{self._ns}/jobs" + (f"/{name}" if name else "")

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._client.request(method, url, **kwargs)
        except JobLaunchRefused:
            raise
        except httpx.HTTPError:
            raise JobLaunchRefused("api_unreachable") from None

    async def _create_or_adopt(self, manifest: dict[str, Any]) -> None:
        name = manifest["metadata"]["name"]
        response = await self._request("POST", self._jobs(), json=manifest)
        if response.status_code in (200, 201):
            return
        if response.status_code != 409:
            raise _refusal_for(response)
        # A retried create names the same Job: adopt it only if it is exactly what we would have
        # created (same pod template), never another workload that happens to hold the name.
        existing = await self._request("GET", self._jobs(name))
        if existing.status_code != 200:
            raise _refusal_for(existing)
        body = existing.json()
        if _fingerprint(body) != _fingerprint(manifest):
            raise JobLaunchRefused("conflict")
        logger.info("QA investigator job adopted after a retried create")

    async def _job_terminal(self, name: str) -> tuple[str, str | None] | None:
        response = await self._request("GET", self._jobs(name))
        if response.status_code != 200:
            raise _refusal_for(response)
        for condition in response.json().get("status", {}).get("conditions", []) or []:
            if condition.get("status") != "True":
                continue
            if condition.get("type") == "Complete":
                return "succeeded", None
            if condition.get("type") == "Failed":
                reason = condition.get("reason")
                if reason == "DeadlineExceeded":
                    return "deadline_exceeded", reason
                return "failed", reason if isinstance(reason, str) else None
        return None

    async def _pods(self, name: str) -> list[dict[str, Any]]:
        response = await self._request(
            "GET",
            f"/api/v1/namespaces/{self._ns}/pods",
            params={"labelSelector": f"job-name={name}"},
        )
        if response.status_code != 200:
            raise _refusal_for(response)
        return list(response.json().get("items", []))

    async def _delete(self, name: str) -> bool:
        """Best-effort Job delete that never raises; False means it may have leaked."""
        try:
            response = await self._client.delete(
                self._jobs(name), params={"propagationPolicy": "Background"}
            )
        except Exception as exc:  # noqa: BLE001 - cleanup must not mask the attempt's outcome
            logger.warning(
                "QA investigator job delete failed (category=%s); the restart sweep collects it",
                type(exc).__name__,
            )
            self.undeleted.add(name)
            return False
        if response.status_code not in (200, 202, 404):
            logger.warning(
                "QA investigator job delete refused (status=%d); the restart sweep collects it",
                response.status_code,
            )
            self.undeleted.add(name)
            return False
        self.undeleted.discard(name)
        return True

    # -- fleet (restart sweep and concurrency) ------------------------------------------------

    async def list_attempt_jobs(self, release: str) -> list[AttemptJob]:
        """This release's attempt Jobs; a read failure raises ``JobLaunchRefused``.

        Selected by component, release instance and the ``pod`` containment label, so another
        release's Jobs and a verifier's probe Jobs (containment ``probe``) are never listed.
        """
        response = await self._request(
            "GET",
            self._jobs(),
            params={
                "labelSelector": (
                    "app.kubernetes.io/component=qa-investigator,"
                    f"app.kubernetes.io/instance={release},butlers.io/qa-containment=pod"
                )
            },
        )
        if response.status_code != 200:
            raise _refusal_for(response)
        jobs = []
        for item in response.json().get("items", []):
            meta = item.get("metadata", {})
            conditions = item.get("status", {}).get("conditions", []) or []
            terminal = any(
                c.get("type") in ("Complete", "Failed") and c.get("status") == "True"
                for c in conditions
            )
            jobs.append(
                AttemptJob(
                    name=str(meta.get("name", "")),
                    attempt_id=(meta.get("labels") or {}).get("butlers.io/qa-attempt"),
                    terminal=terminal,
                )
            )
        return jobs

    async def delete_job(self, name: str) -> bool:
        """Delete one Job; ``False`` (recorded in ``undeleted``) if the API refused."""
        return await self._delete(name)

    # -- public ------------------------------------------------------------------------------

    async def run(self, manifest: dict[str, Any], *, timeout_s: float) -> JobOutcome:
        """Create the Job, wait for a terminal Job and a terminal pod, report, and clean up."""
        name = self._validate(manifest)
        await self._create_or_adopt(manifest)
        try:
            return await self._await_outcome(name, timeout_s)
        finally:
            await asyncio.shield(self._delete(name))

    def _validate(self, manifest: dict[str, Any]) -> str:
        meta = manifest.get("metadata", {})
        name = meta.get("name", "")
        if (
            manifest.get("kind") != "Job"
            or meta.get("namespace") != self._ns
            or not _JOB_NAME.fullmatch(str(name))
        ):
            raise JobLaunchRefused("invalid")
        return str(name)

    async def _await_outcome(self, name: str, timeout_s: float) -> JobOutcome:
        loop = asyncio.get_running_loop()
        give_up = loop.time() + timeout_s + DEADLINE_GRACE_S
        terminal: tuple[str, str | None] | None = None
        while loop.time() < give_up:
            terminal = await self._job_terminal(name)
            if terminal is not None:
                break
            await self._sleep(self._poll)
        if terminal is None:
            # The control plane never reported a terminal Job: the deadline did not fire.
            return JobOutcome(state="deadline_exceeded", exit_code=None, reason="not_terminal")
        state, reason = terminal
        exit_code, pod_terminal = await self._settled_pod(name)
        if not pod_terminal:
            return JobOutcome(
                state="failed", exit_code=None, reason="pod_not_terminal", pod_terminal=False
            )
        if state == "succeeded" and exit_code not in (None, 0):
            state = "failed"
        if exit_code == EGRESS_GATE_EXIT_NOT_ENFORCED:
            # The gate refused to start the investigator: a containment failure, never a result.
            return JobOutcome(
                state="failed", exit_code=exit_code, reason="containment_not_enforced"
            )
        return JobOutcome(state=state, exit_code=exit_code, reason=reason)  # type: ignore[arg-type]

    async def _settled_pod(self, name: str) -> tuple[int | None, bool]:
        """Wait until no pod of the Job can still run; return (exit code, settled)."""
        loop = asyncio.get_running_loop()
        give_up = loop.time() + POD_TERMINAL_WAIT_S
        while True:
            pods = await self._pods(name)
            # "Unknown" means the node is unreachable: the pod may still be writing.
            live = [
                p
                for p in pods
                if p.get("status", {}).get("phase") in (None, "Pending", "Running", "Unknown")
            ]
            if not live:
                return _exit_code(pods), True
            if loop.time() >= give_up:
                return None, False
            await self._sleep(self._poll)


def _volume_shape(volume: dict[str, Any]) -> tuple[str, str, str]:
    """A volume's name, kind and claim, ignoring server-filled defaults such as ``defaultMode``."""
    kind = next((k for k in volume if k != "name"), "")
    claim = (volume.get(kind) or {}).get("claimName", "") if kind == "persistentVolumeClaim" else ""
    return (str(volume.get("name", "")), kind, str(claim))


@dataclass(frozen=True)
class AttemptJob:
    """One investigator Job as the cluster reports it."""

    name: str
    attempt_id: str | None
    terminal: bool


def _fingerprint(job: dict[str, Any]) -> dict[str, Any] | None:
    """The fields the investigator template sets, normalised for comparing a stored Job.

    The API server fills in defaults (DNS policy, scheduler, grace period, ...) on a stored Job,
    so whole-spec equality would refuse a genuine adoption. Only what the builder chooses is
    compared; the admission policy already pins the shape of anything that exists.
    """
    try:
        spec = job["spec"]
        pod = spec["template"]["spec"]
        container = pod["containers"][0]
        return {
            "attempt": job["metadata"]["labels"]["butlers.io/qa-attempt"],
            "service_account": pod["serviceAccountName"],
            "image": container["image"],
            "command": container["command"],
            "args": container.get("args"),
            "pod_security": pod.get("securityContext"),
            "container_security": container.get("securityContext"),
            "volumes": sorted(_volume_shape(v) for v in pod.get("volumes", [])),
            "working_dir": container.get("workingDir"),
            "env": sorted((e["name"], e.get("value")) for e in container.get("env", [])),
            "mounts": sorted(
                (m["name"], m["mountPath"], m.get("subPath")) for m in container["volumeMounts"]
            ),
            "deadline": spec.get("activeDeadlineSeconds"),
            "backoff": spec.get("backoffLimit"),
        }
    except (KeyError, IndexError, TypeError):
        return None


def _exit_code(pods: list[dict[str, Any]]) -> int | None:
    for pod in pods:
        for status in pod.get("status", {}).get("containerStatuses", []) or []:
            terminated = (status.get("state") or {}).get("terminated")
            if terminated and isinstance(terminated.get("exitCode"), int):
                return terminated["exitCode"]
    return None
