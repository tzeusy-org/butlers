# Chronicles Content Polish — Gen-1 Spec-to-Code Reconciliation

**Bead:** bu-pas8k  
**Epic:** bu-rdvnx — "Chronicles dashboard: content polish phase 2"  
**Date:** 2026-04-30  
**Auditor:** agent/bu-pas8k (autonomous worker)

---

## Summary

This audit compares the six success criteria of epic bu-rdvnx against the
code delivered by sibling beads (all merged as of origin/main @ bb806835).
Code-level coverage is **COMPLETE** for all six criteria. Live visual
verification **CANNOT** be performed from this autonomous worker context
and is explicitly flagged as pending human action.

---

## Criterion-by-Criterion Audit

### Criterion 1 — Zero heartbeat/QA/schedule:* episodes visible

**Status: CODE PASS — live verify pending**

**Implementing beads:**
- bu-6t63s → PR #1299 (primary: Alembic migration chronicler_007)
- bu-aqqx0 → PR #1308 (retroactive tombstone of memory_* calendar episodes via chronicler_008)

**Code evidence:**

PR #1299 adds `roster/chronicler/migrations/007_tombstone_heartbeat_episodes.py`
(183 lines). The migration imports constants directly from the adapter (single
source of truth) and tombstones all matching rows:

```python
# roster/chronicler/migrations/007_tombstone_heartbeat_episodes.py lines 69-70
from butlers.chronicler.adapters.sessions import (
    EXCLUDED_TRIGGER_SOURCE_PREFIX,
    EXCLUDED_TRIGGER_SOURCES,
)
```

The constants themselves live at:
```python
# src/butlers/chronicler/adapters/sessions.py lines 78-79 (origin/main)
EXCLUDED_TRIGGER_SOURCES: frozenset[str] = frozenset({"tick", "qa", "healing"})
EXCLUDED_TRIGGER_SOURCE_PREFIX: str = "schedule:"
```

The migration SQL (lines 111-114 of the migration file):
```sql
SET tombstone_at = now(),
    tombstone_reason = 'butler-internal session retroactively excluded ...'
WHERE source_name = 'core.sessions'
  AND tombstone_at IS NULL
  AND (payload->>'trigger_source' = ANY($1) OR payload->>'trigger_source' LIKE $2)
```

PR #1308 adds `roster/chronicler/migrations/008_tombstone_memory_calendar_episodes.py`
(180 lines) for the retroactive tombstone of `memory_consolidation`,
`memory_episode_cleanup`, and `memory_purge_superseded` calendar episodes.

The migration chains after chronicler_007 (`revision = "chronicler_008"`).

**Ops note:** Migrations self-execute on chronicler daemon restart. The two
open qa episodes (ccbfbe85, ed23b522) are covered by the chronicler_007
criteria (`trigger_source='qa'` with `tombstone_at IS NULL`).

**Cannot live-verify:** Whether the dashboard at /butlers-dev/chronicles
currently shows zero heartbeat rows requires a live browser session.

---

### Criterion 2 — All timestamps render in Asia/Singapore timezone

**Status: CODE PASS — live verify pending**

**Implementing bead:** bu-k18cm → PR #1298

**Code evidence:**

PR #1298 adds `frontend/src/components/chronicles/tz-format.ts` (116 lines):

```typescript
// frontend/src/components/chronicles/tz-format.ts lines 10, 19
import { formatInTimeZone, toZonedTime, fromZonedTime } from "date-fns-tz"
export const DEFAULT_TZ = "Asia/Singapore"
```

`ChroniclesPage.tsx` resolves the owner timezone from `/api/settings/general`
and wraps the page tree in `ChroniclesTimezoneProvider`:

```typescript
// frontend/src/pages/ChroniclesPage.tsx (PR #1298 diff)
const { data: generalSettings } = useGeneralSettings()
const ownerTz = generalSettings?.data?.timezone ?? DEFAULT_TZ
const timeWindow = useTimeWindow(ownerTz)
// ...
<ChroniclesTimezoneProvider timezone={ownerTz}>
```

Components updated: `GanttSwimlaneInner.tsx`, `EpisodeDrawer.tsx`,
`Scrubber.tsx`, `use-time-window.ts` — all consume `useChroniclesTimezone()`
and pass it to `formatInTimeZone` calls.

16 new timezone-specific test assertions added in
`frontend/src/components/chronicles/timezone-rendering.test.tsx`.

Documentation added to `roster/chronicler/AGENTS.md` section
"Frontend timezone source (bu-k18cm)" (line 307 on origin/main).

**Cannot live-verify:** Whether rendered times show "SGT" abbreviation and
correct wall-clock hours requires a live browser session.

---

### Criterion 3 — Calendar lane free of butler-internal scheduled-task entries

**Status: CODE PASS — live verify pending**

**Implementing beads:**
- bu-daaff → PR #1297 (dual-track prevention: writer-side + adapter-side)
- bu-aqqx0 → PR #1308 (retroactive tombstone of existing memory_* calendar episodes)

**Code evidence (Track A — writer-side, PR #1297):**

`src/butlers/modules/calendar.py` adds `dispatch_mode='job'` exclusion in
`_project_scheduler_source()` so `memory_consolidation`, `memory_episode_cleanup`,
and `memory_purge_superseded` are never projected into `calendar_event_instances`.

**Code evidence (Track B — adapter-side, PR #1297):**

`src/butlers/chronicler/adapters/calendar.py` adds an inner join and WHERE guard
(lines 154-157 on origin/main):

```python
INNER JOIN {quoted}.calendar_sources AS cs ON cs.id = i.source_id
# ...
AND cs.lane != 'butler'
```

This filter appears in both the `ends_at <=` (no-since) and the with-since
query paths (lines 157 and 179 on origin/main).

The constant `BUTLER_MANAGED_SOURCE_KINDS` documents the two internal source kinds.

**Code evidence (retroactive tombstone, PR #1308):**

Migration `chronicler_008` tombstones all pre-existing
`google_calendar.completed` episodes with exact title match against
`['memory_consolidation', 'memory_episode_cleanup', 'memory_purge_superseded']`.

Documentation added to `roster/shared/AGENTS.md` section
"Butler-Managed Calendar Contract" (lines 38-62 on origin/main), documenting
both Track A and Track B enforcement layers.

**Cannot live-verify:** Whether the Calendar lane is clean within a 7-day
window requires a live browser session.

---

### Criterion 4 — Spotify shows track-level info; Owntracks shows category/duration without total masking

**Status: CODE PASS — live verify pending**

**Implementing bead:** bu-6c5i6 → PR #1303

**Code evidence (adapter defaults):**

`src/butlers/chronicler/adapters/spotify.py` line 219 on origin/main:
```python
privacy=Privacy.NORMAL,
```
(Previously `Privacy.SENSITIVE`; changed in PR #1303.)

`src/butlers/chronicler/adapters/owntracks.py` retains `privacy=SENSITIVE`
for GPS coordinate payloads; docstring updated to clarify envelope vs payload
contract.

**Code evidence (frontend envelope rendering):**

PR #1303 modifies `frontend/src/components/chronicles/GanttSwimlaneInner.tsx`
so sensitive episodes show `"<Category>: <duration>"` in tooltip instead of
`"Private activity / Sensitive"`.

`frontend/src/components/chronicles/EpisodeDrawer.tsx` updated: sensitive
episodes show `start_at`, `end_at`, and duration (envelope visible), while
title and source are masked.

**Code evidence (backfill):**

`alembic/versions/core/core_085_backfill_spotify_owntracks_privacy.py` (76 lines)
backfills the 13 pre-existing Spotify rows from `privacy='sensitive'` to
`privacy='normal'` with a `DO $$ IF EXISTS` guard for missing `chronicler` schema.

**Code evidence (three-level privacy contract):**

`roster/chronicler/AGENTS.md` section "Three privacy levels" (line 112 on
origin/main) documents the contract: envelope always visible for normal/sensitive;
payload-level fields masked for sensitive; restricted hides envelope entirely.

**Cannot live-verify:** Whether the Music lane renders track titles and the
Travel lane shows category/duration requires a live browser session.

---

### Criterion 5 — Conversation episodes show human-readable contact title

**Status: CODE PASS (new projection) / PARTIALLY PENDING (historical backfill)**

**Implementing beads:**
- bu-fkqv0 → PR #1300 (adapter title-resolution logic; frontend drawer changes)
- bu-jpf3o → IN PROGRESS (watermark reset to re-title existing episodes)

**Code evidence (title-resolution logic, PR #1300):**

`src/butlers/chronicler/adapters/sessions.py` now has `_compute_episode_title()`
method (lines 347+ on origin/main) implementing the documented rules:

```python
# src/butlers/chronicler/adapters/sessions.py (origin/main)
def _compute_episode_title(self, schema, trigger_source, contact_info):
    # 1. trigger_source='route' AND display_name resolved
    #        → 'Conversation with {display_name}'
    # 2. trigger_source='route' AND display_name unresolved, channel known
    #        → 'Conversation via {channel}'
    # 3. trigger_source='route' AND channel unknown
    #        → 'Conversation via unknown channel'
    # 4. trigger_source in ('trigger', 'external', 'dashboard')
    #        → '{schema}: manual task'
    # 5. Fallback → '{schema} session'
```

The explicit fallback to `'Conversation via {channel}'` (not silent drop)
satisfies the "no silent dropouts" clause of criterion 5.

The `_resolve_contacts()` method JOINs `ingestion_events → contact_info →
contacts` at projection time; guarded against missing `public.*` tables via
`PostgresError` catch.

**Code evidence (frontend, PR #1300):**

`frontend/src/components/chronicles/EpisodeDrawer.tsx` surfaces
`canonical_title` as primary heading (`data-testid="episode-primary-title"`)
with `source_name` as subordinate footnote
(`data-testid="episode-source-footnote"`).

**Documentation:** `roster/chronicler/AGENTS.md` section
"CoreSessionsAdapter — episode title-resolution rules (bu-fkqv0)" (line 150
on origin/main).

**Open gap — historical backfill:** bu-jpf3o ("ops(chronicler): re-title
existing {schema} session conversation episodes") is **in progress** with no
PR yet. Pre-bu-fkqv0 episodes that were projected as `'{schema} session'`
remain untitled until watermark reset triggers re-projection. This is a
known accepted gap from the sibling work decomposition.

**Cannot live-verify:** Whether the Conversations lane shows human-readable
contact names on the live dashboard requires a browser session. Additionally,
the bu-jpf3o re-title backfill may be incomplete, meaning older episodes may
still show `'{schema} session'`.

---

### Criterion 6 — Doc updates land in the same changes

**Status: CODE PASS**

All same-change documentation updates are confirmed present on origin/main:

| Document | Section added/updated | Implementing bead |
|---|---|---|
| `roster/chronicler/AGENTS.md` | "Three privacy levels" + "Adapter defaults" + "Frontend contract" + "Backfill (core_085)" (lines 112-148) | bu-6c5i6 / PR #1303 |
| `roster/chronicler/AGENTS.md` | "CoreSessionsAdapter — episode title-resolution rules (bu-fkqv0)" (lines 150-190) | bu-fkqv0 / PR #1300 |
| `roster/chronicler/AGENTS.md` | "Frontend timezone source (bu-k18cm)" (lines 307-327) | bu-k18cm / PR #1298 |
| `roster/chronicler/AGENTS.md` | "Heartbeat tombstone migration verification" (lines 328-370) | bu-6t63s / PR #1299 |
| `roster/chronicler/AGENTS.md` | "Memory calendar episode tombstone migration" (lines 371+) | bu-aqqx0 / PR #1308 |
| `roster/shared/AGENTS.md` | "Butler-Managed Calendar Contract" with Track A/B documentation (lines 38-62) | bu-daaff / PR #1297 |

**This criterion does not require live verification.** All doc sections are
inspectable directly in the source tree.

---

### Criterion 7 — Live verification of the dashboard

**Status: CANNOT PERFORM FROM WORKER CONTEXT**

This autonomous worker has no browser access and cannot load
`/butlers-dev/chronicles`. This is the explicit blocker that prevents
closing bu-pas8k and the parent epic bu-rdvnx.

Per the bead description: "If you cannot reach the dashboard from the worker
context, file an explicit gap bead documenting the inability and do NOT close
this reconciliation."

This reconciliation is **NOT closed**. A follow-up bead must be filed to
gate on user live-verification (see below).

---

## Live-Verify Checklist (for human)

When loading `/butlers-dev/chronicles` after all migrations run:

- [ ] C1: Zero episodes with `trigger_source` in `{tick, qa, healing}` or
  matching `schedule:*` visible. Specifically: the two open qa episodes
  `ccbfbe85` and `ed23b522` (stuck open 24h+ as of 2026-04-29) should not
  appear.
- [ ] C2: All timestamps show "SGT" abbreviation. A time at
  `2026-04-30T00:00:00+00:00` should render as `2026-04-30 08:00 SGT`.
- [ ] C3: Calendar lane shows zero entries titled `memory_consolidation`,
  `memory_episode_cleanup`, or `memory_purge_superseded` within the 7-day
  window.
- [ ] C4: Music lane shows track titles and session durations (not "Private
  activity / sensitive"). Travel lane shows category and duration (not fully
  masked).
- [ ] C5: Conversations lane shows human-readable titles like "Conversation
  with Alice" or "Conversation via telegram" (not bare `{schema} session`
  for new episodes). Note: older pre-bu-fkqv0 episodes may still show
  `{schema} session` until bu-jpf3o (watermark reset backfill) completes.

---

## Open Gaps and Follow-Up Beads Required

The coordinator must file the following beads. This worker does NOT file them.

### Follow-up bead 1 (REQUIRED — gates bu-pas8k and bu-rdvnx closure)

**Title:** `verify(chronicles): live-verify bu-rdvnx criteria on /butlers-dev/chronicles dashboard`

**Description:**
Load `/butlers-dev/chronicles` in a real browser session and verify all five
visual criteria from bu-rdvnx:

1. Zero tick/qa/healing/schedule:* episodes visible (including ccbfbe85, ed23b522).
2. All times in Asia/Singapore (SGT abbreviation visible).
3. Calendar lane free of memory_consolidation / memory_episode_cleanup / memory_purge_superseded.
4. Music lane shows track titles; Travel lane shows category and duration.
5. Conversations show "Conversation with {name}" or "Conversation via {channel}", not bare schema labels.

Prerequisite: chronicler daemon must have restarted on dev so migrations
chronicler_007 and chronicler_008 run. Record pass/fail per sub-criterion.
If all pass, close bu-pas8k and then bu-rdvnx.

**Type:** task | **Priority:** P1 | **Dependency:** `discovered-from:bu-pas8k`

---

### Follow-up bead 2 (KNOWN ACCEPTED — in progress)

**bu-jpf3o** ("ops(chronicler): re-title existing {schema} session conversation
episodes") is already open and in progress. It provides the watermark reset
needed to re-title pre-bu-fkqv0 historical conversation episodes. Not a new
gap — it is a known accepted scope item. Criterion 5 is PASS for new episodes;
historical re-title depends on bu-jpf3o completion.

---

## Code-Level Pass/Fail Summary

| # | Criterion | Implementing Bead(s) | PR(s) | Code-Level Verdict |
|---|---|---|---|---|
| 1 | Zero heartbeat/QA/schedule:* episodes | bu-6t63s, bu-aqqx0 | #1299, #1308 | PASS |
| 2 | Timestamps in Asia/Singapore | bu-k18cm | #1298 | PASS |
| 3 | Calendar lane free of butler-internal tasks | bu-daaff, bu-aqqx0 | #1297, #1308 | PASS |
| 4 | Spotify track info; Owntracks envelope visible | bu-6c5i6 | #1303 | PASS |
| 5 | Conversation titles show contact names (new episodes) | bu-fkqv0 | #1300 | PASS (new) / historical backfill pending bu-jpf3o |
| 6 | Doc updates in same changes | all above | all above | PASS |
| 7 | Live-verify dashboard | — | — | CANNOT PERFORM (autonomous worker) |

---

## Criteria That Cannot Be Live-Verified from Worker Context

ALL visual criteria (1–5) and the explicit live-verify criterion (7) require
a real browser session:

- **Criterion 1:** Dashboard rendering of filtered episodes.
- **Criterion 2:** Timezone abbreviation display in the UI.
- **Criterion 3:** Calendar lane content in the 7-day view.
- **Criterion 4:** Music/Travel lane rendering with or without masking.
- **Criterion 5:** Conversation title rendering from resolved contacts.
- **Criterion 7:** The explicit mandate to load the dashboard in a real browser.

**bu-pas8k remains open.** Coordinator must file the live-verify follow-up
bead and coordinate user verification before closing this bead or the parent
epic bu-rdvnx.
