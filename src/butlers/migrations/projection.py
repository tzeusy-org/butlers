"""Migration helpers for tables the public MCP projection replicates (RFC 0039 D2).

PG15+ allows a row filter on a table that publishes UPDATE and DELETE only over replica-identity
columns. A row-filtered table therefore needs a non-partial unique index over its key columns plus
its filter columns, every column ``NOT NULL``, set as ``REPLICA IDENTITY USING INDEX``. Because the
filter columns are then in the replica identity, an update that moves a row out of the filter
reaches the subscriber as a DELETE, which is how a later restriction removes a published row.
``REPLICA IDENTITY FULL`` would defeat the column list and is never used.

The owning chain's migration calls these helpers. A shared ``public`` table's migration runs once
per schema, so both are idempotent; hold that migration's advisory lock around them.
"""

from __future__ import annotations

import re

from sqlalchemy import text

from alembic import op

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
_MAX_IDENTIFIER_BYTES = 63


def replica_identity_index_name(table: str) -> str:
    """Return the deterministic index name for *table* (optionally schema-qualified)."""
    bare = table.rpartition(".")[2]
    name = f"uq_{bare}_replica_identity"
    if len(name) > _MAX_IDENTIFIER_BYTES:
        raise ValueError(f"replica identity index name is too long: {name}")
    return name


def _validate(table: str, columns: tuple[str, ...]) -> None:
    for part in (*table.split("."), *columns):
        if not _IDENTIFIER.fullmatch(part):
            raise ValueError(f"unsafe SQL identifier: {part!r}")
    if len(set(columns)) != len(columns):
        raise ValueError(f"replica identity columns repeat on {table}: {columns}")


def _nullable_or_missing(table: str, columns: tuple[str, ...]) -> list[str]:
    rows = op.get_bind().execute(
        text(
            "SELECT attname FROM pg_catalog.pg_attribute "
            "WHERE attrelid = to_regclass(:table) AND attnum > 0 "
            "AND NOT attisdropped AND attnotnull"
        ),
        {"table": table},
    )
    not_null = {row[0] for row in rows}
    return [column for column in columns if column not in not_null]


def _index_columns(table: str, index_name: str) -> tuple[list[str], bool] | None:
    """Return an index's key columns and whether it qualifies as a replica identity."""
    row = (
        op.get_bind()
        .execute(
            text(
                "SELECT ARRAY(SELECT a.attname::text "
                "FROM unnest(i.indkey::int2[]) WITH ORDINALITY AS k(attnum, position) "
                "JOIN pg_catalog.pg_attribute a "
                "ON a.attrelid = i.indrelid AND a.attnum = k.attnum "
                "ORDER BY k.position) AS columns, "
                "i.indisunique AND i.indimmediate AND i.indpred IS NULL "
                "AND i.indexprs IS NULL AND i.indisvalid AS qualifies "
                "FROM pg_catalog.pg_index i "
                "JOIN pg_catalog.pg_class c ON c.oid = i.indexrelid "
                "WHERE i.indrelid = to_regclass(:table) AND c.relname = :index_name"
            ),
            {"table": table, "index_name": index_name},
        )
        .first()
    )
    return None if row is None else (list(row[0]), bool(row[1]))


def ensure_replica_identity(
    table: str,
    key_columns: tuple[str, ...],
    filter_columns: tuple[str, ...],
) -> str:
    """Make ``(key_columns, filter_columns)`` the replica identity of *table*; return the index.

    *table* resolves through the session ``search_path``, so a per-schema chain passes the bare
    name. A missing table, a nullable column or an existing index of another shape raises rather
    than leaving a row filter that fails every UPDATE on the primary, or one that cannot see a
    reclassification.
    """
    columns = (*key_columns, *filter_columns)
    _validate(table, columns)
    if not key_columns or not filter_columns:
        raise ValueError(f"{table} needs key and filter columns for a replica identity")

    invalid = _nullable_or_missing(table, columns)
    if invalid:
        raise ValueError(
            f"{table} cannot use {', '.join(invalid)} in a replica identity: "
            "the column is missing or allows NULL"
        )

    index_name = replica_identity_index_name(table)
    op.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table} ({', '.join(columns)})")
    found = _index_columns(table, index_name)
    if found is None or found[0] != list(columns) or not found[1]:
        raise ValueError(
            f"{index_name} on {table} does not match the replica identity {columns}: {found}"
        )
    op.execute(f"ALTER TABLE {table} REPLICA IDENTITY USING INDEX {index_name}")
    return index_name


def drop_replica_identity(table: str) -> None:
    """Restore ``REPLICA IDENTITY DEFAULT`` on *table* and drop the index the helper made."""
    _validate(table, ())
    schema, _, _bare = table.rpartition(".")
    index_name = replica_identity_index_name(table)
    qualified = f"{schema}.{index_name}" if schema else index_name
    op.execute(f"ALTER TABLE IF EXISTS {table} REPLICA IDENTITY DEFAULT")
    op.execute(f"DROP INDEX IF EXISTS {qualified}")
