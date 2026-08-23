"""Unit tests for the stored-function body drift probe's comparison rules (bu-bi5an).

The real-PostgreSQL contract -- drift is reported, a cosmetic rewrite is not --
lives in ``tests/config/test_stored_function_body_drift.py``.  What is here is
the part that needs no database: parsing ``scripts/init-db.sql`` into committed
definitions, and the comparison rules that a live catalog cannot easily be made
to exhibit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from butlers.core.stored_function_drift import (
    DRIFTED,
    INIT_DB_SQL_PATH,
    MATCHED,
    NOT_DEPLOYED,
    FunctionDefinition,
    StoredFunctionDriftReport,
    compare_stored_functions,
    compute_stored_function_drift,
    parse_function_definitions,
)

pytestmark = pytest.mark.unit

#: A ``CREATE FUNCTION`` nested inside an installer body -- the hard parser case.
_PLANTER = "public.runtime_attention_plant_legacy_debounce_marker"
#: A top-level ``CREATE OR REPLACE FUNCTION``.
_TOP_LEVEL = "runtime_attention_admin.finalize_interface"
#: Two committed variants: v1 from ``install_interface``, v2 from
#: ``upgrade_producers_v2`` (invoked once by ``core_199``).
_TWO_VARIANT = "public.append_runtime_attention_model_breaker"


def test_parses_top_level_and_nested_committed_function_bodies() -> None:
    definitions = parse_function_definitions(INIT_DB_SQL_PATH.read_text(encoding="utf-8"))

    by_name: dict[str, list[FunctionDefinition]] = {}
    for definition in definitions:
        by_name.setdefault(definition.function, []).append(definition)

    assert _TOP_LEVEL in by_name, "a top-level CREATE FUNCTION was not parsed"
    assert _PLANTER in by_name, "a CREATE FUNCTION nested inside an installer was not parsed"
    assert len(by_name[_TWO_VARIANT]) == 2, (
        f"{_TWO_VARIANT} has a v1 and a v2 committed body; both must be parsed, "
        "or a database on either one reads as drift"
    )
    assert "legacy_debounce_planted" in by_name[_PLANTER][0].body, (
        "the parsed planter body does not carry the literal init-db.sql commits, "
        "so the parser sliced the wrong span"
    )
    assert by_name[_PLANTER][0].line > 1


def test_an_overload_the_committed_source_does_not_describe_is_reported() -> None:
    """Every deployed body must match a committed one, not merely one of them."""
    definitions = (FunctionDefinition(function="public.f", line=10, body="BEGIN\nRETURN 1;\nEND"),)

    entries = compare_stored_functions(
        definitions, {"public.f": ["BEGIN\nRETURN 1;\nEND", "BEGIN\nRETURN 2;\nEND"]}
    )

    assert [entry.status for entry in entries] == [DRIFTED]


def test_a_deployed_body_matching_the_second_committed_variant_is_matched() -> None:
    definitions = (
        FunctionDefinition(function="public.f", line=10, body="BEGIN\nRETURN 1;\nEND"),
        FunctionDefinition(function="public.f", line=90, body="BEGIN\nRETURN 2;\nEND"),
    )

    entries = compare_stored_functions(definitions, {"public.f": ["BEGIN\nRETURN 2;\nEND"]})

    assert [entry.status for entry in entries] == [MATCHED]
    assert entries[0].matched_line == 90


def test_a_committed_function_absent_from_the_catalog_is_named() -> None:
    definitions = (FunctionDefinition(function="public.f", line=10, body="BEGIN\nEND"),)

    entries = compare_stored_functions(definitions, {})

    assert [entry.status for entry in entries] == [NOT_DEPLOYED]
    assert entries[0].deployed_digests == ()
    assert entries[0].matched_line is None


async def test_an_unreadable_committed_source_degrades_rather_than_reporting_all_clear(
    tmp_path: Path,
) -> None:
    """A probe that cannot read init-db.sql must not answer 'no drift'."""
    report: StoredFunctionDriftReport = await compute_stored_function_drift(
        None,  # type: ignore[arg-type]
        init_db_path=tmp_path / "does-not-exist.sql",
    )

    assert not report.is_available
    assert report.check_error
    assert report.entries == ()
    assert not report.is_drifted
