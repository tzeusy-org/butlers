# Dispatch — Page Recipes

One recipe per page. Each recipe answers: *what's the narrative spine,
what's the right column, what's the empty state, where (if anywhere)
does the system speak in serif?*

Read `DESIGN_LANGUAGE.md` and `PATTERNS.md` first. Use this as a
starting menu, not a constraint.

---

## Overview (canonical reference)

**Spine:** date eyebrow → display headline (greet + body) → serif
elaboration → attention list → KPI strip.
**Right:** Butlers index, Next list.
**Voice:** the briefing paragraph (LLM with deterministic fallback).
**Empty:** *"Everything is in hand."* + serif single sentence describing
the day.
**Status pill:** briefing source (llm / templated / composing…).

---

## Approvals

**Spine:** eyebrow → "**N items waiting**" display headline → optional
serif gloss ("Two are routine, one is unusual.") → grouped list:
*Awaiting you* / *Awaiting butler* / *Recently resolved*. Each row
ends with a primary commit button (`Approve` / `Decline`).
**Right:** *Filters* (kind, butler, severity), *Lately approved* index.
**Voice:** none by default. The serif gloss is optional, only when
counts hit a threshold worth narrating.
**Empty:** *"Nothing waiting your call."*
**Status pill:** none — this page reports on the world, not on itself.
**Density:** rows are 56px tall (taller than Overview's 40px) — the
user reads each one and decides.

---

## Butler detail (e.g., `/butlers/calendar`)

**Spine:** large letter-mark in butler hue (40×40, `tone="fill"`) +
butler name display headline → serif gloss ("The calendar butler reads
your shared calendars and notices what's new.") → 24h stripe chart →
recent moments list → settings link.
**Right:** *Status* (uptime, last error, last successful run),
*Connected sources* index, *Spend* mini-KPI.
**Voice:** the butler's own description. One paragraph, written by you,
not the LLM. This is the brand speaking.
**Empty:** *"This butler hasn't done anything in 24 hours."*
**Status pill:** "last patrol · 3m ago" — the canonical process pill.

---

## Calendar

**Spine:** day-view as a single 720-px-wide column, mono times in the
gutter, no grid lines — events are rule-separated rows positioned
vertically. The "now" line is a hairline of `--fg`. No alternate
month/week chrome.
**Right:** *Today's totals* (hours booked / free), *Up next* index
(showing the next 3 events with relative time).
**Voice:** none. Calendar is data, not prose.
**Empty:** *"Nothing scheduled today."*
**Status pill:** "synced · 1m ago" if the calendar source pushes status.
**Critical anti-pattern:** do not draw a 7-column week grid. We are not
Google Calendar.

---

## Audit Log

**Spine:** date eyebrow → infinite rule-list. Each row:
mono-timestamp / actor letter-mark / sans verb-phrase / serif detail.
No filters chrome, no card.
**Right:** *Filters* (actor, kind, time-window). Filters are pill
buttons in a vertical stack, not a search-bar form.
**Voice:** none. Audit is forensic — the data speaks.
**Empty:** *"No entries in the selected window."*
**Density:** 32px row height. Headline this is a place to *scan*.
**Critical pattern:** every row is fully self-contained — no expandable
"details" panel, no modal. If the row needs more, write more in the row.

---

## Settings

**Spine:** date-less. Eyebrow ("Settings") → display headline ("Tune
the staff.") → two-column form: section eyebrow + serif description on
the left, controls on the right. **No card around the form.**
**Right:** non-applicable — the form *is* the page.
**Voice:** every section opens with a serif sentence describing what
the setting changes. *"How frequently the calendar butler checks for
new events."*
**Empty:** non-applicable.
**Status pill:** none.
**Critical pattern:** form labels are sans-medium, not all-caps. The
serif description is what tells the user what's going on.

---

## Issues

**Spine:** eyebrow → "**N issues**" display headline → grouped by
severity (`high` → `medium` → `low`). Within group: attention-list
pattern. Severity glyph in the gutter (6px square).
**Right:** *Recently resolved* index.
**Voice:** when 0 issues, *"All clear."* (serif italic).
**Empty:** *"All clear."* — same line.
**Status pill:** none.
**Color rules:** state color appears in severity glyphs only; do not
color row backgrounds, do not color row borders.

---

## Memory / Entities

**Spine:** eyebrow → "**N entities**" display headline → search input
(plain, hairline-bottom only — not a card-input) → infinite rule-list of
entities. Each row: kind glyph / canonical name / serif aliases / count.
**Right:** *Kinds* index (filter by entity type).
**Voice:** none.
**Empty:** *"No entities indexed yet."*
**Status pill:** "indexed · 12m ago".

---

## Sessions

**Spine:** stripe-chart hero (24h) → rule-list of sessions, newest
first. Each row: mono-time / butler letter-mark / serif "what happened"
/ duration / cost.
**Right:** *Active* (currently-running sessions), *Cost today* mini-KPI.
**Voice:** the per-session description (already serif in the prototype
data shape). Brief.
**Empty:** *"No sessions in the last 24 hours."*
**Status pill:** none.

---

## QA

**Spine:** eyebrow → display headline reflecting last patrol state
("Last patrol — clean.") → serif gloss summarizing what was checked →
rule-list of recent patrols.
**Right:** *Findings* (24h count, novel count), *Patrol cadence*
mini-stat.
**Voice:** the post-patrol gloss is the killer feature here. *"All
butlers responded within 200ms. Health checks normal. One stale
moment cleaned up."*
**Empty:** *"QA staffer not active."* (serif italic).
**Status pill:** "patrolling…" / "idle · last 12m ago".

---

## Notifications

**Spine:** segmented pill (`failed` / `delivered` / `all`) → rule-list.
Each row: severity glyph / channel / serif body / mono-time / retry
arrow.
**Right:** *Channels* index (per-channel health).
**Voice:** none.
**Empty:** *"No notifications in the selected view."*
**Status pill:** none.

---

## A note on commands & search (Cmd-K)

The command palette is a separate surface, not a page. It uses the
same primitives (mono eyebrow per group, sans-medium command label,
serif description below) but lives in a centered modal at 600px wide.
Background dims to 60% black. Keep one keystroke per command.

---

## When you can't find a recipe

If you're redesigning a page not listed above:

1. Decide: is it **narrative** (events, flows, state-of-the-day) or
   **data** (lists, filters, fields)?
2. Narrative → two-column editorial. Headline + voice + attention list
   on the left, indices on the right.
3. Data → single 1280-max column. No headline; eyebrow + filter pills +
   rule-list.
4. Pick a *voice surface* only if the system has something it can
   honestly say. If not, no serif. Sans + mono is enough.
