"""Pin the remaining migration-owned SECURITY DEFINER search paths.

Revision ID: core_252
Revises: core_251
Create Date: 2026-09-29 00:00:00.000000

bu-mzm3su.2.  Every definer below still resolved names through a schema the
caller can influence:

- ``search_path = pg_catalog`` (the ``dashboard_turn_*`` family and the two
  ``resolve_*`` helpers) leaves ``pg_temp`` implicit, and an implicit
  ``pg_temp`` is searched *first* for relation and type names.  Any role with
  TEMP can plant ``pg_temp.uuid`` as a domain whose CHECK calls its own
  function, and the definer's ``::uuid`` casts then run it as the owner.
- ``pg_catalog, public`` / ``pg_catalog, dashboard_auth`` add the same implicit
  ``pg_temp`` plus a schema the migration login can CREATE in.
- ``connectors_filtered_events_ensure_partition`` exists once per schema the
  core chain ran in (``connectors`` plus a ``public`` or butler-schema wrapper),
  pinned to ``connectors[, public], pg_temp``.  A ``format(text, text)``
  overload planted in ``connectors`` or ``public`` beats the catalog's variadic
  ``format``.

Each body already schema-qualifies its relations, types, and non-catalog
calls, so the fix is ALTER only: ``SET search_path = pg_catalog, pg_temp``.
``ALTER FUNCTION ... SET`` replaces just that one setting, so
``cost_claim_restore_row`` keeps ``row_security=on``.

The target set is catalog-driven by exact name, not a fixed signature list,
because the core chain runs once per butler schema and each run leaves its own
ensure-partition wrapper.  A function this revision expects but does not find
(its schema or revision is absent from this database) is skipped.  A function
whose path is neither the known prior value nor already pinned is refused
rather than pinned, because downgrade could not then restore it exactly.

Downgrade restores each function's exact prior path.  Like every core revision
touching ``public``, both directions act database-wide: the first schema-scoped
run pins every copy, and later runs find nothing left to change.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "core_252"
down_revision = "core_251"
branch_labels = None
depends_on = None

PINNED_SEARCH_PATH = "pg_catalog, pg_temp"

_LOCK = """
SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_252:definer_search_paths', 0))
"""

DASHBOARD_TURN_FUNCTIONS = (
    "dashboard_turn_acknowledge_cancel",
    "dashboard_turn_bind_ingress",
    "dashboard_turn_claim_external_action",
    "dashboard_turn_claim_ingress",
    "dashboard_turn_claim_invoke",
    "dashboard_turn_claim_target",
    "dashboard_turn_complete_session",
    "dashboard_turn_confirm_cancel",
    "dashboard_turn_dispatch_status",
    "dashboard_turn_live_sessions",
    "dashboard_turn_mark_route_enqueued",
    "dashboard_turn_mark_terminal",
    "dashboard_turn_open",
    "dashboard_turn_reconcile_route_recovery",
    "dashboard_turn_record_ingress_failure",
    "dashboard_turn_register_session",
    "dashboard_turn_release_invoke",
    "dashboard_turn_request_cancel",
    "dashboard_turn_require_role",
)

#: (schema, function name) -> the search_path the creating revision installed.
#: ``None`` as the schema matches every schema holding a copy.
PRIOR_SEARCH_PATHS: dict[tuple[str | None, str], str] = {
    **{("public", name): "pg_catalog" for name in DASHBOARD_TURN_FUNCTIONS},
    ("public", "resolve_owner_triple"): "pg_catalog",
    ("public", "resolve_relationship_prepared_action_status"): "pg_catalog",
    ("public", "record_runtime_probe_verification"): "pg_catalog, public",
    ("public", "cost_claim_restore_row"): "pg_catalog, public",
    ("dashboard_auth", "api"): "pg_catalog, dashboard_auth",
    ("dashboard_auth", "host"): "pg_catalog, dashboard_auth",
    ("dashboard_auth", "cleanup"): "pg_catalog, dashboard_auth",
    # core_135: the connectors function and each unqualified wrapper differ.
    ("connectors", "connectors_filtered_events_ensure_partition"): "connectors, pg_temp",
    (None, "connectors_filtered_events_ensure_partition"): "connectors, public, pg_temp",
}

_TARGETS_SQL = """
SELECT format(
           '%I.%I(%s)', n.nspname, p.proname, pg_catalog.pg_get_function_identity_arguments(p.oid)
       ) AS signature,
       n.nspname AS schema_name,
       p.proname AS function_name,
       (
           SELECT substr(setting, length('search_path=') + 1)
           FROM unnest(p.proconfig) AS setting
           WHERE setting LIKE 'search_path=%'
       ) AS search_path
FROM pg_catalog.pg_proc AS p
JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
WHERE p.prosecdef
  AND p.proname = ANY(:names)
ORDER BY n.nspname, p.proname, p.oid
"""


def _prior_path(schema_name: str, function_name: str) -> str | None:
    exact = PRIOR_SEARCH_PATHS.get((schema_name, function_name))
    if exact is not None:
        return exact
    return PRIOR_SEARCH_PATHS.get((None, function_name))


def _targets(bind: sa.Connection) -> list[tuple[str, str, str | None]]:
    names = sorted({name for _, name in PRIOR_SEARCH_PATHS})
    rows = bind.execute(sa.text(_TARGETS_SQL), {"names": names}).all()
    targets = []
    for row in rows:
        prior = _prior_path(row.schema_name, row.function_name)
        if prior is None:
            # Same name in a schema this revision does not own (for example a
            # butler's own ``api``); leave it alone.
            continue
        targets.append((row.signature, prior, row.search_path))
    return targets


def _alter(signature: str, search_path: str) -> str:
    # The signature is built with %I quoting; the path is a constant.
    return f"ALTER FUNCTION {signature} SET search_path = {search_path}"


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(_LOCK))
    for signature, prior, current in _targets(bind):
        if current == PINNED_SEARCH_PATH:
            continue
        if current != prior:
            raise RuntimeError(
                f"core_252 refuses to pin {signature}: its search_path is neither the "
                "known prior value nor already pinned, so downgrade could not restore it"
            )
        op.execute(_alter(signature, PINNED_SEARCH_PATH))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(_LOCK))
    for signature, prior, current in _targets(bind):
        if current == prior:
            continue
        if current != PINNED_SEARCH_PATH:
            raise RuntimeError(
                f"core_252 refuses to restore {signature}: its search_path is not the "
                "value this revision pinned"
            )
        op.execute(_alter(signature, prior))
