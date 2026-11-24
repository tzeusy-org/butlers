"""Subscriber-side search columns and their GIN indexes (RFC 0039 D2, D11).

A ``mcp_read`` view hands readers ``fields`` as jsonb, and no index can match an expression
over it. A source whose search scan is too costly to run unindexed therefore declares search
columns here. Its read view appends each column after ``fields``, and the owner step builds a
GIN index on the same expression over the subscriber's base table. The view column inlines to
that exact expression, so the planner can use the index through the view as ``mcp_public_ro``.
Each expression reads published columns only, and only through immutable built-ins, because an
index expression must be immutable. ``concat_ws`` is stable, so the vector joins its inputs
with ``||`` instead. ``to_tsvector`` ignores the extra blanks a NULL leaves, so the vector equals
the ``concat_ws`` form the other sources use.

Both the owner step (``butlers.migrations.public_mcp_owner``) and the ``search`` reader import
these declarations. Neither spells an expression of its own.
"""

from __future__ import annotations

from dataclasses import dataclass

# The ranked title is cut to this many characters before trigram similarity (search.TITLE_MAX).
TITLE_CHARS = 200


@dataclass(frozen=True)
class SearchColumn:
    """One read-view column over published base columns, and the GIN index on it."""

    name: str
    type: str
    expression: str
    index: str
    opclass: str | None = None


SEARCH_COLUMNS: dict[str, tuple[SearchColumn, ...]] = {
    "chronicler.episodes_public": (
        SearchColumn(
            "search_title",
            "text",
            f"left(COALESCE(title, kind), {TITLE_CHARS})",
            "episodes_public_mcp_search_title",
            "mcp_ext.gin_trgm_ops",
        ),
        SearchColumn(
            "search_vector",
            "tsvector",
            "to_tsvector('english'::regconfig, COALESCE(title, '') || ' ' || "
            "COALESCE(kind, '') || ' ' || COALESCE(category, ''))",
            "episodes_public_mcp_search_vector",
        ),
    ),
}
# The published columns each source's expressions read; a manifest without them gets none.
SEARCH_INPUTS: dict[str, frozenset[str]] = {
    "chronicler.episodes_public": frozenset({"title", "kind", "category"}),
}


def search_columns(source: str, columns: tuple[str, ...]) -> tuple[SearchColumn, ...]:
    """The search columns of ``source`` when every input column is published, else none."""
    if not SEARCH_INPUTS.get(source, frozenset()) <= set(columns):
        return ()
    return SEARCH_COLUMNS.get(source, ())


def index_sql(column: SearchColumn, name: str, table: str) -> str:
    """``CREATE INDEX IF NOT EXISTS`` for one search column; the index joins ``table``'s schema."""
    opclass = f" {column.opclass}" if column.opclass else ""
    expression = f"({column.expression}){opclass}"
    return f"CREATE INDEX IF NOT EXISTS {name} ON {table} USING gin ({expression})"
