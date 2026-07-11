"""Shared matching/heuristics for email sender -> entity identity resolution.

Backing logic for two features (bu-qeaou):

1. ``roster/relationship/jobs/relationship_jobs.py::run_email_identity_enrichment``
   — a scheduled deterministic job that proposes entity creation/linking for
   recurring human email correspondents, surfaced through the approvals queue
   (``pending_actions``). Never writes a fact directly.
2. ``scripts/backfill_email_identity_facts.py`` — an operator-run backfill that
   links already-ingested email senders to entities that already exist under a
   matching name, writing ``has-email`` facts via the normal central-writer
   path (``relationship_assert_fact``), for unambiguous exact matches only.

Both consumers read ONLY ``public.ingestion_events`` (never
``switchboard.message_inbox`` — that table lives in the Switchboard butler's
own schema and is not reachable from another butler's schema-scoped DB role;
see CLAUDE.md's "Database Isolation" section). This means the raw ``From:``
header (with its human display name) is not recoverable here for historical
rows — only the normalized bare address survives in
``public.ingestion_events.source_sender_identity``. Display names are
therefore *derived* heuristically from the address local-part
(:func:`derive_display_name_from_address`) rather than read verbatim. This is
a deliberate scoping decision, not an oversight — see the module docstring of
``relationship_jobs.py``'s ``run_email_identity_enrichment`` for the full
rationale and the discovered-follow-up this implies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

import asyncpg

from butlers.identity import normalize_email_sender

# Bounded scan window: only consider ingestion activity from the last N days.
# Keeps the query cheap and keeps proposals grounded in *recent* recurring
# correspondence rather than resurrecting a years-old one-off contact.
DEFAULT_LOOKBACK_DAYS = 180

# Safety valve on the number of raw ingestion_events rows fetched per run.
# Bounds memory/latency regardless of table size; if the cap is hit the caller
# is told via EmailSenderStatsResult.truncated so it can log a visible warning
# rather than silently under-counting (craft-and-care: no silent caps).
DEFAULT_ROW_LIMIT = 20_000

# Local-part substrings that mark a sender as automated/bulk rather than a
# human correspondent. Deliberately conservative (a false negative just means
# a human reviewer sees one more proposal; a false positive means a real
# recurring human correspondent never gets proposed) but still a heuristic —
# every proposal this filter lets through is approval-gated regardless, so a
# stray miss here is not a fact silently written, only a candidate silently
# not considered.
_BULK_LOCAL_PART_RE = re.compile(
    r"no.?reply|do.?not.?reply|notifications?|notices?|notify|newsletter|newsletters?|"
    r"mailer.?daemon|postmaster|automated|automation|bounce|digest|updates?|alerts?|"
    r"billing|marketing|info|hello|support|noreply|receipts?|invoices?|welcome|"
    r"verif(?:y|ication)|confirm(?:ation)?",
    re.IGNORECASE,
)

# Domain labels that mark a sender as automated/bulk even when the local-part
# looks human (e.g. ``notice@email.anthropic.com``). Transactional and marketing
# mail is almost always sent from a dedicated sending subdomain — the leading
# label of the domain (``email.``, ``mail.``, ``e.``, ``mailer.``, ...) — or
# from a known email-service-provider domain. A real human's From address is
# the apex/registrable domain (``@anthropic.com``), never one of these sending
# subdomains, so matching the leading label carries a low false-positive risk.
_BULK_DOMAIN_LABEL_RE = re.compile(
    r"^(?:email|mail|mailer|mailing|e|em|mg|news|newsletter|notify|notifications?|"
    r"send|sender|smtp|bounce|bounces|reply|noreply|no-reply|marketing|mkt|mktg|"
    r"edm|campaigns?|click|links?|t)\.",
    re.IGNORECASE,
)

# Registrable domains of common email-service providers — mail from these is
# bulk/transactional regardless of the local-part or subdomain shape.
_BULK_ESP_DOMAIN_RE = re.compile(
    r"(?:^|\.)(?:sendgrid\.net|amazonses\.com|mailgun\.(?:org|net)|mcsv\.net|"
    r"mailchimpapp\.net|sparkpostmail\.com|mandrillapp\.com|postmarkapp\.com|"
    r"mailjet\.com|sendinblue\.com|sib\.email|rsgsv\.net)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EmailSenderStats:
    """Aggregated evidence for one normalized email sender address."""

    address: str
    event_count: int
    distinct_threads: int
    distinct_days: int
    first_seen: datetime
    last_seen: datetime


@dataclass(frozen=True)
class EmailSenderStatsResult:
    """Result of :func:`fetch_email_sender_stats` — candidates plus truncation flag."""

    stats: list[EmailSenderStats] = field(default_factory=list)
    truncated: bool = False


async def fetch_email_sender_stats(
    pool: asyncpg.Pool,
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    row_limit: int = DEFAULT_ROW_LIMIT,
) -> EmailSenderStatsResult:
    """Aggregate ``public.ingestion_events`` email traffic per normalized sender.

    Reads only ``public.ingestion_events`` (accessible to every butler role —
    see module docstring). Rows are normalized in Python via
    :func:`~butlers.identity.normalize_email_sender` before aggregation so that
    historical rows still holding a raw ``"Name <addr>"`` ``source_sender_identity``
    (pre bu-qeaou ingest-time normalization) group correctly with newer,
    already-bare rows for the same address.

    Returns
    -------
    EmailSenderStatsResult
        ``stats`` sorted by ``distinct_threads`` descending; ``truncated=True``
        when ``row_limit`` was hit (caller should log this — never silently
        under-count).
    """
    since = datetime.now(UTC) - timedelta(days=lookback_days)
    rows = await pool.fetch(
        """
        SELECT source_sender_identity, source_thread_identity, received_at
        FROM public.ingestion_events
        WHERE source_channel = 'email'
          AND status = 'ingested'
          AND source_sender_identity IS NOT NULL
          AND received_at >= $1
        ORDER BY received_at DESC
        LIMIT $2
        """,
        since,
        row_limit + 1,
    )

    truncated = len(rows) > row_limit
    if truncated:
        rows = rows[:row_limit]

    by_address: dict[str, dict] = {}
    for row in rows:
        address = normalize_email_sender(row["source_sender_identity"])
        if not address or "@" not in address:
            continue
        received_at = row["received_at"]
        thread_id = row["source_thread_identity"]

        bucket = by_address.setdefault(
            address,
            {
                "event_count": 0,
                "threads": set(),
                "days": set(),
                "first_seen": received_at,
                "last_seen": received_at,
            },
        )
        bucket["event_count"] += 1
        if thread_id:
            bucket["threads"].add(thread_id)
        bucket["days"].add(received_at.date())
        if received_at < bucket["first_seen"]:
            bucket["first_seen"] = received_at
        if received_at > bucket["last_seen"]:
            bucket["last_seen"] = received_at

    stats = [
        EmailSenderStats(
            address=address,
            event_count=b["event_count"],
            distinct_threads=len(b["threads"]),
            distinct_days=len(b["days"]),
            first_seen=b["first_seen"],
            last_seen=b["last_seen"],
        )
        for address, b in by_address.items()
    ]
    stats.sort(key=lambda s: s.distinct_threads, reverse=True)
    return EmailSenderStatsResult(stats=stats, truncated=truncated)


def is_bulk_or_noreply_address(address: str) -> bool:
    """Heuristic: does *address* look like an automated/bulk sender?

    Two independent signals, either of which flags the address:

    1. **Local-part** (before ``@``) against a conservative denylist of
       automated-sender substrings (``noreply``, ``notifications``, ``notice``,
       ``mailer-daemon``, ...).
    2. **Domain** — either a dedicated sending subdomain (leading label like
       ``email.``/``mail.``/``e.`` in ``notice@email.anthropic.com``) or a known
       email-service-provider registrable domain. This catches human-looking
       local-parts that are really transactional/marketing senders.

    See module docstring for the false-negative / false-positive tradeoff
    rationale.
    """
    local, _, domain = address.partition("@")
    if _BULK_LOCAL_PART_RE.search(local):
        return True
    if domain and (_BULK_DOMAIN_LABEL_RE.search(domain) or _BULK_ESP_DOMAIN_RE.search(domain)):
        return True
    return False


def derive_display_name_from_address(address: str) -> str:
    """Derive a best-effort human display name from an email local-part.

    ``public.ingestion_events`` does not retain the raw ``From:`` display name
    (see module docstring), so this is a heuristic placeholder — e.g.
    ``"john.doe"`` -> ``"John Doe"``. A human reviewer can rename the proposed
    entity after approval; this only needs to be a reasonable starting point,
    not a verified name.
    """
    local = address.split("@", 1)[0]
    raw_parts = re.split(r"[._+\-]+", local)
    # Strip embedded digits from each token (not just whole-digit tokens) —
    # disambiguation suffixes like "john.doe2" or "doe123" are common and the
    # digits are never part of a real name.
    parts = [cleaned for p in raw_parts if (cleaned := re.sub(r"\d+", "", p))]
    if not parts:
        return local
    return " ".join(p.capitalize() for p in parts)


async def fetch_active_has_email_addresses(
    pool: asyncpg.Pool,
    addresses: list[str],
) -> set[str]:
    """Return the subset of *addresses* that already have an active has-email fact.

    Bulk variant so a candidate scan issues one query, not one per address.
    """
    if not addresses:
        return set()
    rows = await pool.fetch(
        """
        SELECT DISTINCT object
        FROM relationship.entity_facts
        WHERE predicate = 'has-email'
          AND object_kind = 'literal'
          AND validity = 'active'
          AND object = ANY($1::text[])
        """,
        addresses,
    )
    return {row["object"] for row in rows}


async def match_existing_person_entity(
    pool: asyncpg.Pool,
    display_name: str,
) -> UUID | None:
    """Conservative name match against existing, real (non-placeholder) entities.

    Mirrors ``ContactBackfillResolver._match_name`` (``modules/contacts/backfill.py``):
    case-insensitive match against ``canonical_name`` or any alias. Excludes
    ``unidentified`` placeholder entities (auto-minted by switchboard ingress
    for unresolved senders — see ``identity.py::create_temp_contact``) and
    tombstoned/merged/deleted entities. Returns ``None`` on zero matches OR
    more than one match (ambiguous — never auto-link on a guess).
    """
    name_stripped = display_name.strip()
    if not name_stripped:
        return None
    rows = await pool.fetch(
        """
        SELECT id FROM public.entities
        WHERE entity_type = 'person'
          AND (metadata->>'unidentified') IS NULL
          AND (metadata->>'merged_into') IS NULL
          AND (metadata->>'deleted_at') IS NULL
          AND (
            canonical_name ILIKE $1
            OR EXISTS (SELECT 1 FROM unnest(aliases) AS a WHERE a ILIKE $1)
          )
        """,
        name_stripped,
    )
    if len(rows) != 1:
        return None
    return rows[0]["id"] if isinstance(rows[0]["id"], UUID) else UUID(str(rows[0]["id"]))


__all__ = [
    "DEFAULT_LOOKBACK_DAYS",
    "DEFAULT_ROW_LIMIT",
    "EmailSenderStats",
    "EmailSenderStatsResult",
    "derive_display_name_from_address",
    "fetch_active_has_email_addresses",
    "fetch_email_sender_stats",
    "is_bulk_or_noreply_address",
    "match_existing_person_entity",
]
