"""The one narrow write a runtime probe is allowed to make.

REQ-core-credentials-002 says a probe "updates verification evidence only, and
cannot close a breaker".  ``core_201`` expresses that as a fixed-search-path
``SECURITY DEFINER`` function whose ``EXECUTE`` is revoked from ``PUBLIC`` and
granted only to ``butler_switchboard_rw``: the function touches the four
verification columns and nothing else, so no probe outcome can reach ``enabled``,
``priority``, or any breaker-derived state even if the calling code is wrong.
"""

from __future__ import annotations

from typing import Any, Final
from uuid import UUID

#: Matches the truncation the dashboard verify path already applies before
#: storing provider error text on the catalog row.
VERIFY_ERROR_TRUNCATE_LEN: Final = 4096

_RECORD_VERIFICATION_SQL: Final = "SELECT public.record_runtime_probe_verification($1, $2, $3, $4)"


class RuntimeProbeVerificationPersistence:
    """Persist a probe outcome through the migration-owned definer function."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def record(
        self,
        *,
        catalog_entry_id: UUID,
        ok: bool,
        latency_ms: int | None = None,
        error: str | None = None,
    ) -> bool:
        """Record one probe outcome; ``False`` means the catalog entry is gone.

        A successful probe clears the stored error.  Error text is truncated by
        the database, so an oversized provider dump cannot bloat the row.
        """
        recorded = await self._pool.fetchval(
            _RECORD_VERIFICATION_SQL,
            catalog_entry_id,
            ok,
            latency_ms,
            None if ok else error,
        )
        return bool(recorded)
