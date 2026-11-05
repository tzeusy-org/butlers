"""SQL visibility predicates shared by Chronicler-aware memory readers."""

from __future__ import annotations

import re
from typing import Any

_ALIAS = re.compile(r"^[a-z][a-z0-9_]*$")
_GENERATED_FACT_PREDICATES = "'lane-skew', 'sleep-debt', 'revisit-low-confidence-block'"


def catalog_projection_visible(alias: str) -> str:
    """Return the fixed core row-ID predicate for a public catalog alias."""
    if not _ALIAS.fullmatch(alias):
        raise ValueError("unsafe SQL alias")
    return f"public.chronicler_catalog_row_visible({alias}.id)"


def graph_projection_visible(alias: str) -> str:
    """Return the fixed core row-ID predicate for a public graph alias."""
    if not _ALIAS.fullmatch(alias):
        raise ValueError("unsafe SQL alias")
    return f"public.chronicler_graph_row_visible({alias}.id)"


async def local_fact_visibility_condition(
    pool: Any, *, memory_schema: str | None, alias: str
) -> str | None:
    """Build the local fact filter from the configured memory schema.

    Chronicler's 030 migration installs ``chronicler_mem.facts_readable``. Before
    that migration, keep owner and ordinary facts readable while withholding the
    recognized generated predicates whose repair state is not installed yet.
    The optional relation name is introduced only after a catalog-presence check,
    so pre-030 readers never parse a reference to it.
    """
    if memory_schema != "chronicler_mem":
        return None
    if not _ALIAS.fullmatch(alias):
        raise ValueError("unsafe SQL alias")

    readable_view_exists = await pool.fetchval(
        "SELECT pg_catalog.to_regclass('chronicler_mem.facts_readable') IS NOT NULL"
    )
    if readable_view_exists:
        return (
            "EXISTS (SELECT 1 FROM chronicler_mem.facts_readable AS chronicler_visible "
            f"WHERE chronicler_visible.id = {alias}.id)"
        )

    return (
        f"({alias}.content_authority IN ('owner', 'owner_device') "
        f"OR {alias}.predicate NOT IN ({_GENERATED_FACT_PREDICATES}))"
    )
