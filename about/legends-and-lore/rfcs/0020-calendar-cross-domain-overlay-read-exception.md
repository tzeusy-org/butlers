# RFC 0020: Calendar Cross-Domain Overlay Read Exception

**Status:** Proposed
**Date:** 2026-06-20

---

## Summary

The calendar workspace wants to become the surface where every butler's
time-bound output converges: read-only domain overlays (finance bills/renewals,
travel trip ribbons, relationship important-dates, health appointments), a
meeting-prep rail, and a cross-signal "tomorrow at a glance" day-briefing card
(bead `bu-1ajgg9`). Every one of these features reads another butler's per-schema
domain state — `finance.*`, `travel.*`, `health.*`, `relationship.*` — which
Non-Negotiable Rule 3 (MCP-only inter-butler communication) forbids except under
the sanctioned exception already defined in **RFC 0010 (Cross-Butler Briefing
Exception)**.

This RFC evaluates the proposed calendar design against RFC 0010's reuse criteria
and finds the **naive design** — a per-open, on-demand overlay that fans out to
sibling schemas and runs LLM synthesis at render time — **fails two of the five
required criteria** (#2 Deterministic / no LLM session, and #3 Batch / not
real-time or on-demand). Under current doctrine the answer to that design is
therefore **NO**.

The recommendation is to adopt the RFC-0010-compliant pattern instead: a
**scheduled deterministic job precomputes per-day overlay and briefing
contributions into a read-only cached view** that the calendar reads directly
(zero LLM at render, batch-refreshed) — the exact mechanism RFC 0010 already
sanctions for the EOD briefing. The rejected alternatives are documented below.
Acceptance is left to the owner; this RFC is **Proposed**.

## Motivation

The calendar Dispatch redesign (commit `b02eb9227`) turned the workspace into a
clean read-mostly grid. The next roadmap tier (`bu-1ajgg9`, under epic
`bu-l3k0zg`) wants to layer cross-domain context onto it:

- **Domain overlays** — finance bills/renewals, travel trip ribbons,
  relationship important-dates, and health appointments rendered as ribbons or
  pills on the relevant calendar days.
- **Meeting-prep rail** — for a selected event, attendees plus recent Gmail
  threads, relationship notes, and last-met context.
- **Day-briefing card** — an on-page cross-signal "tomorrow at a glance"
  summary.

All of this data lives in other butlers' schemas. The calendar module today fans
out a read-model **only across `butlers_with_module('calendar')`** — it does not
read `finance.bills`, `travel.trips`, `health.*`, or `relationship.*`. A calendar
API that reached directly into those schemas would violate Rule 3.

RFC 0010 already governs exactly this territory. It permits the General butler to
read specialist schemas through a read-only SQL view for the daily briefing, and
it defines, normatively, when that exception **MAY be reused** and when it **MUST
NOT** be. This RFC does not invent a new exception; it tests the calendar design
against the existing one and records the disposition.

## Design

### How the naive design is described in the bead

The originating epic (`bu-1ajgg9`) sketches the overlays/prep-rail/briefing as
**per-open LLM synthesis plus on-demand overlay render**: when the user opens the
calendar (or selects a day/event), the calendar resolves the cross-domain data
live and an LLM session synthesizes the briefing/prep text at that moment. This
is the design under evaluation.

### Evaluating the naive design against RFC 0010's reuse criteria

RFC 0010 §"MAY Be Reused When ALL of These Hold" lists five criteria; reuse is
permitted only when **all five** hold. The naive design is scored below.

| # | RFC 0010 criterion | Naive design | Verdict |
|---|--------------------|--------------|---------|
| 1 | Read-only, DB-enforced | Overlays read, do not write | PASS (if via view/grant) |
| 2 | **Deterministic — no LLM session in the extraction/aggregation** | Per-open LLM synthesizes briefing/prep text at render | **FAIL** |
| 3 | **Batch — fixed schedule, not real-time/on-demand** | Resolves on calendar open / day select | **FAIL** |
| 4 | Auditable — migration-tracked view/grants, explicit source attribution | Achievable | PASS (if implemented that way) |
| 5 | Cost-justified vs. Switchboard fan-out | A daily batch is; a per-open render is not (it multiplies LLM cost by opens/day) | conditional |

Two criteria fail outright:

- **Criterion #2 (Deterministic).** RFC 0010 is explicit: "No LLM session is
  involved in the data extraction or aggregation," and conversely it MUST NOT be
  reused when "LLM sessions are involved... If the cross-schema data needs LLM
  reasoning to extract, transform, or interpret, use Switchboard fan-out. The
  whole justification for this exception is avoiding LLM sessions for
  deterministic work." A per-open synthesis embeds an LLM session in the
  cross-schema read path — the precise condition the exception forbids.
- **Criterion #3 (Batch).** RFC 0010 requires the access be "batch-oriented
  (daily, hourly) with a fixed schedule, not real-time or on-demand," and MUST
  NOT be reused for "Real-time queries... If the data is needed on-demand during
  an LLM session... use MCP tool calls through the Switchboard. The exception is
  for pre-scheduled batch aggregation, not interactive queries." On-demand render
  at calendar-open time is exactly the real-time/on-demand pattern the exception
  excludes.

Because RFC 0010 grants reuse only when **all** criteria hold, the naive design
is **out of scope of the exception**. Under current doctrine, a calendar API that
reads sibling schemas on-demand with per-open LLM synthesis is **not permitted**
— RFC 0010 already answers it NO as designed.

### Recommendation: the RFC-0010-compliant overlay path

Adopt the same mechanism RFC 0010 already sanctions for the EOD briefing, applied
to the calendar:

1. **Scheduled deterministic contribution jobs.** Each contributing butler
   (finance, travel, health, relationship) runs a `dispatch_mode="job"`
   deterministic Python/SQL job on a fixed cron that writes its per-day overlay
   contributions into its own `state` store under a filtered key prefix (mirroring
   RFC 0010's `briefing/daily/%`; e.g. `calendar/overlay/<date>`). These jobs
   carry **zero LLM cost** and run on the daemon, not in a session.

2. **Read-only cross-schema view.** A migration-tracked SQL view in the calendar
   reader's schema UNIONs those filtered contributions across the contributing
   schemas, with an explicit hardcoded `butler` source-column per UNION term
   (RFC 0010 Guardrail #2) and a key filter that bounds access to overlay keys
   only (Guardrail #3). PostgreSQL forbids writes through a UNION view
   (Guardrail #1); SELECT grants are created by Alembic migration and reversible
   on downgrade (Guardrail #5).

3. **Calendar reads the cached view at render — zero LLM.** The overlay/prep-rail/
   briefing UI reads the already-computed, cached contributions through the view.
   Render is a pure read of precomputed data; no LLM session and no cross-schema
   fan-out happen at open time.

4. **If any LLM synthesis is genuinely wanted, it is batch and pre-rendered.**
   Any natural-language briefing/prep text is produced by **one scheduled
   session** that writes pre-rendered `summary`-style text into the contribution
   (exactly as RFC 0010's contribution envelope pre-renders `summary` so the LLM
   need not analyze raw domain data at delivery). The calendar then displays that
   text verbatim — no per-open session.

Under this pattern every RFC 0010 criterion holds: read-only (DB-enforced view),
deterministic (no LLM in the cross-schema read), batch (fixed-schedule
contribution + refresh), auditable (migration-tracked view + grants with explicit
source attribution), and cost-justified (a bounded number of scheduled jobs/
sessions per day rather than one synthesis per calendar open).

### Alternative compliant variant: drop the LLM/real-time aspects entirely

If precomputed LLM summaries are not worth the batch session, the simplest
compliant variant is to **drop synthesis altogether**: deterministic jobs write
structured (non-narrative) overlay rows, and the calendar renders them directly
as ribbons/pills/lists with no generated prose. This trivially satisfies
criteria #2 and #3 because no LLM is involved anywhere. It is the
lowest-risk path and is recommended if the briefing prose is not essential to v1.

## Reuse Criteria

This RFC does not create a new exception class; it **reuses** RFC 0010's
exception under RFC 0010's own reuse criteria. The recommended path is in scope
because it holds all five MAY-criteria; the naive path is out of scope because it
trips MUST-NOT criteria #1 (LLM sessions involved) and #3 (real-time queries).
Any future calendar overlay feature MUST be re-evaluated against RFC 0010's
criteria independently — adopting this RFC does not pre-authorize on-demand or
LLM-in-the-read variants.

## Alternatives Considered

**Per-open, on-demand overlay with LLM synthesis (the naive design).** Resolve
cross-domain data and synthesize briefing/prep text live whenever the calendar is
opened. **Rejected** — fails RFC 0010 reuse criteria #2 (deterministic / no LLM
session) and #3 (batch / not real-time), and trips MUST-NOT conditions #1 (LLM
sessions involved) and #3 (real-time queries). It also has no cost ceiling: LLM
cost scales with the number of calendar opens per day rather than being bounded
to a fixed daily schedule.

**Direct cross-schema fan-out from a calendar API (no view).** A calendar route
issues `SELECT ... FROM finance.bills`, `FROM travel.trips`, etc. directly in
application code at request time. **Rejected** — it is on-demand (criterion #3),
not migration-auditable (criterion #4; RFC 0010 §Alternatives rejects "Direct
cross-schema queries in application code" as fragile and invisible to DBAs
reviewing grants), and provides unbounded rather than key-filtered access
(MUST-NOT #4). Even with no LLM, the real-time/auditability failures are
disqualifying.

**Switchboard MCP fan-out at render time.** The calendar sends an MCP request to
each contributing butler via the Switchboard on every open; each spawns a session
to formulate its overlay/prep contribution. This is the architecturally pure
interactive path, but at calendar-open frequency it multiplies LLM sessions per
day for what is deterministic data extraction — the same cost objection RFC 0010
raised against the 9:1 briefing fan-out, made worse by being keyed to user opens
rather than a single daily run. **Rejected for the render-time path on cost
grounds.** (Switchboard fan-out remains the correct mechanism for any genuinely
interactive, judgment-requiring cross-butler query — see RFC 0010 MUST-NOT #3 and
RFC 0003.)

**Shared public.calendar_overlays table all butlers write to.** Contributing
butlers write overlay rows into a shared `public` table the calendar reads.
**Rejected** for the same reason RFC 0010 rejected `public.briefing_contributions`:
it introduces cross-butler write coupling to the `public` schema (read-only for
most butlers under RFC 0006) and sets precedent for arbitrary shared tables. The
per-schema contribution + read-only view keeps writes inside each butler's own
schema.

## Integration

- **RFC 0002 / Non-Negotiable Rule 3:** MCP-only inter-butler communication
  remains the default. This RFC does not modify the rule; it reuses the existing
  RFC 0010 exception under RFC 0010's own criteria. Interactive, judgment-bearing,
  or state-mutating cross-butler needs MUST still go through the Switchboard.
- **RFC 0003:** Switchboard fan-out remains the correct pattern for interactive
  cross-butler requests. It is rejected here only for the high-frequency
  render-time path, not as a mechanism.
- **RFC 0006:** The cross-schema view and SELECT grants are implemented as a
  reversible Alembic migration within the multi-chain migration model, scoped to
  the calendar reader's role (overriding the RFC 0006 search_path constraint for
  this one view via explicit grants only, exactly as RFC 0010 does for
  `general.v_briefing_contributions`).
- **RFC 0010:** This RFC is a scoped reuse of the Cross-Butler Briefing Exception.
  The recommended path mirrors RFC 0010's view + five guardrails + contribution
  envelope; the prohibition on LLM-in-the-read and on-demand access is inherited
  verbatim from RFC 0010's MUST-NOT criteria.
- **Calendar spec (`openspec/specs/module-calendar`):** The calendar module today
  registers "16 MCP tools total" and is "series-scoped in v1"; its read-model
  fans out only across `butlers_with_module('calendar')`. Any overlay-read
  implementation is additive to that surface and MUST land its own OpenSpec delta
  against the relevant capability (per the `bu-l3k0zg` planning contract);
  widening the documented contract is spec drift, not just code.
- **`bu-1ajgg9`:** If this RFC is accepted, that epic's overlay/prep-rail/briefing
  children must be rebuilt on the precompute-and-cache path (or the
  drop-synthesis variant); the per-open LLM render described in the bead is not
  buildable under current doctrine. The prep rail additionally depends on
  contact-link coverage (`bu-mcz0o9`) and co-attended edges (`bu-xgz7g.1`) or it
  renders empty for ~93% of events.

## Decision

**Proposed.** This RFC records the RFC-0010-compliant overlay path
(scheduled deterministic precompute → read-only cached view → zero-LLM render,
with any synthesis batched and pre-rendered) as the recommended design, and
documents why the naive per-open / on-demand / LLM-synthesis design is rejected
under RFC 0010's reuse criteria. The owner accepts or rejects; no overlay build
(`bu-1ajgg9`) proceeds until this decision is resolved.
