"""The closed v1 projection manifest (RFC 0039 D2/D4).

Exposure is explicit data in projection.toml. This module enforces the contract;
loading a registry or observing a new catalog column never expands that exposure.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass, replace
from importlib.resources import files

_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]*\Z")
_TYPE = re.compile(
    r"(?:text|uuid|boolean|integer|bigint|smallint|real|double precision|date|tsvector|bytea|"
    r"timestamp (?:with|without) time zone|time (?:with|without) time zone|numeric(?:\(\d+,\d+\))?|"
    r"character(?: varying)?(?:\(\d+\))?)(?:\[\])?\Z"
)
_CREDENTIALS = tuple(
    tuple(x.split("_"))
    for x in (
        "secret",
        "token",
        "password",
        "credential",
        "otp",
        "cvv",
        "card",
        "api_key",
        "private_key",
        "access_key",
        "client_key",
        "signing_key",
        "access_url",
        "passcode",
        "passphrase",
        "pin",
        "bearer",
        "cookie",
        "auth",
    )
)
_BREADTH = tuple(
    tuple(x.split("_"))
    for x in (
        "iban",
        "account_number",
        "cancellation_url",
        "raw",
        "body",
        "metadata",
        "tags",
        "confirmation",
        "pnr",
        "seat",
        "address",
        "passport",
        "document_number",
        "last_four",
        "phone",
        "email",
        "description",
        "memo",
        "note",
        "notes",
        "receipt_url",
        "external_ref",
        "source_message",
        "source_value",
        "source_schema",
        "source_table",
        "source_id",
        "blob_ref",
        "payment_method",
    )
)
_DOMAINS = {"knowledge", "travel", "relationship", "time", "finance", "health", "proposals", ""}
_PRIVATE_TABLES = {
    "butler_secrets",
    "entity_info",
    "credentials",
    "pending_actions",
    "approval_events",
    "sessions",
    "session_process_logs",
    "state",
    "scheduled_tasks",
    "route_inbox",
    "corrections",
    "overrides",
    "episodes",
    "point_events",
    "location_fixes",
    "memory_catalog",
    "rules",
    "predicate_registry",
    "entity_predicate_registry",
}


class ManifestError(ValueError):
    """A manifest or catalog violates the declared public boundary."""


def identifier(value: object) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ManifestError(f"unsafe identifier: {value!r}")
    return value


def quote(value: str) -> str:
    return '"' + identifier(value) + '"'


def qualified(source: str) -> str:
    return ".".join(quote(part) for part in source.split("."))


def _segments(value: str) -> tuple[str, ...]:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return tuple(x.lower() for x in re.split(r"[.\s_-]+", value) if x)


def _matches(parts: tuple[str, ...], rules: tuple[tuple[str, ...], ...]) -> bool:
    return any(parts[i : i + len(rule)] == rule for rule in rules for i in range(len(parts)))


def credential(value: str) -> bool:
    """Match the shared credential tier on column or dotted JSON path segments."""
    return _matches(_segments(value), _CREDENTIALS)


def private_predicate(value: str) -> bool:
    parts = _segments(value)
    return value in {"contact_phone", "contact_email", "birthday", "lives_in", "nickname"} or (
        bool(
            set(parts) & {"phone", "email", "address", "handle", "website", "birthday", "passport"}
        )
        or _matches(parts, (("identity", "document"),))
        or credential(value)
    )


def _strings(value: object, label: str, *, nonempty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        raise ManifestError(f"{label}: expected string array")
    if len(value) != len(set(value)) or (nonempty and not value):
        raise ManifestError(f"{label}: empty or duplicate values")
    return tuple(value)


def literal(value: str | bool) -> str:
    return (
        ("true" if value else "false")
        if type(value) is bool
        else "'" + value.replace("'", "''") + "'"
    )


@dataclass(frozen=True)
class Table:
    source: str
    domain: str
    columns: tuple[str, ...]
    types: tuple[tuple[str, str], ...]
    key: tuple[str, ...]
    identity: tuple[str, ...]
    time_column: str | None
    filters: tuple[tuple[str, tuple[str | bool, ...]], ...]
    identifiers: tuple[str, ...] = ()
    json_identifiers: tuple[str, ...] = ()
    derived: str | None = None
    uri: str = ""
    tools: tuple[str, ...] = ()

    def filter_sql(self) -> str:
        return " AND ".join(
            f"{quote(column)} IN ({', '.join(literal(v) for v in values)})"
            for column, values in self.filters
        )

    def publication_sql(self) -> str:
        sql = f"TABLE {qualified(self.source)} ({', '.join(quote(c) for c in self.columns)})"
        return sql + (f" WHERE ({self.filter_sql()})" if self.filters else "")


@dataclass(frozen=True)
class Manifest:
    tables: tuple[Table, ...]

    @property
    def canonical(self) -> str:
        # Retain column/key order: those declarations also determine subscriber shapes.
        return json.dumps(
            {
                "version": 1,
                "tables": [
                    {
                        "source": t.source,
                        "domain": t.domain,
                        "columns": t.columns,
                        "types": t.types,
                        "key": t.key,
                        "identity": t.identity,
                        "time_column": t.time_column,
                        "uri": t.uri,
                        "tools": t.tools,
                        "filters": t.filters,
                        "identifiers": t.identifiers,
                        "json_identifiers": t.json_identifiers,
                    }
                    for t in self.tables
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.canonical.encode()).hexdigest()


def load_manifest(data: bytes | None = None) -> Manifest:
    """Load the packaged allowlist, or explicit bytes for validation/testing."""
    try:
        raw = tomllib.loads(
            (
                data
                if data is not None
                else files(__package__).joinpath("projection.toml").read_bytes()
            ).decode()
        )
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ManifestError("manifest: invalid TOML/encoding") from exc
    if set(raw) != {"version", "table"} or type(raw["version"]) is not int or raw["version"] != 1:
        raise ManifestError("manifest: expected version1 and table array")
    if not isinstance(raw["table"], list) or not raw["table"]:
        raise ManifestError("manifest: empty table array")
    tables = []
    fields = {
        "source",
        "domain",
        "columns",
        "types",
        "key",
        "time_column",
        "row_filter",
        "identifiers",
        "json_identifiers",
        "derived",
        "replica_identity",
        "uri",
        "tools",
    }
    for item in raw["table"]:
        if (
            not isinstance(item, dict)
            or set(item) - fields
            or not {"source", "domain", "columns", "types", "key"} <= set(item)
        ):
            raise ManifestError("table: invalid fields")
        source = item["source"]
        if not isinstance(source, str) or len(source.split(".")) != 2:
            raise ManifestError("table: expected qualified source")
        schema, name = (identifier(x) for x in source.split("."))
        domain = item["domain"]
        if (
            not isinstance(domain, str)
            or domain not in _DOMAINS
            or (domain == "") != (schema == "mcp_surface")
        ):
            raise ManifestError(f"{source}: invalid domain")
        schema_domains = {
            "general": "knowledge",
            "lifestyle": "knowledge",
            "education": "knowledge",
            "travel": "travel",
            "relationship": "relationship",
            "public": "relationship",
            "chronicler": "time",
            "finance": "finance",
            "health": "health",
            "mcp_surface": "",
        }
        expected_domain = (
            "proposals"
            if source == "relationship.external_fact_proposals"
            else schema_domains.get(schema)
        )
        if expected_domain != domain:
            raise ManifestError(f"{source}: excluded schema/domain")
        expected_derived = {
            "public.entity_graph_edges": "edges",
            "public.entity_graph_withheld_counts": "counts",
        }
        if source in expected_derived and item.get("derived") != expected_derived[source]:
            raise ManifestError(f"{source}: derived graph filter required")
        if name in _PRIVATE_TABLES or name.startswith("oauth_") or name.endswith("_tokens"):
            raise ManifestError(f"{source}: private source")
        cols = _strings(item["columns"], source)
        for column in cols:
            identifier(column)
            if (
                credential(column)
                or "embedding" in _segments(column)
                or (domain not in {"finance", "health"} and _matches(_segments(column), _BREADTH))
            ):
                raise ManifestError(f"{source}.{column}: forbidden column")
        types = item["types"]
        if (
            not isinstance(types, dict)
            or set(types) != set(cols)
            or any(not isinstance(t, str) or not _TYPE.fullmatch(t) for t in types.values())
        ):
            raise ManifestError(f"{source}: unsafe/missing column types (JSON/vector forbidden)")
        key = _strings(item["key"], source + " key")
        if not set(key) <= set(cols):
            raise ManifestError(f"{source}: key omitted from columns")
        if domain == "proposals" and (
            cols != ("proposal_id", "state", "decided_at")
            or types
            != {"proposal_id": "uuid", "state": "text", "decided_at": "timestamp with time zone"}
            or key != ("proposal_id",)
            or item.get("replica_identity") != ["proposal_id"]
            or item.get("row_filter")
            or item.get("identifiers")
            or item.get("json_identifiers")
        ):
            raise ManifestError(f"{source}: proposals expose only canonical status columns")
        if domain in {"finance", "health"} and len(key) != 1:
            raise ManifestError(f"{source}: finance/health key must be single column")
        identity = _strings(item.get("replica_identity"), source + " identity")
        if not set(key) <= set(identity) or not set(identity) <= set(cols):
            raise ManifestError(f"{source}: incomplete identity columns")
        uri = item.get("uri", "")
        tools = _strings(item.get("tools", []), source + " tools", nonempty=False)
        if not isinstance(uri, str) or (uri and not uri.startswith("butlers://")):
            raise ManifestError(f"{source}: invalid URI")
        if any(not _IDENTIFIER.fullmatch(tool) for tool in tools):
            raise ManifestError(f"{source}: invalid tool source")
        time_column = item.get("time_column")
        if time_column is not None and time_column not in cols:
            raise ManifestError(f"{source}: time column omitted")
        filters = item.get("row_filter", {})
        if not isinstance(filters, dict) or not set(filters) <= set(cols):
            raise ManifestError(f"{source}: filter column omitted")
        if not set(filters) <= set(identity):
            raise ManifestError(f"{source}: filter omitted from identity")
        normalized = []
        for column, values in sorted(filters.items()):
            if (
                not isinstance(values, list)
                or not values
                or any(type(v) not in (str, bool) for v in values)
            ):
                raise ManifestError(f"{source}.{column}: expected nonempty literal filter")
            if (
                len({(type(v), v) for v in values}) != len(values)
                or len({type(v) for v in values}) != 1
            ):
                raise ManifestError(f"{source}.{column}: duplicate/mixed filter literals")
            if types[column] not in {"text", "boolean"} or any(
                type(v) is not (bool if types[column] == "boolean" else str) for v in values
            ):
                raise ManifestError(f"{source}.{column}: filter literal type differs from column")
            normalized.append((column, tuple(sorted(values))))
        if name == "facts":
            if domain in {"finance", "health"}:
                if filters != {"validity": ["active"]}:
                    raise ManifestError(f"{source}: finance/health facts require active only")
            elif (
                set(filters) != {"sensitivity", "validity", "predicate"}
                or filters["sensitivity"] != ["normal"]
                or set(filters["validity"]) != {"active", "fading"}
                or any(private_predicate(x) for x in filters["predicate"])
            ):
                raise ManifestError(f"{source}: invalid memory fact filters")
        if name == "entity_facts" and (
            filters.get("validity") != ["active"] or not filters.get("predicate")
        ):
            raise ManifestError(f"{source}: relationship predicate allowlist required")
        if source == "public.entities" and filters != {
            "deleted": [False],
            "unidentified": [False],
            "placeholder_name": [False],
        }:
            raise ManifestError(f"{source}: entity privacy flags required")
        if source == "chronicler.episodes_public" and filters != {
            "effective_privacy": ["normal", "sensitive"],
            "tombstoned": [False],
        }:
            raise ManifestError(f"{source}: corrected episode privacy filters required")
        identifiers = _strings(item.get("identifiers", []), source, nonempty=False)
        paths = _strings(item.get("json_identifiers", []), source, nonempty=False)
        if not set(identifiers) <= set(cols) or any(credential(x) for x in (*identifiers, *paths)):
            raise ManifestError(f"{source}: unsafe identifier mark")
        if (identifiers or paths) and (
            domain not in {"finance", "health"} or name == "json_fields_public"
        ):
            raise ManifestError(f"{source}: identifier marks not permitted")
        if any(not re.fullmatch(r"[a-zA-Z_][a-zA-Z_0-9]*(?:\.[a-zA-Z_0-9-]+)+", x) for x in paths):
            raise ManifestError(f"{source}: malformed JSON identifier path")
        derived = item.get("derived")
        if derived is not None and (
            not isinstance(derived, str) or derived not in {"edges", "counts"}
        ):
            raise ManifestError(f"{source}: invalid derived filter")
        tables.append(
            Table(
                source,
                domain,
                cols,
                tuple((c, types[c]) for c in cols),
                key,
                identity,
                time_column,
                tuple(normalized),
                identifiers,
                paths,
                derived,
                uri,
                tools,
            )
        )
    if len({t.source for t in tables}) != len(tables):
        raise ManifestError("duplicate sources")
    facts = [
        t
        for t in tables
        if t.source.endswith((".facts", ".entity_facts")) and t.domain not in {"finance", "health"}
    ]
    keys = tuple(
        sorted(
            f"{t.source}:{predicate}"
            for t in facts
            for col, values in t.filters
            if col == "predicate"
            for predicate in values
        )
    )
    sources = tuple(sorted(t.source for t in facts))
    for i, table in enumerate(tables):
        if table.derived:
            expected_source = (
                "public.entity_graph_edges"
                if table.derived == "edges"
                else "public.entity_graph_withheld_counts"
            )
            if table.source != expected_source or table.filters:
                raise ManifestError(f"{table.source}: derived graph filters cannot be hand edited")
            filters = (
                (("publish_key", keys), ("sensitivity", ("normal",)))
                if table.derived == "edges"
                else (("source_key", sources),)
            )
            if not keys or not all(column in table.identity for column, _ in filters):
                raise ManifestError(f"{table.source}: incomplete graph identity/filter columns")
            tables[i] = replace(table, filters=filters)
    return Manifest(tuple(sorted(tables, key=lambda t: t.source)))
