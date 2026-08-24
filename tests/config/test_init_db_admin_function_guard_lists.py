"""Static source coverage for the admin ownership guard lists in ``init-db.sql``.

``scripts/init-db.sql`` protects each admin schema with two families of ownership
assertion. Functions::

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

and tables::

    SELECT admin_table.relowner INTO v_table_owner
    FROM pg_class AS admin_table
    JOIN pg_namespace AS admin_schema ON admin_schema.oid = admin_table.relnamespace
    WHERE admin_schema.nspname = '<schema>'
      AND admin_table.relname = 'bootstrap_configuration'
      AND admin_table.relkind = 'r';

Both share one failure mode: the ``nspname`` filter and the name list must agree.
A name listed under the wrong schema's block -- or a table renamed, or a table
added to an admin schema and never added to the guard -- can never match the
enclosing lookup, so the guard reads as protective while protecting nothing. No
error, no warning. The function form shipped that defect once
(``upgrade_producers_v2`` and ``deactivate_producers_v2`` listed under
``dnd_generation_admin`` while defined in ``runtime_attention_admin``) and was
fixed without regression coverage. The table form has the identical shape and has
only ever held because nobody has renamed or added one yet.

These checks parse the SQL source only. They never execute ``init-db.sql``, start
PostgreSQL, or open a connection.

Known blind spots. They apply to the table checks exactly as they do to the
function checks, and are restated here rather than assumed away:

* A schema not named ``*_admin`` escapes the "every admin schema is guarded"
  sweep entirely -- ``restore_drill_executor.restore_drill_results`` is one such
  table. The per-guard equality checks still cover any schema that *is* guarded.
* ``DROP`` is not tracked. A table or function dropped later in the file still
  reads as defined here, and a guard entry for a dropped object is not reported
  as stale. Neither is ``CREATE TABLE ... AS``, which has no column list to match.
* The ``nspname`` binding is textual proximity -- the region between a guard's
  name filter and the ``FROM pg_proc`` / ``FROM pg_class`` that precedes it --
  not a SQL parse. A table guard restructured so its ``nspname = '...'`` equality
  falls outside that region is dropped from the sweep rather than mis-bound;
  ``test_every_admin_schema_has_a_table_ownership_guard`` is the backstop that
  catches a guard vanishing from a schema that defines tables.
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

_CREATE_TABLE = re.compile(
    r"CREATE\s+(?:UNLOGGED\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\s*\(",
    re.IGNORECASE,
)
# Either ``relname = 'name'`` or ``relname IN (...)``, with or without a table
# alias. The ``IN`` form is not used today; accepting it means the fix for a
# second table in an admin schema is to extend the guard, not to defeat this
# parser.
_RELNAME_FILTER = re.compile(
    r"(?:[a-z_][a-z0-9_]*\.)?relname\s*(?:=\s*'([a-z_][a-z0-9_]*)'|IN\s*\()",
    re.IGNORECASE,
)
_RELKIND_ORDINARY = re.compile(r"relkind\s*=\s*'r'", re.IGNORECASE)
_FROM_PG_CLASS = re.compile(r"FROM\s+pg_class\b", re.IGNORECASE)

# How far past a ``relname`` filter the companion ``relkind = 'r'`` predicate may
# sit before we treat the guard as malformed.
_RELKIND_LOOKAHEAD = 400


@dataclass(frozen=True)
class _Definition:
    """A ``CREATE FUNCTION`` site in the SQL source."""

    schema: str
    name: str
    line: int
    zero_arg: bool


@dataclass(frozen=True)
class _TableDefinition:
    """A ``CREATE TABLE`` site in the SQL source."""

    schema: str
    name: str
    line: int


@dataclass(frozen=True)
class _GuardBlock:
    """One ``nspname = '<schema>'`` ownership assertion and the names it lists.

    Used for both the ``proname IN (...)`` function guards and the ``relname``
    table guards; the two differ only in which catalog they read.
    """

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


def _table_definitions(source: str) -> list[_TableDefinition]:
    return [
        _TableDefinition(
            schema=match.group(1).lower(),
            name=match.group(2).lower(),
            line=_line_of(source, match.start()),
        )
        for match in _CREATE_TABLE.finditer(source)
    ]


def _table_guard_blocks(source: str) -> list[_GuardBlock]:
    """Every ``relname`` filter that reads as an admin table-ownership guard.

    A ``relname`` filter counts as a guard when it sits inside a ``pg_class``
    lookup that also pins ``nspname`` to a literal schema. That excludes filters
    on names other than tables -- the ``idx_user_context_active_signals`` index
    check, which reaches ``pg_class`` through a ``JOIN`` off ``pg_index`` and
    binds no schema -- without needing an allowlist here.
    """
    blocks: list[_GuardBlock] = []
    for match in _RELNAME_FILTER.finditer(source):
        line = _line_of(source, match.start())

        preceding = [item.end() for item in _FROM_PG_CLASS.finditer(source, 0, match.start())]
        if not preceding:
            continue
        scope = source[preceding[-1] : match.start()]
        schema_filters = _NSPNAME_FILTER.findall(scope)
        if not schema_filters:
            continue

        single_name = match.group(1)
        if single_name is not None:
            names = (single_name.lower(),)
            tail_start = match.end()
        else:
            open_index = match.end() - 1
            close_index = _close_paren(source, open_index)
            names = tuple(_QUOTED_NAME.findall(source[open_index + 1 : close_index]))
            assert names, f"init-db.sql:{line}: `relname IN ()` guard list is empty."
            tail_start = close_index

        schema = schema_filters[-1].lower()
        tail = source[tail_start : tail_start + _RELKIND_LOOKAHEAD]
        assert _RELKIND_ORDINARY.search(tail), (
            f"init-db.sql:{line}: table ownership guard for schema '{schema}' is not paired "
            "with `relkind = 'r'`, so it does not constrain the ordinary tables this test "
            "compares against."
        )

        blocks.append(_GuardBlock(schema=schema, names=names, line=line))
    return blocks


def _describe_unguarded_table(
    definition: _TableDefinition, block: _GuardBlock
) -> str:  # pragma: no cover - exercised only on failure
    return (
        f"table {definition.name!r} is created in schema '{definition.schema}' "
        f"(init-db.sql:{definition.line}) but the ownership guard for that schema at "
        f"init-db.sql:{block.line} does not name it, so its owner is never asserted. A "
        "renamed or newly added table drops out of the guard silently: the lookup simply "
        "stops matching and the guard reports success."
    )


def _describe_unmatched_table(
    block: _GuardBlock, name: str, definitions: list[_TableDefinition]
) -> str:  # pragma: no cover - exercised only on failure
    same_name = [item for item in definitions if item.name == name]
    if not same_name:
        return (
            f"table {name!r} is named by the ownership guard for '{block.schema}' at "
            f"init-db.sql:{block.line} but no table of that name is created anywhere in "
            "init-db.sql, so that guard asserts nothing."
        )
    elsewhere = sorted({item.schema for item in same_name})
    return (
        f"table {name!r} is named by the ownership guard for '{block.schema}' at "
        f"init-db.sql:{block.line} but is created in "
        f"{', '.join(repr(schema) for schema in elsewhere)} instead "
        f"(init-db.sql:{same_name[0].line}). The nspname filter and the relname filter "
        "disagree, so this guard can never match."
    )


def test_every_admin_schema_has_a_table_ownership_guard() -> None:
    source = _read_sql()
    blocks = _table_guard_blocks(source)
    definitions = _table_definitions(source)

    guarded = {block.schema for block in blocks}
    admin_schemas = _admin_schemas(source)
    owning_admin_schemas = {
        definition.schema for definition in definitions if definition.schema in admin_schemas
    }

    assert owning_admin_schemas, (
        "no admin schema with a CREATE TABLE was found in init-db.sql; the parser matched "
        "nothing and every comparison below would be vacuously true."
    )
    unguarded = sorted(owning_admin_schemas - guarded)
    assert not unguarded, (
        "admin schemas create tables but have no `relname` table-ownership guard: "
        f"{', '.join(unguarded)}."
    )


def test_table_guards_match_the_tables_defined_in_their_own_schema() -> None:
    source = _read_sql()
    blocks = _table_guard_blocks(source)
    definitions = _table_definitions(source)

    assert blocks, "no `relname` table-ownership guard was found in init-db.sql."

    failures: list[str] = []
    for block in blocks:
        defined = [definition for definition in definitions if definition.schema == block.schema]
        assert defined, (
            f"init-db.sql:{block.line}: table ownership guard for '{block.schema}' compares "
            "against an empty set of tables; the parser found no CREATE TABLE for that schema "
            "and this assertion would be vacuous."
        )

        listed = set(block.names)
        missing = [item for item in defined if item.name not in listed]
        extra = sorted(listed - {item.name for item in defined})

        for definition in missing:
            failures.append(_describe_unguarded_table(definition, block))
        for name in extra:
            failures.append(_describe_unmatched_table(block, name, definitions))

    assert not failures, "admin table-ownership guards are out of sync:\n" + "\n".join(failures)
