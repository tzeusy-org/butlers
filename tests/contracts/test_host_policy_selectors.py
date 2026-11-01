"""The source inventory rejects growth and loss; it does not certify runtime policy."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_host_policy_selectors as gate  # noqa: E402

pytestmark = pytest.mark.unit


def test_reviewed_selector_inventory_rejects_growth_and_loss(tmp_path: Path) -> None:
    # Real checked-in source, classifications and normative headings must agree.
    inventory = json.loads((ROOT / gate.INVENTORY).read_text())
    sources = {
        str(p.relative_to(ROOT)): p.read_text() for p in (ROOT / "src/butlers").rglob("*.py")
    }
    actual = gate.scan(sources, set(inventory["roster_names"]))
    assert gate.check(actual, inventory, ROOT) == []
    assert gate.counts(inventory["selectors"]) == inventory["counts"]
    assert inventory["baseline"]["counts"]["direct"] == 59
    assert inventory["baseline"]["counts"]["direct_files"] == 30
    assert inventory["baseline"]["counts"]["container"] == 63

    # Small actual Python fixtures isolate each rejecting predicate. They keep
    # another selector alive so loss cannot pass merely by failing the empty scan.
    spec = tmp_path / "openspec/specs/fixture/spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("### Requirement: Bound identity\n")
    names = {"switchboard", "messenger", "finance", "general"}
    path = "src/butlers/fixture.py"
    source = "def admit(name):\n    return name == 'switchboard' or name == 'messenger'\n"

    def reviewed(rows):
        result = copy.deepcopy(rows)
        for row in result:
            row.update(
                category="CANONICAL_IDENTITY_AUTHORITY_OR_PROVENANCE",
                purpose="Require the canonical source identity before admission.",
                reason="Reviewed fixture identity boundary.",
                baseline_ids=[row["id"]],
                citations=[
                    {"path": "openspec/specs/fixture/spec.md", "requirement": "Bound identity"}
                ],
            )
        return result

    rows = gate.scan({path: source}, names)
    base = dict(
        selectors=reviewed(rows),
        baseline={"selectors": reviewed(rows)},
        retired=[],
        review_changes=[],
    )
    assert len(rows) == 2
    assert gate.check(rows, base, tmp_path) == []

    for addition in [
        "\nif name == 'finance': pass\n",
        "\nif name in ('finance', 'general'): pass\n",
        "\nTARGET = 'fi' + 'nance'\nALIAS = TARGET\nif name == ALIAS: pass\n",
        "\nif getattr(config, 'name') == 'finance': pass\n",
        "\nif name != required_name: pass\n",
        "\nclients.get('finance')\n",
    ]:
        added = gate.scan({path: source + addition}, names)
        errors = gate.check(added, base, tmp_path)
        assert any(e.startswith("unclassified selector:") for e in errors), errors

    # Even explicitly classifying the new host behavior cannot raise its baseline.
    added = gate.scan({path: source + "\nif name == 'finance': pass\n"}, names)
    classified = copy.deepcopy(base)
    new = next(row for row in reviewed(added) if row["id"] not in {r["id"] for r in rows})
    new["category"] = "EVIDENCED_HOST_POLICY"
    new["baseline_ids"] = []
    classified["selectors"].append(new)
    errors = gate.check(added, classified, tmp_path)
    assert any(e.startswith("evidenced host-policy growth:") for e in errors), errors

    lost = gate.scan({path: source.replace("name == 'switchboard'", "False")}, names)
    assert len(lost) == 1
    errors = gate.check(lost, base, tmp_path)
    assert any(e.startswith("lost selector:") for e in errors), errors
    moved = gate.scan({path: source.replace("def admit", "def wrapped")}, names)
    errors = gate.check(moved, base, tmp_path)
    assert any(e.startswith("lost selector:") for e in errors)
    assert any(e.startswith("unclassified selector:") for e in errors)

    # Deleting source AND its inventory cannot silently erase prior custody.
    dropped = copy.deepcopy(base)
    missing = next(row for row in rows if row["id"] not in {r["id"] for r in lost})
    dropped["selectors"] = [r for r in dropped["selectors"] if r["id"] != missing["id"]]
    errors = gate.check(lost, dropped, tmp_path, previous=base)
    assert any(e.startswith("unexplained inventory loss/reclassification:") for e in errors), errors
    dropped["retired"] = [dict(id=missing["id"], reason="Reviewed removal of fixture behavior.")]
    dropped["review_changes"] = copy.deepcopy(dropped["retired"])
    assert gate.check(lost, dropped, tmp_path, previous=base) == []

    candidate = copy.deepcopy(base)
    candidate["baseline"]["selectors"][0]["category"] = "HOST_POLICY_CANDIDATE_NEEDS_SHAPING"
    errors = gate.check(rows, candidate, tmp_path)
    assert any(e.startswith("unresolved policy candidate lost/reclassified:") for e in errors)
    candidate["selectors"][0]["category"] = "HOST_POLICY_CANDIDATE_NEEDS_SHAPING"
    assert gate.check(rows, candidate, tmp_path) == []
    assert "empty selector scan" in gate.check(
        [], dict(selectors=[], baseline={"selectors": []}, retired=[], review_changes=[]), tmp_path
    )

    # The actual local and hosted required guard paths both execute this check.
    makefile = (ROOT / "Makefile").read_text()
    target = makefile.split("check-guards:", 1)[1].split("\n", 1)[0]
    assert "check-host-policy-selectors" in target
    assert (
        "check-host-policy-selectors:\n\tpython3 scripts/check_host_policy_selectors.py" in makefile
    )
    guards = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]["guards"]
    step = next(s for s in guards["steps"] if s.get("id") == "host_policy_selectors")
    assert step["run"] == "python3 scripts/check_host_policy_selectors.py"
    assert step["if"] == "${{ !cancelled() }}"
    finalizer = next(s for s in guards["steps"] if s.get("name") == "Fail if any guard failed")
    assert (
        "host_policy_selectors=${{ steps.host_policy_selectors.outcome }}"
        in finalizer["env"]["GUARD_OUTCOMES"]
    )
