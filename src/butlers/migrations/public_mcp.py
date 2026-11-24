"""Primary-only catalog validation and transactional publication convergence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from butlers.knowledge_read.manifest import Manifest, ManifestError, Table, load_manifest

_PUBLICATION = '"butlers_public_v1"'
_ACTIONS = {"pubinsert": True, "pubupdate": True, "pubdelete": True, "pubtruncate": True}
# Preserve PostgreSQL's ordinary publication defaults, including TRUNCATE.
_PUBLISH = "insert, update, delete, truncate"
_LOCK = 716793632804263789


def _filter_values(expression: str | None) -> tuple[tuple[str, tuple[str | bool, ...]], ...]:
    """Parse only the fixed equality/IN deparser grammar emitted for this manifest.

    PostgreSQL rewrites IN to '= ANY (ARRAY[...])'. Parsing the closed form avoids
    treating whitespace/casts as policy drift, without accepting arbitrary SQL.
    """
    if expression is None:
        return ()
    pattern = re.compile(
        r"""\s*(?:("[a-z_][a-z0-9_]*")|('(?:''|[^'])*')|([a-zA-Z_][a-zA-Z_0-9]*)|(::|[=(),\[\]]))"""
    )
    tokens = []
    position = 0
    while position < len(expression):
        match = pattern.match(expression, position)
        if not match:
            raise ManifestError("publication: unsupported filter syntax")
        tokens.append(next(x for x in match.groups() if x is not None))
        position = match.end()
    cursor = 0

    def take(expected: str | None = None) -> str:
        nonlocal cursor
        if cursor >= len(tokens) or (expected is not None and tokens[cursor].upper() != expected):
            raise ManifestError("publication: unsupported filter shape")
        value = tokens[cursor]
        cursor += 1
        return value

    def scalar() -> str | bool:
        value = take()
        if value.startswith("'"):
            result: str | bool = value[1:-1].replace("''", "'")
        elif value.lower() in {"true", "false"}:
            result = value.lower() == "true"
        else:
            raise ManifestError("publication: nonliteral filter")
        if cursor < len(tokens) and tokens[cursor] == "::":
            take("::")
            take("TEXT")
        return result

    def group() -> list[tuple[str, tuple[str | bool, ...]]]:
        if cursor < len(tokens) and tokens[cursor] == "(":
            take("(")
            pairs = conjunction()
            take(")")
            return pairs
        column = take().strip('"')
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", column):
            raise ManifestError("publication: noncolumn filter")
        operator = take().upper()
        if operator == "IN":
            take("(")
            close = ")"
        elif operator == "=" and cursor < len(tokens) and tokens[cursor].upper() == "ANY":
            take("ANY")
            take("(")
            take("ARRAY")
            take("[")
            close = "]"
        elif operator == "=":
            return [(column, (scalar(),))]
        else:
            raise ManifestError("publication: unsupported filter operator")
        values = [scalar()]
        while cursor < len(tokens) and tokens[cursor] == ",":
            take(",")
            values.append(scalar())
        take(close)
        if close == "]":
            take(")")
        if len({type(v) for v in values}) != 1 or len(set(values)) != len(values):
            raise ManifestError("publication: invalid filter literals")
        return [(column, tuple(sorted(values)))]

    def conjunction() -> list[tuple[str, tuple[str | bool, ...]]]:
        result = group()
        while cursor < len(tokens) and tokens[cursor].upper() == "AND":
            take("AND")
            result.extend(group())
        return result

    pairs = conjunction()
    if cursor != len(tokens) or len({c for c, _ in pairs}) != len(pairs):
        raise ManifestError("publication: unsupported/duplicate filter")
    return tuple(sorted(pairs))


def _columns(conn: Connection, table: Table) -> dict[str, Any]:
    rows = conn.execute(
        text("""
        SELECT a.attname, format_type(a.atttypid,a.atttypmod) AS type,
               a.attnotnull, a.attnum, a.attgenerated, c.relkind,
               pg_get_userbyid(c.relowner)=current_user AS owned
        FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
        WHERE c.oid=to_regclass(:source) AND a.attnum>0 AND NOT a.attisdropped
    """),
        {"source": table.source},
    ).mappings()
    return {row["attname"]: row for row in rows}


def validate_catalog(conn: Connection, manifest: Manifest) -> None:
    """Validate source facts without modifying schema, identity or grants."""
    for table in manifest.tables:
        cols = _columns(conn, table)
        for column, expected in table.types:
            row = cols.get(column)
            if row is None:
                raise ManifestError(f"{table.source}.{column}: missing column")
            if row["type"] != expected or row["attgenerated"]:
                raise ManifestError(f"{table.source}.{column}: type/generated drift")
            if row["relkind"] != "r" or not row["owned"]:
                raise ManifestError(f"{table.source}: unsupported relation/owner")
        for column in table.identity:
            if not cols[column]["attnotnull"]:
                raise ManifestError(f"{table.source}.{column}: nullable identity/filter")
        for column in {"sensitivity", "privacy", "effective_privacy"} & cols.keys():
            if table.domain not in {"finance", "health"}:
                values = dict(table.filters).get(column, ())
                safe = {"normal", "sensitive"} if column == "effective_privacy" else {"normal"}
                if not values or not set(values) <= safe:
                    raise ManifestError(f"{table.source}.{column}: unsafe privacy filter")
        primary = conn.execute(
            text("""
            SELECT ARRAY(SELECT a.attname::text FROM unnest(i.indkey) WITH ORDINALITY k(n,p)
                JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.n ORDER BY k.p)
            FROM pg_index i WHERE i.indrelid=to_regclass(:source) AND i.indisprimary
        """),
            {"source": table.source},
        ).scalar_one_or_none()
        if tuple(primary or ()) != table.key:
            raise ManifestError(f"{table.source}: primary key differs from manifest")
        identity = (
            conn.execute(
                text("""
            SELECT c.relreplident, i.indisunique, i.indimmediate, i.indisvalid,
                i.indpred IS NULL AND i.indexprs IS NULL AS plain,
                ARRAY(SELECT a.attname::text FROM unnest(i.indkey) WITH ORDINALITY k(n,p)
                    JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.n
                    ORDER BY k.p) AS columns
            FROM pg_class c LEFT JOIN pg_index i ON i.indrelid=c.oid
                AND ((c.relreplident='i' AND i.indisreplident)
                     OR (c.relreplident='d' AND i.indisprimary))
            WHERE c.oid=to_regclass(:source)
        """),
                {"source": table.source},
            )
            .mappings()
            .one()
        )
        if (
            identity["relreplident"] not in {"i", "d"}
            or not all(identity[k] for k in ("indisunique", "indimmediate", "indisvalid", "plain"))
            or tuple(identity["columns"] or ()) != table.identity
        ):
            raise ManifestError(f"{table.source}: invalid/mismatched replica identity")
        for path in table.json_identifiers:
            root = path.split(".")[0]
            if root not in cols or cols[root]["type"] != "jsonb":
                raise ManifestError(f"{table.source}.{path}: identifier path has no JSON source")
        if table.source == "relationship.entity_facts":
            allowed = set(
                conn.execute(
                    text(
                        "SELECT predicate FROM relationship.entity_predicate_registry "
                        "WHERE kind='relational'"
                    )
                ).scalars()
            )
            if not set(dict(table.filters)["predicate"]) <= allowed:
                raise ManifestError(f"{table.source}: contact/override/unknown predicate")
        elif table.source.endswith(".facts") and table.domain not in {"finance", "health"}:
            schema = table.source.split(".")[0]
            # A registry can validate an explicit decision, never expand it.
            allowed = set(
                conn.exec_driver_sql(f'SELECT name FROM "{schema}".predicate_registry').scalars()
            )
            if not set(dict(table.filters)["predicate"]) <= allowed:
                raise ManifestError(f"{table.source}: unknown predicate")


@dataclass(frozen=True)
class PublicationState:
    exists: bool
    exact: bool
    reason: str


def publication_state(conn: Connection, manifest: Manifest) -> PublicationState:
    """Expanded table rows alone do not prove action flags or finite column lists."""
    pub = (
        conn.execute(
            text("""
        SELECT *, pg_get_userbyid(pubowner)=current_user AS owned
        FROM pg_publication WHERE pubname='butlers_public_v1'
    """)
        )
        .mappings()
        .one_or_none()
    )
    if pub is None:
        return PublicationState(False, False, "missing publication")
    if not pub["owned"]:
        raise ManifestError("publication: unsupported owner")
    if (
        pub["puballtables"]
        or conn.execute(
            text("SELECT EXISTS(SELECT 1 FROM pg_publication_namespace WHERE pnpubid=:oid)"),
            {"oid": pub["oid"]},
        ).scalar_one()
    ):
        raise ManifestError("publication: wildcard membership refused")
    if pub["pubviaroot"] or any(pub[k] != value for k, value in _ACTIONS.items()):
        return PublicationState(True, False, "publication action flags")
    rows = (
        conn.execute(
            text("""
        SELECT n.nspname||'.'||c.relname AS source, pr.prattrs::int2[] AS attrs,
               pg_get_expr(pr.prqual,pr.prrelid) AS filter
        FROM pg_publication_rel pr JOIN pg_class c ON c.oid=pr.prrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE pr.prpubid=:oid
    """),
            {"oid": pub["oid"]},
        )
        .mappings()
        .all()
    )
    actual = {row["source"]: row for row in rows}
    if set(actual) != {table.source for table in manifest.tables}:
        return PublicationState(True, False, "publication table membership")
    for table in manifest.tables:
        row = actual[table.source]
        columns = _columns(conn, table)
        expected = {columns[c]["attnum"] for c in table.columns}
        if row["attrs"] is None or set(row["attrs"]) != expected:
            return PublicationState(True, False, f"{table.source}: explicit column membership")
        try:
            filters = _filter_values(row["filter"])
        except ManifestError:
            return PublicationState(True, False, f"{table.source}: unsupported filter")
        if filters != table.filters:
            return PublicationState(True, False, f"{table.source}: row filter")
    return PublicationState(True, True, "exact")


def reconcile_connection(conn: Connection, manifest: Manifest) -> bool:
    """Converge within the caller's transaction; True means persistent state changed."""
    if not conn.in_transaction():
        raise ManifestError("reconcile requires a transaction")
    conn.execute(text("SET LOCAL lock_timeout='5s'"))
    conn.execute(text("SET LOCAL statement_timeout='60s'"))
    conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK})
    if (
        conn.execute(text("SELECT to_regclass('mcp_surface.manifest_state')")).scalar_one_or_none()
        is None
    ):
        raise ManifestError("projection primary metadata not bootstrapped")
    validate_catalog(conn, manifest)
    state = publication_state(conn, manifest)
    digests = list(conn.execute(text("SELECT digest FROM mcp_surface.manifest_state")).scalars())
    if len(digests) > 1:
        raise ManifestError("manifest state: multiple rows")
    if state.exact and digests == [manifest.digest]:
        return False
    if not state.exact:
        tables = ", ".join(t.publication_sql() for t in manifest.tables)
        if state.exists:
            conn.exec_driver_sql(f"ALTER PUBLICATION {_PUBLICATION} SET {tables}")
            conn.exec_driver_sql(
                f"ALTER PUBLICATION {_PUBLICATION} SET "
                f"(publish='{_PUBLISH}', publish_via_partition_root=false)"
            )
        else:
            conn.exec_driver_sql(
                f"CREATE PUBLICATION {_PUBLICATION} FOR {tables} WITH (publish='{_PUBLISH}')"
            )
    if not publication_state(conn, manifest).exact:
        raise ManifestError("publication: readback differs from manifest")
    conn.execute(text("DELETE FROM mcp_surface.manifest_state"))
    conn.execute(
        text("INSERT INTO mcp_surface.manifest_state(digest) VALUES(:digest)"),
        {"digest": manifest.digest},
    )
    return True


def reconcile(db_url: str) -> bool:
    """Explicit migration-principal entrypoint. Never connects to a subscriber."""
    manifest = load_manifest()
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            return reconcile_connection(conn, manifest)
    finally:
        engine.dispose()
