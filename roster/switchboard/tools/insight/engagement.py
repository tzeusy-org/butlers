"""Owner-ingress engagement detection for proactive insights."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


async def record_owner_ingress_engagement(
    pool: Any,
    *,
    source: str | None,
    sender_value: str | None,
    received_at: datetime,
) -> None:
    """Mark recent insight deliveries engaged when the owner sends ingress.

    On each ingress request FROM THE OWNER, mark unengaged insight_engagement
    rows delivered within the last 60 minutes as engaged=TRUE, and record the
    owner-ingress day in the durable daily rollup (bu-tdd4k.5).
    Connector/automated/non-owner ingress must NOT count as engagement — the
    disengagement ratchet (check_total_disengagement_auto_off) is a vision
    success marker and can never fire if noise impersonates owner attention.
    Gated on a direct, read-only identity lookup rather than the full
    resolve_and_inject_identity() preamble machinery (no temp-contact creation
    or owner-notify side effects belong in a best-effort gate).

    This is a best-effort side effect: failures are logged and never raised.
    The imports stay local so callers and tests patch the owning modules.
    """
    try:
        from butlers.core.attention_ledger import record_owner_ingress_rollup
        from butlers.identity import resolve_contact_by_channel
        from butlers.tools.switchboard.insight.broker import check_and_update_engagement

        is_owner_ingress = False
        if sender_value and source:
            resolved_sender = await resolve_contact_by_channel(pool, source, sender_value)
            is_owner_ingress = resolved_sender is not None and "owner" in resolved_sender.roles

        if is_owner_ingress:
            await check_and_update_engagement(pool)
            await record_owner_ingress_rollup(pool, occurred_at=received_at)
    except Exception:
        logger.debug("Engagement detection failed; proceeding without update", exc_info=True)
