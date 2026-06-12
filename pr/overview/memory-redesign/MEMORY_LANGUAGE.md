# Memory Language — the `/memory` extension of Dispatch

> This document extends `DESIGN_LANGUAGE.md` (the canonical Dispatch spec,
> binding) with the grammar specific to the memory surface. Where the two
> disagree, Dispatch wins. Read Dispatch first.

---

## 1 · Thesis and metaphor

> **Show the believing, not just the beliefs.**

A well-run house keeps three books:

- the **daybook** — pencil notes taken during service. Provisional,
  voluminous, dated, mostly discarded. *(Episodes.)*
- the **ledger** — entries transcribed in ink at the evening write-up.
  Durable, attributed, amendable, occasionally re-inked. *(Facts.)*
- **standing orders** — rules of service distilled from experience,
  kept only while they prove themselves. *(Rules.)*

The evening write-up is consolidation. Fading ink is decay. Re-inking is
confirmation. The back office — retention policies, compaction, re-embed —
is housekeeping: essential, and never performed in front of guests.

**The metaphor governs form, never nouns.** Labels in the UI are Episodes,
Facts, Rules, consolidation, permanence, validity — the product's precise
vocabulary. The daybook/ledger/standing-orders grammar dictates only the
*shape* each register takes: its row template, its rhythm, its typography.

---

## 2 · Page grammar

One column, 1280px max, in four bands top to bottom:

```
┌──────────────────────────────────────────────────────────────┐
│ MEMORY                                    (mono eyebrow)     │
│ What the house believes.                  (display, 44px)    │
│ Forty-one observations await the evening                     │
│ write-up; the last ran at 06:00 and       (voice, serif)     │
│ produced twelve facts.                                       │
│                                                              │
│ PENDING      ACTIVE FACTS   PROVEN RULES   LAST WRITE-UP     │
│ 41           3,182          9              06:00 · 12 facts  │ ← KPI strip
├──────────────────────────────────────────────────────────────┤
│ episodes 1,204 ─→ pending 41 ─→ facts 3,182 · fading 207     │
│ ─→ rules 58 · proven 9        dead letters 0                 │ ← pipeline band
├───────────────────────────────────┬──────────────────────────┤
│ / search                          │  NEEDS ATTENTION         │
│ [Facts] [Rules] [Episodes]        │  · 3 dead-letter eps     │
│                                   │  · rule §07 harmful ×4   │
│ ┌ the focused register ─────────┐ │  ──────────────────────  │
│ │ (ledger / standing orders /   │ │  RECENT ACTIVITY         │
│ │  daybook row grammar)         │ │  14:21 [m] fact stored   │
│ └───────────────────────────────┘ │  14:09 [r] rule applied  │
├───────────────────────────────────┴──────────────────────────┤
│ HOUSEKEEPING                              (mono eyebrow)     │
│ retention policies · compaction log · embeddings             │ ← quiet band
└──────────────────────────────────────────────────────────────┘
```

- **Band 1 — Overture.** Eyebrow, display headline, one Voice sentence
  narrating the system's own process (cadence, last run, output), KPI
  strip (4 hairline-divided cells, mono eyebrows + mega-numbers).
- **Band 2 — Pipeline.** The lifecycle as a single line of mono numerals
  with `─→` connectors. This is the page's load-bearing state readout.
- **Band 3 — Registers + rail.** `grid-template-columns: 1.4fr 1fr`,
  gap 56px. Left: one search input, kind pills, the focused register.
  Right: attention list, then recent activity. State color appears in the
  rail and nowhere else (one exception: dead-letter numeral in the
  pipeline band when non-zero).
- **Band 4 — Housekeeping.** Single mono eyebrow; three quiet sub-surfaces
  in small type. No cards, no panel chrome.

Register choice (`Facts` default), search query, and validity filter are
URL params — back button works. Search text input itself is local state.

---

## 3 · Register shapes

### 3a · The ledger (Facts) — default register

Rule-separated grid rows, hairline `--border-soft` between rows:

```
subject · predicate              content                      belief
─────────────────────────────────────────────────────────────────────
Owner · preferred_pain_relief    ibuprofen, after meals       0.94  st
Owner · works_at                 Endowus, since 2023          0.88 pm
Wei · favorite_coffee            flat white, oat milk         0.31 vo   ← dimmed
```

- **Subject** sans 13px; entity-anchored subjects are links (underline
  offset 4px) to `/entities/:id`. **Predicate** mono 11px muted, joined
  with a `·`.
- **Content** sans 13px, single line, truncated; the row is the hit
  target, opening `/memory/facts/:id`.
- **Belief column**, right-aligned, mono tabular: effective (decayed)
  confidence to two places, then a two-letter permanence tag
  (`pm` permanent · `st` stable · `sd` standard · `vo` volatile ·
  `ep` ephemeral), muted.
- **Confidence is ink.** Row foreground maps to effective confidence:
  ≥ fading threshold → `--fg`; below it (validity `fading`) → `--dim`,
  including the content. No color, no italic, no badge — dimming is the
  single affordance for decay.
- Validity filter pills (`active` default · `fading` · `superseded` ·
  `expired` · `retracted`); non-active validities never render unfiltered.
- A `derived_from` glyph (`↳`, mono, muted) at row end when a source
  episode exists; hover reveals `from episode <id-short>`.

### 3b · Standing orders (Rules)

Numbered directives, generous row padding (rules are read, not scanned):

```
§01  Suggest a sleep study when fatigue is reported          stable
     three days running.
     applied 41 · helpful 38 · harmful 1                     0.86
```

- `§NN` mono in the gutter, zero-padded, ordered by maturity rank then
  confidence.
- Directive content sans 14px, wrapping, max 2 lines in the register.
- Tally line mono 11px muted: `applied N · helpful N · harmful N`; the
  word `harmful` and its numeral take `--red` only when harmful > 0.
- Maturity as a plain mono word at row end (`candidate` · `established` ·
  `proven` · `anti_pattern` — exactly the API's vocabulary, lowercase, no
  chip). Anti-pattern rules additionally carry a 2px left sliver in
  `--red` — the only state color permitted inside a register.

### 3c · The daybook (Episodes)

A journal feed grouped by day, mono day-headers as hairline-bound rules:

```
─ THU 12 JUN ────────────────────────────────────────────────
14:21  [c]  Owner mentioned fatigue again during the          ◦
            afternoon check-in; took ibuprofen.
09:02  [h]  Logged breakfast: eggs, toast, espresso.          •
```

- Mono time gutter (50px), butler letter-mark (the only place butler hue
  appears), content sans 13px clamped to 2 lines, expandable in place.
- Consolidation state is a single glyph at row end: `◦` pending (hollow),
  `•` consolidated (filled), `✕` dead-letter (`--red`). Never a word,
  never a chip.
- Importance ≥ 8 renders the time in `--fg` instead of muted — weight
  through ink, consistent with the ledger.

### 3d · Search results

The search band (one input, backed by the inspect endpoint, kind pills
scoping it) does not introduce a fourth shape: results render in the
register shape of their kind, under mono kind-group headers. Clearing the
query restores browsing. Empty result: one serif-italic line —
*"Nothing in the books."*

---

## 4 · Belief typography

| Signal | Rendering | Never |
|---|---|---|
| Effective confidence | mono numeral, 2 places, tabular | progress bar, donut, percent sign |
| Decay | foreground dims to `--dim` at fading threshold | color, strikethrough, opacity animation |
| Permanence | two-letter mono tag, muted | colored chip, icon |
| Confirmation | detail page stamp: `confirmed 2026-06-02 · health` mono | green check, toast celebration |
| Consolidation state | glyph {`◦`, `•`, `✕`} | word badge ("Consolidated") |
| Rule maturity | lowercase mono word | colored pill, star rating |
| Rule harm | `--red` on the harmful tally + left sliver when anti-pattern | red row background |
| Importance | ink weight (muted → `--fg`) | flame icons, numbered badges |

Detail pages show the decay arithmetic honestly, in one mono line:

```
confidence 0.94 · decays 0.002/day · last confirmed 12d ago · effective 0.92
```

---

## 5 · The attention rail

The rail is the only surface where state demands color. Attention-list
rows (24px glyph gutter · title + serif detail · action arrow), at most
one commit-class action per row:

| Condition | Severity | Row reads |
|---|---|---|
| dead-letter episodes > 0 | red | `3 episodes dead-lettered` → episodes register, filtered |
| consolidation stalled (last run > 2× cadence) | amber | `write-up overdue · last 06:00` |
| rule turned anti-pattern / harmful streak | red | `§07 harmful ×4` → rule page |
| high-importance fact entering fading | amber | `2 important facts fading` → ledger, filtered |
| stale embeddings (model drift) | amber | `412 rows on old embedding` → housekeeping |

Empty rail: the header stays, body collapses to one serif-italic line —
*"Nothing waiting."* Below the attention list, **Recent activity**: mono
time · butler letter-mark · sans summary, 20 rows, no color.

---

## 6 · Color discipline

- A healthy memory page renders **zero red/amber/green pixels**.
- `--red` may appear in exactly three places, each only when its state
  exists: the dead-letter numeral in the pipeline band, the rail, an
  anti-pattern rule's sliver + harmful tally.
- `--amber` appears only in the rail.
- `--green` does not appear on this page. A healthy pipeline is the
  absence of alarm, not a celebration.
- Butler category hues appear only on letter-marks (daybook gutter,
  activity rows).

---

## 7 · Voice

The Voice line narrates process, never content. Past tense for events,
present for state, numbers exact:

- `Forty-one observations await the evening write-up; the last ran at
  06:00 and produced twelve facts.`
- `The pipeline is idle. Nothing pending since 06:00.`
- Empty ledger (filtered): *"No facts answer this."* (serif italic)
- Empty daybook: *"Nothing observed yet today."*
- Empty rail: *"Nothing waiting."*

No first person. The system does not say "I remember."

---

## 8 · Motion

Inherits Dispatch's table verbatim. The only additions permitted:

| What | Duration | Easing |
|---|---|---|
| Register cross-fade on pill switch | 200ms | `cubic-bezier(0.22, 1, 0.36, 1)` |
| Daybook row expand | 120ms height | linear |

No skeleton-pulse while registers load — reserve layout, render rows as
they arrive. Numerals never count up.

---

## 9 · Memory-specific anti-patterns

Beyond the Dispatch list:

- Confidence as a progress bar, gauge, or traffic-light.
- A "memory health score" or any composite grade.
- Knowledge-graph hairballs, embedding scatterplots, t-SNE clouds.
- Decay sparklines on register rows (the detail page states the
  arithmetic in one line instead).
- "🧠" or any memory iconography; the sidebar icon is the only icon.
- Metaphor nouns leaking into labels ("Daybook", "Ledger" as tab names).
- Rendering superseded/expired facts in the default ledger view.
- A second search box.
- Celebrating consolidation runs ("12 new memories! 🎉").
- Equal visual weight for housekeeping and knowledge.

---

## 10 · North-star test, localized

The global test: *would I trust the typography of this sheet of paper?*

The memory variant: **would a meticulous house ledger, opened to today's
page, look like this?** Ruled lines, ink in two or three weights, marginal
glyphs, a fading entry visibly older than a fresh one, and not one
ornament. If a proposed element would embarrass that ledger, it does not
ship.
