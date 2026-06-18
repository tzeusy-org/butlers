"""entity_facts: backfill non-secret public.entity_info rows into has-* triples.

Revision ID: rel_028
Revises: rel_027
Create Date: 2026-06-18 00:00:00.000000

Phase 2 of the contact-schema retirement epic (bu-oluyt / bu-oluyt.2).

Seam law (RFC 0004 Amendment 3, bu-oluyt.1): ``relationship.entity_facts`` is
the single source of truth for ALL non-secret facts / identifiers / routing
handles; ``public.entity_info`` holds ONLY secured=True credentials (plus a
tiny carve-out of technical config entries that have no predicate home).

Legacy non-secret rows written to ``public.entity_info`` BEFORE the Phase 1
write-time guard (``credential_store.assert_entity_info_secured``) still sit in
the secret store with ``secured = false``.  This migration projects every such
row into the canonical ``has-*`` triple via a direct INSERT into
``relationship.entity_facts`` (the migration is the authoritative author, so it
bypasses ``relationship_assert_fact``'s owner carve-out — exactly as
``owner_bootstrap._seed_owner_telegram_handle`` does — and the owner's own
identity rows ARE included; the self-identity carve-out shipped in bu-oluyt.4).
The owner Telegram seed added in PR #2465 is subsumed: it writes the same
``(subject, has-handle, telegram:<id>)`` triple, so the ON CONFLICT below makes
the two converge to one active row regardless of ordering.

Type -> predicate mapping (mirrors identity._CHANNEL_TYPE_TO_PREDICATE and
relationship_assert_fact._CI_TYPE_TO_PREDICATE; frozen here so the migration is
self-contained and immune to later code drift):

    email                                            -> has-email
    phone, whatsapp_phone                            -> has-phone
    website                                          -> has-website
    telegram, telegram_chat_id, telegram_user_id,
      telegram_username, telegram_bot,
      telegram_user_client                           -> has-handle (telegram: prefix)
    linkedin, twitter, whatsapp_jid, other           -> has-handle (verbatim)

Telegram handles are stored in the canonical ``telegram:<bare>`` form (the same
encoding as ``_ef_channel_helpers.encode_handle_object`` /
``identity._telegram_prefixed_value``): any pre-existing ``telegram:`` prefix is
stripped first and a leading ``@`` removed, then the prefix is re-applied.

Technical config carve-out (NOT projected, legitimately stays in entity_info):
    telegram_api_id, home_assistant_url
(matches credential_store._ENTITY_INFO_NON_SECRET_ALLOWED_TYPES).

Zero-data-loss bar
------------------
1. Before projecting, the migration RAISES if any non-secret entity_info row
   has a type that is neither in the projection map nor in the technical
   carve-out — an unexpected type must be a human decision, never silently
   dropped or mis-projected.
2. After projecting, a row-count + value parity assertion RAISES unless every
   projectable non-secret row has a matching active triple.

Idempotency
-----------
``INSERT ... ON CONFLICT (subject, predicate, object) WHERE validity='active'
DO NOTHING`` (partial unique index ``uq_ef_spo_active``).  Running twice is a
no-op the second time; the parity assertion then passes trivially.

Cross-chain safety
-------------------
The core and relationship alembic chains have no guaranteed ordering.  The
migration no-ops if ``relationship.entity_facts`` or ``public.entity_info`` is
absent (see ``cross-chain-migration-drop-hazard``).

Downgrade
---------
Non-reversible data backfill: triples projected here are indistinguishable from
those written by the live write path once committed, and the source rows are
not removed.  ``downgrade()`` is intentionally a no-op (it does NOT delete
triples, to avoid clobbering facts that the live path may have re-asserted).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "rel_028"
down_revision = "rel_027"
branch_labels = None
depends_on = None


# entity_info types that are non-secret CONFIG entries with no predicate home;
# they legitimately remain in entity_info (mirror of
# credential_store._ENTITY_INFO_NON_SECRET_ALLOWED_TYPES).
_TECHNICAL_CONFIG_TYPES = ("telegram_api_id", "home_assistant_url")

# entity_info channel types that project to a has-* triple, with their predicate.
_TYPE_TO_PREDICATE = {
    "email": "has-email",
    "phone": "has-phone",
    "whatsapp_phone": "has-phone",
    "website": "has-website",
    "telegram": "has-handle",
    "telegram_chat_id": "has-handle",
    "telegram_user_id": "has-handle",
    "telegram_username": "has-handle",
    "telegram_bot": "has-handle",
    "telegram_user_client": "has-handle",
    "linkedin": "has-handle",
    "twitter": "has-handle",
    "whatsapp_jid": "has-handle",
    "other": "has-handle",
}

# Telegram channel types whose object is stored with the canonical
# ``telegram:<bare>`` prefix (mirror of identity._TELEGRAM_PREFIX_CHANNEL_TYPES).
_TELEGRAM_PREFIX_TYPES = (
    "telegram",
    "telegram_chat_id",
    "telegram_user_id",
    "telegram_username",
    "telegram_bot",
    "telegram_user_client",
)


def _sql_str_list(values) -> str:
    """Render a Python string iterable as a SQL ``IN (...)`` literal list."""
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def _predicate_case() -> str:
    """SQL CASE expression mapping ``ei.type`` to its canonical predicate."""
    whens = "\n".join(
        f"            WHEN ei.type = '{t}' THEN '{p}'" for t, p in _TYPE_TO_PREDICATE.items()
    )
    return f"CASE\n{whens}\n        END"


def _object_case() -> str:
    """SQL CASE expression rendering the canonical object for (ei.type, ei.value).

    Telegram types get the ``telegram:<bare>`` encoding (strip an existing
    ``telegram:`` prefix, drop a leading ``@``, then re-prefix); everything else
    is stored verbatim.
    """
    telegram_types = _sql_str_list(_TELEGRAM_PREFIX_TYPES)
    return (
        "CASE\n"
        f"            WHEN ei.type IN ({telegram_types})\n"
        "                THEN 'telegram:' || ltrim(regexp_replace(ei.value, '^telegram:', ''), '@')\n"
        "            ELSE ei.value\n"
        "        END"
    )


# Rows in scope: non-secret, linked to an entity, with a non-blank value.
_PROJECTABLE_WHERE = (
    "ei.secured = false\n"
    "          AND ei.entity_id IS NOT NULL\n"
    "          AND ei.value IS NOT NULL\n"
    "          AND btrim(ei.value) <> ''"
)


def projectable_types_sql() -> str:
    return _sql_str_list(_TYPE_TO_PREDICATE)


def known_types_sql() -> str:
    return _sql_str_list(tuple(_TYPE_TO_PREDICATE) + _TECHNICAL_CONFIG_TYPES)


def unexpected_types_sql() -> str:
    """SELECT returning DISTINCT non-secret types not mapped and not carved out."""
    return (
        "SELECT array_agg(DISTINCT ei.type)\n"
        "        FROM public.entity_info ei\n"
        f"        WHERE {_PROJECTABLE_WHERE}\n"
        f"          AND ei.type NOT IN ({known_types_sql()})"
    )


def projection_insert_sql() -> str:
    """INSERT ... SELECT projecting every projectable row into a has-* triple."""
    return (
        "INSERT INTO relationship.entity_facts (\n"
        '            subject, predicate, object, object_kind, src, last_seen, "primary"\n'
        "        )\n"
        "        SELECT\n"
        "            ei.entity_id,\n"
        f"            ({_predicate_case()}),\n"
        f"            ({_object_case()}),\n"
        "            'literal',\n"
        "            'migration',\n"
        "            ei.created_at,\n"
        "            ei.is_primary\n"
        "        FROM public.entity_info ei\n"
        f"        WHERE {_PROJECTABLE_WHERE}\n"
        f"          AND ei.type IN ({projectable_types_sql()})\n"
        "        ON CONFLICT (subject, predicate, object) WHERE validity = 'active'\n"
        "        DO NOTHING"
    )


def parity_missing_sql() -> str:
    """SELECT count(*) of projectable rows lacking a matching active triple."""
    return (
        "SELECT count(*)\n"
        "        FROM public.entity_info ei\n"
        f"        WHERE {_PROJECTABLE_WHERE}\n"
        f"          AND ei.type IN ({projectable_types_sql()})\n"
        "          AND NOT EXISTS (\n"
        "              SELECT 1\n"
        "              FROM relationship.entity_facts ef\n"
        "              WHERE ef.subject   = ei.entity_id\n"
        "                AND ef.validity  = 'active'\n"
        f"                AND ef.predicate = ({_predicate_case()})\n"
        f"                AND ef.object    = ({_object_case()})\n"
        "          )"
    )


def _tables_present() -> bool:
    bind = op.get_bind()
    ready = bind.execute(
        sa.text(
            "SELECT to_regclass('relationship.entity_facts') IS NOT NULL "
            "AND to_regclass('public.entity_info') IS NOT NULL"
        )
    ).scalar()
    return bool(ready)


def upgrade() -> None:
    if not _tables_present():
        return

    bind = op.get_bind()

    # Guard 1: surface any unexpected non-secret type loudly. Zero-data-loss
    # bar — never silently skip or mis-project an unmapped type.
    unexpected = bind.execute(sa.text(unexpected_types_sql())).scalar()
    if unexpected:
        raise RuntimeError(
            "rel_028 backfill: encountered non-secret public.entity_info rows with "
            f"unmapped type(s) {list(unexpected)!r}. Add an explicit predicate mapping "
            "(or technical-config carve-out) before re-running — refusing to silently "
            "drop or mis-project per the zero-data-loss bar (RFC 0004 Amendment 3)."
        )

    # Project every projectable non-secret row into its canonical has-* triple.
    op.execute(projection_insert_sql())

    # Guard 2: row-count + value parity. Every projectable non-secret row MUST
    # now have a matching active triple, else the backfill lost data.
    missing = bind.execute(sa.text(parity_missing_sql())).scalar()
    if missing:
        raise RuntimeError(
            f"rel_028 backfill parity FAILED: {missing} non-secret public.entity_info "
            "row(s) have no matching active relationship.entity_facts triple. "
            "Refusing to complete with data loss (RFC 0004 Amendment 3 zero-loss bar)."
        )


def downgrade() -> None:
    """Non-reversible data backfill — intentional no-op.

    Projected triples are indistinguishable from live-path writes once committed,
    and the source entity_info rows are left intact, so there is nothing safe to
    reverse without risking deletion of facts re-asserted by the write path.
    """
    return
