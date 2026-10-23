"""Single committed-source inventory guard for RFC0038 obligation coverage.

REQ-goal-orchestration-007. This guard proves static inventory coverage only;
production declarations, dynamic repository rows and runtime proofs are separate.
"""

import ast
import hashlib
import json
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract
_ROOT = Path(__file__).resolve().parents[2]


def test_committed_inventory_matches_all_roster_schedules_and_named_source_boundaries():
    inventory = json.loads((_ROOT / "docs/runtime/obligation-inventory.json").read_text())
    expected = {}
    for path in sorted((_ROOT / "roster").glob("*/butler.toml")):
        for row in tomllib.loads(path.read_text()).get("butler", {}).get("schedule", []):
            expected[(str(path.relative_to(_ROOT)), row["name"])] = (
                row.get("dispatch_mode", "prompt"),
                row.get("job_name"),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
    actual = {
        (row["path"], row["name"]): (row["dispatch_mode"], row["job_name"], row["source_sha256"])
        for row in inventory["schedules"]
    }
    assert expected  # An empty/misparsed roster inventory cannot pass.
    assert len(actual) == len(inventory["schedules"])
    assert actual == expected
    assert {row["name"] for row in inventory["schedules"] if row["dispatch_mode"] == "prompt"}
    for family in inventory["producer_families"]:
        assert (_ROOT / family["path"]).is_file()
    tree = ast.parse((_ROOT / "src/butlers/scheduled_jobs.py").read_text())
    definitions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for entry in inventory["known_model_backed_job_wrappers"]:
        assert entry["symbol"] in definitions
    for entry in inventory["protected_deterministic_jobs"]:
        assert (_ROOT / entry["path"]).is_file()
        assert any(
            row["dispatch_mode"] == "job" and row["job_name"] == entry["job_name"]
            for row in inventory["schedules"]
        )

    # The committed model-job list must cover the actual registry, including
    # wrappers hidden behind job mode. Unknown jobs are not declared safe here.
    from butlers.scheduled_jobs import (
        classify_schedule_job,
        get_deterministic_schedule_job_registry,
    )

    registered_model_wrappers = {
        handler.__name__
        for jobs in get_deterministic_schedule_job_registry().values()
        for handler in jobs.values()
        if classify_schedule_job(handler) == "model_backed"
    }
    assert registered_model_wrappers == {
        entry["symbol"] for entry in inventory["known_model_backed_job_wrappers"]
    }
