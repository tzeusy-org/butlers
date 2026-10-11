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


@pytest.fixture(autouse=True)
def _disposable_git_template(tmp_path, monkeypatch):
    """Keep actual generated carriers outside disposable commits, as the repo does."""
    template = tmp_path / "git-template"
    info = template / "info"
    info.mkdir(parents=True)
    (info / "exclude").write_text(".tmp/\n")
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(template))
    if not os.environ.get("TEST_EVIDENCE_DIR"):
        diagnostics = tmp_path / "closed-evidence"
        diagnostics.mkdir()
        monkeypatch.setenv("TEST_EVIDENCE_DIR", str(diagnostics))


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
    assert _disjoint_execution_phases(proof, config, tmp_path / "proof")
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
    # Ordinary additive imports keep all original bindings but still require a
    # fresh executable account. The actual default CLI discovers only tracked
    # source requests; it does not search for or restamp previous proof outputs.
    ordinary_home = tmp_path / "ordinary"
    ordinary_home.mkdir()
    ordinary = _toy(ordinary_home)
    ordinary_product = ordinary / "toy.py"
    ordinary_product.write_text(ordinary_product.read_text() + "\nunused_new_alias = object()\n")
    ordinary_owner = ordinary / "tests/test_toy.py"

    def ordinary_commit(message):
        subprocess.run(["git", "-C", str(ordinary), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(ordinary),
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "user.name=Proof",
                "-c",
                "user.email=proof@example.invalid",
                "commit",
                "-qm",
                message,
            ],
            check=True,
        )

    ordinary_commit("unchanged available additional export")
    ordinary_base = consumer.git(ordinary, "rev-parse", "HEAD")
    ordinary_before = ordinary_owner.read_text()
    ordinary_current = ordinary_before.replace(
        "from toy import reply", "from toy import reply, unused_new_alias"
    )
    ordinary_owner.write_text(ordinary_current)
    plan = _config()
    plan.update(base="event-base", removed=owners, survivors=owners)
    plan["mapping"] = {
        n: {"survivors": [n], "reason": "same complete owner and original import bindings"}
        for n in owners
    }
    plans = ordinary / "tests/condensation-plans"
    plans.mkdir()
    request = plans / "ordinary.json"
    request.write_text(json.dumps(plan))
    ordinary_commit("add import and current execution request")
    with pytest.raises(consumer.EvidenceError, match="unproven"):
        consumer.verify(ordinary, ordinary_base, [])
    consumer.same_owner_context_accounts(ordinary, ordinary_base, set(owners))
    wildcard_before = ordinary_before.replace("from toy import reply", "from toy import *")
    assert consumer.preserving_import_bindings(wildcard_before, wildcard_before)
    assert not consumer.preserving_import_bindings(
        wildcard_before,
        wildcard_before.replace(
            "from toy import *", "from toy import *\nfrom toy import unused_new_alias"
        ),
    )
    assert consumer.main(["--repo-root", str(ordinary), "--base", ordinary_base]) == 0
    first = set((ordinary / ".tmp/condensation-ci").glob("run-*/proof-0/ledger.json"))
    assert len(first) == 1
    assert consumer.verify(ordinary, ordinary_base, list(first))["status"] == "PASS"
    # Real PR union uses a newer independent base; requests execute anew at that
    # base/head, rather than rewriting the first carrier's nonce or identity.
    source_head = consumer.git(ordinary, "rev-parse", "HEAD")
    subprocess.run(
        ["git", "-C", str(ordinary), "checkout", "-qb", "public", ordinary_base], check=True
    )
    (ordinary / "public-note.md").write_text("independent public history\n")
    ordinary_commit("independent public note")
    public_base = consumer.git(ordinary, "rev-parse", "HEAD")
    subprocess.run(
        [
            "git",
            "-C",
            str(ordinary),
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "user.name=Proof",
            "-c",
            "user.email=proof@example.invalid",
            "merge",
            "--no-ff",
            "-qm",
            "actual union",
            source_head,
        ],
        check=True,
    )
    union = consumer.git(ordinary, "rev-parse", "HEAD")
    assert (
        consumer.main(
            [
                "--repo-root",
                str(ordinary),
                "--base",
                public_base,
                "--ci-event",
                "pull_request",
                "--expected-head",
                union,
                "--source-head",
                source_head,
            ]
        )
        == 0
    )
    all_carriers = set((ordinary / ".tmp/condensation-ci").glob("run-*/proof-0/ledger.json"))
    second = all_carriers - first
    assert len(second) == 1
    before_request = consumer.strict_json(next(iter(first)))
    after_request = consumer.strict_json(next(iter(second)))
    assert before_request["binding"]["nonce"] != after_request["binding"]["nonce"]
    assert after_request["binding"]["head"] == union
    assert after_request["binding"]["base"] == public_base
    with pytest.raises(consumer.EvidenceError, match="base-identity"):
        consumer.verify(ordinary, public_base, list(first))
    # Additional real fixture requests and appended execution also use fresh
    # same-owner proof, never an argument/import/context exemption.
    fixture_current = ordinary_current
    for name in ("test_removed", "test_survivor", "test_error_only"):
        fixture_current = fixture_current.replace(
            f"def {name}():", f"def {name}(tmp_path):\n    assert tmp_path.is_dir()"
        )
    ordinary_owner.write_text(fixture_current)
    ordinary_commit("added fixture and execution with original statements retained")
    assert consumer.main(["--repo-root", str(ordinary), "--base", public_base]) == 0
    ordinary_owner.write_text(ordinary_current)
    # Unsafe old-binding changes, marker loss and early exits cannot request a
    # context-only account. Removing the current request restores default refusal.
    for body in (
        ordinary_current.replace("reply, unused_new_alias", "reply as changed, unused_new_alias"),
        ordinary_current.replace("reply, unused_new_alias", "reply, unused_new_alias as reply"),
        ordinary_current.replace("def test_removed():", "def test_removed():\n    return"),
    ):
        ordinary_owner.write_text(body)
        with pytest.raises(consumer.EvidenceError, match="same-owner-context-not-preserving"):
            consumer.same_owner_context_accounts(ordinary, public_base, set(owners))
        ordinary_owner.write_text(ordinary_current)
    # Adding a separate import statement cannot silently shadow an old binding
    # merely because all original import AST statements remain present.
    ordinary_owner.write_text(
        ordinary_before.replace(
            "from toy import reply",
            "from toy import reply\nfrom toy import unused_new_alias as reply",
        )
    )
    with pytest.raises(consumer.EvidenceError, match="unproven"):
        consumer.verify(ordinary, public_base, [])
    ordinary_owner.write_text(ordinary_current)
    saved_request = request.read_bytes()
    request.unlink()
    assert consumer.main(["--repo-root", str(ordinary), "--base", public_base]) == 1
    request.write_bytes(saved_request)
    # A missing tracked request is not a generated or cache-backed green. An
    # unchanged restored source has no loss and needs no request or execution.
    ordinary_owner.write_text(ordinary_before)
    assert consumer.verify(ordinary, public_base, [])["status"] == "PASS"
    assert consumer.main(["--repo-root", str(ordinary), "--base", public_base]) == 0
    ordinary_owner.write_text(ordinary_current)
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
    # Named True-only coverage cannot borrow the unrelated selected False case.
    named_home = tmp_path / "named"
    named_home.mkdir()
    named = _toy(named_home)
    (named / "toy.py").write_text(
        "def reply(flag):\n    if flag:\n        value = 'branch_a'\n    else:\n        value = 'branch_b'\n    return value\n"
    )
    owner = named / "tests/test_toy.py"
    old = (
        "from toy import reply\n"
        "def test_removed():\n    assert reply(True) == 'branch_a'\n    assert reply(False) == 'branch_b'\n"
        "def test_survivor():\n    assert reply(True) == 'branch_a'\n"
        "def test_other():\n    assert reply(False) == 'branch_b'\n"
    )
    owner.write_text(old)

    def commit_named(message):
        subprocess.run(["git", "-C", str(named), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(named),
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "user.name=Proof",
                "-c",
                "user.email=proof@example.invalid",
                "commit",
                "-qm",
                message,
            ],
            check=True,
        )

    commit_named("both original branches")
    base = evidence.git(named, "rev-parse", "HEAD")
    owner.write_text(
        old[: old.index("def test_removed():")] + old[old.index("def test_survivor():") :]
    )
    commit_named("candidate named mapping")
    config = _config()
    config.update(
        base=base, survivors=["tests/test_toy.py::test_survivor", "tests/test_toy.py::test_other"]
    )
    missing = evidence.prove(named, config, tmp_path / "named-refused")
    assert missing["status"] == "REFUSED"
    assert missing["coverage"]["residue"] == [["toy.py", 2, 5], ["toy.py", 5, 6]]
    assert missing["mutation"]["lost"] == []
    config["mapping"]["tests/test_toy.py::test_removed"]["survivors"] = config["survivors"]
    complete = evidence.prove(named, config, tmp_path / "named-complete")
    assert complete["status"] == "PASS"
    consumer = evidence.sys.modules["check_condensation_ledger"]
    carrier = tmp_path / "named-complete/ledger.json"
    assert consumer.verify(named, base, [carrier])["status"] == "PASS"
    saved = carrier.read_bytes()
    forged = consumer.strict_json(carrier)
    forged["removed"][0]["survivors"] = ["tests/test_toy.py::test_survivor"]
    carrier.write_text(json.dumps(forged))
    with pytest.raises(consumer.EvidenceError, match="coverage-residue-summary-mismatch"):
        consumer.verify(named, base, [carrier])
    carrier.write_bytes(saved)
    assert consumer.verify(named, base, [carrier])["status"] == "PASS"
    config["mapping"]["tests/test_toy.py::test_removed"]["survivors"] = [
        "tests/test_toy.py::test_survivor"
    ]
    config["survivors"] = ["tests/test_toy.py::test_survivor"]
    unmapped_absent = evidence.prove(named, config, tmp_path / "named-unmapped-absent")
    assert unmapped_absent["status"] == "REFUSED"
    assert unmapped_absent["coverage"]["residue"] == missing["coverage"]["residue"]

    # Keep complete indirect execution and lexical companions in this existing
    # proof owner so neither item accumulates every independent subprocess run.
    assert _assert_indirect_execution_boundaries(tmp_path / "indirect-boundaries") == {
        "child-request-same": "worker-incomplete",
        "child-request-paired": "worker-incomplete",
        "startup-request-same": "worker-incomplete",
        "startup-request-paired": "worker-incomplete",
        "native-request-same": "worker-incomplete",
        "native-request-paired": "worker-incomplete",
        "fresh-request-healthy": "PASS",
        "ordinary-child-healthy": "PASS",
        "alias-namespace": "changed-or-missing-function-lineage-signature",
        "imported-frame-observer": "unsupported-indirect-namespace-binding",
        "operator-frame-observer": "unsupported-indirect-namespace-binding",
        "pure-local-alias-healthy": "PASS",
    }
    assert _assert_finalizer_rebinding(tmp_path / "finalizer-rebinding") == "PASS"


def test_fresh_paired_runtime_lineage_uses_each_source_and_named_owner(tmp_path):
    """REQ-testing-053: changed implementations need genuine paired runs, not an exemption."""
    root = _toy(tmp_path)
    source = Path(__file__).resolve().parents[2]
    shutil.copy2(
        source / "scripts/condensation_lineage.py", root / "scripts/condensation_lineage.py"
    )
    owner = root / "tests/test_toy.py"
    owner.write_text(
        "from toy import reply\n"
        "def test_removed():\n"
        "    assert reply(True) == 'accepted'\n"
        "    assert reply(False) == 'error'\n"
        "def test_survivor():\n    assert reply(True) == 'accepted'\n"
        "def test_other():\n    assert reply(False) == 'error'\n"
        "def test_outside():\n    assert reply(True) == 'accepted'\n"
    )
    fixture = root / "conftest.py"
    fixture.write_text("# Complete actual ancestral fixture source\nimport pytest\n")

    def commit(message):
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "user.name=Proof",
                "-c",
                "user.email=proof@example.invalid",
                "commit",
                "-qm",
                message,
            ],
            check=True,
        )

    commit("complete independent before source")
    base = evidence.git(root, "rev-parse", "HEAD")
    old_product = (root / "toy.py").read_bytes()
    current_product = b"# Actual new source location\n\n" + old_product.replace(
        b"    if flag:", b"    bool(flag)\n    if flag:"
    )
    (root / "primitive.py").write_bytes(current_product)
    (root / "toy.py").write_text("from primitive import reply\n")
    owner.write_text(
        "from toy import reply\n"
        + "def test_survivor():"
        + owner.read_text().split("def test_survivor():", 1)[1]
    )
    config = _config()
    config.update(
        base=base,
        scope=["toy.py", "primitive.py"],
        removed=["tests/test_toy.py::test_removed", "tests/test_toy.py::test_other"],
        survivors=["tests/test_toy.py::test_survivor", "tests/test_toy.py::test_other"],
        lineage={
            "mode": "paired",
            "scope": ["toy.py"],
            "functions": [
                {
                    "before": {"path": "toy.py", "function": "reply"},
                    "current": {"path": "primitive.py", "function": "reply"},
                },
            ],
        },
    )
    config["mapping"]["tests/test_toy.py::test_removed"]["survivors"] = config["survivors"]
    config["mapping"]["tests/test_toy.py::test_other"] = {
        "survivors": ["tests/test_toy.py::test_other"],
        "reason": "Complete unchanged owner and actual inherited fixture remain executable",
    }
    plan = root / "tests/condensation-plans/paired.json"
    plan.parent.mkdir()
    plan.write_text(json.dumps(dict(config, base="event-base")))
    commit("single-home source with exact fresh execution request")
    consumer = evidence.sys.modules["check_condensation_ledger"]
    missing = copy.deepcopy(config)
    del missing["lineage"]
    with pytest.raises(consumer.EvidenceError, match="proof-unknown"):
        evidence.prove(root, missing, tmp_path / "lineage-missing")
    assert evidence.strict_json(tmp_path / "lineage-missing/receipt.json")["category"] == (
        "changed-production-scope-needs-independent-lineage-proof"
    )
    proof = evidence.prove(root, config, tmp_path / "paired-complete")
    assert proof["status"] == "PASS"
    assert proof["coverage"]["residue_arcs"] == 0 and proof["mutation"]["lost"] == []
    carrier = tmp_path / "paired-complete/ledger.json"
    assert consumer.verify(root, base, [carrier])["status"] == "PASS"
    assert proof["runs"][0]["scope_inputs"] != proof["runs"][1]["scope_inputs"]
    for mutant in proof["mutation"]["mutants"]:
        assert mutant["path"] == "toy.py" and mutant["current"]["path"] == "primitive.py"
        assert mutant["before_sha256"] != mutant["current"]["before_sha256"]
        assert mutant["status"] == mutant["current"]["status"] == "RESTORED"
    assert (root / "primitive.py").read_bytes() == current_product

    actual_fixture = fixture.read_bytes()
    fixture.write_bytes(actual_fixture + b"pytestmark = pytest.mark.skip\n")
    try:
        with pytest.raises(consumer.EvidenceError, match="same-owner-ancestral-fixture-change"):
            consumer.same_owner_context_accounts(root, base, {"tests/test_toy.py::test_other"})
    finally:
        fixture.write_bytes(actual_fixture)
    consumer.same_owner_context_accounts(root, base, {"tests/test_toy.py::test_other"})

    import condensation_lineage as lineage

    annotated_pair = [
        {
            "before": {"path": "old.py", "function": "reply"},
            "current": {"path": "new.py", "function": "reply"},
        }
    ]
    for annotated, changed in (
        ("def reply(value: int) -> int:\n    return value\n", "-> str"),
        ("def reply[T: int](value: T) -> T:\n    return value\n", "T: str"),
    ):
        healthy = {"new.py": annotated}
        lineage.alignment({"old.py": annotated}, healthy, annotated_pair)
        corrupted = annotated.replace("-> int", changed).replace("T: int", changed)
        with pytest.raises(consumer.EvidenceError, match="changed-or-missing-function"):
            lineage.alignment({"old.py": annotated}, {"new.py": corrupted}, annotated_pair)
        lineage.alignment({"old.py": annotated}, healthy, annotated_pair)

    for unbound in ({"mode": "paired", "scope": ["*.py"], "functions": []},):
        with pytest.raises(consumer.EvidenceError):
            lineage.request(unbound)
    before_sources = {"toy.py": old_product.decode()}
    current_sources = {
        "primitive.py": current_product.decode(),
        "toy.py": "from primitive import reply\n",
    }
    for corrupted in (
        current_product.decode().replace("def reply(flag):", "def reply(other):"),
        current_product.decode().replace("if flag:", "if not flag:"),
    ):
        with pytest.raises(consumer.EvidenceError):
            points = lineage.alignment(
                before_sources,
                dict(current_sources, **{"primitive.py": corrupted}),
                config["lineage"]["functions"],
            )
            lineage.paired_mutants(
                points,
                evidence.generate_mutants({"toy.py": old_product}),
                evidence.generate_mutants({"primitive.py": corrupted.encode()}),
            )
    with pytest.raises(consumer.EvidenceError, match="ambiguous-source-function-identity"):
        lineage.alignment(
            before_sources,
            dict(current_sources, **{"toy.py": old_product.decode()}),
            config["lineage"]["functions"],
        )

    saved = carrier.read_bytes()
    for corruption in ("old-input", "mutant", "unrelated-owner"):
        value = consumer.strict_json(carrier)
        if corruption == "old-input":
            value["binding"]["lineage"]["inputs"][0]["sha256"] = "0" * 64
        elif corruption == "mutant":
            value["proof"]["mutation"]["mutants"][0]["current"]["after_sha256"] = "0" * 64
        else:
            value["removed"][0]["survivors"] = ["tests/test_toy.py::test_survivor"]
        carrier.write_text(json.dumps(value))
        with pytest.raises(consumer.EvidenceError):
            consumer.verify(root, base, [carrier])
        carrier.write_bytes(saved)
        assert consumer.verify(root, base, [carrier])["status"] == "PASS"

    complete_owner = owner.read_bytes()
    omitted = b"def test_outside():\n    assert reply(True) == 'accepted'\n"
    assert omitted in complete_owner
    owner.write_bytes(complete_owner.replace(omitted, b""))
    commit("owned negative: destructive owner omitted from declared proof")
    omitted_proof = evidence.prove(root, config, tmp_path / "omitted-owner")
    assert omitted_proof["status"] == "PASS"
    with pytest.raises(consumer.EvidenceError, match="unproven-test-or-assertion-loss"):
        consumer.verify(root, base, [tmp_path / "omitted-owner/ledger.json"])
    owner.write_bytes(complete_owner)
    commit("restore whole omitted owner and source")
    restored_proof = evidence.prove(root, config, tmp_path / "restored-owner")
    assert restored_proof["status"] == "PASS"
    carrier = tmp_path / "restored-owner/ledger.json"
    assert consumer.verify(root, base, [carrier])["status"] == "PASS"

    wrong = copy.deepcopy(config)
    wrong["scope"] = ["primitive.py"]
    with pytest.raises(consumer.EvidenceError, match="proof-unknown"):
        evidence.prove(root, wrong, tmp_path / "unbound-bridge")
    assert evidence.strict_json(tmp_path / "unbound-bridge/receipt.json")["category"] == (
        "changed-dependency-outside-paired-scope"
    )
    product = root / "primitive.py"
    healthy = product.read_bytes()
    product.write_bytes(healthy.replace(b"'accepted'", b"'corrupted'"))
    try:
        with pytest.raises(consumer.EvidenceError, match="proof-unknown"):
            evidence.prove(root, config, tmp_path / "actual-runtime-corruption")
        assert evidence.strict_json(tmp_path / "actual-runtime-corruption/receipt.json")[
            "category"
        ] == ("baseline-not-healthy")
    finally:
        product.write_bytes(healthy)
    assert consumer.verify(root, base, [carrier])["status"] == "PASS"

    wrong = copy.deepcopy(config)
    wrong["mapping"]["tests/test_toy.py::test_removed"]["survivors"] = [
        "tests/test_toy.py::test_survivor"
    ]
    unmapped = evidence.prove(root, wrong, tmp_path / "paired-unmapped-false")
    assert unmapped["status"] == "REFUSED"
    assert unmapped["coverage"]["residue_arcs"] > 0 and unmapped["mutation"]["lost"]
    # Default CLI discovers this exact source-owned request and creates new
    # carriers. It neither searches our earlier outputs nor rebinds their nonce.
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/check_condensation_ledger.py"),
            "--repo-root",
            str(root),
            "--base",
            base,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    fresh = list((root / ".tmp/condensation-ci").glob("run-*/proof-*/ledger.json"))
    assert len(fresh) == 1
    fresh_value = consumer.strict_json(fresh[0])
    assert fresh_value["binding"]["nonce"] != consumer.strict_json(carrier)["binding"]["nonce"]
    assert consumer.verify(root, base, fresh)["status"] == "PASS"

    _assert_definition_and_dependency_bindings(tmp_path / "lexical-and-config")


def _assert_definition_and_dependency_bindings(tmp_path):
    """Real paired/same-source producer and required-reader refusal with restoration."""
    import ast

    import condensation_lineage as lineage

    consumer = evidence.sys.modules["check_condensation_ledger"]
    for kind, mode in (("default", "paired"), ("config", "same-source"), ("config", "paired")):
        home = tmp_path / (kind + "-" + mode)
        home.mkdir(parents=True)
        root = _toy(home)
        source = Path(__file__).resolve().parents[2]
        shutil.copy2(
            source / "scripts/condensation_lineage.py", root / "scripts/condensation_lineage.py"
        )
        product = root / "toy.py"
        if kind == "default":
            before = "a=1\nb=2\n\ndef reply(a=a, /):\n    return a\n"
            changed = "a=1\nb=2\n\ndef reply(b=b, /):\n    return b\n"
            restored = changed.replace("b=b", "b=a")
        else:
            before = changed = restored = (
                "from pathlib import Path\nimport json\n\ndef reply():\n"
                "    return json.loads(Path(__file__).with_name('settings.json').read_text())['value']\n"
            )
            (root / "settings.json").write_text('{"value":1}\n')
        product.write_text(before)
        owner = root / "tests/test_toy.py"
        owner.write_text(
            "from toy import reply\ndef test_removed():\n    assert reply() == 1\n"
            "def test_survivor():\n    assert isinstance(reply(), int)\n"
        )

        def commit(message):
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-c",
                    "user.name=Proof",
                    "-c",
                    "user.email=proof@example.invalid",
                    "commit",
                    "-qm",
                    message,
                ],
                check=True,
            )

        commit("original definition/config binding and exact-value owner")
        base = evidence.git(root, "rev-parse", "HEAD")
        product.write_text(changed)
        owner.write_text(
            "from toy import reply\ndef test_survivor():\n    assert isinstance(reply(), int)\n"
        )
        if kind == "config":
            (root / "settings.json").write_text('{"value":2}\n')
        commit("planted changed binding with a weak integer survivor")
        config = _config()
        config["base"] = base
        if mode == "paired":
            config["lineage"] = {
                "mode": "paired",
                "scope": ["toy.py"],
                "functions": [
                    {
                        "before": {"path": "toy.py", "function": "reply"},
                        "current": {"path": "toy.py", "function": "reply"},
                    }
                ],
            }
        category = (
            "changed-or-missing-function-lineage-signature"
            if kind == "default"
            else "changed-non-python-dependency-needs-independent-runtime-proof"
        )
        with pytest.raises(consumer.EvidenceError, match="proof-unknown"):
            evidence.prove(root, config, home / "current-refusal")
        assert evidence.strict_json(home / "current-refusal/receipt.json")["category"] == category
        # Neutralize the exact new lexical/dependency predicate through the real
        # producer and reader, then restore it before re-reading that same carrier.
        signature = lineage._function_signature
        dependencies = evidence.production_dependencies
        reader_dependencies = consumer.production_dependencies

        def wrong_signature(node, names, enclosing):
            node = copy.deepcopy(node)

            class WrongDefinitionScope(ast.NodeTransformer):
                def visit_Name(self, item):
                    return ast.copy_location(
                        ast.Name(id=names.get(item.id, item.id), ctx=item.ctx), item
                    )

            node.args = WrongDefinitionScope().visit(node.args)
            return signature(node, names, enclosing)

        def ignored_configuration(old, new, scope, paired, requests):
            dependencies(
                [r for r in old if r["path"].endswith(".py")],
                [r for r in new if r["path"].endswith(".py")],
                scope,
                paired,
                requests,
            )

        try:
            if kind == "default":
                lineage._function_signature = wrong_signature
            else:
                evidence.production_dependencies = ignored_configuration
                consumer.production_dependencies = ignored_configuration
            weak = evidence.prove(root, config, home / "neutralized")
            assert weak["status"] == "PASS"
            carrier = home / "neutralized/ledger.json"
            assert consumer.verify(root, base, [carrier])["losses"] == 1
        finally:
            lineage._function_signature = signature
            evidence.production_dependencies = dependencies
            consumer.production_dependencies = reader_dependencies
        with pytest.raises(consumer.EvidenceError, match=category):
            consumer.verify(root, base, [carrier])
        product.write_text(restored)
        if kind == "config":
            (root / "settings.json").write_text('{"value":1}\n')
        commit("restore actual original value with healthy local rename")
        healthy = evidence.prove(root, config, home / "restored-healthy")
        assert healthy["status"] == "PASS"
        assert (
            consumer.verify(root, base, [home / "restored-healthy/ledger.json"])["status"] == "PASS"
        )
        assert product.read_text() == restored

    pair = [
        {
            "before": {"path": "a.py", "function": "reply"},
            "current": {"path": "b.py", "function": "reply"},
        }
    ]
    for before, changed in (
        ("def reply(a: a, /):\n    return a\n", "def reply(b: b, /):\n    return b\n"),
        ("@a\ndef reply(a, /):\n    return a\n", "@b\ndef reply(b, /):\n    return b\n"),
        ("def reply(a, /) -> a:\n    return a\n", "def reply(b, /) -> b:\n    return b\n"),
        ("def reply[T: a](a, /):\n    return a\n", "def reply[T: b](b, /):\n    return b\n"),
        ("def reply(a, /):\n    return a\n", "def reply(b, /):\n    return parameter_0\n"),
        ("def reply(a, /):\n    return locals()\n", "def reply(b, /):\n    return locals()\n"),
        (
            "def reply(a, /):\n    def inner():\n        return a\n    return inner()\n",
            "def reply(b, /):\n    def inner():\n        return a\n    return inner()\n",
        ),
    ):
        with pytest.raises(consumer.EvidenceError):
            points = lineage.alignment({"a.py": before}, {"b.py": changed}, pair)
            lineage.paired_mutants(
                points,
                evidence.generate_mutants({"a.py": before.encode()}),
                evidence.generate_mutants({"b.py": changed.encode()}),
            )

    _assert_execution_request_is_not_runtime_data(tmp_path / "request-role")


def _assert_indirect_execution_boundaries(tmp_path):
    """Use actual fresh producer and required reader, including their children."""
    tmp_path.mkdir()
    outcomes = {}
    consumer = evidence.sys.modules["check_condensation_ledger"]
    source = Path(__file__).resolve().parents[2]
    child = (
        "import subprocess\nimport sys\nfrom pathlib import Path\n"
        "def reply():\n"
        "    value = subprocess.check_output([sys.executable, '-c', "
        "\"import json,sys;print(json.load(open(sys.argv[1]))['cluster'])\", "
        "str(Path(__file__).parent / 'tests/condensation-plans/current.json')], text=True)\n"
        "    return int(value)\n"
    )
    for species in (
        "child-request-same",
        "child-request-paired",
        "startup-request-same",
        "startup-request-paired",
        "native-request-same",
        "native-request-paired",
        "fresh-request-healthy",
        "ordinary-child-healthy",
        "alias-namespace",
        "imported-frame-observer",
        "operator-frame-observer",
        "pure-local-alias-healthy",
    ):
        home = tmp_path / species
        home.mkdir()
        root = _toy(home)
        shutil.copy2(source / "scripts/condensation_lineage.py", root / "scripts")
        product = root / "toy.py"
        namespace = species in {
            "alias-namespace",
            "imported-frame-observer",
            "operator-frame-observer",
        }
        if species.startswith("child-request"):
            before = child
        elif species.startswith("startup-request"):
            before = "import builtins\ndef reply():\n    return builtins._proof_value\n"
            (root / "sitecustomize.py").write_text(
                "import builtins,json\nfrom pathlib import Path\n"
                "builtins._proof_value=int(json.loads((Path(__file__).parent/"
                "'tests/condensation-plans/current.json').read_text())['cluster'])\n"
            )
        elif species.startswith("native-request"):
            before = (
                "import ctypes\nimport json\nfrom pathlib import Path\n"
                "def reply():\n"
                "    path = Path(__file__).parent/'tests/condensation-plans/current.json'\n"
                "    libc = ctypes.CDLL(None)\n"
                "    libc.fopen.argtypes = [ctypes.c_char_p, ctypes.c_char_p]\n"
                "    libc.fopen.restype = ctypes.c_void_p\n"
                "    libc.fread.argtypes = [ctypes.c_void_p, ctypes.c_size_t, "
                "ctypes.c_size_t, ctypes.c_void_p]\n"
                "    libc.fclose.argtypes = [ctypes.c_void_p]\n"
                "    stream = libc.fopen(bytes(path), b'rb')\n"
                "    buffer = ctypes.create_string_buffer(path.stat().st_size)\n"
                "    libc.fread(buffer, 1, len(buffer), stream)\n"
                "    libc.fclose(stream)\n"
                "    return int(json.loads(buffer.raw)['cluster'])\n"
            )
        elif species == "ordinary-child-healthy":
            before = (
                "import subprocess\nimport sys\n"
                "def reply():\n"
                "    value = subprocess.check_output([sys.executable, '-c', 'print(1)'], text=True)\n"
                "    return int(value)\n"
            )
        elif species == "alias-namespace":
            before = "def reply(a=1, /):\n    observe = locals\n    return tuple(observe())\n"
        elif species == "imported-frame-observer":
            before = "from observer import observe\ndef reply(a=1, /):\n    return observe()\n"
            (root / "observer.py").write_text(
                "import inspect\ndef observe():\n"
                "    return tuple(inspect.currentframe().f_back.f_locals)\n"
            )
        elif species == "operator-frame-observer":
            before = "from observer import value\ndef reply(a=1, /):\n    return value+a\n"
            (root / "observer.py").write_text(
                "import inspect\nclass Value:\n    def __add__(self, other):\n"
                "        return tuple(inspect.currentframe().f_back.f_locals)\nvalue=Value()\n"
            )
        elif species == "pure-local-alias-healthy":
            before = "def reply(a=1, /):\n    value=a\n    return value\n"
        else:
            before = "def reply():\n    return 1\n"
        product.write_text(before)
        config = _config()
        config.update(base="event-base", cluster="1")
        if species in {"imported-frame-observer", "operator-frame-observer"}:
            config["scope"] = ["toy.py", "observer.py"]
        request = root / "tests/condensation-plans/current.json"
        if "request" in species:
            request.parent.mkdir()
            request.write_text(json.dumps(config))
        exact = (
            "('a','observe')" if species == "alias-namespace" else "('a',)" if namespace else "1"
        )
        weak = "tuple" if namespace else "int"
        owner = root / "tests/test_toy.py"
        owner.write_text(
            "from toy import reply\ndef test_removed():\n"
            f"    assert reply()=={exact}\ndef test_survivor():\n"
            f"    assert isinstance(reply(),{weak})\n"
        )

        def commit(message):
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-c",
                    "user.name=Proof",
                    "-c",
                    "user.email=proof@example.invalid",
                    "commit",
                    "-qm",
                    message,
                ],
                check=True,
            )

        commit("original exact value and complete execution boundary")
        base = evidence.git(root, "rev-parse", "HEAD")
        changed = before.replace("reply(a=1", "reply(b=1").replace("value+a", "value+b")
        changed = changed.replace("value=a", "value=b")
        if namespace or species == "pure-local-alias-healthy":
            product.write_text(changed)
        elif request.exists():
            request.write_text(json.dumps(dict(config, cluster="2")))
        owner.write_text(
            f"from toy import reply\ndef test_survivor():\n    assert isinstance(reply(),{weak})\n"
        )
        commit("remove exact assertion at actual indirect boundary")
        config["base"] = base
        if namespace or species.endswith("paired") or species == "pure-local-alias-healthy":
            config["lineage"] = {
                "mode": "paired",
                "scope": config["scope"],
                "functions": [
                    {
                        "before": {"path": "toy.py", "function": "reply"},
                        "current": {"path": "toy.py", "function": "reply"},
                    }
                ],
            }
        if namespace or species.startswith(("child-request", "startup-request", "native-request")):
            with pytest.raises(consumer.EvidenceError, match="proof-unknown"):
                evidence.prove(root, config, home / "refused")
            receipt = evidence.strict_json(home / "refused/receipt.json")
            expected = (
                "unsupported-indirect-namespace-binding"
                if species in {"imported-frame-observer", "operator-frame-observer"}
                else "changed-or-missing-function-lineage-signature"
                if namespace
                else "worker-incomplete"
            )
            assert receipt["category"] == expected and receipt["restored"] and receipt["cleanup"]
            outcomes[species] = receipt["category"]
            with pytest.raises(consumer.EvidenceError, match="unproven"):
                consumer.verify(root, base, [])
            if species.startswith("child-request"):
                run = evidence.strict_json(home / "refused/removed-baseline.json")
                assert run["request_boundary"]["descendant_execution"] is True
                assert run["request_boundary"]["runtime_read"] is False
            if not namespace:
                run = evidence.strict_json(home / "refused/removed-baseline.json")
                assert run["request_access"]["complete"] is True
                assert run["request_access"]["probe_accesses"] > 0
                assert run["request_access"]["accesses"] > 0
                assert all(c["phases"]["call"] == "passed" for c in run["cases"])
            assert product.read_text() == (changed if namespace else before)
        else:
            if species == "fresh-request-healthy":
                actual_which = evidence.shutil.which
                for label, observer, category in (
                    ("unavailable", None, "request-access-observer-unavailable"),
                    ("unobserved", actual_which("true"), "worker-incomplete"),
                ):
                    assert label == "unavailable" or observer is not None
                    with pytest.MonkeyPatch.context() as patch:
                        patch.setattr(
                            evidence.shutil,
                            "which",
                            lambda name: observer if name == "strace" else actual_which(name),
                        )
                        with pytest.raises(consumer.EvidenceError, match="proof-unknown"):
                            evidence.prove(root, config, home / label)
                    receipt = evidence.strict_json(home / label / "receipt.json")
                    assert receipt["category"] == category
                    assert receipt["restored"] and receipt["cleanup"]
                    with pytest.raises(consumer.EvidenceError, match="unproven"):
                        consumer.verify(root, base, [])
                    if label == "unobserved":
                        access = evidence.strict_json(home / label / "removed-baseline.access.json")
                        assert access["complete"] is False and access["probe_accesses"] == 0
                    assert product.read_text() == before
            proof = evidence.prove(root, config, home / "healthy")
            assert proof["status"] == "PASS"
            outcomes[species] = proof["status"]
            carrier = home / "healthy/ledger.json"
            assert consumer.verify(root, base, [carrier])["losses"] == 1
            saved = carrier.read_bytes()
            bad = json.loads(saved)
            bad["proof"]["runs"][0]["request_boundary"]["descendant_execution"] = True
            carrier.write_text(json.dumps(bad))
            try:
                with pytest.raises(
                    consumer.EvidenceError, match="execution-request-runtime-boundary"
                ):
                    consumer.verify(root, base, [carrier])
            finally:
                carrier.write_bytes(saved)
            assert consumer.verify(root, base, [carrier])["status"] == "PASS"
            if "request" in species:
                bad = json.loads(saved)
                bad["proof"]["runs"][0]["request_access"]["probe_accesses"] = 0
                carrier.write_text(json.dumps(bad))
                try:
                    with pytest.raises(
                        consumer.EvidenceError, match="execution-request-access-observation"
                    ):
                        consumer.verify(root, base, [carrier])
                finally:
                    carrier.write_bytes(saved)
                assert consumer.verify(root, base, [carrier])["status"] == "PASS"
            assert product.read_text() == (
                changed if species == "pure-local-alias-healthy" else before
            )
    return outcomes


def _assert_finalizer_rebinding(tmp_path):
    """Implicit reference release must not hide a renamed frame from the reader."""
    import condensation_lineage as lineage

    tmp_path.mkdir()
    root = _toy(tmp_path)
    source = Path(__file__).resolve().parents[2]
    shutil.copy2(source / "scripts/condensation_lineage.py", root / "scripts")
    consumer = evidence.sys.modules["check_condensation_ledger"]
    before = "def reply(a, /):\n    a=0\n    return a\n"
    current = before.replace("a", "b")
    product = root / "toy.py"
    product.write_text(before)
    (root / "observer.py").write_text(
        "import sys\nseen=[]\nclass Observe:\n"
        "    def __del__(self):\n        seen.append(tuple(sys._getframe(1).f_locals))\n"
    )
    owner = root / "tests/test_toy.py"
    owner.write_text(
        "from toy import reply\nfrom observer import Observe,seen\n"
        "def test_removed():\n    seen.clear()\n    value=reply(Observe())\n"
        "    assert value == 0\n    assert seen == [('a',)]\n"
        "def test_survivor():\n    value=reply(Observe())\n    assert value == 0\n"
    )

    def commit(message):
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "user.name=Proof",
                "-c",
                "user.email=proof@example.invalid",
                "commit",
                "-qm",
                message,
            ],
            check=True,
        )

    commit("original finalizer observes the overwritten parameter")
    base = evidence.git(root, "rev-parse", "HEAD")
    product.write_text(current)
    owner.write_text(
        "from toy import reply\nfrom observer import Observe,seen\n"
        "def test_survivor():\n    value=reply(Observe())\n    assert value == 0\n"
    )
    commit("renamed overwrite loses the finalizer invariant")
    config = _config()
    config.update(
        base=base,
        scope=["toy.py", "observer.py"],
        lineage={
            "mode": "paired",
            "scope": ["toy.py", "observer.py"],
            "functions": [
                {
                    "before": {"path": "toy.py", "function": "reply"},
                    "current": {"path": "toy.py", "function": "reply"},
                },
            ],
        },
    )
    category = "unsupported-indirect-namespace-binding"
    with pytest.raises(consumer.EvidenceError, match="proof-unknown"):
        evidence.prove(root, config, tmp_path / "current-refusal")
    assert evidence.strict_json(tmp_path / "current-refusal/receipt.json")["category"] == category
    original = lineage._transparent_binding_body
    try:
        # The predecessor's exact supported grammar accepted this overwrite.
        lineage._transparent_binding_body = lambda node, parameters: True
        weak = evidence.prove(root, config, tmp_path / "neutralized")
        assert weak["status"] == "PASS"
        carrier = tmp_path / "neutralized/ledger.json"
        assert consumer.verify(root, base, [carrier])["losses"] == 1
    finally:
        lineage._transparent_binding_body = original
    with pytest.raises(consumer.EvidenceError, match=category):
        consumer.verify(root, base, [carrier])
    product.write_text(before)
    commit("restore the exact original binding and finalizer behavior")
    healthy = evidence.prove(root, config, tmp_path / "restored-healthy")
    assert healthy["status"] == "PASS"
    assert (
        consumer.verify(root, base, [tmp_path / "restored-healthy/ledger.json"])["status"] == "PASS"
    )
    assert product.read_text() == before
    return healthy["status"]


def _assert_execution_request_is_not_runtime_data(tmp_path):
    tmp_path.mkdir()
    root = _toy(tmp_path)
    consumer = evidence.sys.modules["check_condensation_ledger"]
    request = root / "tests/condensation-plans/current.json"
    request.parent.mkdir()
    request.write_text(json.dumps(dict(_config(), base="event-base")))
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "user.name=Proof",
            "-c",
            "user.email=proof@example.invalid",
            "commit",
            "-qm",
            "actual source-owned execution request",
        ],
        check=True,
    )
    records = consumer.source_records(root, consumer.tracked_paths(root))
    controls = consumer.execution_request_inputs(root, records)
    assert controls == {"tests/condensation-plans/current.json"}
    selected = [_config()["removed"][0], _config()["survivors"][0]]
    original = (root / "toy.py").read_bytes()
    healthy = evidence.suite(
        root, selected, ["toy.py"], "1" * 64, tmp_path / "healthy.json", "healthy", 20, controls
    )
    assert healthy["complete"] and healthy["exit"] == 0
    product = root / "toy.py"
    product.write_text(
        "from pathlib import Path\n"
        + original.decode().replace(
            "    if flag:\n",
            "    try:\n"
            "        Path(__file__).with_name('tests').joinpath('condensation-plans/current.json').read_text()\n"
            "    except RuntimeError:\n        pass\n    if flag:\n",
        )
    )
    try:
        with pytest.raises(consumer.EvidenceError, match="worker-incomplete"):
            evidence.suite(
                root,
                selected,
                ["toy.py"],
                "2" * 64,
                tmp_path / "runtime-data.json",
                "runtime-data",
                20,
                controls,
            )
        refused = evidence.strict_json(tmp_path / "runtime-data.json")
        assert refused["complete"] is False and refused["exit"] == 0
        assert all(c["phases"]["call"] == "passed" for c in refused["cases"])
    finally:
        product.write_bytes(original)
    restored = evidence.suite(
        root, selected, ["toy.py"], "3" * 64, tmp_path / "restored.json", "restored", 20, controls
    )
    assert restored["complete"] and restored["exit"] == 0
    assert product.read_bytes() == original


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
    assert _direct_producer_diagnostic()

    assert _direct_namespace_unchanged(tmp_path)


def _direct_producer_diagnostic():
    diagnostics = [
        evidence.strict_json(p)
        for p in Path(os.environ["TEST_EVIDENCE_DIR"]).glob("condensation-refusal-*.json")
    ]
    producer_hash = hashlib.sha256(Path(evidence.__file__).read_bytes()).hexdigest()
    matches = [
        row
        for row in diagnostics
        if row["producer_binding"]
        and row["producer_binding"]["program_sha256"] == producer_hash
        and row["canonical_request_sha256"] == evidence.canonical(_config())
    ]
    assert matches and any(row["category"] == "worker-incomplete" for row in matches)
    assert all(row["status"] == "UNKNOWN" for row in matches)
    assert all("private operand" not in json.dumps(row) for row in matches)
    assert all(len(row["producer_binding"]["nonce"]) == 64 for row in matches)
    return True


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
    assert outcome != "timeout" or _assert_either_side_failure_cancels(
        tmp_path / "either-side-failure"
    )
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


def _assert_either_side_failure_cancels(tmp_path):
    # Both orders execute the same real worker failure beside a sleeping worker.
    # Observing only the first submitted future hides the second failure until
    # timeout; the original incomplete worker category must survive cancellation.
    tmp_path.mkdir()
    for removed, survivor in (("sleeper", "failure"), ("failure", "sleeper")):
        home = tmp_path / removed
        home.mkdir()
        root = _toy(home)
        (root / "tests/test_toy.py").write_text(
            "from toy import reply\nimport time\n"
            "def test_sleeper():\n    time.sleep(30)\n"
            "    assert reply(True) == 'accepted'\n"
            "def test_failure():\n    raise RuntimeError('synthetic call failure')\n"
        )
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "user.name=Proof",
                "-c",
                "user.email=proof@example.invalid",
                "commit",
                "-qm",
                "owned concurrent failure",
            ],
            check=True,
        )
        config = _config(
            "tests/test_toy.py::test_" + removed, "tests/test_toy.py::test_" + survivor
        )
        output = home / "proof"
        with pytest.raises(evidence.EvidenceError, match="proof-unknown"):
            evidence.prove(root, config, output, timeout=4)
        receipt = evidence.strict_json(output / "receipt.json")
        assert receipt == {
            "status": "UNKNOWN",
            "category": "worker-incomplete",
            "restored": True,
            "cleanup": True,
        }
        processes = [evidence.strict_json(p) for p in output.glob("*.process.json")]
        assert len(processes) == 2 and all(p["cleanup"] for p in processes)
        assert len({p["identity"]["group"] for p in processes}) == 2
        assert not Path(evidence.strict_json(output / "journal.json")["owned_copy"]).exists()
        failed = evidence.strict_json(
            output / (("removed" if removed == "failure" else "survivors") + "-baseline.json")
        )
        assert failed["complete"] is False and failed["exit"] == 1
    # Model the timeout notification racing with an actual terminal OS child.
    # A sibling refusal must not discard that child's completed observer input.
    cancellation = evidence.threading.Event()
    actual_popen = evidence.subprocess.Popen
    children = []

    class TerminalAtTimeout:
        def __init__(self, child):
            self.child = child
            self.notified = False

        def __getattr__(self, name):
            return getattr(self.child, name)

        def wait(self, timeout):
            code = self.child.wait(timeout=timeout)
            if not self.notified:
                self.notified = True
                cancellation.set()
                raise subprocess.TimeoutExpired("synthetic-terminal-child", timeout)
            return code

    def launch(*args, **kwargs):
        child = actual_popen(*args, **kwargs)
        children.append(child)
        return TerminalAtTimeout(child)

    record = tmp_path / "terminal-child.process.json"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(evidence.subprocess, "Popen", launch)
        assert evidence.owned_run(
            [sys.executable, "-I", "-c", "pass"],
            cwd=tmp_path,
            env=os.environ.copy(),
            timeout=2,
            process_record=record,
            cancellation=cancellation,
        ) == (0, True)
    assert len(children) == 1 and children[0].poll() == 0
    assert evidence.strict_json(record)["cleanup"] is True
    return True


def _disjoint_execution_phases(proof, config, output):
    # Every freshly executed side keeps its own request, SQLite file and group,
    # including both baselines and both mutant/restored sides of every phase.
    assert len(proof["runs"]) == 2 + 4 * len(proof["mutation"]["mutants"])
    assert [run["side"] for run in proof["runs"]] == ["removed", "survivors"] * (
        1 + 2 * len(proof["mutation"]["mutants"])
    )
    assert len({run["id"] for run in proof["runs"]}) == len(proof["runs"])
    assert len({run["coverage_artifact"]["path"] for run in proof["runs"]}) == len(proof["runs"])
    groups = set()
    roots = {}
    for run in proof["runs"]:
        carrier = output / run["artifact"]["path"]
        request = evidence.strict_json(carrier.with_suffix(".request.json"))
        process = evidence.strict_json(carrier.with_suffix(".process.json"))
        assert request["id"] == run["id"]
        assert request["nonce"] == evidence.strict_json(output / "ledger.json")["binding"]["nonce"]
        assert request["selection"] == config[run["side"]]
        assert request["scope"] == run["scope"]
        assert process["cleanup"] is True
        assert process["identity"]["group"] not in groups
        groups.add(process["identity"]["group"])
        roots.setdefault(run["side"], set()).add(request["root"])
    assert len(roots["removed"]) == len(roots["survivors"]) == 1
    assert roots["removed"].isdisjoint(roots["survivors"])
    return True


@pytest.fixture(autouse=True)
def _own_direct_diagnostic_namespace(request, tmp_path, monkeypatch):
    # Only this original diagnostic owner performs deliberate producer refusal.
    # Other owners keep their ordinary hosted publication destination.
    if request.function is test_setup_failure_is_unknown_with_durable_receipt:
        foreign_evidence = tmp_path / "foreign-evidence"
        foreign_evidence.mkdir()
        foreign = foreign_evidence / "condensation-refusal-unrelated.json"
        foreign.write_bytes(b"UNRELATED_SENTINEL\n")
        monkeypatch.setenv("TEST_EVIDENCE_DIR", str(foreign_evidence))
        own_evidence = tmp_path / "direct-evidence"
        own_evidence.mkdir()
        monkeypatch.setenv("TEST_EVIDENCE_DIR", str(own_evidence))


def _direct_namespace_unchanged(tmp_path):
    foreign = tmp_path / "foreign-evidence/condensation-refusal-unrelated.json"
    assert foreign.read_bytes() == b"UNRELATED_SENTINEL\n"
    assert list(foreign.parent.iterdir()) == [foreign]
    assert list((tmp_path / "direct-evidence").glob("condensation-refusal-*.json"))
    return True


def test_complete_whole_owner_mutations_cross_batch_boundary(tmp_path):
    """REQ-testing-053: all whole-owner mutants and restored phases remain mandatory."""
    root = _toy(tmp_path)
    path = root / "toy.py"
    before = path.read_text()
    count = len(evidence.generate_mutants({"toy.py": before.encode()}))
    path.write_text(before + "1 == 1\n" * (65 - count))
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
            "complete finite whole-owner subject",
        ],
        check=True,
    )
    original = path.read_bytes()
    assert len(evidence.generate_mutants({"toy.py": original})) == 65
    # Use the existing explicit bounded diagnostic option; the ordinary producer
    # default remains60 and this software subject earns no cluster timing claim.
    proof = evidence.prove(root, _config(), tmp_path / "complete", timeout=240)
    assert proof["status"] == "PASS"
    assert proof["mutation"]["generated"] == 65
    assert [len(row["mutants"]) for row in proof["mutation"]["batches"]] == [64, 1]
    assert all(row["status"] == "RESTORED" for row in proof["mutation"]["batches"])
    assert len(proof["runs"]) == 2 + 4 * 65
    assert proof["coverage"]["residue_arcs"] == 0
    assert proof["mutation"]["lost"] == []
    assert path.read_bytes() == original
    value = evidence.strict_json(tmp_path / "complete/ledger.json")
    reader = evidence.sys.modules["check_condensation_ledger"]

    def read(candidate):
        reader.executable_proof(
            tmp_path / "complete",
            candidate,
            value["binding"],
            value["removed"],
            value["survivors"],
            root=root,
        )

    read(proof)
    for corruption in ("missing", "duplicate", "order", "unfinished", "operation", "source"):
        changed = copy.deepcopy(proof)
        if corruption == "missing":
            changed["mutation"]["mutants"].pop()
            changed["mutation"]["generated"] -= 1
            changed["mutation"]["batches"].pop()
        elif corruption == "duplicate":
            changed["mutation"]["batches"].append(changed["mutation"]["batches"][0])
        elif corruption == "order":
            changed["mutation"]["batches"].reverse()
        elif corruption == "unfinished":
            changed["mutation"]["batches"][-1]["status"] = "UNKNOWN"
        elif corruption == "operation":
            changed["mutation"]["mutants"][0]["after_sha256"] = "0" * 64
        else:
            changed["mutation"]["sources"] = []
        with pytest.raises(evidence.EvidenceError):
            read(changed)
    read(proof)
