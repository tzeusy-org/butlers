# QA Staffer redesign — Claude Code handoff

The mock lives at `overview/Qa.html` (open in the project preview). It's the **dossier** layout finalized with the **patrol journal** as a full-width, always-visible section under the diagnosis + PR row.

## What's on the page (left → right, top → bottom)

1. **Page header** — `What the staff caught and fixed` + clock + "patrol every 14m" caption.
2. **KPI strip** — `prs landed · 24h`, `mttr · 24h`, `self-resolved · 7d`, `hours saved · 7d`.
3. **Case list rail** (320px, left) — sev dot, id, butler, headline, detected/age, PR-state dot.
4. **Dossier body** (right) — header with sev/id/butler/detected + **state track** (detected → diagnosed → pr open → landed); two columns:
   - **Left col · Diagnosis** — claim-anchored serif blurb (`[1]`, `[2]` superscripts highlight matching evidence on hover), monospaced one-line **hypothesis**, **evidence log fragments** (each tagged with the claims it supports), **considered & ruled out**.
   - **Right col · Proposed fix** — PR panel with state chip, title, branch + CI + diff stats, "Why this fix" italic line, **inline diff preview**.
5. **Patrol journal** (full-width, below) — chronological row per QA decision: timestamp · kind · text · detail. Kinds: `flagged · sampled · cross-checked · considered · concluded · drafted · wait · merged · tick`.

Sticky top bar: severity filter (all/high/medium/low) + theme toggle. Sidebar shared with Overview/Butlers, with `/qa` active.

---

## Assets to export

Drop these into a `qa-redesign/` folder in your codebase. Everything is plain JSX-as-script + a single HTML host — no build step.

### Mock + page logic (the design itself)
- `overview/Qa.html` — host page
- `overview/qa-page.jsx` — dossier layout, KPI strip, sidebar wiring, sticky filter bar
- `overview/qa-shared.jsx` — small components: `QSev`, `QPRChip`, `QStateTrack`, `QLogLine`, `QEyebrow`, `QKpi`, `QBars` + helpers `qaStageOf`, `qaFilterCases`, `qaFilterTail`
- `overview/qa-data.jsx` — synthetic `QA_CASES`, `QA_TAIL`, `QA_KPIS`, `QA_PATROL_24H`, `QA_PR_7D`, `QA_BY_BUTLER_7D`
- `overview/qa-data-extra.jsx` — extends each case with `blurbSegments`, `claims`, `evidence` (with ids), **`reasoning` (the patrol journal)**, `counterEvidence`, `whyThisFix`, `diff`

### Shared infrastructure (already used by Overview/Butlers)
- `overview/primitives.jsx` — palettes, `applyTheme`, color tokens (`window.C`)
- `overview/sidebar.jsx` — left nav with `/qa` route active
- `overview/data.jsx` — `BUTLERS_DATA` (used by sidebar)
- `overview/DESIGN_LANGUAGE.md` — type/color/spacing rationale
- `overview/IMPLEMENTATION.md` — existing implementation notes from Overview/Butlers builds

### Optional / reference only (do not need to ship)
- `overview/QaDirections.html` + `overview/qa-proposals.jsx` — the four original layout explorations (pipeline/dossier/ledger/console)
- `overview/QaEnhancements.html` + `overview/qa-cd-proposals.jsx` — the C/D enhancement explorations (cadence sparkline, related cases, editorial usefulness, heartbeat, coverage gaps) — useful if you want to revisit any of them later

---

## Suggested Claude Code prompts

Drop these in order. Each one is scoped to a single concern so review stays manageable.

### 1 · Backend shape
> I'm rebuilding the QA staffer page. Here's the design mock and synthetic data: `qa-redesign/qa-data.jsx`, `qa-redesign/qa-data-extra.jsx`. Look at the case object shape (id, sev, butler, headline, detected, state, age, blurbSegments, claims, evidence, reasoning, counterEvidence, whyThisFix, pr, diff) and propose the smallest plausible backend schema in our existing FastAPI/SQLModel layer. Where does each field come from — staffer trace, GitHub, parsed log fragments? Which are derived vs. stored? Output a migration plan that keeps existing patrol/PR records compatible.

### 2 · Patrol-journal capture
> The mock's "patrol journal" (the `reasoning` array in `qa-data-extra.jsx`) is the timeline of every QA decision on a case. Look at our current QA staffer loop in `butlers/qa/` — wherever it currently logs steps. Wire up a structured journal: a typed event per `flagged · sampled · cross-checked · considered · concluded · drafted · wait · merged · tick` step, each with `ts`, `kind`, `text`, `detail`. Persist to the case row. Don't change behavior — just capture what's already happening.

### 3 · Page scaffold
> Port `qa-redesign/Qa.html` + `qa-page.jsx` into our Next.js app at `frontend/src/app/qa/page.tsx`. Reuse the existing sidebar, theme toggle, and severity filter from `/overview` and `/butlers`. Match the type stack (Inter Tight / JetBrains Mono / Source Serif 4) and color tokens from `frontend/src/styles/tokens.css`. Use shadcn/ui where it cleanly maps (only for primitives — keep the bespoke layout). No data wiring yet — read from the static fixtures.

### 4 · Component port
> Port these components from `qa-redesign/qa-page.jsx` to `frontend/src/components/qa/`, one per file: `CaseList`, `CaseDossierHeader`, `StateTrack`, `ClaimAnchoredBlurb` (with hover-linked evidence), `EvidenceLog`, `CounterEvidence`, `PRPanel`, `DiffPreview`, `PatrolJournal`. Keep the claim-anchor hover behavior — that's the most important interaction on the page. Use Tailwind for layout but keep the existing exact spacing, type sizes, and 1px hairline borders from the mock.

### 5 · Wire up data
> Replace the static fixtures with real queries against the schema from step 1. Keep the page server-rendered for the case list + selected case; stream the patrol journal in via a server component. The "tick" entries (heartbeat patrol cycles where the case was re-checked but nothing changed) should come from the patrol log, not be persisted per-case. Add `/qa` to the sidebar nav config.

### 6 · Behaviors not in the mock
> Two interactions the static mock doesn't show, please add:
> - URL-driven case selection: `/qa?case=#218` selects that case; clicking a case in the rail updates the URL.
> - Live updates: when a new patrol cycle adds an entry to the selected case's journal, append it without a full reload (server-sent events or polling — match what `/overview` does).

---

## Open questions for whoever owns this

- **Modeling "hours saved"** — currently a constant 22m/case in the mock. What's your real heuristic? (PR review time avoided? mean human-triage minutes from on-call data?)
- **Diff preview source** — pull the full diff from the GitHub PR, or store the staffer's drafted patch separately? Latter survives if the PR is force-pushed; former stays canonical.
- **Claim-anchor authoring** — in the mock the LLM produces `blurbSegments` + `claims` directly. Do you want a post-processing pass that takes a plain blurb and an evidence list and emits anchored segments, or is structured generation upstream OK?
- **Severity heuristic** — currently a hand-set field per case. Worth deriving from PR scope (auto-mergeable mechanical fix → low; touches frontend or auth → high)?

---

## File index — copy these to your handoff bundle

```
qa-redesign/
├── Qa.html
├── qa-page.jsx
├── qa-shared.jsx
├── qa-data.jsx
├── qa-data-extra.jsx
├── primitives.jsx
├── sidebar.jsx
├── data.jsx
├── DESIGN_LANGUAGE.md
├── IMPLEMENTATION.md
└── QA_HANDOFF.md       (this file)
```

To preview standalone: open `Qa.html` in any browser — no server needed.
