"""REQ-testing-053: actual branch contexts and finite source-owned mutation proof."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import condense_evidence as evidence  # noqa: E402

pytestmark = pytest.mark.unit


def _toy(tmp_path):
    root = tmp_path / "subject"
    root.mkdir()
    (root / "toy.py").write_text(
        "def reply(flag):\n    if flag:\n        return 'accepted'\n    return 'error'\n"
    )
    (root / "tests").mkdir()
    (root / "tests/test_toy.py").write_text(
        "from toy import reply\n"
        "def test_removed():\n    assert reply(True) == 'accepted'\n"
        "def test_survivor():\n    assert reply(True) == 'accepted'\n    assert reply(False) == 'error'\n"
        "def test_error_only():\n    assert reply(False) == 'error'\n"
    )
    (root / "scripts").mkdir()
    source = Path(__file__).resolve().parents[2]
    for name in ("condense_evidence.py", "check_condensation_ledger.py", "pre_push.py"):
        shutil.copy2(source / "scripts" / name, root / "scripts" / name)
    governing = root / "openspec/specs/testing/spec.md"
    governing.parent.mkdir(parents=True)
    public_contract = source / "openspec/specs/testing/spec.md"
    if "ID: REQ-testing-053" not in public_contract.read_text():
        public_contract = (
            source / "openspec/changes/executable-condensation-evidence/specs/testing/spec.md"
        )
    governing.write_bytes(public_contract.read_bytes())
    shutil.copy2(source / "uv.lock", root / "uv.lock")
    (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Proof",
            "-c",
            "user.email=proof@example.invalid",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "-qm",
            "owned toy",
        ],
        check=True,
    )
    return root


def _tracked_link_subject(root):
    assets = root / "assets"
    assets.mkdir()
    (assets / "body.txt").write_text("owned link body")
    (root / "asset-home").symlink_to("assets", target_is_directory=True)
    (root / "body-link").symlink_to("assets/body.txt")
    (root / "body-chain").symlink_to("body-link")
    (root / "source-link.py").symlink_to("toy.py")
    owner = root / "tests/test_toy.py"
    owner.write_text(
        "from pathlib import Path\n"
        + owner.read_text().replace(
            "    assert reply(True) == 'accepted'\n",
            "    assert reply(True) == 'accepted'\n"
            "    assert (Path(__file__).parents[1] / 'asset-home/body.txt').read_text() "
            "== 'owned link body'\n"
            "    assert (Path(__file__).parents[1] / 'body-chain').read_text() "
            "== 'owned link body'\n",
        )
    )
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Proof",
            "-c",
            "user.email=proof@example.invalid",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "-qm",
            "complete owned tracked links",
        ],
        check=True,
    )


def _config(removed="tests/test_toy.py::test_removed", survivor="tests/test_toy.py::test_survivor"):
    return {
        "base": "HEAD",
        "scope": ["toy.py"],
        "removed": [removed],
        "survivors": [survivor],
        "cluster": "owned-proof",
        "bead": "bu-ly3lv5.11",
        "contract": {"class": "wire", "cites": ["REQ-testing-053"]},
        "mapping": {
            removed: {"survivors": [survivor], "reason": "same branch and status contract"}
        },
    }


def test_actual_ctrace_arcs_and_removed_kills_are_retained(tmp_path, monkeypatch):
    """REQ-testing-053: real carriers admit retained kills; falsified carriers and authority refuse."""
    root = _toy(tmp_path)
    _tracked_link_subject(root)
    monkeypatch.setenv("COVERAGE_CORE", "sysmon")
    config = _config()
    before = (root / "toy.py").read_bytes()
    original_base = evidence.git(root, "rev-parse", "HEAD")
    owner = root / "tests/test_toy.py"
    original_owner = owner.read_text()
    removed_start = original_owner.index("def test_removed():")
    removed_end = original_owner.index("def test_survivor():")
    owner.write_text(original_owner[:removed_start] + original_owner[removed_end:])
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Proof",
            "-c",
            "user.email=proof@example.invalid",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "-qm",
            "disposable proposed deletion",
        ],
        check=True,
    )
    config["base"] = original_base
    proof = evidence.prove(root, config, tmp_path / "proof")
    assert proof["status"] == "PASS"
    assert proof["coverage"]["core"] == "ctrace"
    assert proof["coverage"]["residue_arcs"] == 0
    assert proof["mutation"]["killed_by_removed"]
    assert proof["mutation"]["lost"] == []
    assert all(
        run["core"] == "ctrace" and run["complete"] and run["cleanup"] for run in proof["runs"]
    )
    assert (root / "toy.py").read_bytes() == before
    assert os.environ["COVERAGE_CORE"] == "sysmon"
    saved = evidence.strict_json(tmp_path / "proof/ledger.json")
    consumer = evidence.sys.modules["check_condensation_ledger"]
    consumer.executable_proof(
        tmp_path / "proof", saved["proof"], saved["binding"], saved["removed"], saved["survivors"]
    )

    # Same retained owners may account for additive helper context only through
    # complete before/current execution, branch arcs, kills and restoration.
    retained_home = tmp_path / "retained"
    retained_home.mkdir()
    retained = _toy(retained_home)
    retained_owner = retained / "tests/test_toy.py"
    helper = (
        "def observe(flag):\n"
        "    def collect(value):\n"
        "        result = reply(value)\n"
        "        assert isinstance(result,str)\n"
        "        return result\n"
        "    result = collect(flag)\n"
        "    return result\n"
    )
    body = retained_owner.read_text().replace("assert reply(", "assert observe(")
    retained_owner.write_text(body.replace("def test_removed", helper + "def test_removed", 1))

    def commit_retained(message):
        subprocess.run(["git", "-C", str(retained), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(retained),
                "-c",
                "user.name=Proof",
                "-c",
                "user.email=proof@example.invalid",
                "-c",
                "core.hooksPath=/dev/null",
                "commit",
                "-qm",
                message,
            ],
            check=True,
        )

    commit_retained("retained source-owned helper")
    retained_base = evidence.git(retained, "rev-parse", "HEAD")
    old_retained = retained_owner.read_text()
    current_retained = (
        old_retained.replace("def collect(value):", "def collect(value, *, probe=False):")
        .replace(
            "        return result\n",
            "        if probe:\n            assert result in ('accepted','error')\n"
            "        return result\n",
        )
        .replace("result = collect(flag)", "result = collect(flag, probe=True)")
    )
    retained_owner.write_text(current_retained)
    commit_retained("add context observation without changing old owners")
    owners = [
        "tests/test_toy.py::" + n for n in ("test_removed", "test_survivor", "test_error_only")
    ]
    context_plan = _config()
    context_plan.update(base=retained_base, removed=owners, survivors=owners)
    context_plan["mapping"] = {
        n: {"survivors": [n], "reason": "retained complete owner with additive helper observation"}
        for n in owners
    }
    with pytest.raises(consumer.EvidenceError, match="unproven"):
        consumer.verify(retained, retained_base, [])
    context_proof = evidence.prove(retained, context_plan, tmp_path / "context-proof")
    assert context_proof["status"] == "PASS"
    context_ledger = tmp_path / "context-proof/ledger.json"
    assert consumer.verify(retained, retained_base, [context_ledger])["losses"] == 3
    for changed in (
        current_retained.replace("        assert isinstance(result,str)\n", ""),
        current_retained.replace(
            "        result = reply(value)\n",
            "        return 'accepted'\n        result = reply(value)\n",
        ),
        current_retained.replace("def observe(flag):", "def observe(flag=False):"),
        current_retained.replace(
            "from toy import reply",
            "from toy import reply\nimport pytest\npytestmark = pytest.mark.skip",
        ),
        current_retained.replace("assert observe(True) == 'accepted'", "assert True"),
        current_retained.replace(
            "        assert isinstance(result,str)\n",
            "        if False:\n            assert isinstance(result,str)\n",
        ),
    ):
        retained_owner.write_text(changed)
        try:
            with pytest.raises(consumer.EvidenceError, match="same-owner-context-not-preserving"):
                consumer.verify(retained, retained_base, [context_ledger])
        finally:
            retained_owner.write_text(current_retained)
        assert consumer.verify(retained, retained_base, [context_ledger])["status"] == "PASS"
    retained_product = retained / "toy.py"
    retained_product.write_text(retained_product.read_text().replace("'accepted'", "'changed'"))
    try:
        with pytest.raises(consumer.EvidenceError, match="proof-unknown"):
            evidence.prove(retained, context_plan, tmp_path / "context-production-drift")
        assert evidence.strict_json(tmp_path / "context-production-drift/receipt.json")[
            "category"
        ] == ("changed-production-scope-needs-independent-lineage-proof")
    finally:
        retained_product.write_bytes(before)
    assert consumer.verify(retained, retained_base, [context_ledger])["status"] == "PASS"
    admitted = consumer.verify(root, original_base, [tmp_path / "proof/ledger.json"])
    assert admitted["losses"] == 1 and admitted["ledger_clusters"] == 1
    links = [r for r in saved["binding"]["inputs"] if r["mode"] == "120000"]
    assert {r["path"] for r in links} == {"asset-home", "body-link", "body-chain", "source-link.py"}
    assert all(r["sha256"] == hashlib.sha256(os.fsencode(r["link"])).hexdigest() for r in links)
    link = root / "body-chain"
    (tmp_path / "outside").write_text("external sentinel must remain unchanged")
    (root / "assets/untracked.txt").write_text("untracked sentinel")
    for target, category in (
        ("assets/body.txt", "changed-input-body-or-mode"),
        (str(tmp_path / "outside"), "unsafe-source-link"),
        ("../outside", "escaping-source-link"),
        ("assets/untracked.txt", "untracked-source-link-target"),
        ("missing", "unresolved-source-link"),
        ("body-chain", "unresolved-source-link"),
    ):
        link.unlink()
        link.symlink_to(target)
        try:
            with pytest.raises(consumer.EvidenceError, match=category):
                consumer.verify(root, original_base, [tmp_path / "proof/ledger.json"])
        finally:
            link.unlink()
            link.symlink_to("body-link")
        assert (
            consumer.verify(root, original_base, [tmp_path / "proof/ledger.json"])["status"]
            == "PASS"
        )
    linked_scope = copy.deepcopy(config)
    linked_scope["scope"] = ["source-link.py"]
    with pytest.raises(consumer.EvidenceError, match="proof-unknown"):
        evidence.prove(root, linked_scope, tmp_path / "linked-scope")
    refused = evidence.strict_json(tmp_path / "linked-scope/receipt.json")
    assert refused["status"] == "UNKNOWN"
    assert refused["category"] == "scope-not-owned-production-python-source"
    assert (tmp_path / "outside").read_text() == "external sentinel must remain unchanged"
    with pytest.raises(consumer.EvidenceError, match="unproven"):
        consumer.verify(root, original_base, [])
    saved_path = tmp_path / "proof/ledger.json"
    original_ledger = saved_path.read_bytes()
    for mutation, category in (
        ("tools", "changed-installed-proof-tools"),
        ("citation", "unresolved-contract-citation"),
        ("migration", "migration-runtime-proof"),
    ):
        mutant = copy.deepcopy(saved)
        if mutation == "tools":
            mutant["binding"]["tools"]["closure"] = "0" * 64
        elif mutation == "citation":
            mutant["contract"]["cites"] = ["REQ-" + "undefined-999"]
        else:
            mutant["contract"]["class"] = "migration"
        saved_path.write_text(json.dumps(mutant))
        with pytest.raises(consumer.EvidenceError, match=category):
            consumer.verify(root, original_base, [saved_path])
        saved_path.write_bytes(original_ledger)
        assert consumer.verify(root, original_base, [saved_path])["status"] == "PASS"
    # Preserve actual SQLite while falsifying both summary and its JSON readback.
    forged = copy.deepcopy(saved)
    run = forged["proof"]["runs"][0]
    path = tmp_path / "proof" / run["artifact"]["path"]
    original = path.read_bytes()
    run["arcs"] = {key: [] for key in run["arcs"]}
    row = {k: v for k, v in run.items() if k not in {"artifact", "coverage_artifact"}}
    path.write_text(json.dumps(row))
    run["artifact"].update(
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(), bytes=path.stat().st_size
    )
    try:
        with pytest.raises(consumer.EvidenceError, match="branch-carrier-summary-mismatch"):
            consumer.executable_proof(
                tmp_path / "proof",
                forged["proof"],
                forged["binding"],
                forged["removed"],
                forged["survivors"],
            )
    finally:
        path.write_bytes(original)
    consumer.executable_proof(
        tmp_path / "proof", saved["proof"], saved["binding"], saved["removed"], saved["survivors"]
    )


def test_distinct_status_owner_loses_kill_and_arcs(tmp_path):
    """REQ-testing-053: different statuses cannot substitute for the removed behavior."""
    root = _toy(tmp_path)
    proof = evidence.prove(
        root, _config(survivor="tests/test_toy.py::test_error_only"), tmp_path / "proof"
    )
    assert proof["status"] == "REFUSED"
    assert proof["coverage"]["residue_arcs"] > 0
    assert proof["mutation"]["lost"]
    assert proof["restored"] and proof["cleanup"]


def test_setup_failure_is_unknown_with_durable_receipt(tmp_path):
    """REQ-testing-053: malformed input and setup failures preserve scoped UNKNOWN."""
    root = _toy(tmp_path)
    bad_config = tmp_path / "malformed.json"
    bad_config.write_text('{"base":1,"base":2}')
    bad_output = tmp_path / "malformed-output"
    assert (
        evidence.main(
            [
                "prove",
                "--repo-root",
                str(root),
                "--config",
                str(bad_config),
                "--output",
                str(bad_output),
            ]
        )
        == 2
    )
    assert evidence.strict_json(bad_output / "receipt.json")["status"] == "UNKNOWN"
    for kind in ("test-scope", "malformed-contract", "empty-owner"):
        refused = _config()
        if kind == "test-scope":
            refused["scope"] = ["tests/test_toy.py"]
        elif kind == "malformed-contract":
            refused["contract"]["cites"] = []
        else:
            refused["bead"] = ""
        with pytest.raises(evidence.EvidenceError, match="proof-unknown"):
            evidence.prove(root, refused, tmp_path / kind)
        assert evidence.strict_json(tmp_path / kind / "receipt.json")["status"] == "UNKNOWN"
    (root / "conftest.py").write_text(
        "import pytest\n@pytest.fixture(autouse=True)\ndef unavailable():\n    raise RuntimeError('private operand must not be retained')\n"
    )
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Proof",
            "-c",
            "user.email=proof@example.invalid",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "-qm",
            "planted setup failure",
        ],
        check=True,
    )
    before = (root / "toy.py").read_bytes()
    with pytest.raises(evidence.EvidenceError, match="proof-unknown"):
        evidence.prove(root, _config(), tmp_path / "proof")
    receipt = evidence.strict_json(tmp_path / "proof/receipt.json")
    assert receipt["status"] == "UNKNOWN"
    assert "private operand" not in (tmp_path / "proof/receipt.json").read_text()
    assert (root / "toy.py").read_bytes() == before


def test_literal_coverage_query_does_not_use_regex_contexts(tmp_path):
    """REQ-testing-053: actual CoverageData queries match literal phase contexts."""
    import coverage

    data = coverage.CoverageData(basename=str(tmp_path / "contexts.coverage"))
    data.set_context("case[one]|call")
    data.add_arcs({"subject.py": {(1, 2)}})
    data.set_context("caseo|call")
    data.add_arcs({"subject.py": {(3, 4)}})
    data.set_query_context("case[one]|call")
    assert data.arcs("subject.py") == [(1, 2)]
    data.set_query_context("caseo|call")
    assert data.arcs("subject.py") == [(3, 4)]


@pytest.mark.parametrize("outcome", ["timeout", "cancel", "crash"])
def test_owned_timeout_leaves_unknown_and_no_active_group(tmp_path, outcome):
    """REQ-testing-053: timeout, cancellation and writer crash preserve owned recovery."""
    if outcome == "timeout":
        with pytest.raises(evidence.EvidenceError, match="proof-timeout"):
            evidence.owned_run(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                cwd=tmp_path,
                env=dict(os.environ),
                timeout=0.1,
            )
        root = _toy(tmp_path)
        with pytest.raises(evidence.EvidenceError, match="proof-unknown"):
            evidence.prove(root, _config(), tmp_path / "total-timeout", timeout=0.01)
        total = evidence.strict_json(tmp_path / "total-timeout/receipt.json")
        assert total["status"] == "UNKNOWN" and total["restored"] and total["cleanup"]
        return
    root = _toy(tmp_path)
    config = tmp_path / "config.json"
    config.write_text(json.dumps(_config()))
    output = tmp_path / "interrupted"
    child = subprocess.Popen(
        [
            sys.executable,
            evidence.__file__,
            "prove",
            "--repo-root",
            str(root),
            "--config",
            str(config),
            "--output",
            str(output),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            journal_path = output / "journal.json"
            if journal_path.exists():
                journal = evidence.strict_json(journal_path)
                if journal["mutations"]:
                    break
            if child.poll() is not None:
                pytest.fail("proof terminated before the positioned mutation seam")
            time.sleep(0.01)
        else:
            pytest.fail("mutation seam was not reached")
        os.kill(child.pid, signal.SIGTERM if outcome == "cancel" else signal.SIGKILL)
        child.wait(timeout=10)
        assert evidence.strict_json(output / "receipt.json")["status"] == "UNKNOWN"
        assert (root / "toy.py").read_text().endswith("    return 'error'\n")
        owned = Path(journal["owned_copy"])
        if outcome == "cancel":
            assert not owned.exists()
            receipt = evidence.strict_json(output / "receipt.json")
            assert receipt["restored"] and receipt["cleanup"]
        else:
            # A killed writer cannot restamp PASS. Wait only for its already-running
            # finite toy worker, then use the actual supported scoped recovery.
            deadline = time.monotonic() + 10
            while True:
                try:
                    result = evidence.recover(output)
                    break
                except evidence.EvidenceError as exc:
                    if (
                        str(exc)
                        not in {
                            "recovery-process-not-settled-no-signal",
                            "recovery-owned-group-completion-unknown",
                        }
                        or time.monotonic() >= deadline
                    ):
                        raise
                    time.sleep(0.01)
            assert result == {
                "status": "UNKNOWN",
                "restored": True,
                "cleanup": True,
                "category": "recovered-needs-fresh-proof",
            }
            for mutation in journal["mutations"]:
                before = (output / mutation["before_file"]).read_bytes()
                for side in ("removed", "survivors"):
                    restored = owned / side / mutation["path"]
                    assert restored.read_bytes() == before
                    assert restored.stat().st_mode & 0o777 == mutation["before_mode"]
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)
