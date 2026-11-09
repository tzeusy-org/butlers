"""Index of RFC 0039 D4 drift checks 1 to 5, each shown red by a bad manifest or catalog edit.

The PostgreSQL-backed checks live in ``tests/integration``; this file holds the PG-free
manifest edits (checks 1 and 3) and ``DRIFT_CHECKS``, the one table that names the test
which turns each rule red. A meta-test fails when a named test disappears, so the index
cannot rot silently (tasks 1.5; REQ-public-mcp-projection-002 and -004).
"""

from __future__ import annotations

import ast
import copy
import tomllib
from importlib.resources import files
from pathlib import Path

import pytest

from butlers.knowledge_read.manifest import ManifestError, _parse_manifest, load_manifest

_TESTS = Path(__file__).resolve().parents[1]
_INTEGRATION = "integration/test_public_mcp_manifest.py"
_LIFECYCLE = "integration/test_public_mcp_owner_lifecycle.py"
_THIS = "knowledge_read/test_projection_drift_contract.py"

# check number -> (rule, test file under tests/, test function that shows it red)
DRIFT_CHECKS: tuple[tuple[int, str, str, str], ...] = (
    (1, "manifest table or column missing", _INTEGRATION, "test_catalog_guards_precede_ddl_with_healthy_companions"),
    (1, "filter column omitted from column list", _THIS, "test_check_1_and_3_bad_manifest_edit_goes_red"),
    (1, "replica identity lists a column not published", _THIS, "test_check_1_and_3_bad_manifest_edit_goes_red"),
    (1, "REPLICA IDENTITY FULL", _INTEGRATION, "test_catalog_guards_precede_ddl_with_healthy_companions"),
    (1, "UPDATE/DELETE fail on the primary", _INTEGRATION, "test_every_row_filtered_table_accepts_update_and_delete_with_the_real_publication"),
    (2, "publication differs from manifest", _INTEGRATION, "test_publication_action_explicit_columns_filters_and_wildcards"),
    (2, "subscriber differs from manifest", _LIFECYCLE, "test_primary_publication_drift_refuses_before_subscriber_mutation"),
    (3, "denylist, JSONB, marks, predicate lists", _THIS, "test_check_1_and_3_bad_manifest_edit_goes_red"),
    (4, "canary present in the projection after a full sync", _LIFECYCLE, "test_canaries_in_excluded_sources_are_absent_after_a_full_sync"),
    (5, "migration drops or renames a published column", _INTEGRATION, "test_dropping_or_renaming_a_published_column_fails_the_catalog_check"),
)  # fmt: skip


def _packaged_raw() -> dict:
    return tomllib.loads(files("butlers.knowledge_read").joinpath("projection.toml").read_text())


def _entry(raw: dict, source: str) -> dict:
    return next(t for t in raw["table"] if t["source"] == source)


def _add_column(raw: dict, source: str, name: str, typ: str = "text") -> None:
    entry = _entry(raw, source)
    entry["columns"].append(name)
    entry["types"][name] = typ
    entry["classes"][name] = "system"


def _drop_column(raw: dict, source: str, name: str) -> None:
    entry = _entry(raw, source)
    entry["columns"].remove(name)
    del entry["types"][name]
    del entry["classes"][name]


def _set(source: str, **fields):
    def edit(raw: dict) -> None:
        _entry(raw, source).update(fields)

    return edit


def _predicates(extra: str):
    def edit(raw: dict) -> None:
        _entry(raw, "general.facts")["row_filter"]["predicate"].append(extra)

    return edit


def _ident_without(raw: dict) -> None:
    _entry(raw, "general.facts")["replica_identity"].remove("sensitivity")


def _validity_unpublished(raw: dict) -> None:
    entry = _entry(raw, "general.facts")
    entry["replica_identity"].remove("validity")
    _drop_column(raw, "general.facts", "validity")


def _facts_filter(**filters):
    def edit(raw: dict) -> None:
        _entry(raw, "general.facts")["row_filter"].update(filters)

    return edit


# (case id, edit, expected error) -- every edit starts from the packaged manifest.
RED = [
    ("1 filter column outside column list", _validity_unpublished, r"filter column omitted \['validity'\]"),
    ("1 identity omits a filter column's published twin", lambda r: _drop_column(r, "general.facts", "validity"), r"incomplete identity columns \['validity'\]"),
    ("1 identity names an unpublished column", _set("general.facts", replica_identity=["id", "nope"]), r"incomplete identity columns \['nope'\]"),
    ("1 filter outside replica identity", _ident_without, "filter omitted from identity"),
    ("3 credential-tier column, finance", lambda r: _add_column(r, "finance.transactions", "api_key"), "forbidden column"),
    ("3 credential-tier column, knowledge", lambda r: _add_column(r, "general.facts", "password"), "forbidden column"),
    ("3 breadth-tier column outside finance/health", lambda r: _add_column(r, "general.facts", "phone"), "forbidden column"),
    ("3 embedding column, finance", lambda r: _add_column(r, "finance.transactions", "embedding"), "forbidden column"),
    ("3 JSONB column", lambda r: _add_column(r, "finance.transactions", "payload", "jsonb"), "unsafe/missing column types"),
    ("3 private table", lambda r: _entry(r, "finance.transactions").update(source="finance.credentials"), "private source"),
    ("3 contact predicate allowlisted", _predicates("contact_phone"), "invalid memory fact filters"),
    ("3 sensitive facts allowlisted", _facts_filter(sensitivity=["normal", "sensitive"]), "invalid memory fact filters"),
    ("3 dlp mark on credential path", _set("finance.transactions", json_identifiers=["metadata.api_token"]), "unsafe identifier mark"),
    ("3 dlp mark on json_fields_public", _set("finance.json_fields_public", identifiers=["value_text"]), "identifier marks not permitted"),
    ("3 dlp mark outside finance/health", _set("general.facts", identifiers=["subject"]), "identifier marks not permitted"),
    ("3 composite finance key", _set("finance.transactions", key=["id", "account_id"]), "single column"),
]  # fmt: skip


@pytest.mark.parametrize(("case", "edit", "reason"), RED, ids=[c[0] for c in RED])
def test_check_1_and_3_bad_manifest_edit_goes_red(case, edit, reason):
    raw = copy.deepcopy(_packaged_raw())
    edit(raw)
    with pytest.raises(ManifestError, match=reason):
        _parse_manifest(raw, require_classes=True)
    assert load_manifest().tables


def test_finance_and_health_share_what_other_domains_withhold():
    """OD17: the breadth tier does not apply there, but the credential tier still does."""
    raw = copy.deepcopy(_packaged_raw())
    _add_column(raw, "finance.transactions", "phone")
    assert _parse_manifest(raw, require_classes=True)


def test_every_drift_check_has_a_named_red_that_exists():
    assert {check for check, *_ in DRIFT_CHECKS} == {1, 2, 3, 4, 5}
    for _, rule, relative, name in DRIFT_CHECKS:
        tree = ast.parse((_TESTS / relative).read_text())
        defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        assert name in defined, f"{rule}: {relative}::{name} is missing"
