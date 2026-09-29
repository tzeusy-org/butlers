"""Unit coverage for the shared SECURITY DEFINER search-path predicate (bu-mzm3su.3)."""

from __future__ import annotations

import pytest

from butlers.core.definer_search_path import is_pinned

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
