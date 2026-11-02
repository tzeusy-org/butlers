"""Base contract for Chronicler projection adapters.

Adapters are NOT modules in the butler sense. They are plain coroutines
invoked by the Chronicler butler's scheduled jobs. Each adapter declares
its source name and provides a ``run`` coroutine that projects new rows
since the last checkpoint and updates the checkpoint on completion.
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from datetime import datetime

import asyncpg

from butlers.chronicler.models import Compatibility
from butlers.chronicler.storage import (
    get_checkpoint,
    mark_source_active,
    upsert_checkpoint,
)

logger = logging.getLogger(__name__)


@dataclass
class AdapterResult:
    """Outcome of a single adapter run."""

    source_name: str
    rows_projected: int = 0
    skipped: bool = False
    skipped_reason: str | None = None
    error: str | None = None
    watermark: datetime | None = None
    watermark_id: int | None = None
    episodes_opened: int = 0
    episodes_closed: int = 0
    # Episodes flipped layer=evidence -> layer=activity by a retroactive
    # corroboration re-check (bu-mul8i). Distinct from episodes_opened/closed:
    # no episode was written or ended, an existing one changed layer.
    episodes_promoted: int = 0
    point_events: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.error is None


class ProjectionAdapter(abc.ABC):
    """Abstract base for projection adapters.

    Concrete adapters MUST:
    - set ``source_name`` to a name declared in
      :data:`butlers.chronicler.contracts.INITIAL_SOURCES`.
    - implement :meth:`project` to read source surface, upsert rows to
      ``chronicler``, and return an ``AdapterResult``.
    - NEVER invoke an LLM inside ``project`` — guardrail tests assert
      this invariant by probing the default ``_llm_probe`` hook.
    """

    source_name: str

    def __init__(self, source_name: str) -> None:
        self.source_name = source_name

    @abc.abstractmethod
    async def project(
        self,
        pool: asyncpg.Pool,
        *,
        chronicler_pool: asyncpg.Pool,
        since: datetime | None,
        since_id: int | None = None,
    ) -> AdapterResult:
        """Project new source rows since ``since`` into Chronicler.

        Parameters
        ----------
        pool:
            Cross-butler read pool (search_path = public, role =
            butler_chronicler_rw). Used for reading the source surface.
        chronicler_pool:
            Write pool scoped to the ``chronicler`` schema. Used for
            upserts, links, and checkpoints.
        since:
            Timestamp watermark from the previous successful run, or None for
            the first run.
        since_id:
            Row ``id`` of the last-projected source row from the previous run.
            When both ``since`` and ``since_id`` are provided, adapters SHOULD
            use the tuple comparison ``WHERE (ts_col, id) > ($1, $2)`` to avoid
            missing rows that share the same timestamp at a batch boundary.
            When ``since_id`` is None (first run, or pre-migration checkpoint),
            adapters fall back to the single-column ``WHERE ts_col > $1`` form.
        """

    async def run(
        self,
        *,
        pool: asyncpg.Pool,
        chronicler_pool: asyncpg.Pool,
    ) -> AdapterResult:
        """Fetch checkpoint, call ``project``, record outcome."""
        checkpoint = await get_checkpoint(chronicler_pool, self.source_name)
        since = checkpoint.watermark if checkpoint is not None else None
        since_id = checkpoint.watermark_id if checkpoint is not None else None

        # Sparse interpretation invariant: the projection path MUST NOT
        # invoke an LLM. Enforced by convention and by guardrail tests
        # that patch _llm_probe to raise if called.
        self._llm_probe()

        try:
            result = await self.project(
                pool,
                chronicler_pool=chronicler_pool,
                since=since,
                since_id=since_id,
            )
        except Exception as exc:  # pragma: no cover — exercised in tests
            logger.exception("Adapter %s failed", self.source_name)
            await upsert_checkpoint(
                chronicler_pool,
                self.source_name,
                success=False,
                error=str(exc),
            )
            return AdapterResult(source_name=self.source_name, error=str(exc))

        if result.skipped:
            await mark_source_active(
                chronicler_pool,
                self.source_name,
                active=False,
                inactive_reason=result.skipped_reason or "adapter skipped",
            )
            return result

        await mark_source_active(chronicler_pool, self.source_name, active=True)
        await upsert_checkpoint(
            chronicler_pool,
            self.source_name,
            watermark=result.watermark,
            watermark_id=result.watermark_id,
            success=result.success,
            rows_projected=result.rows_projected,
            error=result.error,
        )
        return result

    def _llm_probe(self) -> None:
        """Hook used by guardrail tests to detect forbidden LLM calls.

        In production this is a no-op. Tests monkeypatch it (or check
        that the adapter's ``project`` method never imports LLM client
        modules) to enforce RFC 0014 §D5.
        """
        return None


def timestamp_complete_page_sql(
    *,
    columns: str,
    table: str,
    ts_col: str,
    order_by: str,
    limit_param: int,
    where: str = "TRUE",
) -> str:
    """Build a ``ts > watermark`` page query that never splits a timestamp.

    Adapters whose source primary key is UUID (or composite) cannot use the
    integer ``watermark_id`` tuple cursor, so they page on the timestamp
    alone. A plain ``WHERE ts > $1 ... LIMIT n`` then loses every row that
    shares the batch's last timestamp but fell past the limit, because the
    next run starts strictly after that timestamp.

    This page takes the first ``n`` distinct-ordered rows to find the last
    timestamp ``max_ts`` in the page, then returns *every* row up to and
    including ``max_ts``. The batch may exceed ``n`` by the size of one
    same-timestamp run, and the watermark (max ts) always lies past a fully
    drained timestamp, so ``ts > watermark`` loses nothing and repeats
    nothing. Stored checkpoints are unchanged, so no migration is needed.

    ``where`` is the full filter (including any ``ts > $k`` clause) and is
    evaluated twice with the same parameters; ``limit_param`` is the ``$``
    index of the batch size.
    """
    return f"""
        WITH page AS (
            SELECT {ts_col} FROM {table}
            WHERE {where}
            ORDER BY {ts_col} ASC
            LIMIT ${limit_param}
        )
        SELECT {columns}
        FROM {table}
        WHERE ({where})
          AND {ts_col} <= (SELECT max({ts_col}) FROM page)
        ORDER BY {order_by}
    """


def compatibility_guard(compat: Compatibility) -> AdapterResult | None:
    """Return an appropriate skip result if ``compat`` is not ``SUPPORTED``.

    Used by adapters to short-circuit cleanly when their source is
    declared ``deferred``, ``planned``, or ``not_time_bearing``.
    """
    if compat == Compatibility.SUPPORTED:
        return None
    return AdapterResult(
        source_name="",
        skipped=True,
        skipped_reason=f"source compatibility = {compat.value}",
    )


__all__ = [
    "AdapterResult",
    "ProjectionAdapter",
    "compatibility_guard",
    "timestamp_complete_page_sql",
]
