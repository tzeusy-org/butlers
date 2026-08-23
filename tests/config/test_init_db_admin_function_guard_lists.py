"""Static source coverage for the admin function-ownership guard lists.

``scripts/init-db.sql`` protects each admin schema with a block shaped like::

    IF EXISTS (
        SELECT 1
        FROM pg_proc AS admin_function
        JOIN pg_namespace AS admin_schema ON admin_schema.oid = admin_function.pronamespace
        WHERE admin_schema.nspname = '<schema>'
          AND admin_function.proname IN (...)
          AND admin_function.pronargs = 0
          AND admin_function.proowner <> v_bootstrap_owner
    ) THEN
        RAISE EXCEPTION ...

The ``nspname`` filter and the ``proname IN`` list must agree. A name listed
under the wrong schema's block can never match the enclosing ``EXISTS``, so the
guard reads as protective while protecting nothing -- no error, no warning. That
defect shipped once (``upgrade_producers_v2`` and ``deactivate_producers_v2``
listed under ``dnd_generation_admin`` while defined in ``runtime_attention_admin``)
and was fixed without regression coverage.

These checks parse the SQL source only. They never execute ``init-db.sql``, start
PostgreSQL, or open a connection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INIT_DB = _REPO_ROOT / "scripts" / "init-db.sql"

# Any schema whose name is an ``*_admin`` identifier anywhere in the file. Kept
# as a whole-file token sweep rather than a targeted pattern so a newly added
# admin schema is discovered without editing this test. Known blind spot: an
# admin schema not named ``*_admin`` would be invisible here (the per-block
# checks below still cover it, only the "every admin schema is guarded" check
# would miss it).
_ADMIN_SCHEMA_TOKEN = re.compile(r"\b([a-z][a-z0-9_]*_admin)\b")

_CREATE_FUNCTION = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\s*\(",
    re.IGNORECASE,
)
_PRONAME_IN = re.compile(r"proname\s+IN\s*\(", re.IGNORECASE)
_NSPNAME_FILTER = re.compile(r"nspname\s*=\s*'([a-z_][a-z0-9_]*)'", re.IGNORECASE)
_PRONARGS_ZERO = re.compile(r"pronargs\s*=\s*0", re.IGNORECASE)
_QUOTED_NAME = re.compile(r"'([a-z_][a-z0-9_]*)'")
_FROM_PG_PROC = re.compile(r"FROM\s+pg_proc\b", re.IGNORECASE)

# How far past the ``proname IN (...)`` list the companion ``pronargs = 0``
# predicate may sit before we treat the guard as malformed.
_PRONARGS_LOOKAHEAD = 400


@dataclass(frozen=True)
class _Definition:
    """A ``CREATE FUNCTION`` site in the SQL source."""

    schema: str
    name: str
    line: int
    zero_arg: bool


@dataclass(frozen=True)
class _GuardBlock:
    """One ``nspname = '<schema>' AND proname IN (...)`` ownership assertion."""

    schema: str
    names: tuple[str, ...]
    line: int


def _read_sql() -> str:
    return _INIT_DB.read_text(encoding="utf-8")


def _line_of(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def _close_paren(source: str, open_index: int) -> int:
    """Index of the ``)`` matching the ``(`` at ``open_index``."""
    depth = 0
    for index in range(open_index, len(source)):
        char = source[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    raise AssertionError(f"unbalanced parenthesis opened at {_line_of(source, open_index)}")


def _function_definitions(source: str) -> list[_Definition]:
    definitions: list[_Definition] = []
    for match in _CREATE_FUNCTION.finditer(source):
        open_index = match.end() - 1
        params = source[open_index + 1 : _close_paren(source, open_index)]
        definitions.append(
            _Definition(
                schema=match.group(1).lower(),
                name=match.group(2).lower(),
                line=_line_of(source, match.start()),
                zero_arg=not params.strip(),
            )
        )
    return definitions


def _guard_blocks(source: str) -> list[_GuardBlock]:
    blocks: list[_GuardBlock] = []
    for match in _PRONAME_IN.finditer(source):
        line = _line_of(source, match.start())

        # Associate the list with the ``nspname`` filter of the *same* subquery
        # by bounding the backwards search at the enclosing ``FROM pg_proc``.
        preceding = [m.end() for m in _FROM_PG_PROC.finditer(source, 0, match.start())]
        assert preceding, (
            f"init-db.sql:{line}: `proname IN (...)` guard list has no enclosing "
            "`FROM pg_proc`; this test cannot tell which schema it constrains."
        )
        scope = source[preceding[-1] : match.start()]
        schema_filters = _NSPNAME_FILTER.findall(scope)
        assert schema_filters, (
            f"init-db.sql:{line}: `proname IN (...)` guard list has no `nspname = '...'` "
            "filter in the same subquery; the guard constrains an unknown schema."
        )

        open_index = match.end() - 1
        close_index = _close_paren(source, open_index)
        names = tuple(_QUOTED_NAME.findall(source[open_index + 1 : close_index]))
        assert names, f"init-db.sql:{line}: `proname IN ()` guard list is empty."

        tail = source[close_index : close_index + _PRONARGS_LOOKAHEAD]
        assert _PRONARGS_ZERO.search(tail), (
            f"init-db.sql:{line}: guard list for schema "
            f"'{schema_filters[-1].lower()}' is not paired with `pronargs = 0`, so it does "
            "not constrain the zero-argument interface functions this test compares against."
        )

        blocks.append(_GuardBlock(schema=schema_filters[-1].lower(), names=names, line=line))
    return blocks


def _admin_schemas(source: str) -> set[str]:
    return {name.lower() for name in _ADMIN_SCHEMA_TOKEN.findall(source)}


def _describe_unlisted(
    definition: _Definition, blocks: list[_GuardBlock]
) -> str:  # pragma: no cover - exercised only on failure
    elsewhere = sorted(
        {block.schema for block in blocks if definition.name in block.names} - {definition.schema}
    )
    if elsewhere:
        return (
            f"{definition.name!r} is defined in schema '{definition.schema}' "
            f"(init-db.sql:{definition.line}) but is listed under the guard for "
            f"{', '.join(repr(schema) for schema in elsewhere)} instead. A name listed "
            "under the wrong schema can never match the enclosing EXISTS, so both guards "
            "are silently inert for it."
        )
    return (
        f"{definition.name!r} is defined in schema '{definition.schema}' "
        f"(init-db.sql:{definition.line}) but is listed in no guard block at all, so its "
        "ownership is never asserted."
    )


def _describe_unmatched(
    schema: str, name: str, definitions: list[_Definition]
) -> str:  # pragma: no cover - exercised only on failure
    same_name = [item for item in definitions if item.name == name]
    if not same_name:
        return (
            f"{name!r} is listed in the guard for '{schema}' but no function of that name is "
            "created anywhere in init-db.sql, so that entry asserts nothing."
        )
    elsewhere = sorted({item.schema for item in same_name if item.zero_arg} - {schema})
    if elsewhere:
        return (
            f"{name!r} is listed in the guard for '{schema}' but is defined in "
            f"{', '.join(repr(other) for other in elsewhere)} instead "
            f"(init-db.sql:{same_name[0].line}). The nspname filter and the proname list "
            "disagree, so this entry can never match."
        )
    return (
        f"{name!r} is listed in the guard for '{schema}' but every definition of it takes "
        f"arguments (init-db.sql:{same_name[0].line}); the guard's `pronargs = 0` predicate "
        "excludes it."
    )


def test_every_admin_schema_has_a_function_ownership_guard() -> None:
    source = _read_sql()
    blocks = _guard_blocks(source)
    definitions = _function_definitions(source)

    guarded = {block.schema for block in blocks}
    owning_admin_schemas = {
        definition.schema
        for definition in definitions
        if definition.zero_arg and definition.schema in _admin_schemas(source)
    }

    assert owning_admin_schemas, (
        "no admin schema with zero-argument functions was found in init-db.sql; the parser "
        "matched nothing and every comparison below would be vacuously true."
    )
    unguarded = sorted(owning_admin_schemas - guarded)
    assert not unguarded, (
        "admin schemas define zero-argument functions but have no `proname IN (...)` "
        f"ownership guard: {', '.join(unguarded)}."
    )


def test_guard_lists_match_the_functions_defined_in_their_own_schema() -> None:
    source = _read_sql()
    blocks = _guard_blocks(source)
    definitions = _function_definitions(source)

    assert blocks, "no `proname IN (...)` ownership guard was found in init-db.sql."

    failures: list[str] = []
    for block in blocks:
        defined = [
            definition
            for definition in definitions
            if definition.schema == block.schema and definition.zero_arg
        ]
        assert defined, (
            f"init-db.sql:{block.line}: guard for '{block.schema}' compares against an empty "
            "set of zero-argument functions; the parser found no definitions for that schema "
            "and this assertion would be vacuous."
        )

        listed = set(block.names)
        missing = [item for item in defined if item.name not in listed]
        extra = sorted(listed - {item.name for item in defined})

        for definition in missing:
            failures.append(f"init-db.sql:{block.line}: {_describe_unlisted(definition, blocks)}")
        for name in extra:
            failures.append(
                f"init-db.sql:{block.line}: {_describe_unmatched(block.schema, name, definitions)}"
            )

    assert not failures, "admin function-ownership guard lists are out of sync:\n" + "\n".join(
        failures
    )
