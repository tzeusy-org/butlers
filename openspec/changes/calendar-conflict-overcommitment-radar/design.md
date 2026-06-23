# Design — Calendar conflict & overcommitment radar

## Context

The calendar workspace (`src/butlers/api/routers/calendar_workspace.py`) already
flags conflicts at event-create time via `_check_conflicts` → `provider.find_conflicts`
→ Google `/freeBusy`. The existing GIST index (`ix_calendar_events_time_window_gist`,
`USING GIST (tstzrange(starts_at, ends_at, '[)'))` on `calendar_events` and
`calendar_event_instances`) makes range-overlap queries O(log n). The proposals
lane (`calendar_event_proposals` table + accept/dismiss endpoints, landed via
`calendar-event-proposals`) gives us a human-in-write-loop surface for butler-
suggested changes.

This change assembles those pieces into a proactive radar: scan forward, classify
issues, present fix proposals for one-click confirm/decline — all without a new
DB table or a new provider scope.

## Decisions

### D1 — Deterministic SQL scan at read time, not an LLM at request time

The `GET /api/calendar/workspace/conflicts` endpoint runs a pure SQL scan against
the already-synced `calendar_events` / `calendar_event_instances` tables. No
provider call, no LLM invocation at request time. This follows the RFC-0020
no-LLM-at-read-time doctrine: the read is cheap, deterministic, and repeatable.

The LLM layer is **decoupled from the read endpoint**: a scheduled butler session
(low-med model tier) fires asynchronously, only when the SQL scan finds issues,
and creates fix proposals via `calendar_propose_event`. The FE radar banner is
driven by the SQL scan alone; fix cards show up once the LLM session completes.

### D2 — Three issue kinds, computed in SQL

| Kind | Detection | Default threshold |
|---|---|---|
| `overlap` | `tstzrange(a.starts_at, a.ends_at, '[)') && tstzrange(b.starts_at, b.ends_at, '[)')` with `a.id < b.id` and both status `IN ('confirmed', 'tentative')` | n/a — any non-zero overlap |
| `back_to_back` | `(b.starts_at - a.ends_at) < interval '15 minutes'` on adjacent non-cancelled events, within the same day | 15 min gap threshold (configurable via `scan_config`) |
| `overloaded_day` | `SUM(EXTRACT(epoch FROM LEAST(e.ends_at, day_end) - GREATEST(e.starts_at, day_start)) / 3600)` grouped by calendar day exceeds threshold | 6.0 hours (configurable) |

All three queries fan out across all active butler schemas via the same
single-pool pattern used by `query_calendar_overlays`
(`calendar_workspace_v1.py`). The response is always HTTP 200 with `issues: []`
on any query failure (fail-open, consistent with the workspace read path).

### D3 — Response shape: `ConflictIssue` list grouped by date

```json
{
  "issues": [
    {
      "kind": "overlap",
      "date": "2026-06-24",
      "summary": "Tue has 2 overlaps",
      "severity": "warning",
      "events": [
        {"entry_id": "...", "title": "Design review", "start_at": "...", "end_at": "..."},
        {"entry_id": "...", "title": "1:1 with manager", "start_at": "...", "end_at": "..."}
      ],
      "proposal_ids": ["uuid-of-pending-proposal-if-any"]
    },
    {
      "kind": "overloaded_day",
      "date": "2026-06-25",
      "summary": "Wed has 8.5h of meetings",
      "severity": "warning",
      "events": [],
      "proposal_ids": []
    }
  ],
  "scan_window": {"start": "...", "end": "..."},
  "issues_available": true
}
```

`issues_available: false` signals degraded mode (DB unreachable) — the FE hides
the banner rather than showing stale data.

`proposal_ids` lists any `pending` proposals in `calendar_event_proposals` that
were emitted by the LLM fix session for this issue. The FE fetches the proposal
detail from the existing `view=proposals` workspace endpoint to render the fix card.

### D4 — LLM fix-proposal session: scheduled, conditional, low-tier

A butler's scheduler runs a conflict-radar session every N hours (configurable,
default 6 h) **only if** the SQL scan finds at least one `warning`-severity issue
in the configured forward window (default: next 7 days).

The session prompt:
1. Calls `calendar_scan_conflicts(start, end)` to get the issues list.
2. For each `overlap`: calls `calendar_find_free_slots` to locate a free slot,
   then `calendar_propose_event` to propose rescheduling the lower-priority event
   (or declining if it is tentative).
3. For each `back_to_back` cluster: proposes a short buffer block (15 min).
4. For each `overloaded_day`: proposes one declination or reschedule of the
   lowest-priority event.
5. At most one proposal per issue to keep the lane uncluttered.

The session uses a **low-med** model tier (same tier as find-time slots). It
emits proposals via the existing `calendar_propose_event` MCP tool — no new
provider write.

### D5 — `calendar_scan_conflicts` MCP tool wraps the SQL scan

The LLM session calls `calendar_scan_conflicts(start_at, end_at,
back_to_back_gap_minutes?, overloaded_day_hours?)` → structured list of issues.
The tool is a thin wrapper around `query_calendar_conflicts` (the new read-model
function) so the session does not need raw SQL access. This follows the
pattern of `calendar_find_free_slots` → `get_free_busy`.

### D6 — FE radar banner and amber edge

**Radar banner** — rendered above the week/day grid when
`GET /api/calendar/workspace/conflicts?start=...&end=...` returns at least one
`warning` issue in the visible window. The banner is a single line
("Tue has 2 overlaps, no lunch gap") that expands to fix cards.

**Fix cards** — one card per `ConflictIssue`. Each card shows the conflicting
events and, when `proposal_ids` is non-empty, a "Fix: [proposal title]" block
backed by the existing `POST /api/calendar/workspace/proposals/{id}/accept` and
`/dismiss` endpoints. If `proposal_ids` is empty (LLM session not yet run or
no fix found), the card is informational only.

**Amber edge** — overlapping grid event blocks receive a thin amber left border
via a CSS class driven by a new `has_conflict: boolean` field on `UnifiedCalendarEntry`
(server-computed for the `user`/`butler` views). When the conflict scan detects
a given `entry_id` in an overlap issue, that entry's `metadata.has_conflict` is
set to `true`. The FE applies the amber border from `metadata`.

**No new `source_type` value** — the amber-edge signal is a `metadata` flag, not
a new workspace lane.

### D7 — Fail-open everywhere; fail-closed nowhere

- **`GET /api/calendar/workspace/conflicts`**: any DB query failure → HTTP 200
  with `issues: [], issues_available: false`. Never HTTP 500.
- **Fix-proposal session**: if `calendar_find_free_slots` finds no open slot,
  the session emits no proposal rather than proposing an impossible fix.
- **Fix-card accept**: inherits the fail-closed posture of the existing accept
  endpoint (proposal stays `pending` if the butler call fails).

## Risks / Trade-offs

- **Stale event data.** The radar queries the synced `calendar_events` snapshot,
  not the live provider. A race between an external calendar change and the next
  sync could show a false overlap. Mitigated by the existing sync heartbeat
  (calendar sync runs every 5 minutes) and by labeling the banner with the last-
  synced timestamp already exposed by `source_freshness`.

- **Fan-out query cost.** Overlap detection does a self-join across all events
  in the window. For a 7-day window with ~200 events the GIST index keeps this
  sub-millisecond. Guard: reject windows > 90 days (same limit as workspace read).

- **LLM session noise.** The session fires every 6 h if any issue exists; a
  long-standing overlap (e.g. two recurring series that clash forever) would
  generate a proposal every cycle. Mitigated by producer idempotency on
  `source_event_id`: the scan session derives a stable `source_event_id` from the
  pair of `entry_id`s so re-runs produce no duplicate proposals.

- **New `has_conflict` metadata flag on existing entries.** Adding it to the
  `user`/`butler` workspace reads requires the conflict scan to run on every
  workspace page load. Mitigation: run the overlap sub-query only when `view IN
  ('user', 'butler')` (not `proposals`/`overlays`) and only when the result set
  fits in a single page (i.e. `has_more=false`). When paginated, the FE fetches
  the conflicts list separately via the dedicated endpoint.
