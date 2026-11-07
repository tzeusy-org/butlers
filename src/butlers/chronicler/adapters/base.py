"""Base contract for Chronicler projection adapters.

Adapters are NOT modules in the butler sense. They are plain coroutines
invoked by the Chronicler butler's scheduled jobs. Each adapter declares
its source name and provides a ``run`` coroutine that projects new rows
since the last checkpoint and updates the checkpoint on completion.
"""

from __future__ import annotations

import abc
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import asyncpg

from butlers.chronicler.models import Compatibility
from butlers.chronicler.storage import (
    get_checkpoint,
    get_cursor,
    mark_source_active,
    set_cursor,
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
    # Tiebreaker of the last row read, for adapters with ``keyset_cols``.
    watermark_key: list[str] | None = None
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
    # Set by UUID/composite-keyed adapters that page by ``(ts, key)``: the
    # ordered ``(column, sql_type)`` tiebreaker. ``run`` then loads the stored
    # tiebreaker, passes it as ``since_key`` and saves ``result.watermark_key``.
    keyset_cols: KeyCols | None = None

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

        extra: dict[str, Any] = {}
        if self.keyset_cols is not None:
            cursor = await get_cursor(chronicler_pool, self.source_name)
            extra["since_key"] = key_for_watermark(cursor, since)

        try:
            result = await self.project(
                pool,
                chronicler_pool=chronicler_pool,
                since=since,
                since_id=since_id,
                **extra,
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
        # Cursor first, then watermark. The cursor is tagged with its ts and is
        # only honoured while that equals the stored watermark, so a crash
        # between the two writes leaves a cursor ahead of the watermark: it is
        # ignored and the next run re-reads (idempotently) from the old
        # watermark. The reverse order would leave a stale cursor under a new
        # watermark and skip the rest of that timestamp's run.
        if result.success and result.watermark is not None and result.watermark_key:
            await set_cursor(
                chronicler_pool, self.source_name, result.watermark, result.watermark_key
            )
        await self.save_checkpoint(chronicler_pool, result)
        return result

    async def save_checkpoint(self, pool: asyncpg.Pool, result: AdapterResult) -> None:
        """Publish a completed projection; subclasses may add atomic local state."""
        await upsert_checkpoint(
            pool,
            self.source_name,
            watermark=result.watermark,
            watermark_id=result.watermark_id,
            success=result.success,
            rows_projected=result.rows_projected,
            error=result.error,
        )

    def _llm_probe(self) -> None:
        """Hook used by guardrail tests to detect forbidden LLM calls.

        In production this is a no-op. Tests monkeypatch it (or check
        that the adapter's ``project`` method never imports LLM client
        modules) to enforce RFC 0014 §D5.
        """
        return None


KeyCols = Sequence[tuple[str, str]]


def keyset_page(
    *,
    columns: str,
    table: str,
    ts_col: str,
    key_cols: KeyCols,
    since: datetime | None,
    since_key: Sequence[str] | None,
    batch_limit: int,
    where: str = "TRUE",
    where_args: Sequence[Any] = (),
) -> tuple[str, list[Any]]:
    """Build a ``(ts, key) > cursor`` page query and its arguments.

    ``key_cols`` is the ordered ``(column, sql_type)`` tiebreaker, unique per
    row. With a ``since_key`` the page resumes strictly after
    ``(since, *since_key)``; without one (first run, or a pre-cursor
    checkpoint) it falls back to ``ts > since``, exactly as before. The page
    is always capped at ``batch_limit`` rows and ordered by ``(ts, *key)``.

    ``where`` is a filter using ``$1..$len(where_args)``.
    """
    args: list[Any] = list(where_args)
    seek = "TRUE"
    if since is not None:
        n = len(args) + 1
        if since_key is not None and len(since_key) == len(key_cols):
            names = ", ".join([ts_col, *(c for c, _ in key_cols)])
            values = ", ".join(
                [f"${n}", *(f"${n + 1 + i}::text::{t}" for i, (_, t) in enumerate(key_cols))]
            )
            seek = f"({names}) > ({values})"
            args += [since, *since_key]
        else:
            seek = f"{ts_col} > ${n}"
            args.append(since)
    args.append(batch_limit)
    order = ", ".join(f"{c} ASC" for c in [ts_col, *(c for c, _ in key_cols)])
    sql = f"""
        SELECT {columns}
        FROM {table}
        WHERE ({where}) AND {seek}
        ORDER BY {order}
        LIMIT ${len(args)}
    """
    return sql, args


def cursor_key(row: Any, key_cols: KeyCols) -> list[str]:
    """Tiebreaker parts of ``row``, in ``key_cols`` order."""
    return [str(row[c]) for c, _ in key_cols]


def key_for_watermark(
    cursor: tuple[datetime, list[str]] | None, since: datetime | None
) -> list[str] | None:
    """The stored tiebreaker, only if it belongs to the ``since`` watermark."""
    if cursor is None or since is None or cursor[0] != since:
        return None
    return cursor[1]


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
    "cursor_key",
    "key_for_watermark",
    "keyset_page",
]
