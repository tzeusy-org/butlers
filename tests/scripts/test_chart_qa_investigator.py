"""bu-ejow5p.11: the dark chart machinery for per-attempt QA investigator Jobs.

Rendered intent only: no cluster evaluates the admission policy here. The Python builder
(`butlers.core.qa.investigator_job`) and the policy are separate statements of one shape, so
these tests pin the constants they share and the invariants each policy validation names.
The executable proof against a real API server is the k3d verifier (bu-ejow5p.11 PR-E).
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

import celpy
import celpy.celtypes
import pytest
import yaml

from butlers.core.qa import investigator_job as ij

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed"),
]

_CHART = Path(__file__).resolve().parents[2] / "deploy" / "helm" / "butlers"
_TLS_VALUES = Path(__file__).resolve().parents[1] / "fixtures" / "egress-gateway-values.yaml"
_NS = "butlers-dev"
_ON = (
    "qaInvestigator.enabled=true",
    f"qaInvestigator.imageDigest=sha256:{'a' * 64}",
    "image.repository=registry.invalid/butlers-app",
    "egressProfiles.enabled=true",
    "servicePlaneAuth.enabled=true",
    "butlersControl.enabled=true",
    "egressProfiles.sites.database.namespace=postgres-dev",
    "egressProfiles.sites.otlp.cidr=198.51.100.9/32",
    "egressProfiles.sites.home-assistant.cidr=198.51.100.10/32",
    "egressProfiles.sites.offsite-backup.cidr=198.51.100.11/32",
    "egressProfiles.sites.kube-apiserver.cidr=198.51.100.12/32",
)


def _render(*settings: str) -> subprocess.CompletedProcess[str]:
    command = ["helm", "template", "butlers", str(_CHART), "--namespace", _NS]
    command += ["-f", str(_CHART / "values.yaml"), "-f", str(_CHART / "values.dev.yaml")]
    command += ["-f", str(_TLS_VALUES)]
    base = (
        "image.tag=t",
        "frontendImage.tag=t",
        "trackerEgress.enabled=true",
        "trackerEgress.deniedCidrs={192.0.2.1/32}",
        "egressPolicy.enabled=false",
        "connectors.telegram-user.enabled=false",
        "database.host=postgres.invalid",
    )
    for setting in (*base, *settings):
        command += ["--set", setting]
    return subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)


def _docs(*settings: str) -> list[dict]:
    result = _render(*settings)
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _named(docs, kind, name):
    found = [d for d in docs if d["kind"] == kind and d["metadata"]["name"] == name]
    assert len(found) == 1, (kind, name, [d["metadata"]["name"] for d in docs if d["kind"] == kind])
    return found[0]


@pytest.fixture(scope="module")
def on() -> list[dict]:
    return _docs(*_ON)


def test_dark_by_default_renders_nothing_of_it():
    docs = _docs()
    rendered = yaml.safe_dump(docs)
    assert "qa-investigator" not in rendered
    assert not [d for d in docs if d["kind"].startswith("ValidatingAdmissionPolicy")]


def test_enabled_renders_the_whole_machinery(on):
    kinds = {(d["kind"], d["metadata"]["name"]) for d in on}
    assert ("ServiceAccount", "qa-investigator") in kinds
    assert ("Role", "qa-investigator-launcher") in kinds
    assert ("RoleBinding", "qa-investigator-launcher") in kinds
    assert ("PersistentVolumeClaim", "butlers-qa-exchange") in kinds
    for kind in ("job", "pod"):
        assert ("ValidatingAdmissionPolicy", f"butlers-qa-investigator-{kind}") in kinds
        assert ("ValidatingAdmissionPolicyBinding", f"butlers-qa-investigator-{kind}") in kinds


def test_the_investigator_account_has_no_token_and_nothing_bound_to_it(on):
    account = _named(on, "ServiceAccount", "qa-investigator")
    assert account["automountServiceAccountToken"] is False
    for doc in on:
        if doc["kind"] in {"RoleBinding", "ClusterRoleBinding"}:
            assert all(s["name"] != "qa-investigator" for s in doc["subjects"])


def test_the_launcher_role_is_narrow_and_bound_to_the_daemon_account(on):
    role = _named(on, "Role", "qa-investigator-launcher")
    granted = {
        (g, r, v)
        for rule in role["rules"]
        for g in rule["apiGroups"]
        for r in rule["resources"]
        for v in rule["verbs"]
    }
    assert granted == {
        ("batch", "jobs", "create"),
        ("batch", "jobs", "get"),
        ("batch", "jobs", "list"),
        ("batch", "jobs", "watch"),
        ("batch", "jobs", "delete"),
        ("", "pods", "get"),
        ("", "pods", "list"),
        ("", "pods", "watch"),
        ("", "pods/log", "get"),
    }
    binding = _named(on, "RoleBinding", "qa-investigator-launcher")
    assert [(s["kind"], s["name"]) for s in binding["subjects"]] == [
        ("ServiceAccount", "butlers-up")
    ]


def test_the_exchange_claim_is_kept_on_uninstall(on):
    claim = _named(on, "PersistentVolumeClaim", "butlers-qa-exchange")
    assert claim["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"


def _validations(on, kind):
    return _named(on, "ValidatingAdmissionPolicy", f"butlers-qa-investigator-{kind}")["spec"]


@pytest.mark.parametrize("kind", ["job", "pod"])
def test_policy_judges_only_the_investigator_identity_and_fails_closed(on, kind):
    spec = _validations(on, kind)
    assert spec["failurePolicy"] == "Fail"
    condition = spec["matchConditions"][0]["expression"]
    assert "serviceAccountName == 'qa-investigator'" in condition
    assert "== 'qa-investigator'" in condition and "app.kubernetes.io/component" in condition


_IMAGE = "registry.invalid/butlers-app@sha256:" + "a" * 64
_ATTEMPT = uuid.UUID("12345678-1234-5678-1234-567812345678")
_LAUNCHER = f"system:serviceaccount:{_NS}:butlers-up"
_CONTROLLER = "system:serviceaccount:kube-system:job-controller"


def _cel_admit(on: list[dict], kind: str, obj: dict, username: str) -> list[str] | None:
    """Evaluate the rendered policy with a real CEL engine: None if not judged, else the
    messages of every validation that does not pass (an evaluation error counts as a deny,
    as it does under failurePolicy Fail)."""
    spec = _named(on, "ValidatingAdmissionPolicy", f"butlers-qa-investigator-{kind}")["spec"]
    env = celpy.Environment()
    activation = {
        "object": celpy.json_to_cel(obj),
        "request": celpy.json_to_cel({"userInfo": {"username": username}}),
    }

    def run(expression: str, extra: dict | None = None):
        ast = env.compile(" ".join(expression.split()))
        return env.program(ast).evaluate({**activation, **(extra or {})})

    for condition in spec["matchConditions"]:
        if not bool(run(condition["expression"])):
            continue
        break
    else:
        return None
    variables: dict = {}
    for variable in spec["variables"]:
        variables[variable["name"]] = run(
            variable["expression"], {"variables": celpy.json_to_cel(variables)}
        )
    failures = []
    for validation in spec["validations"]:
        try:
            ok = bool(run(validation["expression"], {"variables": _cel_map(variables)}))
        except Exception:  # noqa: BLE001 - evaluation errors deny under failurePolicy Fail
            ok = False
        if not ok:
            failures.append(validation["message"])
    return failures


def _cel_map(variables: dict):
    return celpy.celtypes.MapType({celpy.celtypes.StringType(k): v for k, v in variables.items()})


def _good_job(**over) -> dict:
    return ij.build_investigator_job(
        attempt_id=_ATTEMPT,
        release="butlers",
        namespace=_NS,
        image=_IMAGE,
        exchange_claim="butlers-qa-exchange",
        command=["/bin/true"],
        deadline_s=600,
        model_proxy_url="http://proxy.invalid:3110",
        **over,
    )


def _pod_of(job: dict) -> dict:
    template = job["spec"]["template"]
    return {
        "metadata": {"name": "qa-pod", "labels": dict(template["metadata"]["labels"])},
        "spec": template["spec"],
    }


def test_the_real_builder_output_passes_both_policies_for_launcher_and_controller(on):
    job = _good_job()
    assert _cel_admit(on, "job", job, _LAUNCHER) == []
    assert _cel_admit(on, "pod", _pod_of(job), _CONTROLLER) == []


def _mutate(path: tuple, value, *, pod: bool = False):
    def apply(job: dict) -> dict:
        target = _pod_of(job) if pod else job
        node = target
        for key in path[:-1]:
            node = node[key]
        if value is _DELETE:
            node.pop(path[-1], None)
        else:
            node[path[-1]] = value
        return target

    return apply


_DELETE = object()
_SPEC = ("spec", "template", "spec")
_C0 = (*_SPEC, "containers", 0)
# (case id, mutation of the good Job, the validation message fragment that must fail)
_BAD = [
    (
        "other-service-account",
        _mutate((*_SPEC, "serviceAccountName"), "butlers-control"),
        "own ServiceAccount",
    ),
    ("token-mounted", _mutate((*_SPEC, "automountServiceAccountToken"), True), "no token mounted"),
    ("host-network", _mutate((*_SPEC, "hostNetwork"), True), "no host namespace"),
    ("node-name", _mutate((*_SPEC, "nodeName"), "node-1"), "no host namespace"),
    (
        "tolerations",
        _mutate((*_SPEC, "tolerations"), [{"operator": "Exists"}]),
        "no host namespace",
    ),
    (
        "init-container",
        _mutate((*_SPEC, "initContainers"), [{"name": "i", "image": _IMAGE}]),
        "exactly one container",
    ),
    (
        "pod-seccomp-unconfined",
        _mutate((*_SPEC, "securityContext", "seccompProfile"), {"type": "Unconfined"}),
        "seccomp",
    ),
    ("pod-run-as-root", _mutate((*_SPEC, "securityContext", "runAsUser"), 0), "seccomp"),
    ("fs-group", _mutate((*_SPEC, "securityContext", "fsGroup"), 0), "seccomp"),
    (
        "supplemental-groups",
        _mutate((*_SPEC, "securityContext", "supplementalGroups"), [0]),
        "seccomp",
    ),
    ("privileged", _mutate((*_C0, "securityContext", "privileged"), True), "unprivileged"),
    (
        "privilege-escalation",
        _mutate((*_C0, "securityContext", "allowPrivilegeEscalation"), True),
        "unprivileged",
    ),
    (
        "added-capability",
        _mutate((*_C0, "securityContext", "capabilities", "add"), ["NET_ADMIN"]),
        "unprivileged",
    ),
    (
        "container-seccomp-unconfined",
        _mutate((*_C0, "securityContext", "seccompProfile"), {"type": "Unconfined"}),
        "unprivileged",
    ),
    (
        "writable-root",
        _mutate((*_C0, "securityContext", "readOnlyRootFilesystem"), False),
        "unprivileged",
    ),
    (
        "host-port",
        _mutate((*_C0, "ports"), [{"containerPort": 80, "hostPort": 80}]),
        "unprivileged",
    ),
    (
        "tag-not-digest",
        _mutate((*_C0, "image"), "registry.invalid/butlers-app:abc"),
        "unprivileged",
    ),
    ("other-image", _mutate((*_C0, "image"), "evil.invalid/x@sha256:" + "b" * 64), "unprivileged"),
    (
        "env-value-from",
        _mutate(
            (*_C0, "env"),
            [{"name": "HOME", "valueFrom": {"secretKeyRef": {"name": "s", "key": "k"}}}],
        ),
        "environment",
    ),
    (
        "env-unlisted-name",
        _mutate((*_C0, "env"), [{"name": "AWS_SECRET_ACCESS_KEY", "value": "x"}]),
        "environment",
    ),
    ("env-from", _mutate((*_C0, "envFrom"), [{"secretRef": {"name": "s"}}]), "environment"),
    (
        "secret-volume",
        _mutate((*_SPEC, "volumes"), [{"name": "s", "secret": {"secretName": "s"}}]),
        "volumes",
    ),
    (
        "host-path-volume",
        _mutate((*_SPEC, "volumes"), [{"name": "h", "hostPath": {"path": "/"}}]),
        "volumes",
    ),
    (
        "other-claim",
        _mutate(
            (*_SPEC, "volumes"),
            [{"name": "exchange", "persistentVolumeClaim": {"claimName": "butlers-logs"}}],
        ),
        "volumes",
    ),
    (
        "whole-exchange-mounted",
        _mutate((*_C0, "volumeMounts", 2, "subPath"), "attempts"),
        "volumes",
    ),
    (
        "other-attempt-subpath",
        _mutate((*_C0, "volumeMounts", 2, "subPath"), "attempts/" + str(uuid.uuid4())),
        "volumes",
    ),
    ("retries", _mutate(("spec", "backoffLimit"), 3), "never retries"),
    ("no-deadline", _mutate(("spec", "activeDeadlineSeconds"), _DELETE), "never retries"),
    ("name-not-attempt", _mutate(("metadata", "name"), "qa-" + str(uuid.uuid4())), "never retries"),
    ("parallelism-2", _mutate(("spec", "parallelism"), 2), "never retries"),
    ("completions-2", _mutate(("spec", "completions"), 2), "never retries"),
    (
        "service-links-on",
        _mutate((*_SPEC, "enableServiceLinks"), True),
        "no host namespace",
    ),
    (
        "pod-apparmor-unconfined",
        _mutate((*_SPEC, "securityContext", "appArmorProfile"), {"type": "Unconfined"}),
        "seccomp",
    ),
    (
        "container-apparmor-unconfined",
        _mutate((*_C0, "securityContext", "appArmorProfile"), {"type": "Unconfined"}),
        "unprivileged",
    ),
    ("no-resource-limits", _mutate((*_C0, "resources"), _DELETE), "unprivileged"),
    (
        "memory-emptydir",
        _mutate((*_SPEC, "volumes", 1, "emptyDir"), {"medium": "Memory", "sizeLimit": "1Gi"}),
        "volumes",
    ),
    ("unbounded-emptydir", _mutate((*_SPEC, "volumes", 0, "emptyDir"), {}), "volumes"),
    (
        "label-component-wrong",
        _mutate((*_SPEC[:-1], "metadata", "labels", "app.kubernetes.io/component"), "butlers-up"),
        "identity labels",
    ),
]


@pytest.mark.parametrize(("case", "mutate", "fragment"), _BAD, ids=[b[0] for b in _BAD])
def test_launcher_created_job_that_deviates_is_denied_on_the_named_invariant(
    on, case, mutate, fragment
):
    failures = _cel_admit(on, "job", mutate(_good_job()), _LAUNCHER)
    assert failures is not None and any(fragment in f for f in failures), (case, failures)
    # A mutation breaks its own invariant, not everything: errors must not mask the signal.
    assert len(failures) <= 2, (case, failures)


def test_blocker_a_launcher_cannot_create_any_other_workload_shape(on):
    """The requester, not the object's claims, decides: a Job that claims no investigator
    identity at all is still judged (and denied) when butlers-up creates it."""
    foreign = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": "innocent", "labels": {"app": "x"}},
        "spec": {
            "template": {
                "metadata": {"labels": {"app": "x"}},
                "spec": {
                    "serviceAccountName": "butlers-control",
                    "hostNetwork": True,
                    "containers": [{"name": "c", "image": "evil.invalid/x:1"}],
                    "volumes": [{"name": "h", "hostPath": {"path": "/"}}],
                },
            }
        },
    }
    failures = _cel_admit(on, "job", foreign, _LAUNCHER)
    assert failures and len(failures) >= 5
    # The same object from anyone else is none of this policy's business.
    assert _cel_admit(on, "job", foreign, "system:serviceaccount:other:ci") is None


def test_blocker_b_a_pod_claiming_the_investigator_account_must_carry_the_component_label(on):
    pod = _pod_of(_good_job())
    del pod["metadata"]["labels"]["app.kubernetes.io/component"]
    failures = _cel_admit(on, "pod", pod, _CONTROLLER)
    assert failures and any("identity labels" in f for f in failures)
    # Claiming the label without the account is denied too.
    pod = _pod_of(_good_job())
    pod["spec"]["serviceAccountName"] = "default"
    failures = _cel_admit(on, "pod", pod, _CONTROLLER)
    assert failures and any("own ServiceAccount" in f for f in failures)


def test_a_pod_of_another_workload_is_not_judged(on):
    pod = {"metadata": {"labels": {"app.kubernetes.io/component": "butlers-up"}}, "spec": {}}
    assert _cel_admit(on, "pod", pod, _CONTROLLER) is None


@pytest.mark.parametrize("name", sorted(ij.ALLOWED_ENV_NAMES))
def test_every_builder_env_name_is_admitted_by_the_policy(on, name):
    job = _good_job()
    job["spec"]["template"]["spec"]["containers"][0]["env"] = [{"name": name, "value": "x"}]
    assert _cel_admit(on, "job", job, _LAUNCHER) == []


def test_policy_names_the_requester_and_the_digest(on):
    spec = _named(on, "ValidatingAdmissionPolicy", "butlers-qa-investigator-job")["spec"]
    assert _LAUNCHER in spec["matchConditions"][0]["expression"]


def test_a_non_digest_image_value_refuses_to_render():
    result = _render(*_ON, "qaInvestigator.imageDigest=latest")
    assert result.returncode != 0 and "imageDigest" in result.stderr


def test_bindings_are_namespace_scoped_and_deny_by_default(on):
    for kind in ("job", "pod"):
        binding = _named(on, "ValidatingAdmissionPolicyBinding", f"butlers-qa-investigator-{kind}")
        assert binding["spec"]["validationActions"] == ["Deny"]
        assert binding["spec"]["matchResources"]["namespaceSelector"]["matchLabels"] == {
            "kubernetes.io/metadata.name": _NS
        }


def test_audit_action_is_selectable_and_anything_else_is_refused():
    docs = _docs(*_ON, "qaInvestigator.admissionAction=Audit")
    binding = _named(docs, "ValidatingAdmissionPolicyBinding", "butlers-qa-investigator-job")
    assert binding["spec"]["validationActions"] == ["Audit"]
    result = _render(*_ON, "qaInvestigator.admissionAction=Warn")
    assert result.returncode != 0 and "Deny or Audit" in result.stderr


@pytest.mark.parametrize(
    ("override", "message"),
    [
        (("egressProfiles.enabled=false",), "needs egressProfiles.enabled"),
        (("servicePlaneAuth.enabled=false",), "servicePlaneAuth.enabled"),
        (("qaInvestigator.uid=0",), "non-root UID"),
    ],
)
def test_unsafe_configurations_refuse_to_render(override, message):
    result = _render(*_ON, *override)
    assert result.returncode != 0 and message in result.stderr


def test_investigator_pods_are_denied_ingress_and_start_with_dns_only_egress(on):
    ingress = _named(on, "NetworkPolicy", "butlers-namespace-ingress")
    excluded = ingress["spec"]["podSelector"]["matchExpressions"][0]["values"]
    assert "qa-investigator" in excluded
    egress = _named(on, "NetworkPolicy", "butlers-egress-qa-investigator")
    assert egress["spec"]["podSelector"]["matchLabels"]["app.kubernetes.io/component"] == (
        "qa-investigator"
    )
    rules = egress["spec"]["egress"]
    assert len(rules) == 1
    assert rules[0]["to"][0]["podSelector"]["matchLabels"] == {"k8s-app": "kube-dns"}


def test_the_daemon_mounts_the_exchange_only_when_enabled(on):
    pod = _named(on, "Deployment", "butlers-up")["spec"]["template"]["spec"]
    mounts = {m["name"]: m["mountPath"] for m in pod["containers"][0]["volumeMounts"]}
    assert mounts["qa-exchange"] == "/var/lib/butlers/qa-exchange"
    volume = next(v for v in pod["volumes"] if v["name"] == "qa-exchange")
    assert volume["persistentVolumeClaim"]["claimName"] == "butlers-qa-exchange"
    off = _named(_docs(), "Deployment", "butlers-up")["spec"]["template"]["spec"]
    assert "qa-exchange" not in {v["name"] for v in off["volumes"]}


def test_the_pod_policy_also_judges_ephemeral_container_updates(on):
    policy = _named(on, "ValidatingAdmissionPolicy", "butlers-qa-investigator-pod")
    rules = policy["spec"]["matchConstraints"]["resourceRules"]
    assert any(
        "pods/ephemeralcontainers" in r["resources"] and "UPDATE" in r["operations"] for r in rules
    )
