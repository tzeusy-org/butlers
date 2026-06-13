# Redesign bundles

This directory holds artifacts from Claude Design sessions. Each subdirectory is a redesign bundle consumed by the `butlers-redesign-prompt` skill (`.claude/skills/butlers-redesign-prompt/`).

## Slug map (skill source-of-truth)

Phase 0 of `butlers-redesign-prompt` reads this table first. The skill resolves a user-supplied slug to a folder via the `Slug` column.

| Slug | Folder | Status | Scope |
|------|--------|--------|-------|
| `entity` | `entity-redesign/` | Ready (non-canonical) | `/entities` surface: Index + queue rail, `/hop`, `/columns`, `/concentration`, Editorial+Workbench detail, app-wide Cmd-K Finder. Folds `/contacts` into `/entities?has=contact`. Uses `README.md` instead of `IMPLEMENTATION.md`; per-page recipes under `prompts/00-07.md`; `DESIGN_LANGUAGE.md` lives under `reference/`. Skill must tolerate. |
| `overview` | `overview/` (top level) | Reference | Cross-cutting design assets shared across redesigns. **Not a redesign**; do not pass as slug. |
| — | `dispatch-kit/` | **System (refuse)** | Design system / portable toolkit, not a redesign of a specific page. Skill refuses this slug. |
| — | `design-canvas.jsx`, `data.jsx`, etc. (top level) | **System (refuse)** | Cross-cutting primitives and canvases. Not redesigns. |

When adding a new bundle: add a row here in the same commit. The skill checks this file before falling back to fuzzy match.

> **Graduated bundles (deleted 2026-06-13).** A redesign bundle is reference
> material for implementation workers only; once it has fully shipped into
> `frontend/` and its target state is captured in `openspec/specs/`, the bundle is
> deleted (the spec becomes the long-lived source of truth). Docs that a *live*
> spec or an *active* OpenSpec change still binds are not deleted — they are
> relocated to `docs/redesigns/` (a durable home outside `pr/`) and the references
> repointed. Removed this pass:
>
> - `ingestion-redesign/` → `dashboard-ingestion-dispatch-console`. Binding design
>   language + handoff relocated to `docs/redesigns/ingestion-design-language.md` /
>   `ingestion-handoff.md`; the two connector mocks the active
>   `add-connector-oauth-scope-surface` change cites by line number relocated to
>   `docs/redesigns/ingestion-connector-detail.jsx` / `ingestion-connectors-data.jsx`.
> - `qa-redesign/` → `qa-dashboard`.
> - `settings-refactor/` → `dashboard-settings-console` / `dashboard-model-settings` /
>   `dashboard-permissions` / `dashboard-approvals`.
> - `specific-butler-page-redesign/` (+ top-level butler-detail mocks) →
>   `detail-page-archetype` / `dashboard-butler-management`.
> - `memory-redesign/` → `dashboard-domain-pages` (house-ledger).
> - `secrets-redesign/` → `butler-secrets`; binding design language relocated to
>   `docs/redesigns/secrets-design-language.md`.
> - The original `/` overview and `/butlers` index mocks, plus the
>   `pr/dispatch-redesign-*` epic reports, graduated likewise.
>
> `entity-redesign/` remains because the `entity-v3-lifecycle-and-depth` OpenSpec
> change is still in flight.

## Bundle contract

The `butlers-redesign-prompt` skill expects (but tolerates missing) these files inside each redesign bundle:

| File | Required? | Purpose |
|------|-----------|---------|
| `DESIGN_LANGUAGE.md` | Strongly recommended | Binding visual language: tokens, typography, motion. Phase A treats as authoritative. |
| `IMPLEMENTATION.md` **or** `PLAN.md` | Required | Porting recipe + decisions log. Either filename is accepted. |
| `*_HANDOFF.md` | Preferred | TL;DR + sub-page breakdown. Aids Phase 0 fast-read. |
| `VISION.md` | Optional | Captures the WHY behind design moves. If absent, the skill prompts the user via Phase 0.5 before any subagent runs. |
| `*.jsx` mocks | Required | One per sub-page or major component. Phase B reads these to classify components. |
| `*.html` exports | Preferred | Standalone browser-openable previews. Phase A optionally screenshots them. |
| `*-data.jsx` / `data.jsx` | Optional | Mock fixtures. **Treated as illustrative, not authoritative** — Phase C marks any contract derived from these as `evidence: fixture`. |

## Authoring a `VISION.md`

A `VISION.md` lets the user front-load the design rationale instead of being prompted live during Phase 0.5. Recommended structure:

```markdown
# Vision — SLUG redesign

## Problem being solved
[paragraph]

## Primary audience
[role + ranking if multiple]

## Deliberate design moves
- Move 1 — why.
- Move 2 — why.

## What we are deliberately NOT doing
- Rejection 1 — why.
- Rejection 2 — why.

## Success criteria
- Criterion 1.
- Criterion 2.
```

The skill copies this block verbatim into Section 0 of the generated brief.
