# Dispatch prompts and schemas — JARVIS pursuit fan-out

Templates for the Workflow `agent()` calls. Fill `{...}` slots at dispatch time. Every prompt
gets the same PREAMBLE and the run's "already known" ledger.

Each template names its **model tier** (SKILL.md → Execution discipline §2). Pass it as
`agent(prompt, {model, effort, schema})`. Dispatch these only in ≤3-wide hourly batches (§1) and
checkpoint each batch to the harvest file (§3).

## Shared preamble (prepend to every agent prompt)

```
You are one auditor in a fan-out pursuing a world-class JARVIS-like system for the Butlers
project (repo: /home/tze/gt/butlers). Ground truth for "world-class":
- about/heart-and-soul/vision.md (read it)
- docs/frontend/purpose-and-single-pane.md (read it if auditing a UI surface)
- The north star: {north-star paragraph from the latest dossier}

Backward compatibility is WAIVED. Propose the ideal design; redesigns, removals, and breaking
changes are all in scope. You are generating NEW ideas: the following are already known or
in-flight — if you rediscover one, DROP it, do not report it:
{already-known ledger bullets}

Rules of evidence: every finding cites file:line (or a spec/doc section). Never speculate
about behavior you can read the code for. Your final message is raw data for a synthesizer,
not prose for a human.
```

## Page-surface auditor (Phase 1, one per surface)

**Model:** `sonnet` / `medium`.

```
{PREAMBLE}

Surface: {surface name} — routes {routes} — primary files {entry files from router-config}.

Load /home/tze/.claude/skills/th-design/SKILL.md and its design-bar subskill, then hold this
surface to that bar. Walk it as the owner would: what does it open with, what needs them,
how many keystrokes/hops from any signal to its root evidence, what does failure look like,
what does empty look like, is anything rendered that the system cannot prove?

Specifically probe the five systemic sins from prior runs (report NEW instances only):
fabricated data with real-data authority; errors impersonating calm empty states
(`data ?? []` with isError dropped); drill-down dead ends (links that discard their context);
missing keyboard/shortcut surface on hot loops; visual-dialect drift from the Dispatch spec.

Then go beyond critique: describe the ideal JARVIS-grade version of this surface in one
paragraph, and derive ranked moves from the gap.
```

## Cross-cutting sweeper (Phase 1, one per lens)

Lenses: `shell-discoverability` (command palette, shortcut registry, navigation, empty states
that teach), `visual-language` (Dispatch spec adherence, token usage, chart language),
`interaction-speed` (polling vs streams, optimistic mutations, latency budgets, preloading),
`accessibility` (keyboard operability, focus, contrast, semantics, reduced motion).

**Model:** `sonnet` / `medium`.

```
{PREAMBLE}

Lens: {lens}. Load /home/tze/.claude/skills/th-design/subskills/{matching subskill}/SKILL.md.
Sweep the WHOLE frontend (frontend/src/) for this lens only — you are the one agent who sees
across pages, so prioritize inconsistencies between surfaces and patterns built once but never
generalized. Report NEW instances only; propose the unifying mechanism (registry, lint rule,
shared hook, token) not just the symptom list.
```

## Ecosystem lens ideator (Phase 2, one per lens)

**Model:** `sonnet` / `medium` — except the **inference-flow** and **knowledge-graph** lenses,
which run `opus` / `high` (their reasoning is load-bearing for the dossier).

```
{PREAMBLE}

Lens: {lens name}. Ground yourself in: {lens grounding files from the SKILL.md table} and the
manifestos of the butlers this lens touches (roster/*/MANIFESTO.md).

Ideate 3–7 concrete proposals that advance the pursuit question: "{pursuit question}".
For each proposal name the exact integration points (module, schema/table, spec, API surface),
the owner-visible payoff, a build-cost estimate (S/M/L), and which manifesto it serves — if
none, say which new butler or manifesto amendment it implies. Reject your own idea if it
duplicates the known ledger or exists in openspec/ already (check).
```

## Structured output schema (pass as Workflow `schema` on every Phase 1/2 agent)

```json
{
  "type": "object",
  "required": ["page", "verdict", "jarvis_gap", "findings", "moves"],
  "properties": {
    "page": {"type": "string", "description": "surface key or lens key"},
    "verdict": {"enum": ["world-class", "solid", "functional", "weak", "broken", "n/a"]},
    "jarvis_gap": {"type": "string", "description": "one paragraph: today vs the ideal"},
    "ideal": {"type": "string", "description": "one paragraph describing the ideal design"},
    "findings": {"type": "array", "items": {"type": "object",
      "required": ["title", "evidence", "severity"],
      "properties": {
        "title": {"type": "string"},
        "evidence": {"type": "string", "description": "file:line citations"},
        "severity": {"enum": ["critical", "major", "minor"]},
        "detail": {"type": "string"}}}},
    "moves": {"type": "array", "items": {"type": "object",
      "required": ["title", "why", "cost"],
      "properties": {
        "title": {"type": "string"},
        "why": {"type": "string", "description": "doctrine/vision citation + payoff"},
        "cost": {"enum": ["S", "M", "L"]},
        "integration_points": {"type": "string"},
        "slice_plan": {"type": "string"}}}}
  }
}
```

Ecosystem agents use the same schema with `verdict: "n/a"` and proposals in `moves`.

## Synthesis agent (Phase 3, barrier)

**Model:** `opus` / `high`. Feed it the harvested JSON from disk (the durable harvest file), not
live agent returns, so it survives a lost resume chain.

```
You receive the full JSON output of {N} auditors/ideators (attached below) plus the known
ledger and the prior tier board. Produce:
1. tier_board: verdict per surface + movement vs baseline (improved/regressed/unchanged/new).
2. themes: cross-surface systemic defects, each with 2-3 exemplar citations and affected list.
3. ranked_moves: single ranked list of 10-15 moves mixing UX and ecosystem, deduped, each
   with what/why/evidence/slice plan. Rank by owner-value per unit cost, doctrine-weighted
   (trust/honesty defects outrank polish).
4. dropped: what you deduped or cut, one line each (so nothing silently vanishes).
Return JSON: {"tier_board": ..., "themes": [...], "ranked_moves": [...], "dropped": [...]}.
```
