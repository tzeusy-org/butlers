"""Confidence derivation + evidence-chain helpers for the IEA reframe.

Pure, dependency-light units that the deterministic candidate projectors
(``src/butlers/chronicler/adapters/``) call when they emit ``activity``
episodes. Keeping the logic here — rather than inline in each adapter — gives
the corroboration rule a single, unit-testable home (tasks.md S5).

Two concerns live here:

* ``derive_confidence`` — map a set of corroborating evidence *kinds* onto the
  ``high | medium | low`` ladder. The rule counts *independent* kinds, so two
  samples of the same signal (e.g. two heart-rate reads) never inflate a block
  past what a single signal earns.
* ``evidence_refs_from_event_ids`` — denormalize the canonical
  ``episode_event_links`` chain into the ``episodes.evidence_refs`` convenience
  surface (an ordered, de-duplicated list of point-event ids).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from butlers.chronicler.models import Confidence


@dataclass(frozen=True)
class EvidenceKind:
    """One *kind* of corroborating evidence backing an ``activity`` episode.

    ``name``
        Canonical id of the signal kind, e.g. ``"gps"``, ``"heart_rate"``,
        ``"calendar"``. Two descriptors that share a ``name`` are the *same*
        kind and collapse to a single corroboration.
    ``strong``
        ``True`` for a strong *canonical* signal kind — one that, on its own,
        is enough to lift an episode to ``medium`` confidence (e.g. an explicit
        session marker or a calendar boundary). Most raw signals are weak.
    ``correlated_with``
        Optional grouping key. Kinds that share a ``correlated_with`` value are
        *not* mutually independent (they are weakly-related — e.g. ``steps`` and
        ``heart_rate`` both derive from the same wearable), so they count once
        toward the "independent kinds" tally. ``None`` means the kind stands on
        its own and is independent of every differently-named kind.
    """

    name: str
    strong: bool = False
    correlated_with: str | None = None

    @property
    def independence_group(self) -> str:
        """Key identifying this kind's independence cluster."""
        return self.correlated_with or self.name


def derive_confidence(evidence_kinds: Iterable[EvidenceKind]) -> Confidence:
    """Derive an episode's confidence from its corroborating evidence kinds.

    Ladder (tasks.md S5):

    * ``high``   — 2+ *independent* evidence kinds corroborate the block.
    * ``medium`` — exactly 2 weakly-related (correlated) kinds, OR a single
      strong canonical kind.
    * ``low``    — a single weak / ambiguous signal. Such a block is *still
      counted* as activity (the layer decides counting, not confidence); the
      ``low`` value only flags it for re-reconciliation.

    An empty evidence set yields ``low`` — the conservative default that matches
    the storage column default; the episode is still a real activity row.
    """
    kinds = list(evidence_kinds)
    if not kinds:
        return Confidence.LOW

    distinct_names = {k.name for k in kinds}
    independent_groups = {k.independence_group for k in kinds}
    has_strong_canonical = any(k.strong for k in kinds)

    # 2+ independent kinds is the strongest signal — it wins even if one of the
    # corroborating kinds also happens to be a strong canonical signal.
    if len(independent_groups) >= 2:
        return Confidence.HIGH
    # A single strong canonical kind, or two weakly-related (correlated) kinds.
    if has_strong_canonical or len(distinct_names) >= 2:
        return Confidence.MEDIUM
    return Confidence.LOW


def evidence_refs_from_event_ids(event_ids: Iterable[UUID | str]) -> list[str]:
    """Build an ``evidence_refs`` list from linked point-event ids.

    The canonical evidence chain lives in ``episode_event_links``; this projects
    it onto the denormalized ``episodes.evidence_refs`` convenience surface as an
    ordered, de-duplicated list of point-event id strings. Input order is
    preserved (callers pass events already ordered by occurrence).
    """
    refs: dict[str, None] = {}
    for event_id in event_ids:
        refs.setdefault(str(event_id), None)
    return list(refs)
