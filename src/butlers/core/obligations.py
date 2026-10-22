"""Versioned, callable-free obligation declarations (RFC0038 D5).

Parsing a declaration does not establish origin, creator authority or exposure.
Only trusted source adapters may attach those facts after durable verification.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from butlers.core.goal_admission import Grant, digest, encode_grants

_CAPABILITY_ID = re.compile(r"[a-z][a-z0-9_-]*__[A-Za-z_][A-Za-z0-9_.-]*\Z")
_RESOURCE_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class DeclarationError(ValueError):
    """Malformed declaration; messages contain no supplied field values."""


def qualify_capability(domain: str, canonical_name: str) -> str:
    """Use the single RFC0038 D7 wire name; native dotted names stay intact.

    Qualifying a name is not proof that it is presentable or grantable.
    """
    if not isinstance(domain, str) or not isinstance(canonical_name, str):
        raise DeclarationError("invalid_capability_id")
    qualified = f"{domain}__{canonical_name}"
    if not _CAPABILITY_ID.fullmatch(qualified):
        raise DeclarationError("invalid_capability_id")
    return qualified


@dataclass(frozen=True)
class ObligationDeclaration:
    capabilities: tuple[Grant, ...]
    version: int = 1

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != 1:
            raise DeclarationError("unsupported_declaration_version")
        if type(self.capabilities) is not tuple or any(
            not isinstance(grant, Grant) for grant in self.capabilities
        ):
            raise DeclarationError("invalid_capability_set")
        names = [grant.capability for grant in self.capabilities]
        if any(
            not isinstance(name, str) or not _CAPABILITY_ID.fullmatch(name) for name in names
        ) or len(set(names)) != len(names):
            raise DeclarationError("invalid_capability_set")
        if any(
            not isinstance(key, str) or not _RESOURCE_KEY.fullmatch(key)
            for grant in self.capabilities
            for key, _ in grant.resources
        ):
            raise DeclarationError("invalid_resource_set")
        object.__setattr__(
            self, "capabilities", tuple(sorted(self.capabilities, key=lambda g: g.capability))
        )

    @property
    def digest(self) -> str:
        return digest({"version": self.version, "capabilities": encode_grants(self.capabilities)})


def parse_declaration(value: object) -> ObligationDeclaration:
    """Copy strict TOML/JSON values into immutable exact capability/resource sets.

    `preconditions` is reserved for bu-jv1moa.1. It is not accepted or interpreted
    in version 1, even when empty. Creator/cause/exposure fields are never input.
    """
    if not isinstance(value, Mapping):
        raise DeclarationError("invalid_declaration")
    if set(value) != {"version", "capabilities"}:
        raise DeclarationError("invalid_declaration_fields")
    if type(value["version"]) is not int or value["version"] != 1:
        raise DeclarationError("unsupported_declaration_version")
    raw_grants = value["capabilities"]
    if not isinstance(raw_grants, list):
        raise DeclarationError("invalid_capability_set")
    grants = []
    for raw in raw_grants:
        if (
            not isinstance(raw, Mapping)
            or "capability" not in raw
            or set(raw) - {"capability", "resources", "park_only"}
        ):
            raise DeclarationError("invalid_capability_fields")
        name = raw["capability"]
        resources = raw.get("resources", {})
        park_only = raw.get("park_only", False)
        if (
            not isinstance(name, str)
            or not _CAPABILITY_ID.fullmatch(name)
            or not isinstance(resources, Mapping)
            or type(park_only) is not bool
        ):
            raise DeclarationError("invalid_capability_set")
        if any(
            not isinstance(key, str)
            or not _RESOURCE_KEY.fullmatch(key)
            or not isinstance(values, list)
            for key, values in resources.items()
        ):
            raise DeclarationError("invalid_resource_set")
        try:
            grants.append(
                Grant(
                    name,
                    tuple((key, tuple(values)) for key, values in resources.items()),
                    park_only,
                )
            )
        except (TypeError, ValueError) as exc:
            raise DeclarationError("invalid_resource_set") from exc
    return ObligationDeclaration(tuple(grants))
