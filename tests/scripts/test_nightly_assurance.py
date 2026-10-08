"""Cross-boundary nightly assurance contract, using disposable software executables.

REQ-nightly-ci-assurance-001, REQ-nightly-ci-assurance-002,
REQ-nightly-ci-assurance-003, REQ-nightly-ci-assurance-004,
REQ-nightly-ci-assurance-005. Real PG, kernel, host adoption and scheduled nights
are independent species; this test never claims those outcomes.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from butlers.nightly_assurance import (
    BASE_VARIANTS,
    EXACT_IMAGE_MANIFEST,
    REPOSITORY,
    WORKFLOW,
    EvidenceUnavailable,
    RunIdentity,
    assess,
    atomic_json,
    digest,
    read_json,
    second_red,
    validate_export,
)
from butlers.nightly_github import marker_body, parse_marker
from butlers.testing.nightly_evidence import safe_manifest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _assessment(
    identity: RunIdentity,
    *,
    conclusion="success",
    evidence_missing=False,
    failed=False,
    status="completed",
    corrupt=None,
):
    official = {
        "id": identity.run_id,
        "run_attempt": identity.attempt,
        "head_sha": identity.head,
        "event": identity.event,
        "head_branch": identity.ref,
        "created_at": f"{identity.night}T02:00:00Z",
        "path": WORKFLOW,
        "repository": {"full_name": REPOSITORY},
        "status": status,
        "conclusion": conclusion,
    }
    names = ("nightly", "faketime-matrix (+45d)", "faketime-matrix (+120d)", "exact-image-sandbox")
    jobs = [
        {
            "name": name,
            "status": "completed",
            "conclusion": "failure" if failed and n == 1 else "success",
        }
        for n, name in enumerate(names)
    ]
    manifest = list(
        safe_manifest(
            [
                "tests/example.py::TestClock::test_clock[synthetic-secret]",
                "tests/example.py::TestClock::test_clock[sensitive-other]",
            ]
        ).values()
    )
    evidence = {
        variant: {
            **identity.document(),
            "variant": variant,
            "manifest": sorted(EXACT_IMAGE_MANIFEST) if variant == "exact-image" else manifest,
            "manifest_digest": digest(
                sorted(EXACT_IMAGE_MANIFEST) if variant == "exact-image" else manifest
            ),
            "controller_complete": True,
            "clock_conformance_verified": True,
            "exit_code": 1 if failed and n == 1 else 0,
            "outcomes": {
                node: "FAILED" if failed and n == 1 and i == 0 else "PASSED"
                for i, node in enumerate(
                    sorted(EXACT_IMAGE_MANIFEST) if variant == "exact-image" else manifest
                )
            },
        }
        for n, variant in enumerate(BASE_VARIANTS)
    }
    kernel = evidence["exact-image"]
    if corrupt == "kernel-skips":
        kernel["outcomes"] = dict.fromkeys(kernel["manifest"], "SKIPPED")
    elif corrupt == "kernel-missing":
        kernel["manifest"].pop()
        kernel["outcomes"] = dict.fromkeys(kernel["manifest"], "PASSED")
    elif corrupt == "kernel-extra":
        kernel["manifest"].append("tests/example.py::test_replacement::case-1")
        kernel["outcomes"] = dict.fromkeys(kernel["manifest"], "PASSED")
    elif corrupt == "kernel-duplicate":
        kernel["manifest"].append(kernel["manifest"][0])
    elif corrupt == "kernel-replacement":
        kernel["manifest"] = ["tests/example.py::test_replacement::case-1"]
        kernel["outcomes"] = dict.fromkeys(kernel["manifest"], "PASSED")
    elif corrupt == "ordinary-skip":
        evidence["schema"]["outcomes"][manifest[0]] = "SKIPPED"
    kernel["manifest_digest"] = digest(kernel["manifest"])
    if corrupt == "clock":
        evidence["offset-45"]["clock_conformance_verified"] = False
    if corrupt == "identity":
        evidence["offset-45"]["head"] = "f" * 40
    if corrupt == "outcome":
        evidence["offset-45"]["outcomes"][manifest[0]] = []
    if corrupt == "manifest":
        evidence["offset-45"]["manifest"] = ["tests/../secret.py::test_value::case-1"]
    if evidence_missing:
        evidence.pop("offset-120")
    return assess(
        identity,
        official_run=official,
        official_jobs=jobs,
        evidence=evidence,
        folded_required=False,
    )


def test_nightly_evidence_replay_privacy_and_host_command_boundary(tmp_path, monkeypatch):
    """A missing producer cannot hide red, and replay/lost ACK cannot duplicate work."""
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    runner = _load_script("run_nightly_assurance")
    clock = {"faketime": "@2026-10-01 12:34:50", "library_sha256": "a" * 64}
    from butlers.testing.nightly_evidence import neutralized_qa_cron

    qa_source = ROOT / "tests/core/test_core_scheduler.py"
    qa_snapshot = tmp_path / "historical/core_scheduler_PR4310_cron_only.py"
    qa_code, qa_witness = neutralized_qa_cron(qa_source, qa_snapshot)
    restored = qa_snapshot.read_text().replace(
        'schedule_create(owner, "qa-patrol", "*/1 * * * *", "qa-patrol")',
        'schedule_create(owner, "qa-patrol", "0 0 1 1 *", "qa-patrol")',
    )
    assert restored == qa_source.read_text() and qa_witness["changed_literals"] == 1
    assert qa_code.co_filename == str(qa_snapshot)
    assert qa_witness["source_sha256"] != qa_witness["snapshot_sha256"]
    process = {
        "wall_at_start": "2026-10-01T12:34:51+00:00",
        "monotonic_start": 11.0,
        "monotonic_delta": 0.02,
        "monotonic_unshifted": True,
        "library_sha256": "a" * 64,
    }
    witness = {"process_clocks": {"gw0": process}, "expected_clock_processes": ["gw0"]}
    assert runner.process_clocks_verified(witness, clock, started=10.0, ended=12.0)
    for malformed in (
        None,
        [],
        {},
        {**witness, "process_clocks": []},
        {**witness, "expected_clock_processes": ["gw0", "gw0"]},
        {**witness, "process_clocks": {"gw0": []}},
    ):
        assert not runner.process_clocks_verified(malformed, clock, started=10.0, ended=12.0)
    for key, value in (
        ("monotonic_start", True),
        ("monotonic_delta", float("nan")),
        ("wall_at_start", "not-a-time"),
        ("wall_at_start", "2026-10-01T12:34:51"),
        ("library_sha256", "b" * 64),
        ("monotonic_unshifted", False),
    ):
        altered = {**witness, "process_clocks": {"gw0": {**process, key: value}}}
        assert not runner.process_clocks_verified(altered, clock, started=10.0, ended=12.0)
    # Missing preflight library reaches a real closed receipt and exit 2 without
    # launching pytest. This proves the producer's external failure finalizer.
    refused = tmp_path / "preflight-refused"
    refusal_identity = RunIdentity(99, 1, "a" * 40, "workflow_dispatch", "agent/test", "2026-10-01")
    assert runner.run(identity=refusal_identity, variant="offset-45", output=refused) == 2
    refusal = read_json(refused / "receipt.json")
    assert (
        refusal["controller_complete"] is False and refusal["clock_conformance_verified"] is False
    )
    assert refusal["unavailable"] == ["clock-preflight-unavailable"]
    first = RunIdentity(100, 1, "a" * 40, "schedule", "main", "2026-10-01")
    second = RunIdentity(101, 1, "b" * 40, "schedule", "main", "2026-10-02")
    green = _assessment(second)
    assert green.state == "green" and not green.failures and not green.unavailable
    # Official success/exit0/full completion cannot credit skipped or substituted
    # kernel proofs. Legitimate named skips in the ordinary corpus remain valid.
    assert _assessment(second, corrupt="ordinary-skip").state == "green"
    for species in (
        "kernel-skips",
        "kernel-missing",
        "kernel-extra",
        "kernel-duplicate",
        "kernel-replacement",
    ):
        invalid_kernel = _assessment(second, corrupt=species)
        assert invalid_kernel.state != "green" and invalid_kernel.unavailable
    red1 = _assessment(first, conclusion="failure", failed=True)
    red2 = _assessment(second, conclusion="failure", evidence_missing=True)
    assert red2.state == "red" and red2.workflow_failure and red2.unavailable
    assert second_red([red1], red2)
    assert red1.key != red2.key  # Changed failure sets still form two adjacent reds.
    assert not second_red([_assessment(first)], red2)
    gap = RunIdentity(102, 1, "b" * 40, "schedule", "main", "2026-10-03")
    assert not second_red([red1], _assessment(gap, conclusion="failure", failed=True))
    rerun = RunIdentity(100, 2, "a" * 40, "schedule", "main", "2026-10-01")
    assert not second_red([red1, _assessment(rerun, status="in_progress", conclusion=None)], red2)
    canary = RunIdentity(103, 1, "a" * 40, "workflow_dispatch", "agent/bu-test", "2026-10-02")
    assert not second_red([red1], _assessment(canary, conclusion="failure", failed=True))
    assert _assessment(second, status="completed", conclusion="cancelled").state == "unknown"
    assert _assessment(second, evidence_missing=True).state == "unknown"
    for corrupt in ("clock", "identity", "outcome", "manifest"):
        bad = _assessment(second, corrupt=corrupt)
        assert bad.state == "unknown" and bad.unavailable
    assert _assessment(second, status="in_progress", conclusion=None).completed is False
    # Malformed scalar/list identities and outcomes remain unavailable; they
    # cannot crash a consumer into treating an empty export as all-clear.
    for arguments in (
        (True, 1, "a" * 40, "schedule", "main", "2026-10-01"),
        (1, 1, "a" * 40, [], "main", "2026-10-01"),
        (1, 1, "a" * 40, "schedule", "agent/forged", "2026-10-01"),
    ):
        with pytest.raises(EvidenceUnavailable):
            RunIdentity(*arguments)
    from butlers.nightly_assurance import Assessment

    large = Assessment(
        first,
        "red",
        tuple(f"offset-45:tests/example.py::test_clock::case-{n}:FAILED" for n in range(10000)),
        (),
        True,
        True,
    )
    assert len(marker_body(large)) < 4096
    assert parse_marker(marker_body(large))["failure_digest"] == large.failure_digest
    marker = marker_body(red1)
    assert "synthetic-secret" not in marker and "sensitive-other" not in marker
    assert parse_marker(marker)["key"] == red1.key
    with pytest.raises(EvidenceUnavailable):
        parse_marker(marker + marker)
    with pytest.raises(EvidenceUnavailable):
        parse_marker(marker.replace(REPOSITORY, "foreign/repository"))
    with pytest.raises(ValueError):
        safe_manifest(["../secrets.py::test_value[hidden]"])
    with pytest.raises(ValueError):
        safe_manifest(
            ["tests/test_value.py::test_value[user]", "tests/test_value.py::test_value[user]"]
        )

    # The real opt-in plugin runs in a subprocess. This proves selection,
    # xdist collection identity, setup-vs-call status and output scrubbing.
    import subprocess

    fixture = tmp_path / "tests" / "test_example.py"
    fixture.parent.mkdir()
    fixture.write_text(
        "import pytest\n@pytest.mark.parametrize('value',[1,2],ids=['secret-a','secret-b'])\n"
        "def test_value(value):\n    assert value == 1, 'private assertion body'\n"
    )
    evidence_path = tmp_path / "plugin.json"
    environment = {**os.environ, "BUTLERS_NIGHTLY_PYTEST_EVIDENCE": str(evidence_path)}
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            "/dev/null",
            str(fixture),
            "--rootdir",
            str(tmp_path),
            "-p",
            "butlers.testing.nightly_evidence",
            "-n",
            "2",
            "-q",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        timeout=45,
        check=False,
    )
    # The same literal advancing-clock invariant supports the explicitly
    # authorized branch frozen control and its ordinary progressing companion.
    # Normal software tests need no library; the actual nightly child inherits
    # its installed library, digest and unchanged monotonic setting.
    clock_fixture = fixture.parent / "test_clock_progress.py"
    clock_fixture.write_text(
        "import datetime,time\ndef test_wall_clock_progresses_while_monotonic_is_real():\n"
        "    wall=datetime.datetime.now(datetime.UTC);monotonic=time.monotonic();time.sleep(.05)\n"
        "    assert .03 <= time.monotonic()-monotonic <= 40\n"
        "    assert .03 <= (datetime.datetime.now(datetime.UTC)-wall).total_seconds() <= 40\n"
    )
    clock_environment = {
        **os.environ,
        "BUTLERS_NIGHTLY_PYTEST_EVIDENCE": str(tmp_path / "clock-progress.json"),
    }
    if os.environ.get("BUTLERS_NIGHTLY_FROZEN_CONFORMANCE") == "1":
        assert (
            os.environ.get("LD_PRELOAD") and os.environ.get("FAKETIME_DONT_FAKE_MONOTONIC") == "1"
        )
        clock_environment["FAKETIME"] = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        clock_environment.pop("FAKETIME_TIMESTAMP_FILE", None)
    clock_child = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            "/dev/null",
            str(clock_fixture),
            "--rootdir",
            str(tmp_path),
            "-p",
            "butlers.testing.nightly_evidence",
            "-n",
            "0",
            "-q",
        ],
        cwd=tmp_path,
        env=clock_environment,
        capture_output=True,
        timeout=45,
        check=False,
    )
    # The intentionally frozen branch must reach this genuine failure; it is
    # never skipped or counted as an ordinary/scheduled green invocation.
    assert clock_child.returncode == 0, "controlled-wall-clock-progress-failed"
    assert completed.returncode == 1
    actual = read_json(evidence_path)
    assert actual["controller_complete"] is True
    assert len(actual["manifest"]) == 2 and actual["collection_consistent"]
    assert sorted(actual["outcomes"].values()) == ["FAILED", "PASSED"]
    assert all(
        word not in evidence_path.read_text()
        for word in ("secret-a", "secret-b", "private assertion body")
    )

    # Position the same canonical transport-module patch used by the real-PG
    # helper. This is a software import/producer proof, not role/policy/SQL proof.
    import asyncio
    from unittest.mock import AsyncMock

    from butlers.jobs import nightly_assurance as runtime_job

    transport_module = importlib.import_module("butlers.tools.switchboard.notification.deliver")
    with monkeypatch.context() as transport_patch:
        transport = AsyncMock(
            return_value={
                "status": "sent",
                "notification_id": "00000000-0000-0000-0000-000000000123",
            }
        )
        transport_patch.setattr(transport_module, "deliver", transport)
        transport_patch.setattr(
            runtime_job,
            "get_approvals_policy_quiet_hours",
            AsyncMock(return_value={"enabled": True}),
        )
        transport_patch.setattr(runtime_job, "is_policy_quiet_now", lambda policy, now: False)
        transport_patch.setattr(
            runtime_job, "get_suppressing_context_signal", AsyncMock(return_value=None)
        )
        transport_patch.setattr(
            runtime_job,
            "resolve_owner_telegram_recipient",
            AsyncMock(return_value="synthetic-owner"),
        )
        delivered = asyncio.run(
            runtime_job._decision(
                None,
                {"incident_id": "bu-disposable", "episode_id": "e" * 64},
                now=datetime(2026, 10, 4, tzinfo=UTC),
            )
        )
        assert delivered["outcome"] == "delivered"
        assert delivered["notification_ref"] == "00000000-0000-0000-0000-000000000123"
        transport.assert_awaited_once()

    # A genuine disposable executable, not a mock bd response: commit then
    # lose the create ACK; next readback must find the single external_ref.
    script = _load_script("reconcile_nightly_incidents")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    db = tmp_path / "fake-bd.json"
    atomic_json(db, {"rows": [], "mutations": []})
    fake = fake_bin / "bd"
    fake.write_text(
        f"#!{sys.executable}\n"
        + """import json,os,sys
from pathlib import Path
p=Path(os.environ['NIGHTLY_FAKE_BD_DB']);d=json.loads(p.read_text());a=sys.argv[1:]
def value(flag): return a[a.index(flag)+1]
if a[0]=='list': out=[r for r in d['rows'] if r['external_ref']==value('--external-ref')]
elif a[0]=='show': out=next(r for r in d['rows'] if r['id']==a[1])
elif a[0]=='create':
 r={'id':'bu-fake'+str(len(d['rows'])+1),'external_ref':value('--external-ref'),'status':'open',
    'assignee':value('--assignee'),'title':a[1],'description':sys.stdin.read(),
    'design':'unchanged design','notes':'unchanged notes','acceptance_criteria':value('--acceptance'),
    'labels':value('--labels').split(','),'dependencies':[],'dependents':[], 'pinned':False,
    'metadata':json.loads(value('--metadata')) if '--metadata' in a else {}}
 d['rows'].append(r);d['mutations'].append('create');p.write_text(json.dumps(d))
 if os.environ.get('NIGHTLY_FAKE_ACK_LOSS')=='1': sys.exit(1)
 print(r['id']);sys.exit(0)
elif a[0]=='update':
 out=next(r for r in d['rows'] if r['id']==a[1])
 if os.environ.get('NIGHTLY_FAKE_OWNER_RACE')=='1':out['assignee']='foreign-after-read'
 if ('--if-assignee' in a and out['assignee']!=value('--if-assignee')) or ('--if-status' in a and out['status']!=value('--if-status')):
  p.write_text(json.dumps(d));sys.exit(13)
 if '--set-metadata' in a:out['metadata']['nightly']=json.loads(value('--set-metadata').split('=',1)[1])
 if '--status' in a:out['status']=value('--status')
 if os.environ.get('NIGHTLY_FAKE_CONTRACT_RACE')=='1':out['description']='externally changed contract'
 d['mutations'].append('close' if out['status']=='closed' else 'update')
elif a[0]=='close':
 out=next(r for r in d['rows'] if r['id']==a[1]);out['status']='closed';d['mutations'].append('close')
else: sys.exit(2)
p.write_text(json.dumps(d));print(json.dumps(out))
"""
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("NIGHTLY_FAKE_BD_DB", str(db))
    beads = script.BeadsCoordinator(apply=True, owner="existing-host-coordinator")
    monkeypatch.setenv("NIGHTLY_FAKE_ACK_LOSS", "1")
    # Use unavailable evidence: no invented function owner can be attributed.
    with pytest.raises(EvidenceUnavailable):
        beads.reconcile(issue=51, assessment=red2, recover=False)
    monkeypatch.delenv("NIGHTLY_FAKE_ACK_LOSS")
    rebound = beads.reconcile(issue=51, assessment=red2, recover=False)
    again = beads.reconcile(issue=51, assessment=red2, recover=False)
    assert rebound["incident_id"] == again["incident_id"] == "bu-fake1"
    assert rebound["episode_id"] == again["episode_id"]
    durable = read_json(db)
    assert durable["mutations"].count("create") == 1
    assert durable["mutations"].count("update") == 2
    recovered = beads.reconcile(issue=51, assessment=green, recover=True)
    assert recovered["status"] == "recovered"
    recurrence = beads.reconcile(issue=51, assessment=red2, recover=False)
    # A later occurrence must use a later run/night, not replay the old episode.
    later = RunIdentity(104, 1, "c" * 40, "schedule", "main", "2026-10-04")
    later_red = _assessment(later, conclusion="failure", evidence_missing=True)
    beads.reconcile(issue=51, assessment=green, recover=True)
    recurrence = beads.reconcile(issue=51, assessment=later_red, recover=False)
    assert recurrence["incident_id"] == "bu-fake1"
    assert recurrence["episode_id"] != rebound["episode_id"]
    # Fresh owner/status and whole contract/relations controls, through the
    # same disposable CLI protocol. No live tracker mutation occurs here.
    saved = read_json(db)
    for owner, status in (
        ("foreign-coordinator", "open"),
        (None, "open"),
        ("existing-host-coordinator", "unknown"),
    ):
        snapshot = read_json(db)
        row = next(row for row in snapshot["rows"] if row["id"] == "bu-fake1")
        row.update(assignee=owner, status=status)
        atomic_json(db, snapshot)
        for recover in (False, True):
            with pytest.raises(EvidenceUnavailable):
                beads.reconcile(issue=51, assessment=later_red, recover=recover)
            assert read_json(db) == snapshot
        atomic_json(db, saved)
    monkeypatch.setenv("NIGHTLY_FAKE_OWNER_RACE", "1")
    with pytest.raises(EvidenceUnavailable):
        beads.reconcile(issue=51, assessment=later_red, recover=False)
    assert read_json(db)["mutations"] == saved["mutations"]
    monkeypatch.delenv("NIGHTLY_FAKE_OWNER_RACE")
    atomic_json(db, saved)
    for key, value in (
        ("pinned", True),
        ("dependencies", [{"id": "bu-existing"}]),
        ("dependents", [{"id": "bu-existing"}]),
    ):
        snapshot = read_json(db)
        next(row for row in snapshot["rows"] if row["id"] == "bu-fake1")[key] = value
        atomic_json(db, snapshot)
        with pytest.raises(EvidenceUnavailable):
            beads.reconcile(issue=51, assessment=green, recover=True)
        assert read_json(db) == snapshot
        atomic_json(db, saved)
    monkeypatch.setenv("NIGHTLY_FAKE_CONTRACT_RACE", "1")
    with pytest.raises(EvidenceUnavailable, match="bead-contract-or-relations-changed"):
        beads.reconcile(issue=51, assessment=later_red, recover=False)
    monkeypatch.delenv("NIGHTLY_FAKE_CONTRACT_RACE")
    atomic_json(db, saved)
    cluster_id = beads.cluster("tests/example.py", red1)
    assert cluster_id is not None
    snapshot = read_json(db)
    cluster = next(row for row in snapshot["rows"] if row["id"] == cluster_id)
    cluster.update(assignee="existing-foreign-worker", status="in_progress")
    atomic_json(db, snapshot)
    assert beads.cluster("tests/example.py", red1) == cluster_id
    assert read_json(db) == snapshot
    assert beads.cluster_states[cluster_id]["classification"] == "active-foreign-owned"
    cluster["status"] = "closed"
    atomic_json(db, snapshot)
    with pytest.raises(EvidenceUnavailable):
        beads.cluster("tests/example.py", red1)
    assert read_json(db) == snapshot
    cluster["assignee"] = "existing-host-coordinator"
    atomic_json(db, snapshot)
    assert beads.cluster("tests/example.py", red1) == cluster_id
    reopened = read_json(db)
    assert next(row for row in reopened["rows"] if row["id"] == cluster_id)["status"] == "open"
    assert beads.cluster_states[cluster_id]["classification"] == "active-owned"
    assert beads.contract(
        next(row for row in reopened["rows"] if row["id"] == cluster_id)
    ) == beads.contract(cluster)
    # Restore the incident/export fixture; added ownership controls must not
    # replace any of the original replay, recovery or privacy assertions.
    atomic_json(db, saved)

    exported = {
        "version": 1,
        "repository": REPOSITORY,
        "workflow": WORKFLOW,
        "as_of": "2026-10-04T02:00:00Z",
        "incidents": [
            {
                "incident_id": recurrence["incident_id"],
                "episode_id": recurrence["episode_id"],
                "status": "open",
                "issue": 51,
                "run_id": 104,
                "attempt": 1,
                "head": "c" * 40,
                "night": "2026-10-04",
                "failure_digest": later_red.failure_digest,
            }
        ],
    }
    now = datetime(2026, 10, 4, 3, tzinfo=UTC)
    assert len(validate_export(exported, now=now)) == 1
    for bad in (
        {**exported, "as_of": "2026-10-03T02:00:00Z"},
        {**exported, "raw_test_output": "sensitive"},
        {**exported, "incidents": exported["incidents"] * 2},
    ):
        with pytest.raises(EvidenceUnavailable):
            validate_export(bad, now=now)
    for field, value in (("status", []), ("episode_id", {}), ("head", True), ("run_id", True)):
        malformed = {**exported, "incidents": [{**exported["incidents"][0], field: value}]}
        with pytest.raises(EvidenceUnavailable):
            validate_export(malformed, now=now)
    # An actual transport-shaped counterfactual proves lost labels cannot be
    # credited after successful Issue creation. This is software, not GH adoption.
    from butlers.nightly_github import GitHubEvidence, marker_document

    class Transport(GitHubEvidence):
        def __init__(self):
            super().__init__()
            self.row = None
            self.created = 0
            self.label = False
            self.omit_label = False

        def request(self, suffix, *, method="GET", body=None, binary=False):
            self.remaining -= 1
            if suffix.startswith("issues?"):
                return [] if self.row is None else [self.row]
            if suffix.startswith("labels?"):
                return [{"name": "nightly-assurance"}] if self.label else []
            if suffix == "labels" and method == "POST":
                self.label = True
                return body
            if suffix == "labels/nightly-assurance":
                return {"name": "nightly-assurance"}
            if suffix == "issues" and method == "POST":
                self.created += 1
                self.row = {
                    "number": 51,
                    "state": "open",
                    "body": body["body"],
                    "labels": [] if self.omit_label else [{"name": "nightly-assurance"}],
                }
                return self.row
            if suffix == "issues/51" and method == "PATCH":
                self.row.update(body)
                return self.row
            if suffix == "issues/51":
                return self.row
            raise AssertionError("unexpected-transport-operation")

    transport = Transport()
    assert transport.upsert_issue(red1) == 51
    assert transport.upsert_issue(red1) == 51 and transport.created == 1
    assert parse_marker(transport.row["body"]) == marker_document(red1)
    assert (
        transport.upsert_issue(green, recover=True) is None and transport.row["state"] == "closed"
    )
    denied = Transport()
    denied.omit_label = True
    with pytest.raises(EvidenceUnavailable, match="incident-label-unacknowledged"):
        denied.upsert_issue(red1)
    assert denied.created == 1  # Durable create is not a successful alert.
    atomic_json(tmp_path / "export.json", exported)
    link = tmp_path / "link.json"
    link.symlink_to(tmp_path / "export.json")
    with pytest.raises(OSError):
        read_json(link)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(EvidenceUnavailable):
        read_json(fifo)
