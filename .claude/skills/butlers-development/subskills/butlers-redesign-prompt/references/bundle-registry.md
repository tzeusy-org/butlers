# Bundle registry — slug map + bundle contract

Skill source-of-truth for Phase 0 slug resolution and toolkit refusal. This registry
graduated here from `pr/overview/README.md` (2026-07-03); `pr/overview/` now holds only
in-flight bundles themselves.

## Slug map

Phase 0 reads this table first. The skill resolves a user-supplied slug to a folder via the
`Slug` column.

| Slug | Folder | Status | Scope |
|------|--------|--------|-------|
| `entity` | `pr/overview/entity-redesign/` | Ready (non-canonical) | `/entities` surface: Index + queue rail, `/hop`, `/columns`, `/concentration`, Editorial+Workbench detail, app-wide Cmd-K Finder. Folds `/contacts` into `/entities?has=contact`. Uses `README.md` instead of `IMPLEMENTATION.md`; per-page recipes under `prompts/00-07.md`; `DESIGN_LANGUAGE.md` lives under `reference/`. Skill must tolerate. |
| — | `references/dispatch-kit/` (skill-local) | **System (refuse)** | Design system / portable toolkit, not a redesign of a specific page. Skill refuses this slug. |
| — | `pr/overview/design-canvas.jsx`, `data.jsx`, etc. (top level) | **System (refuse)** | Cross-cutting primitives and canvases. Not redesigns. |

When adding a new bundle under `pr/overview/`: add a row here in the same commit. The skill
checks this file before falling back to fuzzy match.

**Design language is a spec.** The binding design language no longer lives in bundles — it is
`openspec/specs/dashboard-design-language/spec.md` (the Dispatch spec). A bundle MAY carry a
`DESIGN_LANGUAGE.md` only as a *delta* against that spec; absent one, the spec alone binds.

## Graduated bundles (history)

A redesign bundle is reference material for implementation workers only; once it has fully
shipped into `frontend/` and its target state is captured in `openspec/specs/`, the bundle is
deleted (the spec becomes the long-lived source of truth). Docs that a *live* spec or an
*active* OpenSpec change still binds are not deleted — they are relocated to `docs/redesigns/`
and the references repointed.

Removed 2026-06-13:

- `ingestion-redesign/` → `dashboard-ingestion-dispatch-console`. Handoff relocated to
  `docs/redesigns/ingestion-handoff.md`; the two connector mocks the active
  `add-connector-oauth-scope-surface` change cites by line number relocated to
  `docs/redesigns/ingestion-connector-detail.jsx` / `ingestion-connectors-data.jsx`.
- `qa-redesign/` → `qa-dashboard`.
- `settings-refactor/` → `dashboard-settings-console` / `dashboard-model-settings` /
  `dashboard-permissions` / `dashboard-approvals`.
- `specific-butler-page-redesign/` (+ top-level butler-detail mocks) →
  `detail-page-archetype` / `dashboard-butler-management`.
- `memory-redesign/` → `dashboard-domain-pages` (house-ledger).
- `secrets-redesign/` → `butler-secrets`.
- The original `/` overview and `/butlers` index mocks, plus the `pr/dispatch-redesign-*`
  epic reports, graduated likewise.

Graduated 2026-07-03:

- The Dispatch design language (formerly `pr/overview/DESIGN_LANGUAGE.md`, duplicated in
  `dispatch-kit/` and twice under `docs/redesigns/`) → `openspec/specs/dashboard-design-language/spec.md`.
- `dispatch-kit/` → this skill's `references/dispatch-kit/` (execution material, not a bundle).
- The slug map + bundle contract (formerly `pr/overview/README.md`) → this file.

`entity-redesign/` remains under `pr/overview/` because the `entity-v3-lifecycle-and-depth`
OpenSpec change is still in flight.

## Bundle contract

The skill expects (but tolerates missing) these files inside each redesign bundle:

| File | Required? | Purpose |
|------|-----------|---------|
| `DESIGN_LANGUAGE.md` | Optional (delta only) | Bundle-specific *deltas* against the Dispatch spec. The spec itself is always binding; warn if a bundle DL contradicts it. |
| `IMPLEMENTATION.md` **or** `PLAN.md` | Required | Porting recipe + decisions log. Either filename is accepted. |
| `*_HANDOFF.md` | Preferred | TL;DR + sub-page breakdown. Aids Phase 0 fast-read. |
| `VISION.md` | Optional | Captures the WHY behind design moves. If absent, the skill prompts the user via Phase 0.5 before any subagent runs. |
| `*.jsx` mocks | Required | One per sub-page or major component. Phase B reads these to classify components. |
| `*.html` exports | Preferred | Standalone browser-openable previews. Phase A optionally screenshots them. |
| `*-data.jsx` / `data.jsx` | Optional | Mock fixtures. **Treated as illustrative, not authoritative** — Phase C marks any contract derived from these as `evidence: fixture`. |

## Authoring a `VISION.md`

A `VISION.md` lets the user front-load the design rationale instead of being prompted live
during Phase 0.5. Recommended structure:

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
