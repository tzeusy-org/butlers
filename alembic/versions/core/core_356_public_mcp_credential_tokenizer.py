"""Public MCP: widen the JSON credential-name rule and re-materialize (bu-y66o00.71).

Revision ID: core_356
Revises: core_355

core_320's ``public.mcp_json_tokens`` split names only on separators and a lower-to-upper camel
boundary, and ``public.mcp_json_credential`` matched whole tokens only. Acronym-prefixed
(``APIKey``), concatenated (``apikey``, ``accesstoken``), digit-suffixed (``cvv2``, ``pin2``) and
``authorization`` keys therefore reached ``<domain>.json_fields_public`` and
``health.measurements_public``. This revision replaces both functions with the twin of
``butlers.knowledge_read.manifest.credential`` (the rule is documented there;
``tests/knowledge_read/test_credential_tokenizer.py`` pins the constants below to it).

The triggers only recompute a row when it is written, so each schema's own run recomputes its
materialized leaves: every ``trg_mcp_json_fields`` source table in ``current_schema()`` and, where
the Health fact trigger is installed, ``measurements_public``. Only the owning schema's run touches
its tables (a cross-schema rewrite can deadlock against that schema's open chain). The function
replacement is shared, serialized on core_320's lock, and skipped when already current.

Downgrade keeps the wider rule: restoring the narrower one would publish the leaves again.
core_320's downgrade still drops the functions when the last schema leaves.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "core_356"
down_revision = "core_355"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_320:json-projection', 0))"

# Frozen copies of the manifest rule at this revision. A later rule change needs a new migration.
_SPLITS = (
    (r"([A-Z]+)([A-Z][a-z])", r"\1_\2"),
    (r"([a-z])([A-Z])", r"\1_\2"),
    (r"([A-Za-z])([0-9])", r"\1_\2"),
    (r"([0-9])([A-Za-z])", r"\1_\2"),
)
_CREDENTIAL_PATTERN = (
    r"(^|_)(secret|token|password|credential|otp|cvv|card|api_key|private_key|access_key|"
    r"client_key|signing_key|access_url|passcode|passphrase|pin|bearer|cookie|auth|authorization|"
    r"authentication)(s|keys?|codes?|numbers?)?(_|$)|(secret|token|password|credential|apikey|"
    r"privatekey|accesskey|clientkey|signingkey|accessurl|passcode|passphrase|bearer|cookie|"
    r"authorization|authentication)(s|keys?|codes?|numbers?)?(_|$)"
)


def _split(expression: str) -> str:
    for pattern, replacement in _SPLITS:
        expression = f"regexp_replace({expression}, '{pattern}', '{replacement}', 'g')"
    return expression


_TOKENS_BODY = f"""
    SELECT array_remove(regexp_split_to_array(lower({_split("key")}), '[._[:space:]-]+'), '')
"""
_CREDENTIAL_BODY = f"""
    SELECT array_to_string(tokens, '_') ~ '{_CREDENTIAL_PATTERN}'
"""

_REMATERIALIZE = r"""
DO $do$
DECLARE
    item record;
    args text[];
BEGIN
    FOR item IN
        SELECT n.nspname, c.relname, t.tgnargs, encode(t.tgargs, 'escape') AS raw
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = current_schema() AND t.tgname = 'trg_mcp_json_fields'
            AND t.tgfoid = 'public.mcp_sync_json_fields()'::regprocedure
        ORDER BY c.relname
    LOOP
        -- tgargs is NUL-separated: key column, active-only flag, JSON columns.
        args := (regexp_split_to_array(item.raw, '\\000'))[1:item.tgnargs];
        EXECUTE format('LOCK TABLE %I.%I IN SHARE ROW EXCLUSIVE MODE', item.nspname, item.relname);
        EXECUTE format('DELETE FROM %I.json_fields_public WHERE source_table=$1', item.nspname)
            USING item.nspname || '.' || item.relname;
        EXECUTE format('SELECT public.mcp_write_json_fields($1,$2,$3,$4,$5,to_jsonb(src)) '
            'FROM %I.%I AS src', item.nspname, item.relname)
            USING item.nspname, item.relname, args[1], args[3:], args[2]::boolean;
    END LOOP;
    IF to_regclass(format('%I.measurements_public', current_schema())) IS NOT NULL
        AND to_regprocedure(format('%I.mcp_write_health_fact(jsonb)', current_schema()))
            IS NOT NULL
        AND EXISTS (SELECT 1 FROM pg_trigger
                    WHERE tgrelid = to_regclass(format('%I.facts', current_schema()))
                        AND tgname = 'trg_mcp_health_fact') THEN
        -- Only measurement facts write measurements_public, and only they read key names.
        EXECUTE format('LOCK TABLE %I.facts IN SHARE ROW EXCLUSIVE MODE', current_schema());
        EXECUTE format('DELETE FROM %I.measurements_public', current_schema());
        EXECUTE format('SELECT %1$I.mcp_write_health_fact(to_jsonb(f)) FROM %1$I.facts AS f '
            'WHERE starts_with(f.predicate, ''measurement_'')', current_schema());
    END IF;
END
$do$
"""


def _prosrc(signature: str) -> str | None:
    return (
        op.get_bind()
        .execute(
            sa.text("SELECT prosrc FROM pg_proc WHERE oid = to_regprocedure(:signature)"),
            {"signature": signature},
        )
        .scalar()
    )


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '300s'")
    op.execute(_LOCK)
    if _prosrc("public.mcp_json_tokens(text)") is None:
        return  # core_320 installs the current rule's predecessor; nothing is materialized yet.
    if _prosrc("public.mcp_json_tokens(text)") != _TOKENS_BODY:
        op.execute(
            "CREATE OR REPLACE FUNCTION public.mcp_json_tokens(key text) RETURNS text[] "
            "LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog, pg_temp "
            "AS $body$" + _TOKENS_BODY + "$body$"
        )
    if _prosrc("public.mcp_json_credential(text[])") != _CREDENTIAL_BODY:
        op.execute(
            "CREATE OR REPLACE FUNCTION public.mcp_json_credential(tokens text[]) "
            "RETURNS boolean LANGUAGE sql IMMUTABLE STRICT "
            "SET search_path = pg_catalog, pg_temp AS $body$" + _CREDENTIAL_BODY + "$body$"
        )
    op.execute(_REMATERIALIZE)


def downgrade() -> None:
    # Deliberately keeps the wider rule: see the module docstring.
    pass
