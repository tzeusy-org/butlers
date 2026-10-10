"""REQ-testing-053: one consumer refuses unproven loss and malformed evidence."""

from __future__ import annotations

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
