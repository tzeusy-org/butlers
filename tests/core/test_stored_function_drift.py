"""Unit tests for the stored-function body drift probe's comparison rules (bu-bi5an).

The real-PostgreSQL contract -- drift is reported, a cosmetic rewrite is not --
lives in ``tests/config/test_stored_function_body_drift.py``.  What is here is
the part that needs no database: parsing ``scripts/init-db.sql`` into committed
definitions, and the comparison rules that a live catalog cannot easily be made
to exhibit.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.core.stored_function_drift import (
    DRIFTED,
    INIT_DB_SQL_PATH,
    MATCHED,
    NOT_DEPLOYED,
    STORED_FUNCTION_DRIFT_INIT_DB_SQL_PATH_ENV,
    FunctionDefinition,
    StoredFunctionDriftReport,
    compare_stored_functions,
    compute_stored_function_drift,
    log_stored_function_drift,
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


async def test_configured_committed_source_path_is_used(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An explicit source path takes precedence over the package-relative default."""
    configured_source = tmp_path / "mounted-init-db.sql"
    configured_source.write_text(
        """
        CREATE FUNCTION public.configured_probe()
        RETURNS void
        LANGUAGE plpgsql
        AS $body$
        BEGIN
        END;
        $body$;
        """,
        encoding="utf-8",
    )
    connection = MagicMock()
    connection.fetch = AsyncMock(return_value=[])
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=connection)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setenv(STORED_FUNCTION_DRIFT_INIT_DB_SQL_PATH_ENV, str(configured_source))

    report = await compute_stored_function_drift(pool)

    assert report.is_available
    entry = report.entry("public.configured_probe")
    assert entry is not None
    assert entry.status == NOT_DEPLOYED
    connection.fetch.assert_awaited_once()


async def test_missing_configured_committed_source_degrades_with_a_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A missing configured source is unknown, never an all-clear result."""
    missing_source = tmp_path / "not-mounted-init-db.sql"
    monkeypatch.setenv(STORED_FUNCTION_DRIFT_INIT_DB_SQL_PATH_ENV, str(missing_source))

    report: StoredFunctionDriftReport = await compute_stored_function_drift(
        None  # type: ignore[arg-type]
    )

    assert not report.is_available
    assert report.check_error == "cannot read not-mounted-init-db.sql: FileNotFoundError"
    assert report.entries == ()
    assert report.drifted == ()
    assert not report.is_drifted


async def test_invalid_utf8_committed_source_degrades_rather_than_raising(
    tmp_path: Path,
) -> None:
    """A source with invalid UTF-8 is unknown, never an all-clear result."""
    invalid_source = tmp_path / "invalid-init-db.sql"
    invalid_source.write_bytes(b"CREATE FUNCTION public.invalid() AS $body$\xff$body$;")

    report = await compute_stored_function_drift(None, init_db_path=invalid_source)  # type: ignore[arg-type]

    assert not report.is_available
    assert report.check_error == "cannot read invalid-init-db.sql: UnicodeDecodeError"
    assert report.entries == ()
    assert not report.is_drifted


async def test_configured_source_is_named_in_drift_remediation_log(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Drift remediation identifies the source selected by the operator."""
    configured_source = tmp_path / "mounted-init-db.sql"
    configured_source.write_text(
        """
        CREATE FUNCTION public.configured_probe()
        RETURNS void
        LANGUAGE plpgsql
        AS $body$
        BEGIN
        END;
        $body$;
        """,
        encoding="utf-8",
    )
    connection = MagicMock()
    connection.fetch = AsyncMock(
        return_value=[
            {
                "function_name": "public.configured_probe",
                "body": "BEGIN\nRETURN 1;\nEND;",
            }
        ]
    )
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=connection)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setenv(STORED_FUNCTION_DRIFT_INIT_DB_SQL_PATH_ENV, str(configured_source))

    report = await compute_stored_function_drift(pool)

    assert report.is_drifted
    with caplog.at_level(logging.WARNING, logger="butlers.core.stored_function_drift"):
        log_stored_function_drift(report)

    message = "\n".join(record.getMessage() for record in caplog.records)
    assert configured_source.name in message
    assert STORED_FUNCTION_DRIFT_INIT_DB_SQL_PATH_ENV in message
    assert "scripts/init-db.sql" not in message


async def test_configured_source_is_named_in_unavailable_log(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unavailable configured source names the source operators must repair."""
    missing_source = tmp_path / "mounted-init-db.sql"
    monkeypatch.setenv(STORED_FUNCTION_DRIFT_INIT_DB_SQL_PATH_ENV, str(missing_source))

    report = await compute_stored_function_drift(None)  # type: ignore[arg-type]

    assert not report.is_available
    with caplog.at_level(logging.WARNING, logger="butlers.core.stored_function_drift"):
        log_stored_function_drift(report)

    message = "\n".join(record.getMessage() for record in caplog.records)
    assert missing_source.name in message
    assert STORED_FUNCTION_DRIFT_INIT_DB_SQL_PATH_ENV in message
    assert "scripts/init-db.sql" not in message
