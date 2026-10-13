"""REQ-testing-053: retained modules require actual complete execution context."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys

import pytest

from tests.scripts.test_check_condensation_ledger import _repository, ledger
from tests.scripts.test_condense_evidence import _config, _toy, evidence

pytestmark = pytest.mark.unit


def _commit(root):
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
            "module context control",
        ],
        check=True,
        capture_output=True,
    )


def _pytest(root):
    return subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_retained_module_context_requires_complete_execution(tmp_path):
    """Actual suppressions refuse; benign context uses complete source-derived modules."""
    root = _repository(tmp_path / "source-controls")
    path = root / "tests/test_owner.py"
    original = "import pytest\n\ndef test_owner():\n    assert False\n"
    path.write_text(original)
    (root / "suppress.py").write_text(
        "import pytest\nimport sys\n"
        "sys.modules['test_owner'].test_owner.pytestmark = pytest.mark.skip()\n"
    )
    _commit(root)
    base = ledger.git(root, "rev-parse", "HEAD")
    failed = _pytest(root)
    assert failed.returncode == 1 and "1 failed" in failed.stdout
    for kind in ("attribute", "conftest", "import", "decorator", "runtime", "setup"):
        conftest = root / "tests/conftest.py"
        if kind == "attribute":
            changed = original + "\ntest_owner.pytestmark = pytest.mark.skip()\n"
        elif kind == "import":
            changed = original + "\nimport suppress\n"
        elif kind == "decorator":
            changed = original + (
                "\n@(lambda f: (setattr(test_owner, 'pytestmark', pytest.mark.skip()), f)[1])\n"
                "def test_added():\n    pass\n"
            )
        elif kind == "runtime":
            changed = original.replace(
                "def test_owner",
                (
                    "def test_added(request):\n"
                    "    for item in request.session.items:\n"
                    "        if item.name == 'test_owner':\n"
                    "            item.add_marker(pytest.mark.skip())\n\n"
                    "def test_owner"
                ),
            )
        elif kind == "setup":
            changed = original + "\ndef setup_function():\n    pytest.skip('setup')\n"
        else:
            changed = original
            conftest.write_text(
                "import pytest\n@pytest.fixture(autouse=True)\n"
                "def unavailable():\n    pytest.skip('unavailable')\n"
            )
        path.write_text(changed)
        _commit(root)
        assert not ledger.git(root, "status", "--porcelain", "--untracked-files=no")
        skipped = _pytest(root)
        assert skipped.returncode == 0 and "1 skipped" in skipped.stdout
        with pytest.raises(ledger.EvidenceError, match="unproven"):
            ledger.verify(root, base, [])
        path.write_text(original)
        conftest.unlink(missing_ok=True)
        _commit(root)
        assert ledger.verify(root, base, [])["status"] == "PASS"

    # Frameworks may invoke any inherited member, not just familiar hook names.
    original = (
        "import unittest\n\nclass TestOwner(unittest.TestCase):\n"
        "    def test_owner(self):\n        assert False\n"
    )
    path.write_text(original)
    _commit(root)
    base = ledger.git(root, "rev-parse", "HEAD")
    assert _pytest(root).returncode == 1
    path.write_text(original + "\n    def run(self, result=None):\n        pass\n")
    _commit(root)
    assert _pytest(root).returncode == 0
    with pytest.raises(ledger.EvidenceError, match="unproven"):
        ledger.verify(root, base, [])
    path.write_text(original)
    _commit(root)
    assert ledger.verify(root, base, [])["status"] == "PASS"

    # Benign new files are outside retained-module context, not certified isolated.
    unrelated = root / "tests/test_new.py"
    unrelated.write_text("def test_new():\n    assert True\n")
    _commit(root)
    assert ledger.verify(root, base, [])["status"] == "PASS"

    # Real source-bound production proof: caller order is deliberately reversed.
    (tmp_path / "proof").mkdir()
    proof_root = _toy(tmp_path / "proof")
    (proof_root / ".git/info/exclude").write_text(".tmp/\n")
    owner = proof_root / "tests/test_toy.py"
    before = owner.read_text()
    base = ledger.git(proof_root, "rev-parse", "HEAD")
    old_owners = list(ledger.test_shapes("tests/test_toy.py", before))
    owner.write_text(before + "\ndef test_added():\n    assert reply(False) == 'error'\n")
    _commit(proof_root)
    new_owners = list(ledger.test_shapes("tests/test_toy.py", owner.read_text()))
    config = _config()
    config.update(
        base=base, removed=list(reversed(old_owners)), survivors=list(reversed(new_owners))
    )
    config["mapping"] = {
        n: {"survivors": [n], "reason": "complete retained module"} for n in old_owners
    }
    with pytest.raises(ledger.EvidenceError, match="unproven"):
        ledger.verify(proof_root, base, [])
    omitted = copy.deepcopy(config)
    omitted["survivors"].remove("tests/test_toy.py::test_added")
    with pytest.raises(ledger.EvidenceError, match="proof-unknown"):
        evidence.prove(proof_root, omitted, tmp_path / "omitted")
    assert json.loads((tmp_path / "omitted/receipt.json").read_text())["category"] == (
        "module-context-needs-complete-owner-population"
    )
    output = tmp_path / "complete"
    proof = evidence.prove(proof_root, config, output)
    assert proof["status"] == "PASS"
    assert ledger.verify(proof_root, base, [output / "ledger.json"])["status"] == "PASS"
    for run in proof["runs"]:
        assert run["test_selectors"] == ["tests/test_toy.py"]
        expected = old_owners if run["side"] == "removed" else new_owners
        assert [case["node"] for case in run["cases"]] == expected
        assert run["execution_order"] == [case["key"] for case in run["cases"]]
    saved = json.loads((output / "ledger.json").read_text())
    for field, value, category in (
        ("test_selectors", config["removed"], "changed-module-context-selectors"),
        ("execution_order", [], "collection-execution-order-mismatch"),
    ):
        forged = copy.deepcopy(saved)
        run = forged["proof"]["runs"][0]
        artifact = output / run["artifact"]["path"]
        original = artifact.read_bytes()
        run[field] = value
        artifact.write_text(
            json.dumps({k: v for k, v in run.items() if k not in {"artifact", "coverage_artifact"}})
        )
        run["artifact"].update(
            sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
            bytes=artifact.stat().st_size,
        )
        try:
            with pytest.raises(ledger.EvidenceError, match=category):
                ledger.executable_proof(
                    output,
                    forged["proof"],
                    forged["binding"],
                    forged["removed"],
                    forged["survivors"],
                    root=proof_root,
                )
        finally:
            artifact.write_bytes(original)
    assert ledger.verify(proof_root, base, [output / "ledger.json"])["status"] == "PASS"

    # A complete plan cannot turn runtime suppression into healthy execution.
    owner.write_text(
        "import pytest\n"
        + before[: before.index("def test_removed")]
        + "def test_added(request):\n"
        + "    for item in request.session.items:\n"
        + "        if item.name != 'test_added':\n"
        + "            item.add_marker(pytest.mark.skip())\n\n"
        + before[before.index("def test_removed") :]
    )
    _commit(proof_root)
    with pytest.raises(ledger.EvidenceError, match="proof-unknown"):
        evidence.prove(proof_root, config, tmp_path / "runtime-suppression")
    assert json.loads((tmp_path / "runtime-suppression/receipt.json").read_text())["category"] == (
        "worker-incomplete"
    )
    # Ordinary body-only deletion retains node-based collection.
    owner.write_text(
        before[before.index("from toy") :].replace(
            "def test_removed():\n    assert reply(True) == 'accepted'\n", ""
        )
    )
    ordinary = _config()
    assert ledger.proof_selectors(
        proof_root,
        base,
        {
            "removed": ordinary["removed"],
            "survivors": ordinary["survivors"],
        },
    ) == {"removed": ordinary["removed"], "survivors": ordinary["survivors"]}
