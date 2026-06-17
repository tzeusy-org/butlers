#!/usr/bin/env python3
"""Dedupe duplicate ``public.contacts`` rows that share a single entity.

Background
----------
A historical Google Contacts sync bug created contact rows without writing the
``relationship.contacts_source_links`` provenance row (the idempotency anchor
keyed on ``(provider, account_id, external_contact_id)``). Each subsequent sync
could not resolve the existing contact by ``external_id`` and inserted a fresh
duplicate — leaving several near-identical contacts all pointing at the *same*
entity (e.g. four "Ang Zhi Yuan" rows). The backfill engine has since been
hardened to write the contact + source link atomically, but the duplicate rows
already in the database must be reconciled.

Strategy (conservative)
------------------------
For every entity with more than one *listed, non-archived* contact:

  * keeper           = the contact with an active source link, preferring the
                       most recently synced (``source_links.last_seen_at`` DESC,
                       then ``contacts.updated_at`` DESC). If no contact has a
                       link, the most recently updated contact wins.
  * orphans          = the OTHER contacts that have **no** active source link.
                       These are the artefacts of the historical bug; they are
                       merged into the keeper and archived.
  * conflicting kept = any OTHER contact that DOES carry its own active source
                       link. These are distinct provider entries (distinct
                       external_contact_id) that merely resolved to the same
                       entity. They are **left untouched** and reported for
                       manual review — auto-merging them could discard a real
                       second provider record.

Merging an orphan re-points only contact-keyed child rows (notes, interactions,
gifts, contact_info, addresses, ...) to the keeper and archives the orphan. It
deliberately does NOT run any entity-level merge: source and keeper already
share the same entity, so entity facts are already consolidated, and invoking
``entity_merge``/entity_facts re-pointing with identical source==target would
wrongly supersede the entity's own active facts.

Usage
-----
    BUTLERS_DATABASE_URL=postgresql://... python scripts/dedupe_orphan_contacts.py
    BUTLERS_DATABASE_URL=postgresql://... python scripts/dedupe_orphan_contacts.py --apply

Default is a DRY RUN that prints the plan and changes nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from dataclasses import dataclass, field

import asyncpg

# Contact-keyed child tables to re-point from orphan -> keeper. Mirrors the
# child-table list in roster/relationship/tools/contacts.py::contact_merge, but
# we re-point ONLY these contact-scoped rows — never entity-level facts.
_CHILD_TABLES: list[tuple[str, str]] = [
    ("notes", "contact_id"),
    ("interactions", "contact_id"),
    ("dates", "contact_id"),
    ("relationships", "contact_a"),
    ("relationships", "contact_b"),
    ("gifts", "contact_id"),
    ("loans", "contact_id"),
    ("group_members", "contact_id"),
    ("contact_labels", "contact_id"),
    ("contact_info", "contact_id"),
    ("addresses", "contact_id"),
    ("facts", "contact_id"),
    ("tasks", "contact_id"),
    ("life_events", "contact_id"),
    ("stay_in_touch", "contact_id"),
]


@dataclass
class Orphan:
    contact_id: uuid.UUID
    name: str
    child_counts: dict[str, int] = field(default_factory=dict)


@dataclass
class Plan:
    entity_id: uuid.UUID
    canonical_name: str
    keeper_id: uuid.UUID
    keeper_name: str
    keeper_linked: bool
    orphans: list[Orphan]
    kept_for_review: list[tuple[uuid.UUID, str]]  # other source-linked contacts


async def _existing_child_tables(conn: asyncpg.Connection) -> list[tuple[str, str]]:
    """Filter _CHILD_TABLES to those whose table+column actually exist."""
    present: list[tuple[str, str]] = []
    for table, col in _CHILD_TABLES:
        exists = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = $1 AND column_name = $2
                  AND table_schema = ANY (current_schemas(true))
            )
            """,
            table,
            col,
        )
        if exists:
            present.append((table, col))
    return present


async def build_plans(conn: asyncpg.Connection) -> list[Plan]:
    child_tables = await _existing_child_tables(conn)

    dupe_entities = await conn.fetch(
        """
        SELECT entity_id
        FROM public.contacts
        WHERE entity_id IS NOT NULL AND listed AND archived_at IS NULL
        GROUP BY entity_id
        HAVING count(*) > 1
        ORDER BY entity_id
        """
    )

    plans: list[Plan] = []
    for row in dupe_entities:
        entity_id = row["entity_id"]
        canonical_name = await conn.fetchval(
            "SELECT canonical_name FROM public.entities WHERE id = $1", entity_id
        )
        # Contacts on this entity, annotated with their most-recent active link.
        contacts = await conn.fetch(
            """
            SELECT c.id,
                   c.name,
                   c.updated_at,
                   max(sl.last_seen_at) FILTER (WHERE sl.deleted_at IS NULL) AS link_last_seen
            FROM public.contacts c
            LEFT JOIN relationship.contacts_source_links sl
                   ON sl.local_contact_id = c.id
            WHERE c.entity_id = $1 AND c.listed AND c.archived_at IS NULL
            GROUP BY c.id, c.name, c.updated_at
            """,
            entity_id,
        )
        linked = [c for c in contacts if c["link_last_seen"] is not None]
        unlinked = [c for c in contacts if c["link_last_seen"] is None]

        # Keeper: best-linked contact, else most-recently-updated.
        if linked:
            keeper = sorted(
                linked,
                key=lambda c: (c["link_last_seen"], c["updated_at"]),
                reverse=True,
            )[0]
        else:
            keeper = sorted(contacts, key=lambda c: c["updated_at"], reverse=True)[0]

        keeper_id = keeper["id"]
        orphans: list[Orphan] = []
        for c in unlinked:
            if c["id"] == keeper_id:
                continue
            counts: dict[str, int] = {}
            for table, col in child_tables:
                n = await conn.fetchval(
                    f"SELECT count(*) FROM {table} WHERE {col} = $1",  # noqa: S608 — fixed allowlist
                    c["id"],
                )
                if n:
                    counts[f"{table}.{col}"] = n
            orphans.append(Orphan(contact_id=c["id"], name=c["name"], child_counts=counts))

        kept_for_review = [(c["id"], c["name"]) for c in linked if c["id"] != keeper_id]

        if not orphans:
            # Nothing safe to merge (e.g. all remaining contacts are link-bearing).
            if kept_for_review:
                plans.append(
                    Plan(
                        entity_id=entity_id,
                        canonical_name=canonical_name,
                        keeper_id=keeper_id,
                        keeper_name=keeper["name"],
                        keeper_linked=keeper["link_last_seen"] is not None,
                        orphans=[],
                        kept_for_review=kept_for_review,
                    )
                )
            continue

        plans.append(
            Plan(
                entity_id=entity_id,
                canonical_name=canonical_name,
                keeper_id=keeper_id,
                keeper_name=keeper["name"],
                keeper_linked=keeper["link_last_seen"] is not None,
                orphans=orphans,
                kept_for_review=kept_for_review,
            )
        )
    return plans


async def apply_plan(
    conn: asyncpg.Connection, plan: Plan, child_tables: list[tuple[str, str]]
) -> None:
    """Re-point each orphan's child rows to the keeper and archive the orphan."""
    for orphan in plan.orphans:
        async with conn.transaction():
            for table, col in child_tables:
                try:
                    await conn.execute(
                        f"UPDATE {table} SET {col} = $1 WHERE {col} = $2",  # noqa: S608
                        plan.keeper_id,
                        orphan.contact_id,
                    )
                except asyncpg.UniqueViolationError:
                    # Orphan child row duplicates one the keeper already owns
                    # (e.g. same contact_info (type,value)). The keeper's copy
                    # wins; drop the orphan's colliding rows.
                    await conn.execute(
                        f"DELETE FROM {table} WHERE {col} = $1",  # noqa: S608
                        orphan.contact_id,
                    )
            await conn.execute(
                """
                UPDATE public.contacts
                SET listed = false, archived_at = now(), updated_at = now()
                WHERE id = $1
                """,
                orphan.contact_id,
            )


def print_plans(plans: list[Plan], *, apply: bool) -> None:
    mode = "APPLY" if apply else "DRY RUN (use --apply to write changes)"
    print(f"\nMode: {mode}\n")
    total_orphans = sum(len(p.orphans) for p in plans)
    total_review = sum(len(p.kept_for_review) for p in plans)
    print(f"Entities with duplicate contacts: {len(plans)}")
    print(f"Orphan contacts to merge + archive: {total_orphans}")
    print(f"Source-linked contacts flagged for manual review: {total_review}\n")
    print("=" * 78)
    for p in plans:
        link_tag = "linked" if p.keeper_linked else "NO LINK"
        print(f"\n[{p.canonical_name}]  entity={p.entity_id}")
        print(f"  KEEP  {p.keeper_id}  ({link_tag})  name={p.keeper_name!r}")
        for o in p.orphans:
            extra = (
                "  child rows: " + ", ".join(f"{k}={v}" for k, v in o.child_counts.items())
                if o.child_counts
                else "  (no child rows)"
            )
            print(f"  MERGE {o.contact_id}  name={o.name!r}{extra}")
        for cid, name in p.kept_for_review:
            print(f"  REVIEW {cid}  (own source link)  name={name!r}  — left untouched")
    print("\n" + "=" * 78)


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Actually re-point + archive (default: dry-run diagnostic only)",
    )
    args = parser.parse_args(argv)

    db_url = os.environ.get("BUTLERS_DATABASE_URL")
    if not db_url:
        print("ERROR: BUTLERS_DATABASE_URL environment variable is not set", file=sys.stderr)
        return 1

    try:
        pool = await asyncpg.create_pool(db_url, min_size=1, max_size=3)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: Failed to connect to database: {exc}", file=sys.stderr)
        return 1

    try:
        async with pool.acquire() as conn:
            # Unqualified child-table names resolve against relationship first.
            await conn.execute("SET search_path TO relationship, public")
            plans = await build_plans(conn)
            print_plans(plans, apply=args.apply)

            if args.apply and plans:
                child_tables = await _existing_child_tables(conn)
                for p in plans:
                    if p.orphans:
                        await apply_plan(conn, p, child_tables)
                merged = sum(len(p.orphans) for p in plans)
                print(f"\nAPPLIED: archived {merged} orphan contact(s).")
            elif not plans:
                print("\nNothing to do — no entities have duplicate listed contacts.")
    finally:
        await pool.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
