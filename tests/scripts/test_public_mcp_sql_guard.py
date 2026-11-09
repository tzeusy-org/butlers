"""scripts/k8s/public_mcp_sql_guard.py: the psql meta-command allowlist for the owner step.

The owner step pipes a generated file to psql as the CNPG superuser. psql meta-commands can run
shell commands on the primary (`\\!`, backticks in `\\set`/`\\if`, `\\o |cmd`, `\\copy ... program`),
so only the forms the renderer emits may reach it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from butlers.knowledge_read.manifest import load_manifest
from butlers.migrations.public_mcp_owner import render_owner_program

_GUARD = Path(__file__).resolve().parents[2] / "scripts/k8s/public_mcp_sql_guard.py"


def _guard(text: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(_GUARD)],
        input=text, capture_output=True, text=True, timeout=60,
    )  # fmt: skip


def test_the_rendered_program_passes():
    result = _guard(render_owner_program(load_manifest()) + "\n")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "planted",
    [
        "\\! id",
        "  \\! id",
        "\\set ON_ERROR_STOP `id`",
        "\\set X on`id`",
        "\\if `id`",
        "\\if :e_rebuild `id`",
        "\\o |id",
        "\\copy t from program 'id'",
        "\\connect postgres",
        "\\connect butlers_public extra",
        "\\c butlers",
        "\\gset prefix_",
        "\\setenv PGOPTIONS x",
        "\\i /etc/passwd",
        "\\q",
        "SELECT 1; \\! id",
        "SELECT 1 \\g |id",
        "SELECT '\\d' AS ok; \\! id",
        "SELECT `id`;",
    ],
)
def test_a_planted_meta_command_or_backtick_is_refused(planted: str):
    program = render_owner_program(load_manifest())
    lines = program.splitlines()
    lines.insert(3, planted)
    result = _guard("\n".join(lines) + "\n")
    assert result.returncode != 0, planted
    assert "refused" in result.stderr
    assert "line 4" in result.stderr


@pytest.mark.parametrize(
    "text",
    [
        "SELECT 'a \\d \\! b';\n",
        "SELECT 'it''s \\!';\n",
        'SELECT "odd name" FROM t; -- a comment\n',
        "DO $x$ BEGIN PERFORM '\\!'; END $x$;\n",
        "DO $mcp$ BEGIN RAISE NOTICE $$ \\! $$; END $mcp$;\n",
        "SELECT $1, a$b FROM t /* nested /* x */ still */;\n",
        "\\set QUIET on\n\\connect butlers\n\\connect butlers_public\nSELECT 1 AS x\n\\gset\n"
        "\\if :x\n\\else\n\\endif\n",
    ],
)
def test_backslashes_inside_string_and_dollar_quotes_are_data(text: str):
    result = _guard(text)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "text",
    [
        "SELECT :e_old_canonical;\n",
        "SELECT :x::text;\n",
        "SELECT 1 WHERE a = :v1;\n",
        "SELECT :_x;\n",
        "SELECT :{?x};\n",
        "SELECT :\u00e9t\u00e9;\n",
        "SELECT set_config('k', :e_old_oids, false);\n",
    ],
)
def test_an_unquoted_psql_variable_is_refused(text: str):
    """psql rescans an unquoted :name for meta-commands; \\gset fills it from the database."""
    result = _guard(text)
    assert result.returncode != 0
    assert "unquoted psql variable" in result.stderr


@pytest.mark.parametrize(
    "text",
    [
        "SELECT x::text, y::regclass, :'e_old_canonical', :\"e_name\";\n",
        "SELECT set_config('k', :'e_old_oids', false);\n",
        "DO $x$ DECLARE v int; BEGIN v := 1; PERFORM :e_inside_dollar; END $x$;\n",
        "SELECT ':e_in_string' AS s; -- :e_in_comment\n",
        "SELECT /* :e_in_block */ arr[1 : 2], ts AT TIME ZONE 'UTC';\n",
        "SELECT 1 AS e_x\n\\gset\n\\if :e_x\n\\endif\n",
    ],
)
def test_quoted_variables_casts_and_colons_in_quotes_are_admitted(text: str):
    result = _guard(text)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "text",
    [
        "SELECT 'unterminated;\n",
        "DO $x$ BEGIN END;\n",
        "SELECT /* open;\n",
        'SELECT "open;\n',
        # standard_conforming_strings: a backslash does not escape a quote in '...'.
        "SELECT '\\' \\! id';\n",
        # Only the forms the renderer emits: no escape strings, no backslash in a comment or
        # a quoted identifier, no indented meta-command.
        "SELECT E'x';\n",
        "SELECT 1; -- \\! id\n",
        "SELECT /* \\! */ 1;\n",
        'SELECT "a\\!b" FROM t;\n',
        "  \\set QUIET on\n",
        "",
    ],
)
def test_unterminated_empty_or_unexpected_forms_are_refused(text: str):
    result = _guard(text)
    assert result.returncode != 0
    assert "refused" in result.stderr
