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


@pytest.mark.parametrize("kind", ["job", "pod"])
def test_policy_pins_each_isolation_invariant(on, kind):
    text = " ".join(v["expression"] for v in _validations(on, kind)["validations"])
    for needle in (
        "automountServiceAccountToken == false",
        "hostNetwork",
        "hostPID",
        "hostIPC",
        "containers.size() == 1",
        "initContainers",
        "ephemeralContainers",
        "seccompProfile.type == 'RuntimeDefault'",
        f"runAsUser == {ij.INVESTIGATOR_UID}",
        "allowPrivilegeEscalation == false",
        "readOnlyRootFilesystem == true",
        "capabilities.drop == ['ALL']",
        "privileged",
        "envFrom",
        "valueFrom",
        "has(v.emptyDir)",
        "persistentVolumeClaim.claimName == 'butlers-qa-exchange'",
        "m.subPath.matches(",
        "c.image ==",
    ):
        assert needle in text, needle


def test_policy_subpath_regex_and_name_match_the_builder(on):
    text = " ".join(v["expression"] for v in _validations(on, "pod")["validations"])
    assert ij.EXCHANGE_SUBPATH_PATTERN in text.replace("\\", "")
    job_text = " ".join(v["expression"] for v in _validations(on, "job")["validations"])
    sample = ij.job_name(uuid.uuid4())
    assert "'^qa-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'" in job_text
    assert len(sample) <= 63
    assert "backoffLimit == 0" in job_text and "activeDeadlineSeconds <= 14400" in job_text
    assert ij.MAX_DEADLINE_S == 14400


def test_policy_image_is_the_exact_daemon_image(on):
    text = " ".join(v["expression"] for v in _validations(on, "pod")["validations"])
    daemon = _named(on, "Deployment", "butlers-up")["spec"]["template"]["spec"]["containers"][0]
    assert f"c.image == '{daemon['image']}'" in text


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
