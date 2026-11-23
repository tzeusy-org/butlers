"""bu-ejow5p.11: K8sJobProcess against a scripted Kubernetes API (httpx.MockTransport)."""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest

from butlers.core.qa import investigator_job as ij
from butlers.core.qa.k8s_job_process import K8sJobProcess
from butlers.core.runtimes.base import JobLaunchRefused

pytestmark = pytest.mark.unit

_NS = "butlers-dev"
_ATTEMPT = uuid.UUID("12345678-1234-5678-1234-567812345678")
_IMAGE = "registry.invalid/butlers-app@sha256:" + "a" * 64


def _manifest() -> dict[str, Any]:
    return ij.build_investigator_job(
        attempt_id=_ATTEMPT,
        release="butlers",
        namespace=_NS,
        image=_IMAGE,
        exchange_claim="butlers-qa-exchange",
        command=["/bin/true"],
        deadline_s=600,
    )


class FakeApi:
    """A tiny stateful stand-in for the API server: one Job, its pods, scripted responses."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self.job_conditions: list[list[dict[str, Any]]] = [[]]
        self.pod_phases: list[list[dict[str, Any]]] = [[]]
        self.create_status = 201
        self.create_body = ""
        self.requests: list[tuple[str, str]] = []
        self.deleted: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((request.method, path))
        jobs = f"/apis/batch/v1/namespaces/{_NS}/jobs"
        if request.method == "POST" and path == jobs:
            if self.create_status in (200, 201):
                body = json.loads(request.content)
                self.jobs[body["metadata"]["name"]] = body
            return httpx.Response(self.create_status, text=self.create_body)
        if path.startswith(jobs + "/"):
            name = path.rsplit("/", 1)[1]
            if request.method == "DELETE":
                self.deleted.append(name)
                return httpx.Response(200, json={})
            job = dict(self.jobs.get(name, {}))
            if not job:
                return httpx.Response(404, text="nope")
            conditions = (
                self.job_conditions[0]
                if len(self.job_conditions) == 1
                else (self.job_conditions.pop(0))
            )
            return httpx.Response(200, json={**job, "status": {"conditions": conditions}})
        if path == f"/api/v1/namespaces/{_NS}/pods":
            pods = self.pod_phases[0] if len(self.pod_phases) == 1 else self.pod_phases.pop(0)
            return httpx.Response(200, json={"items": pods})
        return httpx.Response(500)


def _pod(phase: str, exit_code: int | None = None) -> dict[str, Any]:
    status: dict[str, Any] = {"phase": phase}
    if exit_code is not None:
        status["containerStatuses"] = [{"state": {"terminated": {"exitCode": exit_code}}}]
    return {"status": status}


def _runner(api: FakeApi) -> K8sJobProcess:
    async def no_sleep(_: float) -> None:
        return None

    client = httpx.AsyncClient(transport=httpx.MockTransport(api.handler), base_url="http://k8s")
    return K8sJobProcess(namespace=_NS, client=client, poll_interval_s=0, sleep=no_sleep)


async def test_succeeded_job_reports_exit_zero_after_the_pod_settles() -> None:
    api = FakeApi()
    api.job_conditions = [[], [{"type": "Complete", "status": "True"}]]
    api.pod_phases = [[_pod("Running")], [_pod("Succeeded", 0)]]
    outcome = await _runner(api).run(_manifest(), timeout_s=600)
    assert (outcome.state, outcome.exit_code, outcome.pod_terminal) == ("succeeded", 0, True)
    assert api.deleted == [f"qa-{_ATTEMPT}"]


async def test_failed_job_carries_the_container_exit_code() -> None:
    api = FakeApi()
    api.job_conditions = [[{"type": "Failed", "status": "True", "reason": "BackoffLimitExceeded"}]]
    api.pod_phases = [[_pod("Failed", 3)]]
    outcome = await _runner(api).run(_manifest(), timeout_s=600)
    assert (outcome.state, outcome.exit_code, outcome.reason) == (
        "failed",
        3,
        "BackoffLimitExceeded",
    )


async def test_the_egress_gate_exit_is_a_containment_failure_not_an_investigation_result() -> None:
    api = FakeApi()
    api.job_conditions = [[{"type": "Failed", "status": "True", "reason": "BackoffLimitExceeded"}]]
    api.pod_phases = [[_pod("Failed", 70)]]
    outcome = await _runner(api).run(_manifest(), timeout_s=600)
    assert (outcome.state, outcome.exit_code, outcome.reason) == (
        "failed",
        70,
        "containment_not_enforced",
    )


async def test_deadline_exceeded_is_its_own_state() -> None:
    api = FakeApi()
    api.job_conditions = [[{"type": "Failed", "status": "True", "reason": "DeadlineExceeded"}]]
    api.pod_phases = [[_pod("Failed", 137)]]
    outcome = await _runner(api).run(_manifest(), timeout_s=600)
    assert outcome.state == "deadline_exceeded"


async def test_a_pod_that_never_settles_is_reported_not_terminal() -> None:
    """Sealing must not read the exchange while the pod can still write to it."""
    api = FakeApi()
    api.job_conditions = [[{"type": "Failed", "status": "True", "reason": "DeadlineExceeded"}]]
    api.pod_phases = [[_pod("Running")]]
    runner = _runner(api)
    import butlers.core.qa.k8s_job_process as module

    original = module.POD_TERMINAL_WAIT_S
    module.POD_TERMINAL_WAIT_S = 0.0
    try:
        outcome = await runner.run(_manifest(), timeout_s=600)
    finally:
        module.POD_TERMINAL_WAIT_S = original
    assert outcome.pod_terminal is False and outcome.reason == "pod_not_terminal"


async def test_complete_job_with_a_nonzero_container_exit_is_a_failure() -> None:
    api = FakeApi()
    api.job_conditions = [[{"type": "Complete", "status": "True"}]]
    api.pod_phases = [[_pod("Succeeded", 2)]]
    assert (await _runner(api).run(_manifest(), timeout_s=600)).state == "failed"


@pytest.mark.parametrize(
    ("status", "body", "category"),
    [
        (403, "forbidden by RBAC", "forbidden"),
        (403, "pods is forbidden: exceeded quota: q", "quota_exceeded"),
        (422, "denied by admission policy", "invalid"),
        (429, "", "quota_exceeded"),
        (500, "", "api_unreachable"),
    ],
)
async def test_create_refusals_map_to_closed_categories_without_echoing_the_body(
    status, body, category
) -> None:
    api = FakeApi()
    api.create_status, api.create_body = status, body
    with pytest.raises(JobLaunchRefused) as raised:
        await _runner(api).run(_manifest(), timeout_s=600)
    assert raised.value.category == category
    assert body not in str(raised.value) or not body


async def test_a_retried_create_adopts_the_identical_existing_job() -> None:
    api = FakeApi()
    api.jobs[f"qa-{_ATTEMPT}"] = _manifest()
    api.create_status = 409
    api.job_conditions = [[{"type": "Complete", "status": "True"}]]
    api.pod_phases = [[_pod("Succeeded", 0)]]
    assert (await _runner(api).run(_manifest(), timeout_s=600)).state == "succeeded"


def _pod_spec(m):
    return m["spec"]["template"]["spec"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: _pod_spec(m)["containers"][0].update(command=["/bin/other"]),
        lambda m: _pod_spec(m)["containers"][0].update(args=["--different"]),
        lambda m: (
            _pod_spec(m)["containers"][0].setdefault("securityContext", {}).update(privileged=True)
        ),
        lambda m: _pod_spec(m).setdefault("securityContext", {}).update(runAsUser=0),
        lambda m: (
            _pod_spec(m)
            .setdefault("volumes", [])
            .append({"name": "evil", "hostPath": {"path": "/"}})
        ),
    ],
    ids=["command", "args", "container-security", "pod-security", "volumes"],
)
async def test_a_conflicting_job_under_the_same_name_is_not_adopted(mutate) -> None:
    api = FakeApi()
    other = _manifest()
    mutate(other)
    api.jobs[f"qa-{_ATTEMPT}"] = other
    api.create_status = 409
    with pytest.raises(JobLaunchRefused) as raised:
        await _runner(api).run(_manifest(), timeout_s=600)
    assert raised.value.category == "conflict"


async def test_transport_failure_is_api_unreachable() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    client = httpx.AsyncClient(transport=httpx.MockTransport(boom), base_url="http://k8s")
    with pytest.raises(JobLaunchRefused) as raised:
        await K8sJobProcess(namespace=_NS, client=client).run(_manifest(), timeout_s=600)
    assert raised.value.category == "api_unreachable"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m.update(kind="Pod"),
        lambda m: m["metadata"].update(namespace="elsewhere"),
        lambda m: m["metadata"].update(name="not-an-attempt"),
    ],
)
async def test_a_manifest_that_is_not_an_attempt_job_never_reaches_the_api(mutate) -> None:
    api = FakeApi()
    manifest = _manifest()
    mutate(manifest)
    with pytest.raises(JobLaunchRefused) as raised:
        await _runner(api).run(manifest, timeout_s=600)
    assert raised.value.category == "invalid" and api.requests == []


async def test_cancellation_deletes_the_job() -> None:
    import asyncio

    api = FakeApi()  # never terminal
    runner = _runner(api)

    async def forever(_: float) -> None:
        await asyncio.sleep(3600)

    runner._sleep = forever  # noqa: SLF001
    task = asyncio.create_task(runner.run(_manifest(), timeout_s=600))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert api.deleted and api.deleted[0] == f"qa-{_ATTEMPT}"


async def test_adoption_tolerates_server_side_defaults_on_the_stored_job() -> None:
    api = FakeApi()
    stored = _manifest()
    stored["spec"]["template"]["spec"].update(
        dnsPolicy="ClusterFirst",
        schedulerName="default-scheduler",
        terminationGracePeriodSeconds=30,
    )
    stored["spec"]["template"]["spec"]["containers"][0]["terminationMessagePath"] = "/dev/x"
    api.jobs[f"qa-{_ATTEMPT}"] = stored
    api.create_status = 409
    api.job_conditions = [[{"type": "Complete", "status": "True"}]]
    api.pod_phases = [[_pod("Succeeded", 0)]]
    assert (await _runner(api).run(_manifest(), timeout_s=600)).state == "succeeded"


async def test_pod_phase_unknown_is_unsettled() -> None:
    import butlers.core.qa.k8s_job_process as module

    api = FakeApi()
    api.job_conditions = [[{"type": "Failed", "status": "True", "reason": "DeadlineExceeded"}]]
    api.pod_phases = [[_pod("Unknown")]]
    original = module.POD_TERMINAL_WAIT_S
    module.POD_TERMINAL_WAIT_S = 0.0
    try:
        outcome = await _runner(api).run(_manifest(), timeout_s=600)
    finally:
        module.POD_TERMINAL_WAIT_S = original
    assert outcome.pod_terminal is False


async def test_a_failing_delete_does_not_mask_the_outcome_and_is_recorded() -> None:
    api = FakeApi()
    api.job_conditions = [[{"type": "Complete", "status": "True"}]]
    api.pod_phases = [[_pod("Succeeded", 0)]]
    base = api.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            raise httpx.ConnectError("gone")
        return base(request)

    async def no_sleep(_: float) -> None:
        return None

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://k8s")
    runner = K8sJobProcess(namespace=_NS, client=client, poll_interval_s=0, sleep=no_sleep)
    outcome = await runner.run(_manifest(), timeout_s=600)
    assert outcome.state == "succeeded"
    assert runner.undeleted == {f"qa-{_ATTEMPT}"}


async def test_list_attempt_jobs_selects_by_release_and_parses_terminal_state() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["selector"] = request.url.params["labelSelector"]
        items = [
            {
                "metadata": {"name": "qa-a", "labels": {"butlers.io/qa-attempt": "a"}},
                "status": {"conditions": [{"type": "Complete", "status": "True"}]},
            },
            {
                "metadata": {"name": "qa-b", "labels": {"butlers.io/qa-attempt": "b"}},
                "status": {"conditions": [{"type": "Failed", "status": "False"}]},
            },
            {"metadata": {"name": "qa-c"}},
        ]
        return httpx.Response(200, json={"items": items})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://k8s")
    jobs = await K8sJobProcess(namespace=_NS, client=client).list_attempt_jobs("rel-1")
    assert [(j.name, j.attempt_id, j.terminal) for j in jobs] == [
        ("qa-a", "a", True),
        ("qa-b", "b", False),
        ("qa-c", None, False),
    ]
    selector = seen["selector"].split(",")
    assert "app.kubernetes.io/instance=rel-1" in selector
    assert "butlers.io/qa-containment=pod" in selector
    assert "app.kubernetes.io/component=qa-investigator" in selector


async def test_an_unreadable_job_list_is_a_refusal_not_an_empty_list() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(403, text="no")),
        base_url="http://k8s",
    )
    with pytest.raises(JobLaunchRefused):
        await K8sJobProcess(namespace=_NS, client=client).list_attempt_jobs("r")
