## Why

The calendar workspace shows conflicts at create time but never scans the
forward horizon proactively — the user discovers a double-booking the morning
of, when it is too late to rearrange gracefully. With `find-me-time`
(bu-140q93) and the `proposals` lane (bu-fh8drm) now landed, the plumbing for
overlap detection and butler-suggested event changes is in place; this change
assembles them into a proactive **conflict and overcommitment radar** that
surfaces issues — overlaps, back-to-back density, overloaded days — before the
user encounters them, and proposes one-click fixes.

## What Changes

- **New read endpoint `GET /api/calendar/workspace/conflicts`** scans a
  caller-supplied window for three issue kinds — `overlap`, `back_to_back`, and
  `overloaded_day` — using the existing `GIST(tstzrange)` index on
  `calendar_events` / `calendar_event_instances`. Returns a structured list of
  `ConflictIssue` objects grouped by day. Deterministic, fail-open (never HTTP
  500), no LLM at request time.

- **New LLM fix-proposal session** (low-med tier, scheduled, ephemeral).
  Triggered only when the SQL scan finds issues in the forward window. The
  session reads the issues list and emits fix proposals via `calendar_propose_event`
  (the existing producer from `calendar-event-proposals`) — reschedule a
  lower-priority overlapping event to a free slot, decline a tentative, add a
  buffer block. Proposals land in the existing `calendar_event_proposals`
  `pending` store; the human confirms or declines inline. **Never silently
  mutates the real calendar — human-in-the-write-loop is non-negotiable.**

- **FE radar banner** — a quiet banner atop week/day view when issues exist in
  the visible range (e.g. "Tue has 2 overlaps and no lunch gap"). Expands to fix
  cards (existing proposal accept/decline affordance). Overlapping grid events
  receive a thin amber left edge to mark the overlap visually.

- **New `calendar_scan_conflicts` MCP tool** — the butler-side scan tool
  consumed by the LLM fix-proposal session; wraps the SQL issue detection so the
  session can read structured issues without a raw SQL query.

## Capabilities

### New Capabilities

- `calendar-conflict-overcommitment-radar`: the forward-window scan endpoint
  contract, the `ConflictIssue` response model, the `calendar_scan_conflicts`
  MCP tool, and the LLM fix-proposal session trigger. The FE radar banner and
  amber-edge UX are covered by this capability.

### Modified Capabilities

- `module-calendar`: the tool count increases from 22 to 23 MCP tools total
  with the addition of `calendar_scan_conflicts`.

## Impact

- **New read endpoint** (`src/butlers/api/routers/calendar_workspace.py`):
  `GET /api/calendar/workspace/conflicts` + request/response models in
  `src/butlers/api/models/calendar_workspace.py`.
- **New read-model query** (`src/butlers/api/read_models/calendar_workspace_v1.py`):
  `query_calendar_conflicts` fan-out that uses the GIST index across all
  active butler-schema `calendar_events` + `calendar_event_instances` tables.
- **New MCP tool** (`src/butlers/modules/calendar.py`): `calendar_scan_conflicts`
  returns structured issues for the butler session.
- **Spec** (`openspec/specs/module-calendar/spec.md`): tool count literal moves
  from "22 MCP tools total" to "23 MCP tools total".
- **FE** (`frontend/src/pages/CalendarWorkspacePage.tsx` and
  `frontend/src/components/calendar/`): radar banner, amber edge, fix-card
  expansion using the existing proposals accept/dismiss surface.
- **No new migration** — the radar is a pure SQL read of existing tables and
  fix proposals go into the already-landed `calendar_event_proposals` table.

## Sequencing

Requires both prerequisites to be merged before implementation starts:
- `calendar-availability-find-time` (bu-140q93, PR #2640 — `get_free_busy`,
  `calendar_find_free_slots`, scheduling preferences) — DONE.
- `calendar-event-proposals` (bu-fh8drm — proposals table, accept/dismiss
  endpoints, `calendar_propose_event` producer) — DONE.

## Out of Scope

- Auto-resolving conflicts without human confirmation — proposals are always
  confirm/decline; silent calendar mutation is explicitly out of scope.
- Cross-butler conflict detection — the radar stays within the calendar
  module's own connected accounts (same trust boundary as find-time).
- Snoozing radar alerts — a future follow-up; v1 shows issues until fixed.
- A per-event "conflicts with" indicator in the event detail drawer — separate
  from the banner; not in this change.

## Archive Note (partial archive, 2026-07-10, reconciliation bu-d287o)

This change was archived after the **read/HTTP + FE** slice shipped, but the
**butler-side scan tool and the LLM fix-proposal session did not ship**. The
radar shipped **HTTP-only**: `GET /api/calendar/workspace/conflicts`
(`src/butlers/api/routers/calendar_workspace.py`), the `query_calendar_conflicts`
read-model (`src/butlers/api/read_models/calendar_workspace_v1.py`), the pure
`detect_conflict_issues` detector (`src/butlers/core/temporal/conflicts.py`), and
the FE radar banner + amber edge (`frontend/src/components/calendar/ConflictRadarBanner.tsx`,
`CalendarWorkspacePage.tsx`, shipped under `bu-q8o90x`).

To keep the authoritative specs true to `main`, the following delta content was
**removed before archive** and is intentionally **not** reflected in
`openspec/specs/`:

- **`module-calendar` delta (entire file removed).** Its only content was a
  `MODIFIED` block bumping the tool count from **22 → 23** and adding
  `calendar_scan_conflicts` to the tool list. That MCP tool was **never
  registered** — the calendar module registers **22 tools** on `main`
  (`calendar_scan_conflicts` appears only in a code comment in `conflicts.py`).
  The authoritative `openspec/specs/module-calendar/spec.md` "22 MCP tools total"
  line is therefore left **unchanged**.
- **`calendar-conflict-overcommitment-radar` capability delta — removed two
  requirements** describing unshipped behavior:
  - `Requirement: calendar_scan_conflicts MCP Tool` — the butler-side scan tool
    is not registered.
  - `Requirement: LLM Fix-Proposal Session` — no scheduled conflict-radar session
    / cron job exists; no skill emits `calendar_propose_event` from a radar scan.

The retained requirements (Forward-Window Conflict Scan Endpoint,
ConflictScanResponse Model, FE Radar Banner, Amber Edge) all describe behavior
verified present on `main`. The endpoint's `proposal_ids` field and the FE fix
cards remain accurate: they read `pending` rows from the already-shipped
`calendar_event_proposals` store and gracefully render an empty/informational
state until a proposal producer runs (the FE "Fix card without proposal" scenario
explicitly covers this).

The `## What Changes` / `## Capabilities` narrative above still describes the full
intended scope (including the scan tool and the LLM session) as historical
record. When the scan tool and fix-proposal session ship, re-introduce that spec
content — and the `22 → 23` tool-count bump — via a **new** OpenSpec change.
