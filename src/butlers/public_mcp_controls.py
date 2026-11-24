"""Owner kill switches for the Public MCP read surface (RFC 0039 D16, bu-y66o00.9).

One contract for the two owner entry points, the dashboard API route and
``butlers public-mcp disable|enable``: the closed domain list, the single write statement,
and the actors they record. The control table is ``mcp_surface.controls`` on the primary. Its
owner is the migration principal, which is also the dashboard API login and the CLI login, so
the write needs no grant beyond table ownership and no definer function: nothing else can
write it (butler runtime roles hold at most SELECT, and only Relationship).

The row replicates to the projection, where the public server reads it, so a change takes
effect there within the replication window. The Relationship proposal importer reads the
primary at its next pass. ``changed_by`` is private to the primary and is never replicated.

Disabling and re-enabling are each one statement with no unlock step. A write that changes
nothing leaves ``changed_at`` and ``changed_by`` as they were, so the row keeps reporting the
real last change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: ``all`` first, then the seven data domains in the order the page lists them.
ALL = "all"
DATA_DOMAINS: tuple[str, ...] = (
    "knowledge",
    "relationship",
    "time",
    "travel",
    "finance",
    "health",
    "proposals",
)
CONTROL_DOMAINS: tuple[str, ...] = (ALL, *DATA_DOMAINS)

State = Literal["enabled", "disabled"]
STATES: tuple[State, ...] = ("enabled", "disabled")

#: Actors recorded in ``changed_by``. The dashboard owner session is the only browser actor.
ACTOR_DASHBOARD = "owner:dashboard"
ACTOR_CLI = "owner:cli"

#: Replication reaches the public server within this many minutes (RFC 0039 D16).
REPLICATION_WINDOW_MINUTES = 15
BREAK_GLASS_CONFIGMAP = "butlers-public-mcp-switches"
BREAK_GLASS_KEY = "disabled_domains"
BREAK_GLASS_REREAD_SECONDS = 10

LIST_SQL = (
    "SELECT domain, state, changed_at, changed_by FROM mcp_surface.controls WHERE domain = ANY({0})"
)

#: Upsert so a missing seed row can still be disabled in an incident. The WHERE keeps the
#: recorded time and actor when the state is already what was asked for (no row returned).
WRITE_SQL = """
INSERT INTO mcp_surface.controls AS c (domain, state, changed_at, changed_by)
VALUES ({domain}, {state}, clock_timestamp(), {actor})
ON CONFLICT (domain) DO UPDATE
   SET state = EXCLUDED.state, changed_at = EXCLUDED.changed_at, changed_by = EXCLUDED.changed_by
 WHERE c.state IS DISTINCT FROM EXCLUDED.state
RETURNING domain, state, changed_at, changed_by
"""

READ_ONE_SQL = (
    "SELECT domain, state, changed_at, changed_by FROM mcp_surface.controls WHERE domain = {domain}"
)


class UnknownDomain(ValueError):
    """The domain is not one of the eight control rows."""


def check_domain(domain: str) -> str:
    """The domain unchanged, or :class:`UnknownDomain`; nothing else may name a row."""
    if domain not in CONTROL_DOMAINS:
        raise UnknownDomain(domain)
    return domain


@dataclass(frozen=True)
class Summary:
    disabled_domains: int
    total_domains: int
    all_disabled: bool


def summarize(states: dict[str, str]) -> Summary:
    """How many of the seven data domains are disabled, and whether ``all`` is.

    A domain with no row counts as neither: the summary reports only what the table says.
    """
    return Summary(
        disabled_domains=sum(1 for d in DATA_DOMAINS if states.get(d) == "disabled"),
        total_domains=len(DATA_DOMAINS),
        all_disabled=states.get(ALL) == "disabled",
    )
