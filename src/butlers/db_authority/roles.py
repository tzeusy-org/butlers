"""Strict server-owned roster projection, independent of runtime config discovery."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

_ROSTER_ROOT = Path(__file__).resolve().parents[3] / "roster"
_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z")


class AuthorityError(RuntimeError):
    """Closed diagnostic; the original failure remains available through its cause."""


def _identifier(value: object) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise AuthorityError("Runtime roster identity is invalid")
    return value


@dataclass(frozen=True)
class RuntimeProfile:
    """Held primary identity, not authority inferred from a private search path."""

    database: str
    primary_schema: str
    role: str
    runtime_roles: tuple[str, ...]
    private_schemas: tuple[str, ...] = ()


def load_runtime_profile(database: str, schema: str | None, role: str) -> RuntimeProfile:
    """Read every roster identity or refuse; never use environment-selected roster roots.

    Only identity and owned-private-schema fields are projected. Module options,
    secrets, schedule payloads and environment expansion are not config authority.
    """
    try:
        if not _ROSTER_ROOT.is_dir():
            raise AuthorityError("Runtime roster authority is unavailable")
        identities = []
        names: set[str] = set()
        schemas: set[tuple[str, str]] = set()
        for entry in sorted(_ROSTER_ROOT.iterdir()):
            source = entry / "butler.toml"
            if not entry.is_dir():
                continue
            if not source.exists():
                if entry.name == "shared":  # shared instructions, never a daemon identity
                    continue
                raise AuthorityError("Runtime roster identity is missing")
            if entry.is_symlink() or source.is_symlink() or not source.is_file():
                raise AuthorityError("Runtime roster authority is ambiguous")
            with source.open("rb") as stream:
                document = tomllib.load(stream)
            butler = document["butler"]
            name = _identifier(butler["name"])
            db = butler["db"]
            target = _identifier(db["name"])
            primary = db.get("schema", name if target == "butlers" else None)
            primary = _identifier(primary)
            derived = _identifier(f"butler_{name}_rw")
            if name != entry.name or name in names or (target, primary) in schemas:
                raise AuthorityError("Runtime roster authority is ambiguous")
            names.add(name)
            schemas.add((target, primary))
            memory = document.get("modules", {}).get("memory", {})
            private = memory.get("memory_schema")
            private_schemas = (
                () if private is None or private == primary else (_identifier(private),)
            )
            identities.append((target, primary, derived, private_schemas))
        if not identities:
            raise AuthorityError("Runtime roster authority is empty")
        # A private schema cannot silently alias a different primary or private owner.
        owned = set(schemas)
        for target, _, _, private in identities:
            for child in private:
                if (target, child) in owned:
                    raise AuthorityError("Runtime roster authority is ambiguous")
                owned.add((target, child))
        selected = [row for row in identities if row[0] == database and row[2] == role]
        if len(selected) != 1 or schema not in (None, selected[0][1]):
            raise AuthorityError("Runtime database identity is not declared")
        target, primary, derived, private = selected[0]
        return RuntimeProfile(
            target,
            primary,
            derived,
            tuple(sorted(row[2] for row in identities if row[0] == target)),
            private,
        )
    except AuthorityError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise AuthorityError("Runtime roster authority is unavailable") from exc
