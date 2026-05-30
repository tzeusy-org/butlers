# secrets-redesign/

A self-contained design hand-off package for `/secrets` in the
Butlers app. Replaces the existing flat-list `SecretsPage` with a
**passport-book** surface: a left spine of every credential the
house holds, a right page that opens any one in editorial depth.

The prototype lives in this folder. The implementation prompt is
`HANDOFF.md`. Everything else supports those two artifacts.

---

## The thesis

> **A butler announcing, not a chatbot reporting.**

Secrets are inherently opaque — the user shouldn't need to *see*
a value to trust it. The redesign replaces the **`••••••••`** masked-
value blob with **evidence about the value**: a fingerprint, a scope
inventory, a last-verified probe, the provider state, and — when sick
— an explicit list of which butler features will silently fail.

Per-Dispatch §1b: state colour appears only when state demands. A
quiet day on `/secrets` reads as a calm inventory; the moment one
OAuth expires, that row's severity claims its own visual authority,
and only that row.

---

## What's in the folder

```
secrets-redesign/
├── README.md                 ← you are here
├── HANDOFF.md                ← the prompt for Claude Code
├── DESIGN_LANGUAGE.md        ← canonical Dispatch spec (copy)
├── BRIEF.md                  ← Stage 1 brief, captured for context
├── INDEX.md                  ← file map and quickstart
├── Secrets.html              ← the canonical prototype, open in a browser
├── SecretsProposals.html     ← the Stage 2 canvas (Ledger / Vault / Passport)
├── primitives.jsx            ← shared with overview/ (palette, ButlerMark, Spark)
├── secrets-data.jsx          ← the data contract (provider catalog, sample records)
├── secrets-shared.jsx        ← base atoms (Mono, Voice, ProviderMark, StateDot, …)
├── secrets-evidence.jsx      ← Stage-3 evidence atoms (WhatBreaks, ProbeResult, …)
├── secrets-spine.jsx         ← the left index
├── secrets-pages.jsx         ← per-kind page renderers (User / System / CLI)
├── secrets-passport.jsx      ← composition (state, header, book body)
├── tweaks-panel.jsx          ← starter Tweaks shell
├── secrets-tweaks.jsx        ← Tweaks bindings (reveal-mode, default-sort, …)
├── prompts/
│   ├── 00-foundation.md      ← data model + API surface + routes
│   ├── 01-spine.md           ← spine implementation
│   ├── 02-page-user.md       ← integration page
│   ├── 03-page-system.md     ← system secret page
│   ├── 04-page-cli.md        ← CLI runtime page
│   └── 05-tweaks-and-state.md← tweaks panel, state, persistence
└── preview/                  ← screenshots from iteration; safe to ignore
```

---

## Open the prototype

```bash
# from the project root
open secrets-redesign/Secrets.html
```

Click any row in the spine to flip the page. Toggle **Tweaks** from
the toolbar to surface the panel.

---

## The five inviolable rules (quick reference)

1. **Composure is the brand.** Calm at 3am. Colour and motion appear
   only when state demands.
2. **Type is the system.** Inter Tight (UI) · Source Serif 4 (Voice) ·
   JetBrains Mono (numerals, eyebrows). Hierarchy from type and rules,
   not shadows or fills.
3. **Surfaces, not cards.** One elevation. Hairlines and rhythm.
4. **Every element earns its place against state.** Empty sections
   disappear or show a single serif-italic line.
5. **One affordance per signal.** Status is one of {dot, sliver,
   numeral, colour}.

See `DESIGN_LANGUAGE.md` for the full spec.

---

## Next steps

1. Read `BRIEF.md` for what `/secrets` does and the decisions taken.
2. Open `Secrets.html` and click around. Read `HANDOFF.md`.
3. Implement in this order:
   foundation (00) → spine (01) → page-user (02) → page-system (03) →
   page-cli (04) → tweaks (05).
4. After ship, write `RECONCILIATION.md` auditing what landed against
   the pack — what's done, what drifted, what to propagate back.
