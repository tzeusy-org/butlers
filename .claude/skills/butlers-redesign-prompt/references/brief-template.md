# Redesign Brief Template

Phase E of the parent skill copies this template to `docs/redesigns/YYYY-MM-DD-<slug>-brief.md` and fills it from the four subagent reports. Quote tables verbatim from the subagent outputs — do not paraphrase. The brief is the input to `/project-direction`, not the spec.

---

```markdown
# <Slug> redesign — integration brief

**Date:** <YYYY-MM-DD>
**Bundle path:** `<resolved bundle path>`
**Status:** draft — pending `/project-direction`
**Phase D verdict:** <clear / proceed-with-amendments / escalate>

---

## 1. Scope

One paragraph: what page(s) does this redesign touch, what is the design language, and what is the integration target?

### Sub-pages

<verbatim Phase A `## Sub-pages` table>

### Design tokens (binding)

<verbatim Phase A `## Design tokens` block>

---

## 2. Component impact

### Classification table

<verbatim Phase B `## Component classification` table>

### Stack delta

<verbatim Phase B `## Stack delta` bullets — blockers at the top if present>

---

## 3. Backend contract delta

### Affordance inventory

<verbatim Phase C `## Affordance inventory` table>

### API delta

<verbatim Phase C `## API delta` table>

### Schema migration impact

<verbatim Phase C `## Schema migration impact` block>

### Proposed backend epic

<verbatim Phase C `## Backend epic outline` — title, child beads with effort, dependencies>

---

## 4. Guardrails

### LLM-cost feasibility

<verbatim Phase D `## LLM-cost feasibility` findings table>

#### Red verdicts

<verbatim Phase D detailed write-ups for every red feature; if none, write "None.">

#### Recommended de-scopes before spec phase

<verbatim Phase D recommendation list; if none, write "None.">

### Manifesto / identity preservation

<verbatim Phase D `## Manifesto / identity preservation` findings table>

#### Drift write-ups

<verbatim Phase D drift write-ups; if none, write "None.">

#### Recommended manifesto updates

<verbatim list; if none, write "None.">

---

## 5. Open questions

Consolidate every open question and risk from Phases A, B, C, D into a single numbered list. Each entry should name the phase it came from in parentheses and reference the file/line cite that surfaced it. These are the items `/project-direction`'s Phase 1 doctrine pass and Phase 2 spec pass will need to resolve.

1. ...
2. ...

---

## 6. Handoff to `/project-direction`

This brief is the input to a `/project-direction` run with **feature evaluation focus** scoped to `<slug>`.

Instructions to carry forward into `/project-direction`:

- `DESIGN_LANGUAGE.md` at `<bundle path>/DESIGN_LANGUAGE.md` is **binding** — every spec section must preserve it.
- All `red`-verdict LLM features must be de-scoped or escalated before being specced.
- All `identity drift flagged` items must be resolved (either redesign tweak or manifesto update) before being specced.
- After `/project-direction` Phase 3 produces the beads graph, Phase G of this skill will split out the backend epic per Section 3 of this brief.
```
