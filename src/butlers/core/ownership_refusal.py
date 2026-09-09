"""Ownership refusal for capture() -- a firm boundary, not a judgment about shape.

General's MANIFESTO amendment (bu-2jtfw.9) narrows "no judgment, no
limitations" to shape only: content that plainly belongs to another
butler's owned domain is refused, naming that butler and the tool to use,
rather than silently absorbed into a General collection.

This module is intentionally pure (no DB, no network) so it can run inline
inside ``capture()`` without a dependency on any other butler being
reachable. The map is deliberately small and precision-biased: a false
refusal (declining content that was actually fine for General to hold) is
worse than an occasional false negative, since a refusal is a hard stop the
caller must react to.

Coverage note: the design's surface map names four kinds -- Finance,
Lifestyle, Relationship, Home. Lifestyle is omitted here: at the time this
was written, the Lifestyle butler (``roster/lifestyle``) registers no MCP
tools at all (no ``tools/`` or ``modules/`` directory), so there is no real
tool this module could honestly name as "the tool to use." Refusing
Lifestyle-shaped content without a citable tool would violate the contract
this module exists to uphold. Revisit once Lifestyle exposes a write tool.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class OwnershipRefusal:
    """A matched ownership boundary: who owns this content, and how to write it."""

    kind: str
    owner_butler: str
    tool_hint: str

    @property
    def reason(self) -> str:
        return (
            f"{self.owner_butler} owns {self.kind.replace('_', ' ')} content -- "
            f"use {self.tool_hint} instead of capturing it into General."
        )


@dataclass(frozen=True)
class _RefusalRule:
    kind: str
    owner_butler: str
    tool_hint: str
    patterns: tuple[re.Pattern[str], ...]


def _compile(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


_RULES: tuple[_RefusalRule, ...] = (
    _RefusalRule(
        kind="bank_transaction",
        owner_butler="Finance",
        tool_hint="finance.record_transaction",
        patterns=_compile(
            r"\bbank\b",
            r"\btransaction\b",
            r"\bwithdrawal\b",
            r"\bdeposit\b",
            r"\bcredit card\b",
            r"\bdebit card\b",
            r"\baccount balance\b",
            r"\bbank statement\b",
            r"\batm\b",
            r"\bcharged \$",
            r"\$\d",
        ),
    ),
    _RefusalRule(
        kind="contact_fact",
        owner_butler="Relationship",
        tool_hint="relationship.fact_set",
        patterns=_compile(
            r"\b(his|her|their)\s+birthday\b",
            r"\banniversary with\b",
            r"\bremember that [A-Z][a-z]+ (likes|prefers|hates|is allergic to)\b",
        ),
    ),
    _RefusalRule(
        kind="home_maintenance",
        owner_butler="Home",
        tool_hint="home.ha_maintenance_create",
        patterns=_compile(
            r"\bfurnace filter\b",
            r"\bhvac\b",
            r"\bthermostat\b",
            r"\bhome maintenance\b",
            r"\bappliance (needs|due for) (service|maintenance)\b",
        ),
    ),
)


def check_ownership_refusal(content: str) -> OwnershipRefusal | None:
    """Return the matched ownership boundary for ``content``, or ``None``.

    Checks rules in declaration order and returns the first match. Content
    that plainly reads as several owners' domains at once still only returns
    one refusal -- the caller reacts to one boundary at a time.
    """
    for rule in _RULES:
        if any(pattern.search(content) for pattern in rule.patterns):
            return OwnershipRefusal(
                kind=rule.kind, owner_butler=rule.owner_butler, tool_hint=rule.tool_hint
            )
    return None
