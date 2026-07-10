# Design — Chronicler Intent / Evidence / Activity

## Problem framing

Today every projected episode lands in one flat pile, and `aggregate/by-category`
unions their durations into a pie. A 5h calendar block and a 5h gaming session
are treated identically, so a scheduled-but-unattended meeting reads as 5h of
"calendar" — the owner's reported nonsense. The chronicler's own spec already
forbids treating calendar as attendance (§4.15), but that rule was never carried
into the counting layer.

The fix is a **layered model**, not a smarter pie.

## The three layers

```
                 owned by others (read-only)        owned by chronicler
                 ─────────────────────────────      ──────────────────────────────
  EVIDENCE   →   raw signals from read surfaces  →   (consumed, never stored as truth)
  INTENT     →   calendar / scheduled blocks     →   stored, shown as "planned", never counted
  ACTIVITY   →   inferred from EVIDENCE,          →   stored, COUNTED, carries confidence
                 optionally confirmed vs INTENT        + evidence_refs[] (the "why?")
```

- **Evidence** is the shared, append-only, butler-agnostic signal layer
  (`public.ingestion_events` + per-connector read surfaces + resolved entities
  via `relationship.entity_facts`). The chronicler reads it; it owns none of it.
  Producers do not know the chronicler exists — adding a producer is how you
  "wire up more data", with zero chronicler changes for it to start counting.
- **Intent** is what was planned. Calendar blocks project to Intent episodes.
  Intent is displayed (the ghost track) and used to *confirm* activities, but is
  **never counted as lived time on its own**.
- **Activity** is the inferred story. Each Activity is derived from one or more
  Evidence signals, carries a `confidence`, points back at its evidence via
  `evidence_refs[]`, and is the **only** layer any time/balance aggregate counts.

### Counting rule (the "calendar = 5h" fix)

An aggregate sums **Activity** layer time only. A calendar Intent block
contributes lived time **iff** an Activity corroborates it (e.g. GPS dwell at
the meeting location, a co-located participant, an on-device session). When
corroborated, the time is attributed to the **Activity lane** (e.g. Work,
Social), never to a "calendar" lane. Uncorroborated Intent contributes zero.

## Activity lane taxonomy

Top-level lanes are life-balance lanes, not data sources:

`Sleep · Exercise · Work · Play · Social · Travel · Eat · Rest`

`music`, `gaming`, `calendar` are no longer top-level slices: music/gaming are
*evidence* that feeds **Play**; calendar is *intent*. Each lane has a stable
color; the FE renders lanes, not sources. Source provenance survives in
`evidence_refs[]` and the drill-down.

## Two-tier inference

### Tier 1 — deterministic candidate projectors (no LLM, runs on cadence)

Existing adapters gain a candidate-emission step. Rule examples:

| Evidence signals | Candidate Activity |
|---|---|
| HR sustained > threshold + step cadence + GPS dwell @ known gym | Exercise |
| GPS still + accelerometer rest + clock window + `google_health` sleep | Sleep |
| `core.sessions` (`trigger_source=route`) | Work / Conversation |
| Spotify / Steam continuous play | Play |
| message burst in a thread + resolved participants (comms adapter) | Social |
| GPS movement between dwell points | Travel |
| finance txn @ food merchant + dwell (future) | Eat (corroborates) |

Candidates are cheap, idempotent, and may overlap/conflict — that is expected;
Tier 2 reconciles.

### Tier 2 — day-close reconciliation (existing once-daily LLM, upgraded)

The existing `chronicler_day_close` session is upgraded from "summarize bullets"
to **reconcile**: (a) merge duplicate candidates describing the same lived block
across sources; (b) resolve Intent-vs-Evidence conflicts (calendar said "gym 9am"
but GPS says home → drop the intent, do not count); (c) label ambiguous blocks
or leave them low-confidence; (d) write the narrative prose. Token-bounded,
once per day — the no-per-event-LLM invariant (§4.8) holds.

## Confidence model

`confidence ∈ {high, medium, low}` derived from the count of **independent
evidence kinds** corroborating the Activity:

- `high` — 2+ independent kinds (e.g. HR + GPS + steps).
- `medium` — 2 weakly-related kinds, or 1 strong canonical kind (e.g.
  `google_health` sleep record).
- `low` — single weak/ambiguous signal, or LLM-labeled without corroboration.

Confidence drives the UI hedge (solid vs dashed) **and** how the block is
surfaced for correction. Low-confidence activities **still count** toward
balance totals but are visually hedged and offered as correction prompts.

## Evidence chain

Each Activity exposes `evidence_refs[]` (already modeled via
`episode_event_links`). The API returns the chain so the FE can render:

```
🏃 Exercise · 45min · high
  └ why?
    • HR avg 142bpm  (google_health)
    • GPS dwell @ Anytime Fitness  (owntracks)
    • 2.1km on foot before/after   (owntracks)
  [ correct this ]
```

## Memory write-back loop (doctrine-amended, narrow)

The chronicler imports the **memory module** (like 9 other butlers). Module
storage is per-butler, so synthesized insights land in **`chronicler.*`** —
satisfying the "writes only to its own schema" invariant. Three write kinds:

1. **Synthesized insights** — durable facts with provenance + confidence + decay
   ("sleep debt building 5d", "haven't seen Alex in 3 weeks", "weekends skew to
   Play"). Cross-butler benefit flows through the **existing** memory
   consolidation / entity-promotion path — not a direct foreign write.
2. **Self-reminders** — low-confidence blocks get a "revisit when more evidence
   lands" marker so the next day-close re-reconciles after backfill.
3. **Entity-enrichment proposals** — when co-presence repeatedly resolves to a
   person, the chronicler **proposes** a fact to `relationship` **over MCP**
   (switchboard-routed), never writing `entity_facts` directly.

All write-backs are derived (never raw evidence), carry `source=chronicler`
provenance, decay, and never assert attendance from intent alone. The chronicler
still never ingests externally and never notifies the owner.

This loop also powers the **trends lens** — "this day vs your usual" reads the
chronicler's own synthesized baselines.

## Page surface

- **Day view (home).** Day Ribbon (horizontal Activity lanes) with a faint ghost
  Intent track above it; balance rings/bars annotated vs-usual; who-you-were-with
  (resolved entities + co-present time + channel); where-you-went map trail;
  Open-questions correction prompts for low-confidence blocks.
- **Zoom-out (week / month).** Balance trends (sleep / exercise frequency / work
  hours / play), streaks & anomalies, social cadence (who's gone quiet).
- Narrative prose sits on top, sourced from the reconciliation pass.

## Data-wiring roadmap (future evidence producers — each its own change)

Ranked by narrative value × effort. None require chronicler changes to count.

- **Tier A (already ingested, just project):** comms → Social (this change);
  finance txns → corroboration.
- **Tier B (new connectors, high value):** Google Photos (time+geo+faces);
  screen/app usage (Work-vs-Play disambiguation).
- **Tier C (depth):** workout detail (Strava/Garmin); reading (browser/Readwise).

## Trade-offs considered

- **Smarter pie vs. layered model.** A smarter pie (exclude all-day events, etc.)
  was rejected — it patches symptoms; the conflation between intent and lived
  time would persist for any scheduled-but-skipped block.
- **Per-event LLM labeling vs. deterministic candidates + day-close.** Rejected
  per-event LLM (violates §4.8, cost). Deterministic candidates keep routine
  projection cheap; the LLM only reconciles once daily.
- **Direct cross-butler writes vs. own-schema + MCP proposals.** Rejected direct
  foreign writes (breaks schema isolation and the chronicler's character). Narrow
  amendment keeps isolation intact.
- **Counting low-confidence activities vs. excluding them.** Chose to count but
  hedge — excluding them would under-report lived time and hide exactly the
  blocks most worth correcting.

## Source References

- Non-Negotiable Rules (vision.md): schema isolation, MCP-only inter-butler
  communication (write-back proposals to `relationship` are MCP-routed).
- `butler-chronicler/spec.md` §4.8 (No Per-Event LLM Invocation), §4.15
  (Calendar Scheduled Blocks Are Not Attendance Assertions).
- RFC 0014 (Chronicler Time Butler).
