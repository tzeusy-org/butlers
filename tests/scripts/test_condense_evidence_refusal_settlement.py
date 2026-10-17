"""REQ-testing-053: refused proof sides settle independently of completion order."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import condense_evidence as evidence  # noqa: E402

from tests.scripts.test_condense_evidence import (  # noqa: E402, F401
    _config,
    _disposable_git_template,
    _toy,
)

pytestmark = pytest.mark.unit


def _commit(root, message):
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


def test_both_refused_sides_settle_whichever_finishes_first(tmp_path):
    """REQ-testing-053: a refused observation never cancels its completing sibling."""
    root = _toy(tmp_path)
    shutil.copy2(
        Path(__file__).resolve().parents[2] / "scripts/condensation_lineage.py", root / "scripts"
    )
    # Both sides read the execution request at interpreter startup, so both refuse.
    (root / "toy.py").write_text(
        "import builtins\ndef reply():\n    return builtins._proof_value\n"
    )
    (root / "sitecustomize.py").write_text(
        "import builtins,json\nfrom pathlib import Path\n"
        "builtins._proof_value=int(json.loads((Path(__file__).parent/"
        "'tests/condensation-plans/current.json').read_text())['cluster'])\n"
    )
    config = _config()
    config.update(base="event-base", cluster="1")
    request = root / "tests/condensation-plans/current.json"
    request.parent.mkdir()
    request.write_text(json.dumps(config))
    owner = root / "tests/test_toy.py"
    output = tmp_path / "refused"
    sibling = output / "survivors-baseline.json"
    # The removed side finishes last: its owner waits until the survivors side has
    # settled, which merges the refused observation into its carrier just before
    # raising. Without the fix that refusal cancels the waiting removed side.
    owner.write_text(
        "import json, os, time\nfrom toy import reply\n"
        "def settled():\n"
        "    try:\n"
        f"        return 'request_access' in json.loads(open({str(sibling)!r}).read())\n"
        "    except (OSError, ValueError):\n"
        "        return False\n"
        "def test_removed():\n"
        "    deadline = time.monotonic() + 40\n"
        "    while not settled() and time.monotonic() < deadline:\n"
        "        time.sleep(0.05)\n"
        "    assert settled() and reply()==1\n"
        "def test_survivor():\n    assert isinstance(reply(),int)\n"
    )
    _commit(root, "original exact value read at startup")
    base = evidence.git(root, "rev-parse", "HEAD")
    request.write_text(json.dumps(dict(config, cluster="2")))
    owner.write_text(
        "from toy import reply\ndef test_survivor():\n    assert isinstance(reply(),int)\n"
    )
    _commit(root, "remove exact assertion")
    config["base"] = base
    with pytest.raises(evidence.EvidenceError, match="proof-unknown"):
        evidence.prove(root, config, tmp_path / "refused")
    receipt = evidence.strict_json(tmp_path / "refused/receipt.json")
    assert receipt == {
        "status": "UNKNOWN",
        "category": "worker-incomplete",
        "restored": True,
        "cleanup": True,
    }
    for side in ("removed", "survivors"):
        run = evidence.strict_json(tmp_path / f"refused/{side}-baseline.json")
        access = run["request_access"]
        assert access["complete"] is True and access["accesses"] > 0, side
        assert access["probe_accesses"] > 0 and all(actor["exited"] for actor in access["actors"])
        assert run["complete"] is False
        assert all(case["phases"]["call"] == "passed" for case in run["cases"]), side
