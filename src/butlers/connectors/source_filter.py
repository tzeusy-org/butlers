"""Shared source-filter evaluation module for all connectors.

Provides SourceFilterEvaluator — a TTL-cached, DB-backed filter engine that
gates incoming messages against named blacklist/whitelist filters registered in
the ``source_filters`` / ``connector_source_filters`` tables.

Filter composition rules
------------------------
1. No active filters → allow (reason='no_filters').
2. Blacklists first (priority ASC): first pattern match → block
   (reason='blacklist_match:<filter_name>').
3. Whitelists next: if any whitelist filter is active AND the key matches none
   of them → block (reason='whitelist_no_match').
4. Otherwise → allow (reason='passed').

Pattern matching per source_key_type
-------------------------------------
- ``domain``:         extract domain from normalised e-mail, exact or suffix
                      match (sub.example.com matches example.com pattern).
- ``sender_address``: normalise full address, exact match.
- ``substring``:      case-insensitive substring search in raw key_value.
- ``chat_id``:        exact string equality against str(chat_id).
- Unknown type:       skip filter with one-time WARNING per filter_id.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from prometheus_client import Counter

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prometheus counter
# ---------------------------------------------------------------------------

_source_filter_counter = Counter(
    "butlers_connector_source_filter_total",
    "Count of messages evaluated by connector source filter",
    labelnames=["endpoint_identity", "action", "filter_name", "reason"],
)

# ---------------------------------------------------------------------------
# Public data structures
# ---------------------------------------------------------------------------

_ANGLE_BRACKET_RE = re.compile(r"<([^>]+)>")


@dataclass(frozen=True)
class SourceFilterSpec:
    """A single named filter loaded from the database."""

    id: str
    """UUID of the filter row in source_filters."""
    name: str
    """Human-readable filter name."""
    filter_mode: str
    """'blacklist' or 'whitelist'."""
    source_key_type: str
    """'domain', 'sender_address', 'substring', or 'chat_id'."""
    patterns: list[str]
    """List of pattern strings to match against."""
    priority: int
    """Lower value = evaluated first."""


@dataclass(frozen=True)
class FilterResult:
    """Result of a single evaluate() call."""

    allowed: bool
    """True if the message is allowed through; False if blocked."""
    reason: str
    """Short machine-readable reason string."""
    filter_name: str | None
    """Name of the filter that triggered the decision (None when no filters active)."""


# ---------------------------------------------------------------------------
# Key-extraction helpers
# ---------------------------------------------------------------------------


def _normalize_email(address: str) -> str:
    """Normalise an e-mail address: strip whitespace, angle brackets, lowercase."""
    address = address.strip()
    m = _ANGLE_BRACKET_RE.search(address)
    if m:
        address = m.group(1).strip()
    return address.lower()


def _extract_domain(from_header: str) -> str:
    """Extract the domain part from a normalised From header value.

    Returns the part after '@', lowercased.  Returns '' if no '@' present.
    """
    norm = _normalize_email(from_header)
    if "@" in norm:
        return norm.split("@", 1)[1]
    return ""


def _extract_sender_address(from_header: str) -> str:
    """Return the normalised e-mail address from a From header."""
    return _normalize_email(from_header)


# ---------------------------------------------------------------------------
# Pattern matching
# ---------------------------------------------------------------------------


def _matches_pattern(
    key_value: str,
    pattern: str,
    source_key_type: str,
) -> bool:
    """Test whether *key_value* matches *pattern* according to *source_key_type*.

    No exceptions are raised; errors return False.
    """
    if source_key_type == "domain":
        pattern_lower = pattern.strip().lower()
        domain_lower = key_value.lower()
        return domain_lower == pattern_lower or domain_lower.endswith(f".{pattern_lower}")

    if source_key_type == "sender_address":
        return key_value.lower() == pattern.strip().lower()

    if source_key_type == "substring":
        return pattern.lower() in key_value.lower()

    if source_key_type == "chat_id":
        return str(key_value) == str(pattern).strip()

    return False


# ---------------------------------------------------------------------------
# SourceFilterEvaluator
# ---------------------------------------------------------------------------


class SourceFilterEvaluator:
    """TTL-cached, DB-backed filter evaluator for a single connector endpoint.

    Parameters
    ----------
    connector_type:
        Connector type string (e.g. ``'gmail'``).
    endpoint_identity:
        Endpoint identity string (e.g. ``'gmail:user:alice@example.com'``).
    db_pool:
        asyncpg connection pool used for filter loading.  May be ``None`` — if
        so, ``_load_filters()`` will skip and leave the cache empty (fail-open).
    refresh_interval_s:
        Seconds between TTL cache refreshes (default 300 = 5 min).
    """

    def __init__(
        self,
        connector_type: str,
        endpoint_identity: str,
        db_pool: asyncpg.Pool | None,
        refresh_interval_s: float = 300,
    ) -> None:
        self._connector_type = connector_type
        self._endpoint_identity = endpoint_identity
        self._db_pool = db_pool
        self._refresh_interval_s = refresh_interval_s

        self._filters: list[SourceFilterSpec] = []
        self._last_loaded_at: float | None = None
        self._load_lock = asyncio.Lock()
        self._background_refresh_task: asyncio.Task[None] | None = None
        # Per-evaluator set of filter_ids for which we have already emitted a
        # one-time warning about an unknown source_key_type.
        self._warned_unknown_key_type: set[str] = set()

    # ------------------------------------------------------------------
    # Internal: DB load
    # ------------------------------------------------------------------

    async def _load_filters(self) -> None:
        """Query DB for active filters assigned to this connector endpoint.

        On DB error: log WARNING and retain the previous cache (fail-open).
        """
        if self._db_pool is None:
            logger.debug(
                "source_filter: no DB pool available for %s; skipping filter load",
                self._endpoint_identity,
            )
            self._last_loaded_at = time.monotonic()
            return

        query = """
            SELECT
                sf.id::text        AS id,
                sf.name            AS name,
                sf.filter_mode     AS filter_mode,
                sf.source_key_type AS source_key_type,
                sf.patterns        AS patterns,
                csf.priority       AS priority
            FROM connector_source_filters csf
            JOIN source_filters sf ON sf.id = csf.filter_id
            WHERE csf.connector_type = $1
              AND csf.endpoint_identity = $2
              AND csf.enabled = true
            ORDER BY csf.priority ASC
        """
        try:
            rows = await self._db_pool.fetch(query, self._connector_type, self._endpoint_identity)
            new_filters: list[SourceFilterSpec] = []
            for row in rows:
                patterns: list[str] = list(row["patterns"]) if row["patterns"] else []
                spec = SourceFilterSpec(
                    id=str(row["id"]),
                    name=str(row["name"]),
                    filter_mode=str(row["filter_mode"]),
                    source_key_type=str(row["source_key_type"]),
                    patterns=patterns,
                    priority=int(row["priority"]),
                )
                new_filters.append(spec)

            self._filters = new_filters
            self._last_loaded_at = time.monotonic()
            logger.debug(
                "source_filter: loaded %d filter(s) for %s",
                len(new_filters),
                self._endpoint_identity,
            )
        except Exception as exc:
            logger.warning(
                "source_filter: failed to load filters for %s (retaining cache): %s",
                self._endpoint_identity,
                exc,
            )
            # Retain previous cache; update timestamp so we don't hammer the DB
            # on every single evaluate() call when the DB is down.
            self._last_loaded_at = time.monotonic()

    # ------------------------------------------------------------------
    # Public: startup load
    # ------------------------------------------------------------------

    async def ensure_loaded(self) -> None:
        """Perform the initial filter load.

        Must be called once before the ingestion loop begins.  Subsequent
        refreshes are triggered lazily from ``evaluate()`` via a background task.
        """
        async with self._load_lock:
            if self._last_loaded_at is None:
                await self._load_filters()

    # ------------------------------------------------------------------
    # Public: evaluate
    # ------------------------------------------------------------------

    def evaluate(self, from_header: str) -> FilterResult:
        """Evaluate *from_header* against the active filter cache.

        Triggers a background TTL refresh if the cache is stale, but does
        **not** await it — callers always receive a response from the current
        cache without blocking.

        Parameters
        ----------
        from_header:
            Raw value of the ``From`` message header (e.g. the Gmail
            ``From`` header).  The evaluator extracts the appropriate key for
            each filter's ``source_key_type`` internally, so domain filters
            receive just the domain and sender_address filters receive the
            normalised address.

        Returns
        -------
        FilterResult
        """
        # TTL refresh: if cache is stale, kick off background reload
        self._maybe_schedule_refresh()

        filters = self._filters

        # Rule 1: no active filters → allow
        if not filters:
            result = FilterResult(allowed=True, reason="no_filters", filter_name=None)
            _source_filter_counter.labels(
                endpoint_identity=self._endpoint_identity,
                action="allowed",
                filter_name="",
                reason="no_filters",
            ).inc()
            return result

        # Rule 2: blacklists first (already sorted by priority ASC from DB)
        for spec in filters:
            if spec.filter_mode != "blacklist":
                continue
            if not self._filter_matches(spec, from_header):
                continue
            # Pattern matched a blacklist → block
            result = FilterResult(
                allowed=False,
                reason=f"blacklist_match:{spec.name}",
                filter_name=spec.name,
            )
            _source_filter_counter.labels(
                endpoint_identity=self._endpoint_identity,
                action="blocked",
                filter_name=spec.name,
                reason=f"blacklist_match:{spec.name}",
            ).inc()
            return result

        # Rule 3: whitelists
        whitelist_filters = [s for s in filters if s.filter_mode == "whitelist"]
        if whitelist_filters:
            matched_any = any(self._filter_matches(s, from_header) for s in whitelist_filters)
            if not matched_any:
                # Active whitelist(s) but no match → block
                result = FilterResult(
                    allowed=False,
                    reason="whitelist_no_match",
                    filter_name=None,
                )
                _source_filter_counter.labels(
                    endpoint_identity=self._endpoint_identity,
                    action="blocked",
                    filter_name="",
                    reason="whitelist_no_match",
                ).inc()
                return result

        # Rule 4: passed all checks → allow
        result = FilterResult(allowed=True, reason="passed", filter_name=None)
        _source_filter_counter.labels(
            endpoint_identity=self._endpoint_identity,
            action="allowed",
            filter_name="",
            reason="passed",
        ).inc()
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _filter_matches(self, spec: SourceFilterSpec, from_header: str) -> bool:
        """Return True if *from_header* matches any pattern in *spec*.

        The key appropriate for each filter's ``source_key_type`` is extracted
        here so that domain filters receive the domain portion and
        sender_address filters receive the normalised address.
        """
        key_type = spec.source_key_type
        if key_type not in ("domain", "sender_address", "substring", "chat_id"):
            if spec.id not in self._warned_unknown_key_type:
                logger.warning(
                    "source_filter: unknown source_key_type %r for filter %r (id=%s); skipping",
                    key_type,
                    spec.name,
                    spec.id,
                )
                self._warned_unknown_key_type.add(spec.id)
            return False

        # Extract the key appropriate for this filter's type from the raw header.
        key_value = extract_gmail_filter_key(from_header, key_type)
        return any(_matches_pattern(key_value, pat, key_type) for pat in spec.patterns)

    def _maybe_schedule_refresh(self) -> None:
        """Schedule a background cache refresh if the TTL has elapsed."""
        if self._last_loaded_at is None:
            return
        elapsed = time.monotonic() - self._last_loaded_at
        if elapsed < self._refresh_interval_s:
            return
        # Don't stack up multiple refresh tasks
        if self._background_refresh_task is not None and not self._background_refresh_task.done():
            return
        self._background_refresh_task = asyncio.create_task(self._load_filters())


# ---------------------------------------------------------------------------
# Gmail filter key extraction helper
# ---------------------------------------------------------------------------


def extract_gmail_filter_key(from_header: str, key_type: str) -> str:
    """Extract the filter key from a Gmail From header for the given key_type.

    Parameters
    ----------
    from_header:
        Raw value of the ``From`` message header.
    key_type:
        One of ``'domain'``, ``'sender_address'``, ``'substring'``.

    Returns
    -------
    Extracted key string.  For unknown key_type, the raw header is returned.
    """
    if key_type == "domain":
        return _extract_domain(from_header)
    if key_type == "sender_address":
        return _extract_sender_address(from_header)
    # substring: return verbatim
    return from_header


# ---------------------------------------------------------------------------
# Telegram filter key extraction helper
# ---------------------------------------------------------------------------


def extract_telegram_filter_key(update: dict, key_type: str) -> str:
    """Extract the filter key from a Telegram update for the given key_type.

    Parameters
    ----------
    update:
        Raw Telegram update dict (as returned by getUpdates or POSTed by webhook).
    key_type:
        Only ``'chat_id'`` is valid for the Telegram connector.  All other
        key_types return an empty string so that the evaluator's unknown-type
        WARNING is emitted and the filter is skipped.

    Returns
    -------
    str
        The chat_id as a string (e.g. ``'987654321'`` or ``'-100987654321'``),
        or ``''`` if the key_type is unsupported or no message is found.
    """
    if key_type != "chat_id":
        return ""

    for msg_key in ("message", "edited_message", "channel_post"):
        msg = update.get(msg_key)
        if isinstance(msg, dict):
            chat = msg.get("chat")
            if isinstance(chat, dict) and "id" in chat:
                return str(chat["id"])
    return ""


__all__ = [
    "SourceFilterSpec",
    "FilterResult",
    "SourceFilterEvaluator",
    "extract_gmail_filter_key",
    "extract_telegram_filter_key",
]
