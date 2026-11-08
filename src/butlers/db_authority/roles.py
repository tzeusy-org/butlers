"""Runtime database roles, derived from the roster.

Every butler connects as ``butler_<schema>_rw`` (``Database._validate_runtime_role``
refuses any other pairing), and ``<schema>`` comes from the butler's
``roster/<butler>/butler.toml`` (``[butler.db] schema``, else the butler name: the
one-database default ``butlers.config`` applies; every roster butler uses the shared
``butlers`` database). This module is the one place that turns the roster into
role names, so a migration that grants to "every butler" cannot silently omit a
butler added after it was written (the fleet lists written before the concierge
existed all omit ``butler_concierge_rw``).

New Alembic revisions grant through :func:`butler_roles` or :func:`runtime_roles`
instead of spelling the fleet out. ``tests/config/test_db_authority_roles.py`` keeps
the remaining hand-written fleet copies (the four ``scripts/init-db.sql`` arrays and
``butlers.bootstrap_prerequisite._BUTLERS``) equal to this derivation, and
``tests/config/test_literal_role_list_ratchet.py`` forbids new literal fleet lists.

The roster is read, never guessed: a missing roster or an unreadable manifest raises,
so a revision cannot grant to a silently empty or partial fleet.
"""

from __future__ import annotations

import re
import tomllib
from functools import cache
from pathlib import Path

ROSTER_DIR = Path(__file__).resolve().parents[3] / "roster"
CONNECTOR_ROLE = "connector_writer"

_SCHEMA = re.compile(r"^[a-z][a-z0-9_]*$")


class RosterRoleError(RuntimeError):
    """The roster cannot produce a trustworthy role list."""


def butler_role(schema: str) -> str:
    """Return the runtime role for one butler schema."""
    if not _SCHEMA.fullmatch(schema):
        raise RosterRoleError(f"invalid butler schema name: {schema!r}")
    return f"butler_{schema}_rw"


def _manifest_schema(manifest: Path) -> str:
    try:
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RosterRoleError(f"unreadable butler manifest: {manifest}") from exc
    butler = data.get("butler")
    if not isinstance(butler, dict) or not isinstance(butler.get("name"), str):
        raise RosterRoleError(f"butler manifest has no [butler] name: {manifest}")
    db = butler.get("db", {})
    if not isinstance(db, dict):
        raise RosterRoleError(f"butler manifest [butler.db] is not a table: {manifest}")
    if "schema" not in db and db.get("name", "butlers") != "butlers":
        raise RosterRoleError(f"butler outside the shared database names no schema: {manifest}")
    schema = db.get("schema", butler["name"])
    if not isinstance(schema, str):
        raise RosterRoleError(f"butler manifest schema is not a string: {manifest}")
    butler_role(schema)
    return schema


def butler_schemas(roster_dir: Path = ROSTER_DIR) -> tuple[str, ...]:
    """Return every roster butler's schema, sorted, read from ``butler.toml`` files.

    Cached per resolved roster path for the life of the process; a roster edited
    mid-process is not re-read.
    """
    return _butler_schemas(roster_dir.resolve())


@cache
def _butler_schemas(roster_dir: Path) -> tuple[str, ...]:
    if not roster_dir.is_dir():
        raise RosterRoleError(f"roster directory not found: {roster_dir}")
    schemas = sorted(_manifest_schema(path) for path in roster_dir.glob("*/butler.toml"))
    if not schemas:
        raise RosterRoleError(f"roster has no butler manifests: {roster_dir}")
    if len(set(schemas)) != len(schemas):
        raise RosterRoleError(f"roster declares a schema twice: {schemas}")
    return tuple(schemas)


def butler_roles(roster_dir: Path = ROSTER_DIR) -> tuple[str, ...]:
    """Return every butler's runtime role (``butler_<schema>_rw``), sorted by schema."""
    return tuple(butler_role(schema) for schema in butler_schemas(roster_dir))


def runtime_roles(roster_dir: Path = ROSTER_DIR) -> tuple[str, ...]:
    """Return every runtime login role: each butler role, then the connector writer."""
    return (*butler_roles(roster_dir), CONNECTOR_ROLE)
