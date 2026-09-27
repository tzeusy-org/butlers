## Context

Slice 1 established `src/butlers/api/session_presentation.py` as the pure,
structured-trigger-first boundary for Timeline session summaries. The Timeline
response still exposes only the legacy boolean `is_heartbeat`, however, so the
frontend cannot distinguish owner-relevant activity from known internal
maintenance sessions. Dashboard Now consumes those same Timeline events.

The dashboard is allowed to read cross-butler data directly through its API
read boundary (RFC 0007 and `about/lay-and-land/integration.md`). This change
is presentation-only: it must not alter a session record, the scheduler,
memory lifecycle, notification delivery, or episode storage.

## Goals / Non-Goals

**Goals:**

- Classify Timeline presentation using a bounded `owner` / `heartbeat` /
  `maintenance` vocabulary derived only from structured `trigger_source`.
- Retain the current `is_heartbeat` wire field and behavior as a compatibility
  projection.
- Keep the default Timeline and Dashboard Now owner-focused, with a keyboard-
  operable, screen-reader-named Internal lens that reveals grouped maintenance
  runs on demand.
- Keep failed maintenance sessions visible as errors when the lens is off.

**Non-Goals:**

- Shared activity-feed summary parity (Slice 3).
- Prompt or provenance persistence, database migrations, session lifecycle
  changes, notification changes, retention changes, or a dashboard redesign.
- Generalizing every `schedule:*` source into maintenance or modifying the
  existing exact `schedule:consolidation` episode-exclusion rule.

## Decisions

### 1. Use one exact presentation taxonomy at the API boundary

`session_presentation.py` will own an immutable mapping from exact trigger
sources to machine classes. It includes the existing heartbeat sources and the
reviewed maintenance family: `schedule:consolidation` plus the current
memory-module maintenance schedule names (`memory_decay_sweep`,
`memory_consolidation`, `memory_episode_cleanup`,
`memory_purge_superseded`, `memory_ann_observability`,
`memory_consolidation_backfill`, and `memory_catalog_backfill`). No prefix
match is allowed for maintenance. Unknown, malformed, future, and ordinary
scheduled sources default to `owner`.

This duplicates no module runtime dependency: importing the memory module into
the dashboard API would couple a display boundary to module startup. The map is
instead a reviewed presentation contract accompanied by table-driven tests.

Alternative: classify every `schedule:*` source as maintenance. Rejected:
scheduled briefs and owner-value tasks would disappear from the household
chronicle, and future sources would be silently demoted.

### 2. Extend the Timeline event contract compatibly

`TimelineEvent.machine_class` is a typed, additive field. Its legacy
`is_heartbeat` field remains and is calculated from `machine_class ==
"heartbeat"`; existing consumers retain their old heartbeat behavior while new
consumers use the richer vocabulary. Notifications are `owner` by default.

Alternative: overload `is_heartbeat` or derive maintenance in each frontend
surface. Rejected: either collapses distinct semantics or recreates the prompt
firewall's multi-reader drift.

### 3. Keep maintenance behind a shared Internal lens

Both surfaces default to the owner lens. Successful maintenance events are
hidden until `internal=1` is selected. The Timeline's URL-backed button uses
native button semantics with `aria-pressed`; its enabled lens renders one
expandable rollup per butler within an hour, showing an exact count from the
loaded events and a failed-run count. Dashboard Now uses the same URL-backed
state and shows compact per-butler maintenance rollups linking to the enabled
Timeline lens. Failed maintenance events remain visible as ordinary error
activity even with the lens off.

Alternative: always show a maintenance rollup. Rejected: it still crowds the
default household view during a high-volume runaway. Alternative: hide failed
maintenance too. Rejected: it converts a diagnosable operational failure into
silence.

## Risks / Trade-offs

- [A new internal task is not in the exact mapping] -> It remains owner
  activity, which is safe and visible until a reviewed taxonomy update adds it.
- [A client receives a legacy Timeline event without `machine_class`] -> The
  frontend treats it as `owner`, except the existing `is_heartbeat` fallback
  preserves heartbeat grouping.
- [A page contains only part of a maintenance burst] -> Rollup wording counts
  only loaded events and makes no claim about a global/hourly total.
- [Failed maintenance gets noisy] -> Its existing error presentation remains
  visible deliberately; successful runs remain behind the opt-in lens.

## Migration Plan

1. Deploy the additive API field and frontend fallback together.
2. Existing clients continue to use `is_heartbeat`; new clients use
   `machine_class` when present.
3. Roll back by removing the frontend lens consumption; no stored data or
   schema requires rollback.

## Open Questions

None. The exact taxonomy is intentionally conservative and can be extended by
a later reviewed presentation change.
