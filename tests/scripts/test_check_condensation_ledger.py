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
    assert change != "fixture" or _committed_context_suppressions(tmp_path / "contexts")


def _committed_context_suppressions(tmp_path):
    """Actual committed additions cannot turn a failure into an admitted skip."""
    root = _repository(tmp_path)
    path = root / "tests/test_owner.py"
    original = "import pytest\n\ndef test_owner():\n    assert False\n"

    def commit():
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
                "context control",
            ],
            check=True,
            capture_output=True,
        )

    def execute():
        return subprocess.run(
            [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )

    path.write_text(original)
    (root / "suppress.py").write_text(
        "import pytest\nimport sys\n"
        "sys.modules['test_owner'].test_owner.pytestmark = pytest.mark.skip()\n"
    )
    commit()
    base = ledger.git(root, "rev-parse", "HEAD")
    failed = execute()
    assert failed.returncode == 1 and "1 failed" in failed.stdout
    for kind in ("attribute", "new-conftest", "import", "added-test-decorator"):
        conftest = root / "tests/conftest.py"
        if kind == "attribute":
            path.write_text(original + "\ntest_owner.pytestmark = pytest.mark.skip()\n")
        elif kind == "import":
            path.write_text(original + "\nimport suppress\n")
        elif kind == "added-test-decorator":
            path.write_text(
                original + "\n@(lambda f: (setattr(test_owner, 'pytestmark', "
                "pytest.mark.skip()), f)[1])\ndef test_added():\n    pass\n"
            )
        else:
            path.write_text(original)
            conftest.write_text(
                "import pytest\n\n@pytest.fixture(autouse=True)\n"
                'def unavailable():\n    pytest.skip("unavailable")\n'
            )
        commit()
        assert not ledger.git(root, "status", "--porcelain", "--untracked-files=no")
        skipped = execute()
        assert skipped.returncode == 0 and "1 skipped" in skipped.stdout
        with pytest.raises(ledger.EvidenceError, match="unproven"):
            ledger.verify(root, base, [])
        path.write_text(original)
        conftest.unlink(missing_ok=True)
        commit()
        assert ledger.verify(root, base, [])["status"] == "PASS"
    return True


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
    assert _inert_context_controls(tmp_path / "inert")


def _inert_context_controls(tmp_path):
    root = _repository(tmp_path)
    path = root / "tests/test_owner.py"
    original = path.read_text()
    for addition in (
        "\nNEW_LABEL = ('fresh', 1, None)\n",
        "\ndef helper(default=None):\n    return default\n",
        "\ndef test_added():\n    assert True\n",
        "\nclass TestAdded:\n    def test_added(self):\n        assert True\n",
    ):
        path.write_text(original + addition)
        assert ledger.verify(root, "HEAD", [])["status"] == "PASS"
    for addition in (
        "\nimport pathlib\n",
        "\n@(lambda f: (setattr(test_owner, 'pytestmark', pytest.mark.skip()), f)[1])\n"
        "def test_added():\n    pass\n",
        "\ndef test_added(value=pytest.skip('definition')):\n    pass\n",
        "\nclass TestAdded:\n"
        "    @(lambda f: (setattr(test_owner, 'pytestmark', pytest.mark.skip()), f)[1])\n"
        "    def test_added(self):\n        pass\n",
        "\ntest_owner.pytestmark = pytest.mark.skip()\n",
        "\nsetattr(test_owner, 'pytestmark', pytest.mark.skip())\n",
        "\ndef helper(default=pytest.skip('definition')):\n    return default\n",
        "\n@pytest.fixture(autouse=True)\ndef extra():\n    pytest.skip('fixture')\n",
        "\nclass Extra:\n    test_owner.pytestmark = pytest.mark.skip()\n",
        "\ndef pytest_collection_modifyitems(items):\n    items.clear()\n",
        "\ndef setup_function():\n    pytest.skip('setup')\n",
        "\n__test__ = False\n",
        "\nvalue = 0\n",
    ):
        path.write_text(original + addition)
        with pytest.raises(ledger.EvidenceError, match="unproven"):
            ledger.verify(root, "HEAD", [])
    path.write_text(original)
    # A new conftest only affects the tests under its actual ancestor scope.
    unrelated = root / "tests/other/conftest.py"
    unrelated.parent.mkdir()
    unrelated.write_text("import pytest\npytestmark = pytest.mark.skip()\n")
    subprocess.run(["git", "-C", str(root), "add", str(unrelated)], check=True)
    assert ledger.verify(root, "HEAD", [])["status"] == "PASS"
    return True


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
