"""RFC 0038 S1 invocation labels: instruction, data and sensitivity sets.

A label is version 1 with three sets:

* ``instr`` (I): who could have supplied instructions the invocation follows;
* ``data`` (D): who supplied the data it has seen;
* ``sens`` (C): which of the six sensitivity categories it has read.

I and D hold opaque, server-resolved principal references: ``owner``,
``owner_device``, ``world``, or ``domain:<id>`` / ``external:<id>`` with a
registered opaque identity (never an address, credential or payload). Only the
two owner principals are owner-authoritative; anything else exposes the
invocation (D6). Unknown or unreadable provenance is ``{world}``, and unknown
sensitivity is all six categories. A label only grows: join is component-wise
set union.

Values are validated, never normalised: an unknown version, principal kind or
category refuses rather than being dropped to make a call admissible.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

LABEL_VERSION = 1

CATEGORIES = frozenset(
    {"health", "finance", "location", "routine", "relationship_notes", "credentials"}
)

#: Closed ``exposure_cause`` values, in S1's order.
EXPOSURE_CAUSES = ("ingress", "read", "child", "inbox", "recovery", "legacy_unknown")

OWNER_AUTHORITATIVE = frozenset({"owner", "owner_device"})
_PRINCIPAL = re.compile(r"owner|owner_device|world|(?:domain|external):[a-z0-9][a-z0-9_.-]{0,127}")
#: The same principal grammar, for the database CHECK functions.
PRINCIPAL_PATTERN = "^(owner|owner_device|world|(domain|external):[a-z0-9][a-z0-9_.-]{0,127})$"


class LabelRefused(ValueError):
    """A label that is missing, malformed or carries an unknown member."""


def _members(value: Any, *, categories: bool) -> frozenset[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise LabelRefused("label component must be a set of names")
    items = list(value)
    if any(type(item) is not str for item in items) or len(set(items)) != len(items):
        raise LabelRefused("label component must be a set of names")
    members = frozenset(items)
    if categories:
        if not members <= CATEGORIES:
            raise LabelRefused("unknown sensitivity category")
    elif not members or any(not _PRINCIPAL.fullmatch(item) for item in members):
        raise LabelRefused("unknown or missing principal")
    return members


@dataclass(frozen=True, slots=True)
class Label:
    instr: frozenset[str]
    data: frozenset[str]
    sens: frozenset[str]
    version: int = LABEL_VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != LABEL_VERSION:
            raise LabelRefused("unknown label version")
        object.__setattr__(self, "instr", _members(self.instr, categories=False))
        object.__setattr__(self, "data", _members(self.data, categories=False))
        object.__setattr__(self, "sens", _members(self.sens, categories=True))

    @classmethod
    def owner(cls) -> Label:
        return cls(frozenset({"owner"}), frozenset({"owner"}), frozenset())

    @classmethod
    def unknown(cls) -> Label:
        return cls(frozenset({"world"}), frozenset({"world"}), CATEGORIES)

    @classmethod
    def from_row(cls, version: Any, instr: Any, data: Any, sens: Any) -> Label:
        """Read a stored label; anything missing or unrecognised refuses."""
        if instr is None or data is None or sens is None:
            raise LabelRefused("missing label component")
        return cls(instr, data, sens, version)

    @property
    def exposed(self) -> bool:
        """D6 exposure: any provenance that is not owner-authoritative."""
        return bool((self.instr | self.data) - OWNER_AUTHORITATIVE)

    def join(self, other: Label) -> Label:
        return Label(self.instr | other.instr, self.data | other.data, self.sens | other.sens)

    def covers(self, other: Label) -> bool:
        return self.instr >= other.instr and self.data >= other.data and self.sens >= other.sens

    def to_row(self) -> tuple[int, list[str], list[str], list[str]]:
        return self.version, sorted(self.instr), sorted(self.data), sorted(self.sens)

    def canonical(self) -> dict[str, Any]:
        version, instr, data, sens = self.to_row()
        return {"v": version, "i": instr, "d": data, "c": sens}


def admission_label(exposed: bool) -> Label:
    """A goal's starting label from D5's server-held admission exposure.

    Unexposed admission means every input was owner-authoritative; exposed
    input is of unknown provenance and sensitivity.
    """
    return Label.unknown() if exposed else Label.owner()


def legacy_label(exposed: bool) -> Label:
    """A pre-label ledger row: keep its Boolean, assume every category."""
    provenance = frozenset({"world"}) if exposed else frozenset({"owner"})
    return Label(provenance, provenance, CATEGORIES)


def output_label(trust: str, stamped_owner_untainted: bool | None, categories) -> Label:
    """The label a read's output joins into its invocation (D6, S1).

    Trusted output and stamped output with the store's complete owner-untainted
    account are owner data (a stamped read then also joins each listed writer's
    label). Untrusted output is world. Stamped output without the account, or
    an unknown trust, is the unknown label: ``{world}`` and all six categories.
    """
    unaccounted = trust == "stamped" and stamped_owner_untainted is not True
    if unaccounted or trust not in ("trusted", "untrusted", "stamped"):
        return Label.unknown()
    provenance = frozenset({"world" if trust == "untrusted" else "owner"})
    return Label(provenance, provenance, frozenset(categories))


__all__ = [
    "CATEGORIES",
    "EXPOSURE_CAUSES",
    "LABEL_VERSION",
    "OWNER_AUTHORITATIVE",
    "PRINCIPAL_PATTERN",
    "Label",
    "LabelRefused",
    "admission_label",
    "legacy_label",
    "output_label",
]
