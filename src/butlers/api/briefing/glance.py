"""Deterministic glance payload for ambient projection surfaces.

compose_glance(state, ...) -> dict
    One expiring "one thing" payload, classified from the SAME composed attention
    model the dashboard briefing uses (``classify``), with no LLM call.
glance_text / glance_png
    The same payload as plain text or a 1-bit PNG, each printing ``valid_until``.
GlanceCache
    Per-grant cache of the composed payload, so format fetches share one composition.

Spec: openspec/changes/add-ambient-projection-grants (REQ-ambient-projections-003, -005).
"""

from __future__ import annotations

import io
import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from butlers.api.briefing.classify import StateClass, classify

logger = logging.getLogger(__name__)

# Every state source _fetch_dashboard_state reads, in its degraded_sources vocabulary.
GLANCE_SOURCES = ("audit", "board", "approvals", "notifications", "qa", "projections")
LINE_MAX = 48

_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}

# Household surfaces never carry free text from an attention item: an item's
# description can name a person, a health fact or an amount. Only its kind is shown.
_HOUSEHOLD_LABELS = {
    "approval": "An approval is waiting",
    "notification": "A notification failed",
    "qa": "System check needs a look",
    "runtime": "A butler needs a look",
    "scheduled_task_failure": "A scheduled task failed",
    "audit_error_group": "A butler reported an error",
    "projection": "A display stopped updating",
}
_HOUSEHOLD_FALLBACK_LABEL = "Something needs a look"


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def glance_line(state_class: StateClass, state: dict) -> str:
    """Return a one-line summary of at most ``LINE_MAX`` characters."""
    items = state.get("attention_items", [])
    high = sum(1 for item in items if item.get("severity") == "high")
    if state_class == "urgent":
        line = f"{high} {_plural(high, 'thing needs', 'things need')} you now"
    elif state_class in {"busy", "mild"}:
        line = f"{len(items)} {_plural(len(items), 'item', 'items')} waiting"
    elif state_class == "degraded-quiet":
        down = sum(
            1 for b in state.get("butler_statuses", []) if b.get("status") in ("degraded", "error")
        )
        line = f"Quiet, but {down} {_plural(down, 'butler is', 'butlers are')} degraded"
    elif state_class == "degraded":
        line = "Some sources unreachable; may be incomplete"
    else:
        line = "Everything is in hand"
    return line[:LINE_MAX]


def _top_item(items: list[dict], audience_class: str) -> dict | None:
    if not items:
        return None
    ranked = sorted(
        enumerate(items),
        key=lambda pair: (_SEVERITY_RANK.get(str(pair[1].get("severity")), 3), pair[0]),
    )
    item = ranked[0][1]
    kind = str(item.get("type") or item.get("source") or "other")
    if audience_class == "household":
        return {
            "kind": kind if kind in _HOUSEHOLD_LABELS else "other",
            "label": _HOUSEHOLD_LABELS.get(kind, _HOUSEHOLD_FALLBACK_LABEL),
            "door_url": None,
        }
    label = str(item.get("description") or kind)
    return {"kind": kind, "label": label[:120], "door_url": item.get("link")}


def _utc_minute(value: str) -> str:
    return datetime.fromisoformat(value).astimezone(UTC).strftime("%Y-%m-%d %H:%M")


def glance_lines(glance: dict) -> list[str]:
    """Return the text glance, one fact per line, ending with its printed expiry.

    The text and PNG formats both render exactly these lines, so a dumb surface
    that cannot parse JSON still shows when its picture stops being true.
    """
    lines = [glance["line"]]
    top = glance.get("top_item")
    if top:
        lines.append(f"Top: {top['label']}")
    lines.append(f"Needs you: {glance['needs_you']}")
    failed = glance.get("coverage", {}).get("failed") or []
    if failed:
        lines.append(f"Unreachable: {', '.join(failed)}")
    lines.append(f"As of {_utc_minute(glance['as_of'])} UTC")
    lines.append(f"Valid until {_utc_minute(glance['valid_until'])} UTC")
    return lines


def glance_text(glance: dict) -> str:
    return "\n".join(glance_lines(glance)) + "\n"


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: Any, width: int) -> list[str]:
    rows: list[str] = []
    row = ""
    for word in text.split():
        candidate = f"{row} {word}".strip()
        if row and draw.textlength(candidate, font=font) > width:
            rows.append(row)
            row = word
        else:
            row = candidate
    return [*rows, row] if row else rows


def glance_png(glance: dict, width: int, height: int) -> bytes:
    """Render the glance lines as a 1-bit PNG for an e-ink or low-colour panel.

    The headline is set larger and the remaining lines follow it; ``Valid until``
    sits on the bottom edge. Rendering is deterministic for the same glance and size.
    """
    image = Image.new("1", (width, height), color=1)
    draw = ImageDraw.Draw(image)
    margin = max(width // 40, 4)
    head_size, body_size = max(height // 9, 10), max(height // 16, 8)
    head = ImageFont.load_default(size=head_size)
    body = ImageFont.load_default(size=body_size)
    *lines, expiry = glance_lines(glance)
    # The expiry is pinned to the bottom edge, shrunk until it fits the width; lines
    # that would reach it are dropped, so overflowing text never clips or overprints it.
    expiry_size = body_size
    expiry_font = body
    while expiry_size > 6 and draw.textlength(expiry, font=expiry_font) > width - 2 * margin:
        expiry_size -= 1
        expiry_font = ImageFont.load_default(size=expiry_size)
    expiry_y = height - margin - int(expiry_size * 1.25)
    draw.text((margin, expiry_y), expiry, fill=0, font=expiry_font)
    y = margin
    for index, line in enumerate(lines):
        font, leading = (
            (head, int(head_size * 1.25)) if index == 0 else (body, int(body_size * 1.25))
        )
        for row in _wrap(draw, line, font, width - 2 * margin):
            if y + leading > expiry_y:
                break
            draw.text((margin, y), row, fill=0, font=font)
            y += leading
        if index == 0:
            y += margin
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


# A grant fetch reuses one composition for this long. Every surface interval is at
# least a minute, so a surface always sees a picture under half its interval old,
# while retries and a burst of format fetches do not fan out to every state source.
GLANCE_CACHE_SECONDS = 30.0


class GlanceCache:
    """Per-grant cache of the composed glance dict (in process, reset on restart).

    Revoked or unknown grants never reach the cache: the auth boundary answers
    404 before the handler runs. Owner previews bypass it.
    """

    def __init__(
        self,
        ttl_seconds: float = GLANCE_CACHE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._entries: dict[uuid.UUID, tuple[float, dict]] = {}

    def get(self, grant_id: uuid.UUID) -> dict | None:
        entry = self._entries.get(grant_id)
        if entry is None:
            return None
        if self._clock() >= entry[0]:
            del self._entries[grant_id]
            return None
        return entry[1]

    def put(self, grant_id: uuid.UUID, glance: dict) -> None:
        now = self._clock()
        # Drop expired entries so grants that stop fetching do not accumulate.
        self._entries = {k: v for k, v in self._entries.items() if v[0] > now}
        self._entries[grant_id] = (now + self._ttl, glance)


def compose_glance(
    state: dict, *, audience_class: str, as_of: datetime, refresh: timedelta
) -> dict:
    """Compose the glance payload; never raises for a malformed classification.

    ``valid_until`` is ``as_of + 2 * refresh``: a surface that stops refreshing
    shows a visibly expired picture instead of a stale one wearing current authority.
    """
    try:
        state_class = classify(state)
    except Exception as exc:
        # Mirrors the briefing: a classifier failure is itself a swallowed failure.
        logger.error("Glance classification failed, defaulting to degraded: %s", exc)
        state_class = "degraded"
    failed = [source for source in GLANCE_SOURCES if source in state.get("degraded_sources", [])]
    items = state.get("attention_items", [])
    return {
        "state_class": state_class,
        "line": glance_line(state_class, state),
        "needs_you": len(items),
        "top_item": _top_item(items, audience_class),
        "as_of": as_of.isoformat(),
        "valid_until": (as_of + 2 * refresh).isoformat(),
        "coverage": {
            "ok": [source for source in GLANCE_SOURCES if source not in failed],
            "failed": failed,
            "stale": [],
        },
        "audience_class": audience_class,
    }
