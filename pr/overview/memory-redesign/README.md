# memory-redesign/

A self-contained design hand-off package for `/memory` in the Butlers
app. Replaces the stacked card-grid memory page with a **house-ledger**
surface: an overture that answers "is remembering working", three
registers shaped to their kind (ledger / standing orders / daybook), an
attention rail that owns all state color, and a demoted housekeeping
band.

This bundle is recipe-driven (like `entity-redesign/`): no JSX mocks;
`MEMORY_LANGUAGE.md` plus the per-surface recipes under `prompts/` are
the implementation contract.

---

## The thesis

> **Show the believing, not just the beliefs.**

Memory is the only subsystem whose defining mechanics are temporal —
consolidation runs, confidence decays, confirmations re-ink, rules are
proven or retired by outcome. Today's page renders the stored rows and
hides the process. The redesign makes the lifecycle the page's spine
(the pipeline band), renders decay typographically (confidence is ink;
fading facts dim), and makes provenance one click (`fact —derived_from→
episode`).

Per Dispatch §1b: state color appears only when state demands. A healthy
memory day reads as a calm set of books — zero red/amber pixels. The
moment episodes dead-letter or a rule turns harmful, that signal alone
claims color, in the rail.

---

## What's in the folder

```
memory-redesign/
├── README.md                  ← you are here
├── VISION.md                  ← binding Section 0: mission, moves, rejections, criteria
├── MEMORY_LANGUAGE.md         ← the /memory extension of Dispatch (the backbone)
├── DESIGN_LANGUAGE.md         ← canonical Dispatch spec (copy)
└── prompts/
    ├── 00-foundation.md       ← routes, data contract, API surface + deltas
    ├── 01-overture.md         ← header, voice, KPI strip, pipeline band
    ├── 02-register-facts.md   ← the ledger (default register)
    ├── 03-register-rules.md   ← standing orders
    ├── 04-register-episodes.md← the daybook
    ├── 05-search-and-rail.md  ← unified search, attention rail, activity
    ├── 06-detail-pages.md     ← fact / rule / episode editorial pages
    └── 07-housekeeping.md     ← retention policies, compaction, re-embed
```

---

## The five inviolable rules (quick reference)

1. **Composure is the brand.** Calm at 3am. Color and motion appear only
   when state demands.
2. **Type is the system.** Inter Tight (UI) · Source Serif 4 (Voice) ·
   JetBrains Mono (numerals, eyebrows). Hierarchy from type and rules,
   not shadows or fills.
3. **Surfaces, not cards.** One elevation. Hairlines and rhythm.
4. **Every element earns its place against state.** Empty sections
   disappear or show a single serif-italic line.
5. **One affordance per signal.** Status is one of {dot, glyph, numeral,
   dimming}.

See `DESIGN_LANGUAGE.md` for the full spec; `MEMORY_LANGUAGE.md` for the
memory-specific grammar (register shapes, belief typography, rail
conditions, voice lines).

---

## Next steps

1. Read `VISION.md` — the binding intent.
2. Read `MEMORY_LANGUAGE.md` — the page grammar and register shapes.
3. Implement in order: foundation (00) → overture (01) → ledger (02) →
   standing orders (03) → daybook (04) → search + rail (05) → detail
   pages (06) → housekeeping (07).
4. After ship, write `RECONCILIATION.md` auditing what landed against
   the pack — what's done, what drifted, what to propagate back.
