"""REQ-testing-053: one consumer refuses unproven loss and malformed evidence."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import check_condensation_ledger as ledger  # noqa: E402

pytestmark = pytest.mark.unit


def _repository(tmp_path):
    root = tmp_path / "repository"
    (root / "tests").mkdir(parents=True)
    (root / "tests/test_owner.py").write_text(
        "import pytest\n@pytest.mark.parametrize('value',[1,2])\n"
        "def test_owner(value):\n    assert value > 0\n    assert value in (1,2)\n"
    )
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
            "source before",
        ],
        check=True,
    )
    return root


def test_current_shape_reuse_preserves_modes_and_new_source_validation(tmp_path, monkeypatch):
    """REQ-testing-053: shared analysis cannot change admission or hide new invalid source."""
    root = _repository(tmp_path)
    owner = root / "tests/test_owner.py"
    original = ledger.test_shapes
    calls = []

    def observe(path, text):
        calls.append((path, text))
        return original(path, text)

    monkeypatch.setattr(ledger, "test_shapes", observe)
    assert ledger.verify(root, "HEAD", []) == {
        "status": "PASS",
        "losses": 0,
        "ledger_clusters": 0,
        "test_definitions": 1,
        "source_read_only": True,
    }
    assert calls.count(("tests/test_owner.py", owner.read_text())) == 1
    cache = {}
    owner.chmod(0o755)
    expected = {"tests/test_owner.py::test_owner": "source-mode-change"}
    assert ledger.destructive_changes(root, "HEAD", _current_shapes=cache) == expected
    assert ledger.destructive_changes(root, "HEAD") == expected
    assert all("mode" not in shape for shapes in cache.values() for shape in shapes.values())
    owner.chmod(0o644)
    added = root / "tests/test_added.py"
    added.write_text("def test_added():\n    assert True\n")
    subprocess.run(["git", "-C", str(root), "add", str(added)], check=True)
    assert ledger.verify(root, "HEAD", [])["test_definitions"] == 2
    added.write_text("def test_added(:\n")
    with pytest.raises(ledger.EvidenceError, match="test-source-parse-failed"):
        ledger.verify(root, "HEAD", [])


def test_current_shape_reuse_keeps_rereads_and_source_race_refusals(tmp_path, monkeypatch):
    """REQ-testing-053: a cached analysis never replaces current source or identity reads."""
    for change in ("body", "invalid-body", "mode", "membership", "head", "link"):
        root = _repository(tmp_path / change)
        owner = root / "tests/test_owner.py"
        original = ledger.tracked_paths
        reads = 0

        def race(repository, ref=None):
            nonlocal reads
            if ref is None:
                reads += 1
                if reads == 2:
                    if change in {"body", "invalid-body"}:
                        owner.write_text(
                            owner.read_text()
                            + ("    assert True\n" if change == "body" else "    (\n")
                        )
                    elif change == "mode":
                        owner.chmod(0o755)
                    elif change == "membership":
                        added = root / "tests/test_new.py"
                        added.write_text("def test_new():\n    assert True\n")
                        subprocess.run(["git", "-C", str(root), "add", str(added)], check=True)
                    elif change == "head":
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
                                "--allow-empty",
                                "-qm",
                                "changed head",
                            ],
                            check=True,
                        )
                    else:
                        target = root / "owner-copy.py"
                        owner.rename(target)
                        owner.symlink_to("../owner-copy.py")
            return original(repository, ref)

        with monkeypatch.context() as context:
            context.setattr(ledger, "tracked_paths", race)
            reason = (
                "test-source-parse-failed"
                if change == "invalid-body"
                else "source-changed-during-admission"
            )
            with pytest.raises(ledger.EvidenceError, match=reason):
                ledger.verify(root, "HEAD", [])


@pytest.mark.parametrize(
    "change",
    [
        "delete",
        "assertion",
        "parameter",
        "rename",
        "feature",
        "fixture",
        "unreachable",
        "early-return",
        "mode",
    ],
)
def test_existing_loss_cannot_hide_behind_name_or_deleted_source(tmp_path, change):
    """REQ-testing-053: existing assertions, execution and case context require survivors."""
    root = _repository(tmp_path)
    path = root / "tests/test_owner.py"
    before = path.read_text()
    original_mode = path.stat().st_mode & 0o777
    assert ledger.verify(root, "HEAD", [])["status"] == "PASS"
    if change in {"delete", "feature"}:
        path.unlink()
    elif change == "assertion":
        path.write_text(before.replace("    assert value in (1,2)\n", ""))
    elif change == "mode":
        path.chmod(0o755)
    elif change == "unreachable":
        path.write_text(
            before.replace(
                "    assert value in (1,2)\n", "    if False:\n        assert value in (1,2)\n"
            )
        )
    elif change == "early-return":
        path.write_text(
            before.replace("    assert value > 0\n", "    return\n    assert value > 0\n")
        )
    elif change == "parameter":
        path.write_text(before.replace("[1,2]", "[1]"))
    elif change == "fixture":
        path.write_text(
            before.replace("import pytest", "import pytest\npytestmark = pytest.mark.skip")
        )
    else:
        path.write_text(before.replace("test_owner", "test_renamed"))
    if change == "feature":
        (root / "removed_product.py").write_text("# Source absence is not retirement authority.\n")
    with pytest.raises(ledger.EvidenceError, match="unproven"):
        ledger.verify(root, "HEAD", [])
    path.write_text(before)
    path.chmod(original_mode)
    assert ledger.verify(root, "HEAD", [])["status"] == "PASS"


@pytest.mark.parametrize("body", ['{"schema":1,"schema":2}', "[]", '{"value":NaN}', "{broken"])
def test_strict_json_never_coerces_or_discards_ambiguity(tmp_path, body):
    """REQ-testing-053: malformed and ambiguous serialized evidence refuses."""
    path = tmp_path / "ledger.json"
    path.write_text(body)
    with pytest.raises(ledger.EvidenceError):
        ledger.strict_json(path)


def test_assertion_addition_preserves_original_order(tmp_path):
    """REQ-testing-053: pure assertion addition preserves the complete original execution."""
    root = _repository(tmp_path)
    path = root / "tests/test_owner.py"
    path.write_text(path.read_text() + "    assert isinstance(value,int)\n")
    assert ledger.verify(root, "HEAD", [])["status"] == "PASS"
    base = ledger.git(root, "rev-parse", "HEAD")
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
            "preserving assertion addition",
        ],
        check=True,
    )
    head = ledger.git(root, "rev-parse", "HEAD")
    assert (
        ledger.ci_binding(root, event="push", base=base, expected_head=head, source_head=head)
        == base
    )
    assert ledger.verify(root, base, [])["status"] == "PASS"
    for event, before, expected, source in (
        ("push", head, head, head),
        ("push", base, base, head),
        ("merge_group", base, head, base),
        ("pull_request", base, head, head),
        ("other", base, head, head),
        ("push", "0" * 40, head, head),
    ):
        with pytest.raises(ledger.EvidenceError):
            ledger.ci_binding(
                root, event=event, base=before, expected_head=expected, source_head=source
            )
        assert (
            ledger.ci_binding(
                root, event="merge_group", base=base, expected_head=head, source_head=head
            )
            == base
        )

    # Build the actual two-parent PR checkout; no metadata-only union claim.
    def command(*args):
        return subprocess.run(
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
                *args,
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    command("branch", "source", head)
    command("checkout", "-q", "-b", "base", base)
    (root / "public-note.md").write_text("Independent base history.\n")
    command("add", ".")
    command("commit", "-qm", "independent base")
    newer_base = ledger.git(root, "rev-parse", "HEAD")
    command("merge", "--no-ff", "-qm", "exact PR union", "source")
    union = ledger.git(root, "rev-parse", "HEAD")
    assert (
        ledger.ci_binding(
            root, event="pull_request", base=newer_base, expected_head=union, source_head=head
        )
        == newer_base
    )
    assert (
        ledger.main(
            [
                "--repo-root",
                str(root),
                "--base",
                newer_base,
                "--ci-event",
                "pull_request",
                "--expected-head",
                union,
                "--source-head",
                head,
            ]
        )
        == 0
    )
    assert (
        ledger.main(
            [
                "--repo-root",
                str(root),
                "--base",
                newer_base,
                "--ci-event",
                "pull_request",
                "--expected-head",
                union,
                "--source-head",
                base,
            ]
        )
        == 1
    )
    assert ledger.verify(root, newer_base, [])["status"] == "PASS"
    command("rm", "-q", "tests/test_owner.py")
    command("commit", "-qm", "empty tracked test inventory")
    with pytest.raises(ledger.EvidenceError, match="empty-baseline-test-inventory"):
        ledger.verify(root, "HEAD", [])
    command("checkout", "-q", "source")
    assert ledger.verify(root, base, [])["status"] == "PASS"

    assert _fresh_refusal_diagnostic(tmp_path)
    assert _ci_union_event_base(tmp_path)


def _ci_union_event_base(tmp_path):
    root = _repository(tmp_path / "union")

    def command(*args, input=None):
        return subprocess.run(
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
                *args,
            ],
            check=True,
            capture_output=True,
            text=True,
            input=input,
        ).stdout.strip()

    base = command("rev-parse", "HEAD")
    command("checkout", "-qb", "source")
    (root / "source-note.md").write_text("Source branch.\n")
    command("add", ".")
    command("commit", "-qm", "source")
    source = command("rev-parse", "HEAD")
    command("checkout", "-qb", "public", base)
    (root / "public-note.md").write_text("New public history.\n")
    command("add", ".")
    command("commit", "-qm", "public")
    public = command("rev-parse", "HEAD")
    command("merge", "--no-ff", "-qm", "provider union", "source")
    union = command("rev-parse", "HEAD")
    assert command("rev-list", "--parents", "-n", "1", union).split()[1:] == [public, source]
    assert (
        ledger.ci_binding(
            root, event="pull_request", base=base, expected_head=union, source_head=source
        )
        == base
    )
    assert (
        ledger.main(
            [
                "--repo-root",
                str(root),
                "--base",
                base,
                "--ci-event",
                "pull_request",
                "--expected-head",
                union,
                "--source-head",
                source,
            ]
        )
        == 0
    )
    assert (
        ledger.ci_binding(
            root, event="pull_request", base=public, expected_head=union, source_head=source
        )
        == public
    )
    # A valid ancestor cannot excuse a wrong source, reversed parents or a
    # checkout whose body is not the exact merge of its declared parents.
    with pytest.raises(ledger.EvidenceError):
        ledger.ci_binding(
            root, event="pull_request", base=base, expected_head=union, source_head=base
        )
    wrong_tree = command(
        "commit-tree",
        command("rev-parse", "source^{tree}"),
        "-p",
        public,
        "-p",
        source,
        input="Not the declared union.\n",
    )
    command("checkout", "-q", "--detach", wrong_tree)
    with pytest.raises(ledger.EvidenceError, match="ci-pr-union-tree-mismatch"):
        ledger.ci_binding(
            root, event="pull_request", base=base, expected_head=wrong_tree, source_head=source
        )
    reversed_union = command(
        "commit-tree",
        command("rev-parse", union + "^{tree}"),
        "-p",
        source,
        "-p",
        public,
        input="Reversed original parents.\n",
    )
    command("checkout", "-q", "--detach", reversed_union)
    with pytest.raises(ledger.EvidenceError):
        ledger.ci_binding(
            root, event="pull_request", base=base, expected_head=reversed_union, source_head=source
        )
    command("checkout", "-q", "--detach", union)
    with pytest.raises(ledger.EvidenceError):
        ledger.ci_binding(
            root, event="pull_request", base=source, expected_head=union, source_head=source
        )
    assert (
        ledger.ci_binding(
            root, event="pull_request", base=base, expected_head=union, source_head=source
        )
        == base
    )
    # The full event-base scan still observes a deletion introduced in the
    # public history; an exact merge alone cannot grant survivor admission.
    command("checkout", "-q", "-b", "public-loss", public)
    command("rm", "-q", "tests/test_owner.py")
    command("commit", "-qm", "unproved public loss")
    command("merge", "--no-ff", "-qm", "union with unproved loss", "source")
    lost_union = command("rev-parse", "HEAD")
    assert (
        ledger.ci_binding(
            root, event="pull_request", base=base, expected_head=lost_union, source_head=source
        )
        == base
    )
    assert (
        ledger.main(
            [
                "--repo-root",
                str(root),
                "--base",
                base,
                "--ci-event",
                "pull_request",
                "--expected-head",
                lost_union,
                "--source-head",
                source,
            ]
        )
        == 1
    )
    command("checkout", "-q", "--detach", union)
    assert ledger.verify(root, base, [])["status"] == "PASS"
    return True


def test_callback_body_exits_do_not_exit_the_preserved_owner(tmp_path):
    """REQ-testing-053: definition-time exits still refuse beside callback bodies."""
    import ast

    root = _repository(tmp_path)
    path = root / "tests/test_owner.py"
    original = path.read_text()
    owner = "tests/test_owner.py::test_owner"
    callbacks = (
        "    def choose():\n        return value\n"
        "    async def reject():\n        raise RuntimeError('synthetic callback')\n"
        "    delayed = lambda: (yield value)\n"
    )
    healthy = original.replace("    assert value > 0\n", callbacks + "    assert value > 0\n")
    path.write_text(healthy)
    ledger.same_owner_context_accounts(root, "HEAD", {owner})
    namespace = {}
    exec(compile(healthy, str(path), "exec"), namespace)
    namespace["test_owner"](1)
    with pytest.raises(AssertionError):
        namespace["test_owner"](0)

    for outer in (
        "    return\n",
        "    raise RuntimeError('synthetic outer exit')\n",
        "    yield value\n",
        "    def callback(probe=(yield value)):\n        return probe\n",
        "    @(yield value)\n    def callback():\n        return value\n",
        "    class Immediate:\n        raise RuntimeError('synthetic class exit')\n",
    ):
        path.write_text(healthy.replace("    assert value > 0\n", outer + "    assert value > 0\n"))
        with pytest.raises(ledger.EvidenceError, match="same-owner-context-not-preserving"):
            ledger.same_owner_context_accounts(root, "HEAD", {owner})
        path.write_text(healthy)
        ledger.same_owner_context_accounts(root, "HEAD", {owner})
    # Existing callback statements still retain their own literal exit/order.
    old = ledger.ast_record(ast.parse("def callback():\n    return 1\n").body[0])
    changed = ledger.ast_record(ast.parse("def callback():\n    return 0\n    return 1\n").body[0])
    assert not ledger.preserving_context(old, changed)
    path.write_text(original)
    ledger.same_owner_context_accounts(root, "HEAD", {owner})


def test_closed_comprehension_captures_retain_literal_lexical_identity():
    """REQ-testing-053: captured frames cannot borrow parameter alpha-equivalence."""
    import condensation_lineage as lineage
    import condense_evidence as evidence

    original = (
        "def reply(items, /):\n"
        "    observed = {item: item for item in items}\n"
        "    if len(items) > 0:\n"
        "        return [observed[item] for item in items if item in observed]\n"
        "    return []\n"
    )
    current = original.replace("    if len(items)", "    pass\n    if len(items)")
    pair = [
        {
            "before": {"path": "a.py", "function": "reply"},
            "current": {"path": "b.py", "function": "reply"},
        }
    ]
    points = lineage.alignment({"a.py": original}, {"b.py": current}, pair)
    old_mutants = evidence.generate_mutants({"a.py": original.encode()})
    new_mutants = evidence.generate_mutants({"b.py": current.encode()})
    matched = lineage.paired_mutants(points, old_mutants, new_mutants)
    assert len(matched) == len(old_mutants) and matched
    namespace = {}
    exec(current, namespace)
    assert namespace["reply"]([1, 2]) == [1, 2]
    assert namespace["reply"]([]) == []

    for changed in (
        original.replace("items", "renamed"),
        original.replace("for item in items", "for items in items", 1),
        original.replace("observed[item]", "missing[item]"),
        original.replace("observed[item]", "globals()[item]"),
        original.replace("observed[item]", "(lambda: items)()"),
        original.replace("observed[item]", "(captured := items)"),
        original.replace("observed[item]", "[child for child in items]"),
        original.replace("if item in observed", "if item not in observed"),
    ):
        with pytest.raises(ledger.EvidenceError):
            changed_points = lineage.alignment({"a.py": original}, {"b.py": changed}, pair)
            lineage.paired_mutants(
                changed_points, old_mutants, evidence.generate_mutants({"b.py": changed.encode()})
            )
        restored_points = lineage.alignment({"a.py": original}, {"b.py": current}, pair)
        assert len(lineage.paired_mutants(restored_points, old_mutants, new_mutants)) == len(
            old_mutants
        )


def _fresh_refusal_diagnostic(tmp_path):
    # Exercise the actual fresh-child refusal boundary without executing a
    # second expensive proof. Seeded child carriers are diagnostics, not proof.
    import pre_push

    root = _repository(tmp_path / "diagnostic")
    path = root / "tests/test_owner.py"
    base = ledger.git(root, "rev-parse", "HEAD")
    plan_dir = root / "tests/condensation-plans"
    plan_dir.mkdir()
    request = plan_dir / "current.json"
    owner = "tests/test_owner.py::test_owner"
    request.write_text(
        json.dumps(
            {
                "base": "event-base",
                "scope": ["tests/test_owner.py"],
                "removed": [owner],
                "survivors": [owner],
                "cluster": "diagnostic",
                "bead": "bu-ly3lv5.11",
                "contract": {"class": "wire", "cites": ["REQ-testing-053"]},
                "mapping": {owner: {"survivors": [owner], "reason": "diagnostic refusal"}},
            }
        )
    )
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    path.write_text(path.read_text().replace("    assert value in (1,2)\n", ""))

    def refused_child(name, arguments, cwd, *, timeout):
        assert name == "condensation-proof" and cwd == root and timeout == 70
        run = Path(arguments[arguments.index("--output") + 1])
        run.mkdir()
        (run / "receipt.json").write_text(
            json.dumps(
                {
                    "status": "UNKNOWN",
                    "category": "worker-incomplete",
                    "restored": True,
                    "cleanup": True,
                    "private": "unpublished-operand",
                }
            )
        )
        (run / "baseline.request.json").write_text(
            json.dumps(
                {
                    "nonce": "a" * 64,
                    "output": "/untrusted/output",
                    "private": "unpublished-operand",
                }
            )
        )
        (run / "baseline.json").write_text(
            json.dumps(
                {
                    "complete": False,
                    "core": "ctrace",
                    "exit": 2,
                    "selected": 2,
                    "deselected": 0,
                    "request_boundary": {"runtime_read": False, "descendant_execution": False},
                }
            )
        )
        (run / "baseline.access.json").write_text(
            json.dumps(
                {
                    "complete": False,
                    "accesses": 0,
                    "probe_accesses": 0,
                }
            )
        )
        raise pre_push.Refusal("condensation-proof:exit-1")

    evidence_home = tmp_path / "diagnostic-evidence"
    evidence_home.mkdir()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("TEST_EVIDENCE_DIR", str(evidence_home))
        patch.setattr(pre_push, "run_checked", refused_child)
        assert ledger.main(["--repo-root", str(root), "--base", base]) == 1
    retained = list((root / ".tmp/condensation-ci").glob("run-*/proof-0-refusal.json"))
    assert len(retained) == 1
    diagnostic = ledger.strict_json(retained[0])
    evidence_paths = list(evidence_home.glob("condensation-refusal-*.json"))
    assert len(evidence_paths) == 1
    assert ledger.strict_json(evidence_paths[0]) == diagnostic
    assert diagnostic["outer"] == "condensation-proof:exit-1"
    assert diagnostic["category"] == "worker-incomplete"
    assert diagnostic["canonical_request_sha256"] == ledger.canonical(
        ledger.strict_json(retained[0].parent / "plan-0.json")
    )
    worker = diagnostic["worker_requests"][0]
    assert worker["nonce"] == "a" * 64 and worker["exit"] == 2
    assert worker["access_complete"] is False and worker["probe_accesses"] == 0
    assert "unpublished-operand" not in retained[0].read_text()
    run = retained[0].parent / "proof-0"
    (run / "receipt.json").write_text('{"status":[],"category":{"private":"unpublished-operand"}}')
    malformed = ledger.fresh_proof_refusal(
        request, run, {}, pre_push.Refusal("unpublished-operand")
    )
    assert malformed["outer"] == "unclassified" and malformed["status"] == "unknown"
    assert malformed["category"] == "unclassified" and malformed["restored"] is None
    assert "unpublished-operand" not in retained[0].read_text()
    # Restore the original ordered source, not merely a green last assertion.
    path.write_bytes(
        subprocess.check_output(["git", "-C", str(root), "show", "HEAD:tests/test_owner.py"])
    )
    assert ledger.verify(root, base, [])["status"] == "PASS"
    # Fault publication deliberately replaces outputs and writes. Confine every
    # actual old helper to an owned namespace, never the shard upload directory.
    fault_evidence = tmp_path / "fault-evidence"
    fault_evidence.mkdir()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("TEST_EVIDENCE_DIR", str(fault_evidence))
        assert _bounded_refusal_carriers(tmp_path)
        assert _bounded_refusal_publication(tmp_path)
    assert list(fault_evidence.glob("condensation-refusal-*.json"))
    # Repeat the same original tracked request with a distinct execution nonce.
    # Exclusive publication must preserve both closed diagnostics.
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("TEST_EVIDENCE_DIR", str(evidence_home))
        (run / "receipt.json").write_text('{"status":"UNKNOWN","category":"worker-incomplete"}')
        (run / "baseline.request.json").write_text(json.dumps({"nonce": "b" * 64}))
        replay = ledger.fresh_proof_refusal(
            retained[0].parent / "plan-0.json",
            run,
            ledger.input_record(root, "tests/condensation-plans/current.json"),
            pre_push.Refusal("condensation-proof:exit-1"),
        )
    bodies = [ledger.strict_json(p) for p in evidence_home.glob("condensation-refusal-*.json")]
    assert len(bodies) == 2 and diagnostic in bodies and replay in bodies
    return True


def _bounded_refusal_carriers(tmp_path):
    import os

    home = tmp_path / "bounded"
    home.mkdir()
    config = home / "config.json"
    config.write_text("{}\n")
    run = home / "proof-0"
    run.mkdir()
    receipt = run / "receipt.json"
    code = (
        "import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
        "import check_condensation_ledger as l; from pre_push import Refusal; "
        "value=l.fresh_proof_refusal(Path(sys.argv[2]),Path(sys.argv[3]),{},"
        "Refusal('condensation-proof:exit-1')); "
        "assert value['receipt_available'] is (sys.argv[4]=='regular'); "
        "assert value['status']==('UNKNOWN' if sys.argv[4]=='regular' else 'unknown')"
    )

    def execute(kind):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                code,
                str(Path(ledger.__file__).parent),
                str(config),
                str(run),
                kind,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=2,
        )
        assert result.returncode == 0 and result.stderr == b""

    receipt.write_text('{"status":"UNKNOWN","category":"worker-incomplete"}\n')
    execute("regular")
    receipt.unlink()
    os.mkfifo(receipt)
    execute("fifo")
    receipt.unlink()
    receipt.symlink_to(config)
    execute("symlink")
    receipt.unlink()
    receipt.mkdir()
    execute("directory")
    receipt.rmdir()
    receipt.write_text("x" * (1024 * 1024 + 1))
    execute("oversized")
    receipt.write_text('{"status":"UNKNOWN","category":"worker-incomplete"}\n')
    execute("regular")

    # The byte bound does not make valid, deeply nested JSON safe to decode
    # or project. Exercise the actual descriptor reader in its own child.
    deep = "[" * 10000 + "0" + "]" * 10000
    receipt.write_text('{"category":' + deep + "}")
    execute("deep-json")
    receipt.write_text('{"category":' + "1" * 10000 + "}")
    execute("integer-conversion")
    receipt.write_text('{"status":"UNKNOWN","category":"worker-incomplete"}\n')
    execute("regular")

    # Each request/result/access carrier passes through that same reader.
    config.write_text("{}\n")
    for name, field, expected in [
        ("baseline.request.json", "available", False),
        ("baseline.json", "result_available", False),
        ("baseline.access.json", "access_complete", None),
    ]:
        (run / "baseline.request.json").write_text(json.dumps({"nonce": "a" * 64}))
        (run / "baseline.json").write_text("{}\n")
        (run / "baseline.access.json").write_text("{}\n")
        (run / name).write_text('{"private":' + deep + "}")
        summary = ledger.fresh_proof_refusal(
            config, run, {}, RuntimeError("condensation-proof:exit-1")
        )
        assert summary["worker_requests"][0][field] is expected
    return True


def _bounded_refusal_publication(tmp_path):
    import stat

    home = tmp_path / "publication"
    home.mkdir()
    child = home / "child.py"
    child.write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "run=Path(sys.argv[1]); kind=sys.argv[2]; run.mkdir()\n"
        "foreign=run.parents[3]/'foreign'; foreign.mkdir()\n"
        "sentinel=foreign/'sentinel'; sentinel.write_text('UNRELATED_SENTINEL\\n')\n"
        "receipt={'status':'UNKNOWN','category':'worker-incomplete'}\n"
        "(run/'receipt.json').write_text(json.dumps(receipt) if kind!='malformed' else '{}')\n"
        "output=run.parent/(run.name+'-refusal.json')\n"
        "if kind=='fifo': os.mkfifo(output)\n"
        "if kind=='symlink': output.symlink_to(sentinel)\n"
        "if kind=='hardlink': os.link(sentinel,output)\n"
        "if kind=='directory': output.mkdir()\n"
        "if kind=='occupied': output.write_text('OCCUPIED_SENTINEL\\n')\n"
        "if kind=='parent-symlink':\n"
        " original=run.parent; original.rename(original.with_name(original.name+'-original'))\n"
        " original.symlink_to(foreign,target_is_directory=True)\n"
        "sys.exit(1)\n"
    )
    code = (
        "import json,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
        "import check_condensation_ledger as l; import pre_push; import os; "
        "write=os.write; "
        'exec("def fail_write(fd,body):\\n'
        " if sys.argv[5] not in {'write-error','replaced-write-error'}: return write(fd,body)\\n"
        " if sys.argv[5]=='replaced-write-error':\\n"
        "  output=Path(os.readlink('/proc/self/fd/'+str(fd)))\\n"
        "  output.unlink(); output.symlink_to(Path(sys.argv[3])/'foreign/sentinel')\\n"
        " raise OSError('fixed-injected-write-failure')\"); "
        "os.write=fail_write; real=pre_push.run_checked; "
        'exec("def child(name,args,cwd,*,timeout):\\n'
        " assert name=='condensation-proof' and timeout==70\\n"
        " run=args[args.index('--output')+1]\\n"
        ' return real(name,[sys.executable,sys.argv[2],run,sys.argv[5]],cwd,timeout=timeout)"); '
        "pre_push.run_checked=child; "
        "assert l.main(['--repo-root',sys.argv[3],'--base',sys.argv[4]])==1; "
        "print('COMPLETE_REAL_CHILD_REFUSAL')"
    )
    for kind in (
        "regular",
        "fifo",
        "symlink",
        "hardlink",
        "directory",
        "occupied",
        "parent-symlink",
        "malformed",
        "write-error",
        "replaced-write-error",
        "restored",
    ):
        root = _repository(home / kind)
        base = ledger.git(root, "rev-parse", "HEAD")
        owner = "tests/test_owner.py::test_owner"
        plans = root / "tests/condensation-plans"
        plans.mkdir()
        (plans / "current.json").write_text(
            json.dumps(
                {
                    "base": "event-base",
                    "scope": ["tests/test_owner.py"],
                    "removed": [owner],
                    "survivors": [owner],
                    "cluster": "diagnostic",
                    "bead": "bu-ly3lv5.11",
                    "contract": {"class": "wire", "cites": ["REQ-testing-053"]},
                    "mapping": {owner: {"survivors": [owner], "reason": "diagnostic refusal"}},
                }
            )
        )
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        test = root / "tests/test_owner.py"
        test.write_text(test.read_text().replace("    assert value in (1,2)\n", ""))
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                code,
                str(Path(ledger.__file__).parent),
                str(child),
                str(root),
                base,
                kind,
            ],
            capture_output=True,
            timeout=3,
        )
        assert result.returncode == 0 and result.stderr == b""
        assert b"COMPLETE_REAL_CHILD_REFUSAL" in result.stdout
        assert b"fresh-condensation-proof-refused" in result.stdout
        sentinel = root / "foreign/sentinel"
        assert sentinel.read_text() == "UNRELATED_SENTINEL\n"
        assert stat.S_ISREG(sentinel.stat().st_mode)
        outputs = list((root / ".tmp/condensation-ci").glob("run-*/proof-0-refusal.json"))
        if kind in {"regular", "malformed", "restored", "parent-symlink"}:
            assert len(outputs) == 1
            summary = ledger.strict_json(outputs[0])
            assert summary["outer"] == "condensation-proof:exit-1"
            assert summary["status"] == (
                "unknown" if kind in {"malformed", "parent-symlink"} else "UNKNOWN"
            )
            assert not (root / "foreign/proof-0-refusal.json").exists()
        elif kind == "write-error":
            assert outputs[0].read_bytes() == b""
        elif kind == "replaced-write-error":
            assert outputs[0].is_symlink() and outputs[0].resolve() == sentinel.resolve()
        elif kind == "fifo":
            assert stat.S_ISFIFO(outputs[0].lstat().st_mode)
        elif kind == "symlink":
            assert outputs[0].is_symlink() and outputs[0].resolve() == sentinel.resolve()
        elif kind == "hardlink":
            assert outputs[0].stat().st_ino == sentinel.stat().st_ino
        elif kind == "directory":
            assert outputs[0].is_dir()
        else:
            assert outputs[0].read_text() == "OCCUPIED_SENTINEL\n"
        # Every actual failed child is complete before bounded publication.
        assert result.stdout.count(b'"kind": "fresh-condensation-proof-refusal"') == 1
    return True


def test_mutations_select_whole_targets_at_shared_coordinates():
    """REQ-testing-053: a child at the same position cannot replace its mutation target."""
    import condensation_lineage as lineage
    import condense_evidence as evidence

    original = (
        "def reply(item):\n"
        "    first = item.value == 1\n"
        "    second = item[0] != 2\n"
        "    if item.value:\n"
        "        first = 3\n"
        "    if item[0]:\n"
        "        second = 4\n"
        "    return first, second\n"
    )
    current = original.replace("    first =", "    pass\n    first =", 1)
    points = lineage.alignment({"toy.py": original}, {"toy.py": current}, [])
    old = evidence.generate_mutants({"toy.py": original.encode()})
    new = evidence.generate_mutants({"toy.py": current.encode()})
    keys = [lineage.mutation_key(points["removed"], row) for row in old]
    assert len(set(keys)) == len(old)
    matched = lineage.paired_mutants(points, old, new)
    assert len(matched) == len(old) and matched

    # Select the whole expression even when Attribute/Subscript chains have
    # several nodes of the same kind and starting coordinate.
    chained = (
        "def reply(item):\n    if item.value.other:\n        item = item[0][1]\n    return item\n"
    )
    chained_points = lineage.alignment({"toy.py": chained}, {"toy.py": chained}, [])
    mutations = evidence.generate_mutants({"toy.py": chained.encode()})
    selected = next(m for m in mutations if m["operator"] == "negate-condition")
    assert selected["target"]["kind"] == "Attribute"
    assert "attr='other'" in selected["target"]["preimage"]
    assert lineage.paired_mutants(chained_points, mutations, mutations)

    # A literal preimage only selects within its parsed source; it cannot grant
    # equivalence to a changed operator, bound name, child or enclosing guard.
    import copy

    for change in (
        original.replace("== 1", "== 9"),
        original.replace("!= 2", "== 2"),
        original.replace("item.value", "item.other"),
        original.replace("item[0]", "item[1]"),
        original.replace("    first =", "    if item:\n        first =", 1),
    ):
        with pytest.raises(ledger.EvidenceError):
            changed_points = lineage.alignment({"toy.py": original}, {"toy.py": change}, [])
            lineage.paired_mutants(
                points=changed_points,
                before=old,
                current=evidence.generate_mutants({"toy.py": change.encode()}),
            )
        assert len(lineage.paired_mutants(points, old, new)) == len(old)

    for field, value in (("kind", "Name"), ("end_column", True), ("preimage", "Name(id='fake')")):
        forged = copy.deepcopy(selected)
        forged["target"][field] = value
        with pytest.raises(ledger.EvidenceError):
            lineage.mutation_key(chained_points["removed"], forged)
    unmarked = {k: v for k, v in selected.items() if k != "target"}
    with pytest.raises(ledger.EvidenceError, match="invalid-mutation-target"):
        lineage.mutation_key(chained_points["removed"], unmarked)
    # Genuine duplicate selections remain ambiguous; no first/last-match rule.
    forged_points = copy.deepcopy(chained_points)
    coordinate = (selected["line"], selected["column"])
    candidates = forged_points["removed"]["toy.py"]["spans"][coordinate]
    candidates.append(
        copy.deepcopy(next(p for p in candidates if p["target"] == selected["target"]))
    )
    with pytest.raises(ledger.EvidenceError, match="ambiguous-mutation-source-target"):
        lineage.mutation_key(forged_points["removed"], selected)

    nested = (
        "def reply(item):\n"
        "    def inner(item):\n        if item == 1:\n            item = 2\n        return item\n"
        "    if item == 1:\n        item = 2\n    return inner(item)\n"
    )
    renamed = nested.replace("def reply", "def other", 1)
    aliases = [
        {
            "before": {"path": "a.py", "function": f},
            "current": {"path": "b.py", "function": f.replace("reply", "other", 1)},
        }
        for f in ("reply", "reply.inner")
    ]
    nested_points = lineage.alignment({"a.py": nested}, {"b.py": renamed}, aliases)
    before = evidence.generate_mutants({"a.py": nested.encode()})
    after = evidence.generate_mutants({"b.py": renamed.encode()})
    assert len(lineage.paired_mutants(nested_points, before, after)) == len(before)
    assert len({lineage.mutation_key(nested_points["removed"], m) for m in before}) == len(before)
    repeated = "def reply(item):\n    first = item == 1\n    second = item == 1\n    return first, second\n"
    repeat_points = lineage.alignment({"toy.py": repeated}, {"toy.py": repeated}, [])
    repeated_mutants = evidence.generate_mutants({"toy.py": repeated.encode()})
    with pytest.raises(ledger.EvidenceError, match="ambiguous-paired-mutation"):
        lineage.paired_mutants(repeat_points, repeated_mutants, repeated_mutants)

    # Same loop headers have distinct complete executing control bodies. Their
    # identity is not their ordinal, line or a first/last duplicate choice.
    loops = (
        "def reply(items):\n"
        "    for item in items:\n        if item == 1:\n            continue\n        item += 2\n"
        "    for item in items:\n        if item == 1:\n            continue\n        item += 3\n"
        "    return items\n"
    )
    loop_points = lineage.alignment({"toy.py": loops}, {"toy.py": loops}, [])
    loop_mutants = evidence.generate_mutants({"toy.py": loops.encode()})
    assert len(lineage.paired_mutants(loop_points, loop_mutants, loop_mutants)) == len(loop_mutants)
    changed_loop = loops.replace("item += 3", "item += 4")
    with pytest.raises(ledger.EvidenceError, match="missing-paired-source-mutation"):
        lineage.paired_mutants(
            lineage.alignment({"toy.py": loops}, {"toy.py": changed_loop}, []),
            loop_mutants,
            evidence.generate_mutants({"toy.py": changed_loop.encode()}),
        )


# Synthetic parser inputs are not citations to repository requirements.
_EXAMPLE_ID = "REQ-" + "example-001"
_FORGED_ID = "REQ-" + "forged-001"
_OTHER_ID = "REQ-" + "other-001"


def _contract_repository(tmp_path, after=None, timeout=60, *, candidate_helper=False, omit=()):
    """Build real protected authority separately from its candidate request."""
    import contract_evolution as evolution

    root = _repository(tmp_path)
    owner = "tests/test_owner.py::test_owner"
    old = (root / "tests/test_owner.py").read_text()
    new = after if after is not None else old.replace("    assert value in (1,2)\n", "")
    spec = f"### Requirement: Example\nID: {_EXAMPLE_ID}\n\nSHALL check positive values.\n"
    updated = spec.replace("positive", "nonzero positive")
    (root / "spec.md").write_text(spec)
    (root / "pyproject.toml").write_text('[tool.pytest.ini_options]\naddopts = ""\n')
    (root / "uv.lock").write_text("version = 1\n")
    (root / "spec-link").symlink_to("spec.md")

    def commit(message):
        ledger.git(root, "add", ".")
        ledger.git(
            root,
            "-c",
            "user.name=Proof",
            "-c",
            "user.email=proof@example.invalid",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "-qm",
            message,
        )
        return ledger.git(root, "rev-parse", "HEAD")

    for name in evolution.MACHINERY:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((Path(evolution.__file__).resolve().parents[1] / name).read_bytes())
    helper = root / "scripts/contract_lifecycle.py"
    helper_body = helper.read_bytes()
    if candidate_helper:
        helper.unlink()
    base = commit("complete original contract")
    if candidate_helper:
        helper.write_bytes(helper_body)
    request = {
        "schema": evolution.REQUEST_SCHEMA,
        "authorization": "example-change",
        "owners": [owner],
        "timeout_s": timeout,
    }
    request_path = root / evolution.REQUESTS / "example.json"
    request_path.parent.mkdir(parents=True)
    request_path.write_text(json.dumps(request))
    (root / "tests/test_owner.py").write_text(new)
    (root / "spec.md").write_text(updated)
    authority = {
        "schema": evolution.AUTH_SCHEMA,
        "id": "example-change",
        "bead": "example.1",
        "decision": {"owner": "Owner", "date": "2026-10-15", "reference": "independent review"},
        "owners": [
            {
                "before": owner,
                "after": owner,
                "contracts": [_EXAMPLE_ID],
                "reason": "Change the explicit expectation while retaining every case.",
            }
        ],
        "contracts": [
            {
                "path": "spec.md",
                "id": _EXAMPLE_ID,
                "before_sha256": evolution.requirement_digest(spec, _EXAMPLE_ID),
                "after_sha256": evolution.requirement_digest(updated, _EXAMPLE_ID),
            }
        ],
        "inputs": [
            {
                "path": path,
                "before": evolution.record_at(root, base, path),
                "after": evolution.current_record(root, path),
            }
            for path in (
                "tests/test_owner.py",
                "pyproject.toml",
                "uv.lock",
                (evolution.REQUESTS + "example.json"),
                *(("scripts/contract_lifecycle.py",) if candidate_helper else ()),
            )
            if path not in omit
        ],
    }
    auth_path = root / evolution.AUTHORIZATIONS / "example-change.json"
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text(json.dumps(authority))
    (root / "tests/test_owner.py").write_text(old)
    (root / "spec.md").write_text(spec)
    request_path.unlink()
    if candidate_helper:
        helper.unlink()
    trusted = commit("independently protected authorization")
    (root / "tests/test_owner.py").write_text(new)
    (root / "spec.md").write_text(updated)
    request_path.write_text(json.dumps(request))
    if candidate_helper:
        helper.write_bytes(helper_body)
    commit("candidate contract evolution request")
    return root, base, trusted, authority, request, commit


def test_contract_evolution_executes_complete_cases_and_consumes_raw_carriers(tmp_path):
    """REQ-testing-053: exact protected authority needs fresh complete before/after PASS."""
    import copy

    import contract_evolution as evolution

    # A real fast shebang descendant must retain both script and interpreter.
    after = (
        "import pytest\n@pytest.mark.parametrize('value',[1,2])\ndef test_owner(value):\n"
        "    import subprocess,sys\n"
        f"    subprocess.run([{str(Path(sys.executable).parent / 'pytest')!r},'--version'], check=True)\n"
        "    import tempfile\n    from pathlib import Path\n"
        "    tool = Path(tempfile.mkdtemp()) / 'temporary-tool'\n"
        "    tool.write_text('#!' + sys.executable + '\\npass\\n')\n"
        "    tool.chmod(0o755)\n    subprocess.run([str(tool)], check=True)\n"
        "    assert value > 0\n"
    )
    root, base, trusted, authority, request, _ = _contract_repository(tmp_path, after=after)
    output = tmp_path / "proofs"
    output.mkdir()
    result = ledger.verify(root, base, [], contract_directory=output, trusted=trusted)
    assert result["status"] == "PASS" and result["losses"] == 1
    projection = result["contract_evolution_proofs"][0]
    assert projection["authorization"] == request["authorization"]
    assert projection["public_authority"] == trusted and projection["base"] == base
    assert projection["complete"] is True and projection["case_count"] == 2
    assert projection["matched_owner_count"] == projection["executed_owner_count"] == 1
    assert 0 < projection["elapsed_s"] <= request["timeout_s"]
    directory = output / "contract-0"
    receipt_path = directory / "receipt.json"
    receipt = ledger.strict_json(receipt_path)
    assert receipt["cases"] == 2 and receipt["status"] == "PASS"
    assert receipt["binding"]["base"] == base
    assert receipt["binding"]["inputs"] != receipt["binding"]["before_inputs"]
    controls = set(receipt["controls"])
    assert {p for p in controls if p.startswith(evolution.AUTHORIZATIONS)}
    assert {p for p in controls if p.startswith(evolution.REQUESTS)}
    assert all(not (directory / side).exists() for side in ("before", "after"))
    for side in ("before", "after"):
        temporary_root = directory / (side + ".runtime-tmp")
        assert temporary_root.is_dir() and not temporary_root.is_symlink()
        environment = dict.fromkeys(("TMPDIR", "PYTEST_DEBUG_TEMPROOT"), str(temporary_root))
        assert (
            ledger.strict_json(directory / (side + ".request.json"))["runtime_environment"]
            == environment
        )
        assert receipt["runs"][side]["runtime_environment"] == {
            **environment,
            "tempfile_root": str(temporary_root),
        }
    for field, value in (
        ("scope", ["spec.md"]),
        ("scope_inputs", []),
        ("command_flags", []),
        ("measurement_root", str(root)),
        ("runtime_environment", {}),
        ("complete", False),
        ("selected", 1),
        ("deselected", True),
    ):
        forged = copy.deepcopy(receipt)
        forged["runs"]["after"][field] = value
        # Rewrite both JSON projections: the independent source/command/population
        # checks must reject even internally consistent forged claimed PASS data.
        receipt_path.write_text(json.dumps(forged))
        (directory / "after.json").write_text(json.dumps(forged["runs"]["after"]))
        with pytest.raises(ledger.EvidenceError):
            evolution.consume(
                root, base, authority, request, directory, controls, receipt["binding"]["nonce"]
            )
    receipt_path.write_text(json.dumps(receipt))
    (directory / "after.json").write_text(json.dumps(receipt["runs"]["after"]))
    execution_request_path = directory / "after.request.json"
    execution_request = ledger.strict_json(execution_request_path)
    forged_request = copy.deepcopy(execution_request)
    forged_request["runtime_environment"]["TMPDIR"] = str(tmp_path)
    execution_request_path.write_text(json.dumps(forged_request))
    with pytest.raises(ledger.EvidenceError, match="changed-contract-execution-request"):
        evolution.consume(
            root, base, authority, request, directory, controls, receipt["binding"]["nonce"]
        )
    execution_request_path.write_text(json.dumps(execution_request))
    changed_nonce = copy.deepcopy(receipt)
    changed_nonce["binding"]["nonce"] = "0" * 64
    receipt_path.write_text(json.dumps(changed_nonce))
    with pytest.raises(ledger.EvidenceError, match="stale-contract-proof-binding"):
        evolution.consume(
            root, base, authority, request, directory, controls, receipt["binding"]["nonce"]
        )
    receipt_path.write_text(json.dumps(receipt))
    lifecycle_path = directory / "after.lifecycle.json"
    lifecycle = ledger.strict_json(lifecycle_path)
    for field, value in (
        ("nonce", "0" * 64),
        ("eof", False),
        ("root", None),
        ("actors", []),
        ("executables", []),
    ):
        forged = copy.deepcopy(lifecycle)
        forged[field] = value
        lifecycle_path.write_text(json.dumps(forged))
        with pytest.raises((ledger.EvidenceError, TypeError)):
            evolution.consume(
                root, base, authority, request, directory, controls, receipt["binding"]["nonce"]
            )
    assert len(lifecycle["executables"]) > 1
    partial = copy.deepcopy(lifecycle)
    partial["executables"].pop()
    lifecycle_path.write_text(json.dumps(partial))
    with pytest.raises(ledger.EvidenceError):
        evolution.consume(
            root, base, authority, request, directory, controls, receipt["binding"]["nonce"]
        )
    lifecycle_path.write_text(json.dumps(lifecycle))
    coverage = directory / "after.coverage"
    original = coverage.read_bytes()
    coverage.write_bytes(b"forged coverage")
    with pytest.raises(ledger.EvidenceError):
        evolution.consume(
            root, base, authority, request, directory, controls, receipt["binding"]["nonce"]
        )
    coverage.write_bytes(original)
    evolution.consume(
        root, base, authority, request, directory, controls, receipt["binding"]["nonce"]
    )
    import os

    import contract_lifecycle

    fifo = tmp_path / "replaced-executable"
    os.mkfifo(fifo)
    with pytest.raises(ledger.EvidenceError, match="nonregular-contract-executable"):
        contract_lifecycle.executable_record(str(fifo))
    for pathname in ('""', r'"\x66\x6f\x6f"', r'"\x2f\x62\x69\x6e\x2f\x73\x68"'):
        stream = contract_lifecycle.Stream()
        stream.consume(
            f"999999 execveat(AT_FDCWD, {pathname}, [], 0x0, AT_EMPTY_PATH) = 0".encode()
        )
        assert stream.failed  # no guessed argv/dirfd/native-image authority
    # Real child death must retain its terminal marker, even if optional child
    # /proc metadata disappears. Missing root custody still cannot admit PASS.
    command = [
        sys.executable,
        "-c",
        (
            "import subprocess,sys,time; "
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)']); "
            "time.sleep(0.2); child.kill(); child.wait(); assert child.returncode==-9"
        ),
    ]
    read_text = Path.read_text
    for drop in ("nonroot", "root"):
        control = tmp_path / ("custody-" + drop)
        control.mkdir()
        output_path = control / "control.json"
        request_path = output_path.with_suffix(".request.json")
        nonce = "2" * 64
        request_path.write_text(json.dumps({"nonce": nonce}))
        root_pid, dropped = [], []

        def disappearing(path, *args, **kwargs):
            if str(path).startswith("/proc/") and str(path).endswith("/status"):
                pid = str(path).split("/")[2]
                if not root_pid:
                    root_pid.append(pid)
                if (drop == "root") == (pid == root_pid[0]):
                    dropped.append(pid)
                    raise FileNotFoundError("actual optional-status race control")
            return read_text(path, *args, **kwargs)

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(Path, "read_text", disappearing)
            if drop == "nonroot":
                code, cleaned, _ = contract_lifecycle.run(
                    command, control, request_path, output_path, dict(os.environ), 5, set()
                )
                assert code == 0 and cleaned is True
                contract_lifecycle.validate(output_path, command, nonce)
                observed = ledger.strict_json(output_path.with_suffix(".lifecycle.json"))
                assert len(observed["actors"]) == 2 and all(r["exited"] for r in observed["actors"])
            else:
                with pytest.raises(ledger.EvidenceError, match="contract-lifecycle-incomplete"):
                    contract_lifecycle.run(
                        command, control, request_path, output_path, dict(os.environ), 5, set()
                    )
            assert dropped  # both controls exercise their actual metadata lookup
    # A new tracked owner invalidates the complete source binding.
    (root / "tests/test_other.py").write_text("def test_other():\n    assert True\n")
    ledger.git(root, "add", ".")
    with pytest.raises(ledger.EvidenceError, match="stale-contract-proof-binding"):
        evolution.consume(
            root, base, authority, request, directory, controls, receipt["binding"]["nonce"]
        )


def test_contract_evolution_authority_refuses_forged_stale_or_unreviewed_changes(tmp_path):
    """REQ-testing-053: candidate review strings never replace protected exact-source approval."""
    import copy

    import contract_evolution as evolution

    root, base, trusted, authority, request, _ = _contract_repository(tmp_path)
    assert evolution.authorize(root, base, trusted, request)[0] == authority
    with pytest.raises(ledger.EvidenceError, match="missing-protected-contract-authority"):
        evolution.authorize(root, base, base, request)
    owner = root / "tests/test_owner.py"
    original = owner.read_text()
    for changed in (
        original + "    assert True\n",
        original.replace("[1,2]", "[3,4]"),
        original.replace("test_owner", "test_renamed"),
    ):
        owner.write_text(changed)
        with pytest.raises(ledger.EvidenceError, match="stale-authorized-source-or-context"):
            evolution.authorize(root, base, trusted, request)
    owner.write_text(original)
    (root / "runtime.py").write_text("UNREVIEWED = True\n")
    ledger.git(root, "add", "runtime.py")
    with pytest.raises(ledger.EvidenceError, match="unapproved-contract-runtime-input"):
        evolution.authorize(root, base, trusted, request)
    ledger.git(root, "rm", "-f", "runtime.py")
    spec = root / "spec.md"
    original_spec = spec.read_text()
    spec.write_text(
        original_spec + "\n### Requirement: unrelated changed behavior\nSHALL disappear.\n"
    )
    with pytest.raises(ledger.EvidenceError):
        evolution.authorize(root, base, trusted, request)
    spec.write_text(original_spec)
    for override in (
        {"owners": []},
        {"timeout_s": float("inf")},
        {"approved": True},
        {"authorization": "../forged"},
        {"timeout_s": True},
    ):
        malformed = copy.deepcopy(request)
        malformed.update(override)
        with pytest.raises(ledger.EvidenceError):
            evolution.validate_request(malformed)
    assert evolution.authorize(root, base, trusted, request)[0] == authority

    authorization = root / evolution.AUTHORIZATIONS / "example-change.json"
    approved = authorization.read_bytes()
    authorization.write_text(json.dumps(dict(authority, bead="self-approved")))
    with pytest.raises(ledger.EvidenceError, match="changed-protected-contract-authority"):
        evolution.authorize(root, base, trusted, request)
    authorization.write_bytes(approved)
    request_path = root / evolution.REQUESTS / "example.json"
    original_request = request_path.read_bytes()
    request_path.write_text(json.dumps(dict(request, timeout_s=59)))
    with pytest.raises(ledger.EvidenceError, match="stale-authorized-source-or-context"):
        evolution.authorize(root, base, trusted, request)
    request_path.write_bytes(original_request)
    # Actual executing source cannot borrow an untracked or altered reviewed copy.
    helper = "scripts/contract_lifecycle.py"
    ledger.git(root, "rm", "--cached", helper)
    with pytest.raises(ledger.EvidenceError, match="unapproved-contract-executor"):
        evolution.authorize(root, base, trusted, request)
    ledger.git(root, "add", helper)
    helper_body = (root / helper).read_bytes()
    (root / helper).write_bytes(helper_body + b"\n# unapproved runtime delta\n")
    with pytest.raises(ledger.EvidenceError, match="unapproved-contract-executor"):
        evolution.authorize(root, base, trusted, request)
    (root / helper).write_bytes(helper_body)
    assert evolution.authorize(root, base, trusted, request)[0] == authority

    for missing in ("scripts/contract_lifecycle.py", evolution.REQUESTS + "example.json"):
        incomplete, old, public, _, asked, _ = _contract_repository(
            tmp_path / ("missing-" + Path(missing).name), candidate_helper=True, omit=(missing,)
        )
        with pytest.raises(ledger.EvidenceError, match="unapproved-contract-runtime-input"):
            evolution.authorize(incomplete, old, public, asked)


def test_contract_evolution_requirement_boundaries_and_inactive_requests(tmp_path, monkeypatch):
    """REQ-testing-053: unrelated unnumbered blocks and unchanged links grant no authority."""
    import contract_evolution as evolution

    text = (
        "### Requirement: historical\nUnnumbered.\n\n### Requirement: selected\n"
        f"ID: {_EXAMPLE_ID}\n\nSHALL pass.\n\n## Other section\nOutside.\n"
    )
    expected = text[text.index("### Requirement: selected") : text.index("## Other section")]
    import hashlib

    assert (
        evolution.requirement_digest(text, _EXAMPLE_ID)
        == hashlib.sha256(expected.encode()).hexdigest()
    )
    fenced = expected + f"````python\n```\n## still inside\nID: {_FORGED_ID}\n````\n"
    assert (
        evolution.requirement_digest(fenced, _EXAMPLE_ID)
        == hashlib.sha256(fenced.encode()).hexdigest()
    )
    with pytest.raises(ledger.EvidenceError, match="missing-contract-block"):
        evolution.requirement_digest(fenced, _FORGED_ID)
    for invalid in (text + expected, text.replace(f"ID: {_EXAMPLE_ID}", f"ID: {_OTHER_ID}")):
        with pytest.raises(ledger.EvidenceError):
            evolution.requirement_digest(invalid, _EXAMPLE_ID)
    root, _, trusted, _, _, _ = _contract_repository(tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("inactive request must not authorize, snapshot or execute")

    monkeypatch.setattr(evolution, "authorize", forbidden)
    monkeypatch.setattr(evolution, "execute", forbidden)
    assert evolution.fresh_accounts(root, trusted, trusted, {}, tmp_path / "unused") == set()
    assert not (tmp_path / "unused").exists()


def test_contract_evolution_refuses_actual_nonpass_case_drift_and_control_oracles(tmp_path):
    """REQ-testing-053: complete cases execute; reviewed input reads need exact authority."""

    parameter = "import pytest\n@pytest.mark.parametrize('value',[1,2])\ndef test_owner(value):\n"
    cases = {
        "failure": parameter + "    assert value < 0\n",
        "skip": parameter + "    pytest.skip('incomplete')\n",
        "same_count_changed_cases": parameter.replace("[1,2]", "[3,4]") + "    assert value > 0\n",
        "authorization_oracle": parameter
        + "    open('tests/contract-evolution-authorizations/example-change.json').read()\n"
        "    assert value > 0\n",
        "request_oracle": parameter
        + "    open('tests/contract-evolution-requests/example.json').read()\n"
        "    assert value > 0\n",
        "absent_machinery_oracle": parameter + "    from pathlib import Path\n"
        "    Path('scripts/check_condensation_ledger.py').exists()\n"
        "    assert value > 0\n",
    }
    for name, after in cases.items():
        root, base, trusted, _, _, _ = _contract_repository(tmp_path / name, after=after)
        output = tmp_path / name / "proofs"
        output.mkdir()
        if name.endswith("_oracle"):
            result = ledger.verify(root, base, [], contract_directory=output, trusted=trusted)
            assert result["status"] == "PASS"
            # The complete reviewed test/input bytes authorize this read, not a
            # claim of non-observation or a generic machinery exemption.
            receipt = ledger.strict_json(output / "contract-0/receipt.json")
            assert receipt["status"] == "PASS" and receipt["cases"] == 2
        else:
            with pytest.raises(
                ledger.EvidenceError, match="fresh-contract-evolution-proof-refused"
            ):
                ledger.verify(root, base, [], contract_directory=output, trusted=trusted)
            receipt = ledger.strict_json(output / "contract-0/receipt.json")
            assert receipt["status"] == "UNKNOWN"
        assert not (output / "contract-0/after").exists()
        assert not (output / "contract-0/before").exists()
        if name == "same_count_changed_cases":
            before = ledger.strict_json(output / "contract-0/before.json")
            after_run = ledger.strict_json(output / "contract-0/after.json")
            assert before["selected"] == after_run["selected"] == 2
            assert before["exit"] == after_run["exit"] == 0
            assert {r["key"] for r in before["cases"]} != {r["key"] for r in after_run["cases"]}


def test_contract_evolution_closes_authorization_race_and_preserves_ordinary_refusal(
    tmp_path, monkeypatch
):
    """REQ-testing-053: freshly hashed but unapproved source cannot race exact approval."""
    import contract_evolution as evolution

    root, base, trusted, authority, request, _ = _contract_repository(tmp_path)
    original = evolution.authorize
    reads = 0

    def raced(*args, **kwargs):
        nonlocal reads
        result = original(*args, **kwargs)
        reads += 1
        if reads == 1:
            path = root / "tests/test_owner.py"
            path.write_text(path.read_text() + "    assert True\n")
        return result

    monkeypatch.setattr(evolution, "authorize", raced)
    with pytest.raises(ledger.EvidenceError, match="stale-authorized-source-or-context"):
        evolution.execute(root, base, authority, request, tmp_path / "race", set(), trusted=trusted)
    assert ledger.strict_json(tmp_path / "race/receipt.json")["status"] == "UNKNOWN"
    monkeypatch.setattr(evolution, "authorize", original)
    request_path = root / evolution.REQUESTS / "example.json"
    ledger.git(root, "rm", "-f", str(request_path))
    with pytest.raises(ledger.EvidenceError, match="unproven-test-or-assertion-loss"):
        ledger.verify(root, base, [], trusted=trusted)

    original_repository = _repository

    def two_original_owners(home):
        repo = original_repository(home)
        owner_file = repo / "tests/test_owner.py"
        owner_file.write_text(
            owner_file.read_text()
            + "\n@pytest.mark.parametrize('value',[1,2])\ndef test_unlisted(value):\n"
            "    assert value > 0\n    assert value in (1,2)\n"
        )
        return repo

    monkeypatch.setitem(globals(), "_repository", two_original_owners)
    other, old, public, _, _, _ = _contract_repository(tmp_path / "unlisted-owner")
    output = tmp_path / "unlisted-proofs"
    output.mkdir()
    with pytest.raises(ledger.EvidenceError, match="unproven-test-or-assertion-loss"):
        ledger.verify(other, old, [], contract_directory=output, trusted=public)
    # Full file bytes were genuinely authorized and the named owner executed;
    # that cannot excuse a second original owner's unmapped assertion loss.
    assert ledger.strict_json(output / "contract-0/receipt.json")["status"] == "PASS"


def test_contract_evolution_watchdog_cleans_real_resistant_descendant(tmp_path):
    """REQ-testing-053: real timeout remains UNKNOWN and owns descendant cleanup."""
    import os
    import time

    marker = tmp_path / "descendant.pid"
    child = (
        "import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        f"open({str(marker)!r},'w').write(str(os.getpid())); time.sleep(120)"
    )
    after = (
        "import pytest\n@pytest.mark.parametrize('value',[1,2])\ndef test_owner(value):\n"
        "    import subprocess,sys,time\n"
        f"    subprocess.Popen([sys.executable,'-c',{child!r}], start_new_session=True)\n"
        "    time.sleep(120)\n    assert value > 0\n"
    )
    root, base, trusted, _, request, _ = _contract_repository(tmp_path, after=after, timeout=40)
    output = tmp_path / "proofs"
    output.mkdir()
    started = time.monotonic()
    with pytest.raises(ledger.EvidenceError, match="fresh-contract-evolution-proof-refused"):
        ledger.verify(root, base, [], contract_directory=output, trusted=trusted)
    assert time.monotonic() - started < 40
    assert marker.is_file(), "real descendant must start before the timeout control proves cleanup"
    pid = int(marker.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert ledger.strict_json(output / "contract-0/receipt.json")["status"] == "UNKNOWN"
    assert not (output / "contract-0/after").exists()


def test_contract_evolution_refuses_request_changed_after_controller_parse(tmp_path, monkeypatch):
    """REQ-testing-053: executing a later valid request cannot rebind the controller request."""
    import contract_evolution as evolution
    import pre_push

    root, base, trusted, _, request, _ = _contract_repository(tmp_path)
    original = pre_push.run_checked
    request_path = root / evolution.REQUESTS / "example.json"

    def changed(*args, **kwargs):
        request_path.write_text(json.dumps(dict(request, timeout_s=59)))
        return original(*args, **kwargs)

    monkeypatch.setattr(pre_push, "run_checked", changed)
    output = tmp_path / "proofs"
    output.mkdir()
    with pytest.raises(ledger.EvidenceError, match="fresh-contract-evolution-proof-refused"):
        ledger.verify(root, base, [], contract_directory=output, trusted=trusted)
    # Exact protected request bytes reject a later valid request before execution.
    assert not (output / "contract-0/receipt.json").exists()


_STEAM_TESTS = (
    "import pytest\n\n\n"
    "class TestSteam:\n"
    "    @pytest.mark.unit\n"
    "    def test_has_steam(self):\n        assert 'steam' in ('steam',)\n\n"
    "    def test_empty_config(self):\n        assert {} == {}\n\n\n"
    "def test_unrelated():\n    assert True\n"
)
_STEAM_AFTER = "import pytest\n\n\ndef test_unrelated():\n    assert True\n"
_STEAM_SPEC = (
    "### Requirement: Steam Module\nGeneral SHALL load steam.\n\n### Requirement: Other\nx\n"
)
_STEAM_SPEC_AFTER = _STEAM_SPEC.replace("SHALL load", "SHALL NOT load")
_RETIRED = (
    "tests/test_steam.py::TestSteam::test_has_steam",
    "tests/test_steam.py::TestSteam::test_empty_config",
)


def _commit_all(root, message):
    ledger.git(root, "add", ".")
    ledger.git(
        root,
        "-c",
        "user.name=Proof",
        "-c",
        "user.email=proof@example.invalid",
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "-qm",
        message,
    )
    return ledger.git(root, "rev-parse", "HEAD")


def _retirement_repository(tmp_path, *, mutate=None, trusted_manifest=True):
    """Real Git history: reviewed base, protected manifest commit, candidate untouched."""
    import contract_evolution as evolution

    root = _repository(tmp_path)
    (root / "tests/test_steam.py").write_text(_STEAM_TESTS)
    (root / "spec.md").write_text(_STEAM_SPEC)
    base = _commit_all(root, "reviewed base")
    manifest = {
        "schema": evolution.RETIREMENT_SCHEMA,
        "id": "example-retirement",
        "bead": "example.1",
        "authority": "owner decision, provenance only",
        "retired": [
            {
                "owner": owner,
                "base_sha256": evolution.owner_body_sha256(_STEAM_TESTS, owner.split("::", 1)[1]),
                "reason": "Steam is no longer a General module.",
            }
            for owner in _RETIRED
        ],
        "requirements": [
            {
                "path": "spec.md",
                "requirement": "Steam Module",
                "before_sha256": evolution.requirement_digest_or_none(_STEAM_SPEC, "Steam Module"),
                "after_sha256": evolution.requirement_digest_or_none(
                    _STEAM_SPEC_AFTER, "Steam Module"
                ),
            }
        ],
    }
    if mutate:
        mutate(manifest)
    path = root / evolution.AUTHORIZATIONS / "example-retirement.json"
    path.parent.mkdir(parents=True)
    if trusted_manifest:
        path.write_text(json.dumps(manifest))
    else:
        (root / "note.txt").write_text("protected public history without the manifest\n")
    trusted = _commit_all(root, "protected retirement authority")
    if not trusted_manifest:
        path.write_text(json.dumps(manifest))
    return root, base, trusted, path


def _retire(root, *, extra=""):
    (root / "tests/test_steam.py").write_text(_STEAM_AFTER + extra)
    (root / "spec.md").write_text(_STEAM_SPEC_AFTER)
    return _commit_all(root, "candidate retirement")


def test_retirement_admits_exact_whole_owners_only_from_trusted_manifest(tmp_path):
    """REQ-testing-053: protected retirement authority admits exactly the named owners."""
    root, base, trusted, path = _retirement_repository(tmp_path)
    _retire(root)
    result = ledger.verify(root, base, [], trusted=trusted)
    (account,) = result["retirement_accounts"]
    assert result["status"] == "PASS" and result["losses"] == 2
    assert account["manifest"] == "example-retirement"
    assert account["admitted_owner_count"] == 2
    assert (
        account["manifest_sha256"]
        == ledger.input_record(root, path.relative_to(root).as_posix())["sha256"]
    )
    assert account["public_authority"] == trusted


def test_historical_retirement_manifest_is_inert_without_a_current_loss(tmp_path):
    """REQ-testing-053: a protected manifest with no matching loss changes nothing."""
    root, base, trusted, _ = _retirement_repository(tmp_path)
    result = ledger.verify(root, base, [], trusted=trusted)
    assert result["status"] == "PASS" and "retirement_accounts" not in result


def test_retirement_cannot_be_claimed_by_candidate_or_edited_authority(tmp_path):
    """REQ-testing-053: candidate-only or altered manifests never authorize loss."""
    root, base, trusted, path = _retirement_repository(tmp_path / "only", trusted_manifest=False)
    _retire(root)
    with pytest.raises(ledger.EvidenceError, match="candidate-only-retirement-authority"):
        ledger.verify(root, base, [], trusted=trusted)
    root, base, trusted, path = _retirement_repository(tmp_path / "edited")
    path.write_text(path.read_text().replace("no longer", "never"))
    _retire(root)
    with pytest.raises(ledger.EvidenceError, match="changed-protected-retirement-authority"):
        ledger.verify(root, base, [], trusted=trusted)


def test_retirement_leaves_unlisted_and_partial_losses_unproven(tmp_path):
    """REQ-testing-053: only whole named owners retire; every other loss keeps its account."""
    root, base, trusted, _ = _retirement_repository(tmp_path / "extra")
    (root / "tests/test_owner.py").write_text(
        (root / "tests/test_owner.py").read_text().replace("    assert value in (1,2)\n", "")
    )
    _retire(root)
    with pytest.raises(ledger.EvidenceError, match="unproven-test-or-assertion-loss"):
        ledger.verify(root, base, [], trusted=trusted)
    root, base, trusted, _ = _retirement_repository(tmp_path / "partial")
    (root / "spec.md").write_text(_STEAM_SPEC_AFTER)
    (root / "tests/test_steam.py").write_text(
        _STEAM_AFTER + "\n\nclass TestSteam:\n    def test_empty_config(self):\n        pass\n"
    )
    _commit_all(root, "candidate keeps a hollowed owner")
    with pytest.raises(ledger.EvidenceError, match="retired-owner-not-wholly-removed"):
        ledger.verify(root, base, [], trusted=trusted)


@pytest.mark.parametrize(
    ("name", "mutate", "category"),
    [
        (
            "stale",
            lambda m: m["requirements"][0].update(before_sha256="0" * 64),
            "stale-retirement",
        ),
        (
            "wrong",
            lambda m: m["requirements"][0].update(after_sha256="1" * 64),
            "mismatched-retirement",
        ),
        ("owner", lambda m: m["retired"][0].update(base_sha256="2" * 64), "stale-retired-owner"),
    ],
)
def test_retirement_refuses_stale_or_mismatched_bindings(tmp_path, name, mutate, category):
    """REQ-testing-053: base hashes and requirement blocks must match exactly."""
    root, base, trusted, _ = _retirement_repository(tmp_path / name, mutate=mutate)
    _retire(root)
    with pytest.raises(ledger.EvidenceError, match=category):
        ledger.verify(root, base, [], trusted=trusted)


def test_retirement_requires_the_governing_requirement_to_have_moved(tmp_path):
    """REQ-testing-053: an unchanged governing block cannot retire any test."""
    root, base, trusted, _ = _retirement_repository(tmp_path)
    (root / "tests/test_steam.py").write_text(_STEAM_AFTER)
    _commit_all(root, "candidate deletes tests without the governing change")
    with pytest.raises(ledger.EvidenceError, match="unchanged-retirement-requirement"):
        ledger.verify(root, base, [], trusted=trusted)


def test_retirement_replacements_must_exist_in_the_candidate(tmp_path):
    """REQ-testing-053: named replacement owners are required beside the retirement."""
    replacement = "tests/test_steam.py::test_excludes_steam"

    def add(manifest):
        manifest["replacements"] = [replacement]

    root, base, trusted, _ = _retirement_repository(tmp_path / "missing", mutate=add)
    _retire(root)
    with pytest.raises(ledger.EvidenceError, match="missing-retirement-replacement"):
        ledger.verify(root, base, [], trusted=trusted)
    root, base, trusted, _ = _retirement_repository(tmp_path / "present", mutate=add)
    _retire(root, extra="\n\ndef test_excludes_steam():\n    assert True\n")
    assert ledger.verify(root, base, [], trusted=trusted)["status"] == "PASS"


def test_cli_prints_the_real_contract_refusal_category_across_module_identities(tmp_path):
    """bu-rf37wu: run as a script, an imported consumer's refusal keeps its own category.

    ``python scripts/check_condensation_ledger.py`` loads the ledger as ``__main__`` while
    ``contract_evolution`` imports it again by name, so without the ``sys.modules`` alias in
    the ``__main__`` block its ``EvidenceError`` is a different class and collapses to
    ``invalid-structural-evidence``. In-process tests cannot see that.
    """
    root, base, _trusted, _authority, _request, _commit = _contract_repository(tmp_path)
    # The protected ref predates the authorization, so the candidate request has no authority.
    ledger.git(root, "update-ref", "refs/remotes/origin/main", base)
    proc = subprocess.run(
        [sys.executable, str(Path(ledger.__file__)), "--repo-root", str(root), "--base", base],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1
    assert json.loads(proc.stdout) == {
        "status": "REFUSED",
        "category": "missing-protected-contract-authority",
    }


def test_stale_base_pr_is_not_forced_through_an_already_landed_contract_request(tmp_path):
    """bu-1bxczh: a request the trusted parent already carries is public history."""
    root, base, authorized, _authority, _request, _commit = _contract_repository(tmp_path)
    landed = ledger.git(root, "rev-parse", "HEAD")
    (root / "tests/test_unrelated.py").write_text("def test_unrelated():\n    assert True\n")
    (root / "docs.md").write_text("an unrelated pull request change\n")
    _commit_all(root, "unrelated pull request delta")
    output = tmp_path / "proofs"
    output.mkdir()

    result = ledger.verify(root, base, [], contract_directory=output, trusted=landed)

    assert result["status"] == "PASS" and result["losses"] == 1
    assert "contract_evolution_proofs" not in result
    # A request only the candidate carries is still consumed as a fresh proof: the unrelated
    # delta is not covered by the request, so it is still refused exactly as before.
    with pytest.raises(ledger.EvidenceError, match="unapproved-contract-runtime-input"):
        ledger.verify(root, base, [], contract_directory=output, trusted=authorized)


def test_stale_base_does_not_launder_a_pull_request_change_to_a_landed_request_owner(tmp_path):
    """bu-1bxczh: touching a covered owner after it landed gets no landed-request shortcut."""
    root, base, _authorized, _authority, _request, _commit = _contract_repository(tmp_path)
    landed = ledger.git(root, "rev-parse", "HEAD")
    owner = root / "tests/test_owner.py"
    owner.write_text(owner.read_text() + "\n# the pull request edits the covered owner\n")
    _commit_all(root, "pull request edits a covered owner")
    output = tmp_path / "proofs"
    output.mkdir()
    with pytest.raises(ledger.EvidenceError):
        ledger.verify(root, base, [], contract_directory=output, trusted=landed)
    # A landed authorization whose bytes the candidate edited is never trusted either.
    root, base, _authorized, _authority, _request, _commit = _contract_repository(tmp_path / "edit")
    landed = ledger.git(root, "rev-parse", "HEAD")
    authorization = root / "tests/contract-evolution-authorizations/example-change.json"
    authorization.write_text(authorization.read_text().replace("Owner", "Someone"))
    _commit_all(root, "candidate edits the landed authorization")
    with pytest.raises(ledger.EvidenceError):
        ledger.verify(root, base, [], contract_directory=output, trusted=landed)


def _landed_retirement(tmp_path, *, extra_loss=False):
    """A retirement the trusted parent already carries, with a base older than its own."""
    root, base, _manifest_commit, _path = _retirement_repository(
        tmp_path,
        mutate=lambda m: m["retired"][0].update(base_sha256="2" * 64),
    )
    _retire(root)
    landed = ledger.git(root, "rev-parse", "HEAD")
    (root / "docs.md").write_text("an unrelated pull request change\n")
    if extra_loss:
        owner = root / "tests/test_owner.py"
        owner.write_text(owner.read_text().replace("    assert value in (1,2)\n", ""))
    _commit_all(root, "pull request delta")
    return root, base, landed


def test_stale_base_pr_is_not_forced_through_an_already_landed_retirement(tmp_path):
    """bu-1bxczh: public-history retirement is not re-litigated against an older base."""
    root, base, landed = _landed_retirement(tmp_path)
    result = ledger.verify(root, base, [], trusted=landed)
    (account,) = result["retirement_accounts"]
    assert result["status"] == "PASS" and account["admitted_owner_count"] == 2


def test_stale_base_still_counts_a_loss_the_pull_request_introduces(tmp_path):
    """bu-1bxczh: landed authority never covers a loss the candidate adds beside it."""
    root, base, landed = _landed_retirement(tmp_path, extra_loss=True)
    with pytest.raises(ledger.EvidenceError, match="unproven-test-or-assertion-loss"):
        ledger.verify(root, base, [], trusted=landed)


@pytest.mark.parametrize("change", ["edit", "delete"])
@pytest.mark.parametrize("fresh_base", [False, True])
def test_landed_request_shortcut_refuses_an_ancestor_conftest_change(
    tmp_path, monkeypatch, change, fresh_base
):
    """bu-1bxczh: a conftest.py change is attributed to every owner below it."""
    real_repository = _repository

    def repository_with_conftest(path):
        root = real_repository(path)
        (root / "tests/conftest.py").write_text("import pytest\n")
        return root

    monkeypatch.setattr(sys.modules[__name__], "_repository", repository_with_conftest)
    root, base, _authorized, _authority, _request, _commit = _contract_repository(tmp_path)
    monkeypatch.undo()
    landed = ledger.git(root, "rev-parse", "HEAD")
    conftest = root / "tests/conftest.py"
    if change == "edit":
        conftest.write_text(
            "import pytest\n\n\n@pytest.fixture(autouse=True)\ndef _skip():\n    pytest.skip()\n"
        )
    else:
        conftest.unlink()
    _commit_all(root, "pull request changes only the ancestor conftest")
    output = tmp_path / "proofs"
    output.mkdir()
    with pytest.raises(ledger.EvidenceError):
        ledger.verify(
            root, landed if fresh_base else base, [], contract_directory=output, trusted=landed
        )


_SKIPPING_CONFTEST = (
    "import pytest\n\n\n@pytest.fixture(autouse=True)\ndef _skip_all():\n    pytest.skip('x')\n"
)


@pytest.mark.parametrize("location", ["conftest.py", "tests/conftest.py"])
def test_added_ancestor_conftest_is_attributed_to_every_owner_below_it(tmp_path, location):
    """bu-0jd93j: a conftest.py absent at base but added by the candidate is new fixture context."""
    root = _repository(tmp_path)
    base = ledger.git(root, "rev-parse", "HEAD")
    (root / location).write_text(_SKIPPING_CONFTEST)
    _commit_all(root, "candidate adds a skipping ancestor conftest")

    losses = ledger.destructive_changes(root, base)

    assert losses
    assert set(losses.values()) == {"ancestral-fixture-context-change"}
    assert all(node.startswith("tests/test_owner.py::") for node in losses)
    with pytest.raises(ledger.EvidenceError, match="unproven-test-or-assertion-loss"):
        ledger.verify(root, base, [])


def test_added_conftest_without_owners_below_it_changes_nothing(tmp_path):
    root = _repository(tmp_path)
    base = ledger.git(root, "rev-parse", "HEAD")
    (root / "unrelated").mkdir()
    (root / "unrelated/conftest.py").write_text(_SKIPPING_CONFTEST)
    _commit_all(root, "candidate adds a conftest above no owner")

    assert ledger.destructive_changes(root, base) == {}


_HOOK_SKIPPING_CONFTEST = (
    "import pytest\n\n\n"
    "def pytest_collection_modifyitems(items):\n"
    "    for item in items:\n"
    "        item.add_marker(pytest.mark.skip(reason='hook'))\n"
)
_HARMLESS_HOOK_CONFTEST = "def pytest_report_header():\n    return 'harmless'\n"
_FIXTURE_ONLY_CONFTEST = "import pytest\n\n\n@pytest.fixture\ndef helper():\n    return 1\n"


def _run_pytest(root):
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout


def _base_commit(root, *, empty):
    return ledger.git(root, "rev-parse", "HEAD") if empty else _commit_all(root, "base")


def _owner_nodes(losses):
    return {node for node in losses if node.startswith("tests/test_owner.py::")}


def test_non_ancestral_hook_conftest_really_skips_an_owner_without_failing_pytest(tmp_path):
    """bu-p3phlx: the threat is real, so the guard must attribute it."""
    root = _repository(tmp_path)
    (root / "tests/zzz").mkdir()
    (root / "tests/zzz/conftest.py").write_text(_HOOK_SKIPPING_CONFTEST)
    (root / "tests/zzz/test_other.py").write_text("def test_other():\n    assert True\n")
    code, out = _run_pytest(root)
    assert code == 0 and "skipped" in out and "passed" not in out


@pytest.mark.parametrize("change", ["add", "edit", "delete"])
def test_non_ancestral_hook_conftest_change_is_attributed_to_every_owner(tmp_path, change):
    root = _repository(tmp_path)
    (root / "tests/zzz").mkdir()
    conftest = root / "tests/zzz/conftest.py"
    if change != "add":
        conftest.write_text(_HARMLESS_HOOK_CONFTEST)
    base = _base_commit(root, empty=change == "add")
    if change == "add":
        conftest.write_text(_HOOK_SKIPPING_CONFTEST)
    elif change == "edit":
        conftest.write_text(_HOOK_SKIPPING_CONFTEST)
    else:
        conftest.unlink()
    _commit_all(root, "candidate changes a sibling hook conftest")

    losses = ledger.destructive_changes(root, base)

    assert _owner_nodes(losses)
    assert set(losses.values()) == {"session-hook-context-change"}
    with pytest.raises(ledger.EvidenceError, match="unproven-test-or-assertion-loss"):
        ledger.verify(root, base, [])


@pytest.mark.parametrize("change", ["add", "edit", "delete"])
def test_non_ancestral_fixture_only_conftest_stays_ancestral(tmp_path, change):
    root = _repository(tmp_path)
    (root / "tests/zzz").mkdir()
    conftest = root / "tests/zzz/conftest.py"
    if change != "add":
        conftest.write_text("import pytest\n")
    base = _base_commit(root, empty=change == "add")
    if change == "delete":
        conftest.unlink()
    else:
        conftest.write_text(_FIXTURE_ONLY_CONFTEST)
    _commit_all(root, "candidate changes a sibling fixture-only conftest")

    assert ledger.destructive_changes(root, base) == {}


def test_ancestral_hook_conftest_is_attributed_as_a_session_hook(tmp_path):
    root = _repository(tmp_path)
    base = ledger.git(root, "rev-parse", "HEAD")
    (root / "tests/conftest.py").write_text(_HOOK_SKIPPING_CONFTEST)
    _commit_all(root, "candidate adds an ancestor hook conftest")

    losses = ledger.destructive_changes(root, base)

    assert _owner_nodes(losses) and set(losses.values()) == {"session-hook-context-change"}


@pytest.mark.parametrize(
    "source",
    [
        "def pytest_configure(config):\n    pass\n",
        "pytest_plugins = ['x']\n",
        "collect_ignore = ['test_owner.py']\n",
        "from helpers import pytest_collection_modifyitems\n",
        "from helpers import *\n",
        "import os\nos.environ['X'] = '1'\n",
        "import sys\nif sys.platform:\n    pass\n",
        "def helper():\n    return 1\n",
        "class Plugin:\n    pass\n",
        "VALUE = compute()\n",
        "import pytest\n\n\n@pytest.fixture\ndef pytest_thing():\n    return 1\n",
        "def broken(:\n",
    ],
)
def test_conftest_classifier_treats_anything_but_plain_fixtures_as_hook_bearing(source):
    assert ledger.conftest_has_session_hooks(source)


@pytest.mark.parametrize(
    "source",
    [
        None,
        "",
        '"""Docstring."""\nimport pytest\nfrom os import path\n',
        "LIMIT = 3\nNAMES: list[str] = ['a']\n",
        "import pytest\n\n\n@pytest.fixture(scope='session')\ndef a():\n    return 1\n",
        "from pytest import fixture\n\n\n@fixture\nasync def b():\n    return 1\n",
    ],
)
def test_conftest_classifier_keeps_fixture_only_modules_ancestral(source):
    assert not ledger.conftest_has_session_hooks(source)


def test_added_symlinked_conftest_is_refused_as_an_indirect_fixture_source(tmp_path):
    root = _repository(tmp_path)
    base = ledger.git(root, "rev-parse", "HEAD")
    (root / "tests/zzz").mkdir()
    (root / "elsewhere.py").write_text(_HOOK_SKIPPING_CONFTEST)
    (root / "tests/zzz/conftest.py").symlink_to("../../elsewhere.py")
    _commit_all(root, "candidate adds a symlinked conftest")

    with pytest.raises(ledger.EvidenceError, match="indirect-fixture-source"):
        ledger.destructive_changes(root, base)


_CONFIG_FILES = {
    "tests/pytest.ini": "[pytest]\naddopts = --deselect tests/test_owner.py\n",
    "pytest.ini": "[pytest]\naddopts = --deselect tests/test_owner.py\n",
    "tox.ini": "[pytest]\naddopts = --deselect tests/test_owner.py\n",
    "setup.cfg": "[tool:pytest]\naddopts = --deselect tests/test_owner.py\n",
    "pyproject.toml": "[tool.pytest.ini_options]\naddopts = '--deselect tests/test_owner.py'\n",
}


@pytest.mark.parametrize("path", sorted(_CONFIG_FILES))
@pytest.mark.parametrize("change", ["add", "edit", "delete"])
def test_pytest_configuration_change_is_attributed_to_every_owner(tmp_path, path, change):
    root = _repository(tmp_path)
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    if change != "add":
        target.write_text(_CONFIG_FILES[path].replace("tests/test_owner.py", "nothing"))
    base = _base_commit(root, empty=change == "add")
    if change == "delete":
        target.unlink()
    else:
        target.write_text(_CONFIG_FILES[path])
    _commit_all(root, "candidate moves the pytest configuration")

    losses = ledger.destructive_changes(root, base)

    assert _owner_nodes(losses)
    assert set(losses.values()) == {"pytest-configuration-change"}


def test_pytest_configuration_that_really_deselects_an_owner_is_attributed(tmp_path):
    root = _repository(tmp_path)
    base = ledger.git(root, "rev-parse", "HEAD")
    (root / "pytest.ini").write_text(_CONFIG_FILES["pytest.ini"])
    _commit_all(root, "candidate adds a deselecting pytest.ini")
    code, out = _run_pytest(root)
    assert "deselected" in out and "passed" not in out
    assert _owner_nodes(ledger.destructive_changes(root, base))


@pytest.mark.parametrize(
    ("path", "text"),
    [
        ("tox.ini", "[tox]\nenvlist = py\n"),
        ("setup.cfg", "[metadata]\nname = x\n"),
        ("pyproject.toml", "[project]\nname = 'x'\n"),
    ],
)
def test_unrelated_changes_to_shared_config_files_are_not_pytest_configuration(
    tmp_path, path, text
):
    root = _repository(tmp_path)
    base = ledger.git(root, "rev-parse", "HEAD")
    (root / path).write_text(text)
    _commit_all(root, "candidate adds a config file with no pytest table")

    assert ledger.destructive_changes(root, base) == {}


def test_symlinked_pytest_configuration_is_refused(tmp_path):
    root = _repository(tmp_path)
    base = ledger.git(root, "rev-parse", "HEAD")
    (root / "elsewhere.ini").write_text(_CONFIG_FILES["pytest.ini"])
    (root / "pytest.ini").symlink_to("elsewhere.ini")
    _commit_all(root, "candidate adds a symlinked pytest.ini")

    with pytest.raises(ledger.EvidenceError, match="indirect-pytest-configuration"):
        ledger.destructive_changes(root, base)
