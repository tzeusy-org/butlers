"""bu-ejow5p.11: the per-attempt investigator Job template holds the isolation invariants."""

from __future__ import annotations

import re
import uuid

import pytest

from butlers.core.qa import investigator_job as ij

pytestmark = pytest.mark.unit

_ATTEMPT = uuid.UUID("12345678-1234-5678-1234-567812345678")


def _job(**over):
    kwargs = dict(
        attempt_id=_ATTEMPT,
        release="butlers",
        namespace="butlers-dev",
        image="registry.invalid/butlers-app:abc123",
        exchange_claim="butlers-qa-exchange",
        command=["/bin/true"],
        deadline_s=600,
    )
    kwargs.update(over)
    return ij.build_investigator_job(**kwargs)


def _pod(job):
    return job["spec"]["template"]["spec"]


def test_job_name_is_deterministic_per_attempt():
    assert _job()["metadata"]["name"] == f"qa-{_ATTEMPT}"
    assert _job()["metadata"]["name"] == _job()["metadata"]["name"]


def test_job_never_retries_and_has_a_kernel_deadline():
    spec = _job(deadline_s=900)["spec"]
    assert spec["backoffLimit"] == 0
    assert spec["activeDeadlineSeconds"] == 900
    assert _pod(_job())["restartPolicy"] == "Never"


@pytest.mark.parametrize("deadline", [0, 59, ij.MAX_DEADLINE_S + 1])
def test_deadline_outside_bounds_is_refused(deadline):
    with pytest.raises(ij.InvestigatorJobError):
        _job(deadline_s=deadline)


def test_pod_has_no_service_account_token_and_no_host_namespaces():
    pod = _pod(_job())
    assert pod["automountServiceAccountToken"] is False
    assert pod["serviceAccountName"] == "qa-investigator"
    assert pod["enableServiceLinks"] is False
    assert not (pod["hostPID"] or pod["hostIPC"] or pod["hostNetwork"])


def test_container_is_non_root_unprivileged_read_only_with_caps_dropped():
    container = _pod(_job())["containers"][0]
    sc = container["securityContext"]
    assert sc["runAsUser"] == ij.INVESTIGATOR_UID != 0
    assert sc["runAsNonRoot"] is True
    assert sc["allowPrivilegeEscalation"] is False
    assert sc["readOnlyRootFilesystem"] is True
    assert sc["capabilities"] == {"drop": ["ALL"]}
    assert _pod(_job())["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}


def test_only_the_attempt_subpath_of_the_exchange_is_mounted():
    pod = _pod(_job())
    mounts = {m["name"]: m for m in pod["containers"][0]["volumeMounts"]}
    assert mounts["exchange"]["subPath"] == f"attempts/{_ATTEMPT}"
    assert re.fullmatch(ij.EXCHANGE_SUBPATH_PATTERN, mounts["exchange"]["subPath"])
    kinds = {v["name"]: next(k for k in v if k != "name") for v in pod["volumes"]}
    assert kinds == {"home": "emptyDir", "tmp": "emptyDir", "exchange": "persistentVolumeClaim"}


def test_no_secret_source_or_value_from_anywhere_in_the_job():
    text = repr(_job(model_proxy_url="http://proxy.invalid:3110"))
    for forbidden in (
        "secretKeyRef",
        "secretName",
        "valueFrom",
        "hostPath",
        "projected",
        "envFrom",
    ):
        assert forbidden not in text


def test_environment_is_an_allowlist_of_plain_values():
    env = _pod(_job(model_proxy_url="http://proxy.invalid:3110"))["containers"][0]["env"]
    assert {e["name"] for e in env} <= ij.ALLOWED_ENV_NAMES
    assert all(set(e) == {"name", "value"} for e in env)
    assert {"name": "ANTHROPIC_BASE_URL", "value": "http://proxy.invalid:3110"} in env


def test_no_model_proxy_means_no_base_url_env():
    env = _pod(_job())["containers"][0]["env"]
    assert "ANTHROPIC_BASE_URL" not in {e["name"] for e in env}


def test_labels_carry_component_attempt_and_containment():
    job = _job(containment="pod")
    labels = job["spec"]["template"]["metadata"]["labels"]
    assert labels["app.kubernetes.io/component"] == "qa-investigator"
    assert labels[ij.ATTEMPT_LABEL] == str(_ATTEMPT)
    assert labels[ij.CONTAINMENT_LABEL] == "pod"
    assert job["metadata"]["labels"] == labels


def test_empty_command_and_untagged_image_are_refused():
    with pytest.raises(ij.InvestigatorJobError):
        _job(command=[])
    with pytest.raises(ij.InvestigatorJobError):
        _job(image="registry.invalid/butlers-app")


def test_non_printable_proxy_url_is_refused():
    with pytest.raises(ij.InvestigatorJobError):
        _job(model_proxy_url="http://proxy.invalid\nX: y")
