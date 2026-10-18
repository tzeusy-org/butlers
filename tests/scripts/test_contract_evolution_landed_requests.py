"""bu-vdgxlp: a landed contract request never blocks, and never admits, a later evolution.

A request and its authorization that the trusted parent already carries are public history. When a
candidate then touches one of that request's owner files, the old request can no longer be re-proven
against the new base. It must be skipped, not re-proven, and it admits nothing by itself: the
owner's current losses still need a candidate-only request.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

_SIBLING = Path(__file__).with_name("test_check_condensation_ledger.py")
_spec = importlib.util.spec_from_file_location("_ledger_fixtures_for_landed_requests", _SIBLING)
_fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)

ledger = _fixtures.ledger
_contract_repository = _fixtures._contract_repository
_commit_all = _fixtures._commit_all
_EXAMPLE_ID = _fixtures._EXAMPLE_ID

_OWNER = "tests/test_owner.py::test_owner"


def _evolve_again(tmp_path):
    """A landed request, then a second authorized evolution of the same owner and contract.

    Returns ``(root, base, trusted, request_path)``. ``trusted`` carries the first request, its
    authorization and the second authorization; the candidate carries the second request and the
    second change to the owner file and to the governing requirement.
    """
    import contract_evolution as evolution

    root, base, _authorized, _authority, _request, _commit = _contract_repository(tmp_path)
    owner_path = root / "tests/test_owner.py"
    spec_path = root / "spec.md"
    landed_owner = owner_path.read_text()
    landed_spec = spec_path.read_text()
    base_spec = ledger.committed_body(root, base, "spec.md").decode()
    next_owner = landed_owner.replace("assert value > 0", "assert value >= 1")
    next_spec = landed_spec.replace("nonzero positive", "nonzero positive integer")
    assert next_owner != landed_owner and next_spec != landed_spec

    request = {
        "schema": evolution.REQUEST_SCHEMA,
        "authorization": "example-change-2",
        "owners": [_OWNER],
        "timeout_s": 60,
    }
    request_rel = evolution.REQUESTS + "example-2.json"
    request_path = root / request_rel
    request_path.write_text(json.dumps(request))
    owner_path.write_text(next_owner)
    spec_path.write_text(next_spec)
    authority = {
        "schema": evolution.AUTH_SCHEMA,
        "id": "example-change-2",
        "bead": "example.2",
        "decision": {"owner": "Owner", "date": "2026-10-18", "reference": "independent review"},
        "owners": [
            {
                "before": _OWNER,
                "after": _OWNER,
                "contracts": [_EXAMPLE_ID],
                "reason": "Tighten the explicit expectation while retaining every case.",
            }
        ],
        "contracts": [
            {
                "path": "spec.md",
                "id": _EXAMPLE_ID,
                "before_sha256": evolution.requirement_digest(base_spec, _EXAMPLE_ID),
                "after_sha256": evolution.requirement_digest(next_spec, _EXAMPLE_ID),
            }
        ],
        "inputs": [
            {
                "path": path,
                "before": evolution.record_at(root, base, path),
                "after": evolution.current_record(root, path),
            }
            for path in ("tests/test_owner.py", "pyproject.toml", "uv.lock", request_rel)
        ],
    }
    auth_path = root / evolution.AUTHORIZATIONS / "example-change-2.json"
    auth_path.write_text(json.dumps(authority))
    owner_path.write_text(landed_owner)
    spec_path.write_text(landed_spec)
    request_path.unlink()
    trusted = _commit_all(root, "second authorization is independently protected")
    owner_path.write_text(next_owner)
    spec_path.write_text(next_spec)
    request_path.write_text(json.dumps(request))
    _commit_all(root, "candidate evolves a landed request's owner")
    return root, base, trusted, request_path


def test_fresh_request_evolves_an_owner_a_landed_request_also_names(tmp_path):
    """bu-vdgxlp: the candidate-only request admits the loss; the landed one is skipped."""
    root, base, trusted, _ = _evolve_again(tmp_path)
    output = tmp_path / "proofs"
    output.mkdir()
    result = ledger.verify(root, base, [], contract_directory=output, trusted=trusted)
    assert result["status"] == "PASS" and result["losses"] == 1


def test_a_landed_request_alone_admits_nothing_once_its_owner_moves(tmp_path):
    """bu-vdgxlp: without the candidate-only request the owner's loss is unproven."""
    root, base, trusted, request_path = _evolve_again(tmp_path)
    request_path.unlink()
    _commit_all(root, "drop the candidate-only request")
    output = tmp_path / "proofs"
    output.mkdir()
    with pytest.raises(ledger.EvidenceError, match="unproven-test-or-assertion-loss"):
        ledger.verify(root, base, [], contract_directory=output, trusted=trusted)


def test_a_landed_request_with_edited_authority_bytes_is_still_reproven(tmp_path):
    """bu-vdgxlp: only byte-identical landed authority is skipped; an edit is refused as before.

    The candidate-only request is removed so the landed pair is the only authority in play; with
    it present the changed-authorization sweep would refuse first and hide the predicate.
    """
    root, base, trusted, request_path = _evolve_again(tmp_path)
    request_path.unlink()
    authorization = root / "tests/contract-evolution-authorizations/example-change.json"
    authorization.write_text(authorization.read_text().replace("Owner", "Someone"))
    _commit_all(root, "candidate edits the landed authorization")
    output = tmp_path / "proofs"
    output.mkdir()
    with pytest.raises(ledger.EvidenceError, match="changed-protected-contract-authority"):
        ledger.verify(root, base, [], contract_directory=output, trusted=trusted)


def test_a_landed_request_with_edited_request_bytes_is_still_reproven(tmp_path):
    """bu-vdgxlp: an edited landed request is re-proven against the new base and refused."""
    root, base, trusted, request_path = _evolve_again(tmp_path)
    request_path.unlink()
    landed = root / "tests/contract-evolution-requests/example.json"
    landed.write_text(landed.read_text().replace('"timeout_s": 60', '"timeout_s": 61'))
    _commit_all(root, "candidate edits the landed request")
    output = tmp_path / "proofs"
    output.mkdir()
    with pytest.raises(ledger.EvidenceError, match="stale-or-unchanged-authorized-contract"):
        ledger.verify(root, base, [], contract_directory=output, trusted=trusted)
