# SLUG redesign — integration brief

**Date:** YYYY-MM-DD
**Version:** v1
**Bundle path:** `RESOLVED_BUNDLE_PATH`
**Mode:** fresh | diff | amend
**Phase D verdict:** TBD
**Prior brief (if any):** PATH_OR_NONE

---

## 0. Design intent

> Captured from the Claude Design session (via `BUNDLE/VISION.md` if present, otherwise via Phase 0.5 user questions). **This section is binding — every spec section, every component decision, every backend contract must trace back to it.** Phase D treats violations of intent as automatic red regardless of cost math.

### Problem being solved

One paragraph. What is wrong with the current page; what specific user pain does this redesign address.

### Primary audience

Who the page is for in v1 (owner / team / operator / external user). If multiple, rank them.

### Deliberate design moves

The 2–5 specific choices that define the redesign. For each: the choice + the reason the user made it during the Claude Design session.

- Move 1 — why.
- Move 2 — why.

### What we are deliberately NOT doing

2–5 bullets. Things rejected during the Claude Design session, with the rejection reason. Implementation must resist these temptations even when they look easy.

- Rejection 1 — why.
- Rejection 2 — why.

### Success criteria

User-observable behaviors that prove integration worked. Not "tests pass" — "owner can do X in N seconds" style.

- Criterion 1.
- Criterion 2.

---

## 1. Scope

One paragraph: what page(s) does this redesign touch, what is the design language, and what is the integration target.

### Sub-pages

Verbatim Phase A `## Sub-pages` table.

### Design tokens (binding)

Verbatim Phase A `## Design tokens` block.

---

## 2. Component impact

### Classification table

Verbatim Phase B `## Component classification` table.

### Stack delta

Verbatim Phase B `## Stack delta` bullets. Blockers at the top if present.

---

## 3. Backend contract delta

### Affordance inventory

Verbatim Phase C `## Affordance inventory` table.

### API delta

Verbatim Phase C `## API delta` table. **Every row carries an `evidence: fixture / live-endpoint / spec` column.** Fixture-only rows must be `status: unclear` and resolved before spec phase.

### Schema migration impact

Verbatim Phase C `## Schema migration impact` block.

### Proposed backend epic

Verbatim Phase C `## Backend epic outline` — title, child beads with effort estimates, dependencies.

---

## 4. Guardrails

### LLM-cost feasibility

Verbatim Phase D `## LLM-cost feasibility` findings table. Pricing source: `references/llm-pricing.md` last_verified date.

#### Red verdicts

Verbatim Phase D detailed write-ups for every red feature. If none, write "None.".

#### Recommended de-scopes before spec phase

Verbatim Phase D recommendation list. If none, write "None.".

### Manifesto / identity preservation

Verbatim Phase D `## Manifesto / identity preservation` findings table.

#### Drift write-ups

Verbatim Phase D drift write-ups. If none, write "None.".

#### Recommended manifesto updates

Verbatim list. If none, write "None.".

### Intent compliance

Cross-reference each red verdict + each drift verdict against Section 0 design intent. For every red/drift, state whether the intent was the original reason (in which case the verdict is reinforced) or whether the verdict contradicts intent (escalate to user).

---

## 5. Open questions

Consolidate every open question and risk from Phases A, B, C, D into a single numbered list. Each entry names the phase it came from and a file/line cite. These are the items `/project-direction` Phase 1 (doctrine) and Phase 2 (spec) must resolve.

1. ...
2. ...

---

## 6. Handoff to `/project-direction`

This brief is the input to a `/project-direction` run with **feature evaluation focus** scoped to `SLUG`.

Concrete invocation:

```
/project-direction --focus=feature \
  --brief=docs/redesigns/YYYY-MM-DD-SLUG-brief.md \
  --bundle=RESOLVED_BUNDLE_PATH \
  --binding-design-language=RESOLVED_BUNDLE_PATH/DESIGN_LANGUAGE.md \
  --binding-design-intent=docs/redesigns/YYYY-MM-DD-SLUG-brief.md#0-design-intent \
  --red-flag-policy=descope-or-escalate
```

Carry-forward instructions:

- `DESIGN_LANGUAGE.md` is **binding**. Every spec section must preserve it.
- Section 0 of this brief is **binding**. Spec drift away from intent fails reconciliation.
- All `red`-verdict LLM features must be de-scoped or escalated before being specced.
- All `identity drift flagged` items must be resolved (redesign tweak or manifesto update) before being specced.
- After `/project-direction` Phase 3 produces the beads graph, Phase G of `butlers-redesign-prompt` will split out the backend epic per Section 3.
