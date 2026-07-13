"""Pydantic models for the attention-ledger reader API.

Maps to ``public.attention_ledger`` (migration ``core_160``) -- see
``butlers.core.attention_ledger`` for the writer, the closed
source/outcome vocabulary, and priority normalization. This module is the
ledger's first reader (bu-tdd4k.4, JARVIS pursuit move 8 slice 5): before
this, `grep attention_ledger src/butlers/api` returned zero matches -- every
suppressed/deferred/delivered decision was durably recorded and never read
back, so a source that was silently failing (e.g. secrets_lifecycle showing
120 suppressed / 0 delivered, bu-tdd4k.2) had no surface that would say so.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from butlers.api.models import PaginatedResponse


class AttentionLedgerEntry(BaseModel):
    """One row of ``public.attention_ledger``."""

    id: UUID
    occurred_at: datetime
    origin_butler: str
    source: str
    channel: str | None = None
    intent: str | None = None
    priority_label: str | None = None
    priority_score: int | None = None
    dedup_key: str | None = None
    outcome: str
    reason: str | None = None
    notification_ref: str | None = None
    metadata: dict | None = None


class AttentionLedgerListResponse(PaginatedResponse[AttentionLedgerEntry]):
    """Paginated ledger list, plus a source-availability flag.

    ``source_available=False`` means the ledger's DB pool was unreachable --
    an empty/short page in that case is never a truthful "no matching rows"
    result (mirrors the repo's ``aggregates_available`` degraded-mode
    convention).
    """

    source_available: bool = True


class AttentionSourceSummary(BaseModel):
    """Delivery-vs-suppression counts for one ``origin_butler`` over a window.

    "Source" here means ``origin_butler`` (e.g. ``secrets_lifecycle``,
    ``home``, ``finance``) -- the meaningful trust dimension for a Trust
    Console panel -- not the ledger's own ``source`` column (the
    ``notify``/``insight``/``discretion`` choke-point literal, which is a
    separate filter on the list endpoint). See the governing spec amendment
    for this naming decision.
    """

    origin_butler: str
    delivered: int
    coalesced: int
    deferred: int
    suppressed: int
    # Genuine terminal failures (no recipient, transport/delivery error, an
    # unexpected exception) -- bu-hmdqz.3. Distinct from ``deferred``, which
    # is reserved for a benign hold (quiet hours, coalescing) that resolves
    # on its own; a "failed" row is only retried if the caller explicitly
    # enqueued a retry envelope.
    failed: int
    total: int
    # The marquee signal this endpoint exists for: a source with
    # suppressed > 0 and delivered == 0 over the window is silently failing
    # -- every egress attempt is being gated shut, never confirmed reaching
    # the owner. This is the exact live failure this epic fixed for
    # secrets_lifecycle (120 suppressed / 0 delivered, bu-tdd4k.2).
    suppressed_never_delivered: bool


class AttentionLedgerSummaryResponse(BaseModel):
    """Per-source (per-``origin_butler``) delivery-vs-suppression rollup."""

    since: datetime | None
    until: datetime | None
    by_source: list[AttentionSourceSummary]
    # Convenience projection of by_source's origin_butler names for a FE that
    # only needs to render the loud banner without re-deriving the filter.
    flagged_sources: list[str]
    # False when the ledger's DB pool was unreachable -- all counts above are
    # empty in that case, never a truthful "every source is healthy".
    source_available: bool = True
