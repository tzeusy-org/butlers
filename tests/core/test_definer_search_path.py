"""Unit coverage for the shared SECURITY DEFINER search-path predicate (bu-mzm3su.3)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

from butlers.core.definer_search_path import (
    REMEDY_INIT_DB,
    DefinerSearchPathReport,
    UnpinnedDefiner,
    is_pinned,
    log_unpinned_definers,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("proconfig", "expected"),
    [
        (["search_path=pg_catalog, pg_temp"], True),
        # Other settings are allowed; only the search_path entry is judged.
        (["search_path=pg_catalog, pg_temp", "row_security=on"], True),
        (["row_security=on", "search_path=pg_catalog, pg_temp"], True),
        # No search_path inherits the caller's path.
        (None, False),
        ([], False),
        (["row_security=on"], False),
        # pg_temp left implicit is searched first for relations and types.
        (["search_path=pg_catalog"], False),
        # Any CREATE-able schema on the path can shadow a catalog overload.
        (["search_path=pg_catalog, public, pg_temp"], False),
        (["search_path=pg_catalog, switchboard, pg_temp"], False),
        (["search_path=switchboard, pg_temp"], False),
        (["search_path=pg_catalog, switchboard"], False),
        # Order matters: pg_temp first would shadow catalog relations.
        (["search_path=pg_temp, pg_catalog"], False),
        # Exact text only; a quoted or respaced variant is not the pinned form.
        (['search_path="pg_catalog", "pg_temp"'], False),
        (["search_path=pg_catalog,pg_temp"], False),
        # Two search_path entries are ambiguous, never pinned.
        (["search_path=pg_catalog, pg_temp", "search_path=pg_catalog, pg_temp"], False),
    ],
)
def test_is_pinned_accepts_only_the_exact_catalog_then_temp_path(
    proconfig: list[str] | None, expected: bool
) -> None:
    assert is_pinned(proconfig) is expected


_NOW = datetime(2026, 9, 29, tzinfo=UTC)
_UNPINNED = UnpinnedDefiner(
    signature="restore_drill_executor.is_due(p_interval_seconds integer)",
    owner="restore_drill_executor_owner",
    search_path="pg_catalog, public, pg_temp",
    remedy=REMEDY_INIT_DB,
)


@pytest.mark.parametrize(
    ("report", "level", "must_contain", "must_not_contain"),
    [
        (
            DefinerSearchPathReport(checked_at=_NOW, entries=(), checked_count=74),
            logging.INFO,
            ["all 74 SECURITY DEFINER functions are pinned"],
            ["unavailable"],
        ),
        (
            DefinerSearchPathReport(checked_at=_NOW, entries=(_UNPINNED,), checked_count=74),
            logging.WARNING,
            [
                "1 of 74 SECURITY DEFINER functions are not pinned",
                "restore_drill_executor.is_due(p_interval_seconds integer)",
                "owner=restore_drill_executor_owner",
                "search_path=pg_catalog, public, pg_temp",
                "run migrations to head",
                "re-run scripts/init-db.sql",
            ],
            [],
        ),
        (
            DefinerSearchPathReport(
                checked_at=_NOW, entries=(), check_error="cannot read pg_proc: OSError"
            ),
            logging.WARNING,
            ["check unavailable", "NOT checked", "not a clean result"],
            ["all 0", "pinned to"],
        ),
    ],
    ids=["clean", "unpinned", "unavailable"],
)
def test_log_unpinned_definers_names_the_remedy_and_never_fakes_a_clean_result(
    caplog, report, level, must_contain, must_not_contain
) -> None:
    with caplog.at_level(logging.INFO, logger="butlers.core.definer_search_path"):
        log_unpinned_definers(report)
    assert [record.levelno for record in caplog.records] == [level]
    message = caplog.records[0].getMessage()
    for text in must_contain:
        assert text in message
    for text in must_not_contain:
        assert text not in message
