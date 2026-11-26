"""What a schedule row is anchored to (bu-5ii4gj.5 P6).

One predicate for every reader. A row that declares ``anchor_kind`` is read as declared; a row that
does not (NULL: nothing is backfilled, so this stays live) is derived from its columns. Writers
that change a row's timezone, cron, task type or until_at clear a declaration. Two CHECKs on the
table keep a declared kind and the ``timezone`` column the scheduler evaluates in from disagreeing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

AnchorKind = Literal["owner_local", "zone_pinned", "absolute", "interval"]
KINDS: frozenset[str] = frozenset({"owner_local", "zone_pinned", "absolute", "interval"})
#: A window that closes this soon after the next run is a one-shot (the ``remind`` shape).
ONE_SHOT_WINDOW = timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class Anchor:
    kind: AnchorKind
    #: The pinned zone of a ``zone_pinned`` row, else ``None``.
    zone: str | None = None


def _is_sentinel(timezone: str | None) -> bool:
    return (timezone or "").strip().upper() in {"", "UTC"}


def effective_anchor(row: Mapping[str, Any]) -> Anchor:
    """The anchor of one ``scheduled_tasks`` row: declared, else derived from the row."""
    declared = row.get("anchor_kind")
    # Defence in depth: a recurring-wall-clock declaration cannot survive on a row that has no cron
    # or is not a cron task (a writer that missed the clear), so it is derived instead.
    recurring = bool(row.get("cron")) and (row.get("task_type") or "cron") == "cron"
    if declared in {"owner_local", "zone_pinned"} and not recurring:
        declared = None
    if declared in KINDS:
        zone = row.get("anchor_zone") if declared == "zone_pinned" else None
        return Anchor(declared, zone)
    until_at, next_run_at = row.get("until_at"), row.get("next_run_at")
    one_shot = (
        until_at is not None
        and next_run_at is not None
        and until_at <= next_run_at + ONE_SHOT_WINDOW
    )
    if not row.get("cron") or (row.get("task_type") or "cron") != "cron" or one_shot:
        return Anchor("absolute")
    timezone = row.get("timezone")
    if _is_sentinel(timezone):
        return Anchor("owner_local")
    return Anchor("zone_pinned", (timezone or "").strip())


def resolve_declared_anchor(
    kind: str | None, zone: str | None, timezone: str | None
) -> tuple[str | None, str | None, str | None]:
    """Validate a creator's declared anchor; returns ``(kind, zone, timezone)`` to store.

    ``kind=None`` declares nothing (the row is derived at read) and a stray ``zone`` is refused.
    ``owner_local`` needs the sentinel timezone, ``zone_pinned`` needs a real IANA zone (spell a UTC
    pin ``Etc/UTC``, since the literal ``UTC`` is the follow-the-owner sentinel) and stores it as
    the row's ``timezone``, which is what the scheduler evaluates in.
    """
    if kind is None:
        if zone is not None:
            raise ValueError("anchor_zone needs an anchor_kind")
        return None, None, timezone
    if kind not in KINDS:
        raise ValueError(f"anchor_kind must be one of {sorted(KINDS)} (got {kind!r})")
    if kind != "zone_pinned":
        if zone is not None:
            raise ValueError(f"anchor_zone is only for zone_pinned (got it with {kind!r})")
        if kind == "owner_local" and not _is_sentinel(timezone):
            raise ValueError("an owner_local schedule cannot also name a timezone")
        return kind, None, timezone
    name = (zone or "").strip()
    if not name or name.upper() == "UTC":
        raise ValueError("a zone_pinned schedule needs an IANA anchor_zone (use Etc/UTC for UTC)")
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown anchor_zone {name!r}") from exc
    if timezone is not None and timezone.strip() != name:
        raise ValueError("a zone_pinned schedule's timezone must equal its anchor_zone")
    return kind, name, name
