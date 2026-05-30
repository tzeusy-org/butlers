# Redesign bundles

This directory holds artifacts from Claude Design sessions. Each subdirectory is a redesign bundle consumed by the `butlers-redesign-prompt` skill (`.claude/skills/butlers-redesign-prompt/`).

## Slug map (skill source-of-truth)

Phase 0 of `butlers-redesign-prompt` reads this table first. The skill resolves a user-supplied slug to a folder via the `Slug` column.

| Slug | Folder | Status | Scope |
|------|--------|--------|-------|
| `ingestion` | `ingestion-redesign/` | Ready | `/ingestion` page (timeline, connectors, filters tabs). |
| `qa` | `qa-redesign/` | Ready | `/qa` page (dossier + patrol journal). |
| `settings` | `settings-refactor/` | Ready (non-canonical) | `/settings` + `/approvals`. Uses `PLAN.md` instead of `IMPLEMENTATION.md` + `*_HANDOFF.md`. Skill must tolerate. |
| `butler-detail` | `specific-butler-page-redesign/` | Draft | `/butlers/{name}` detail page. Missing `DESIGN_LANGUAGE.md`; intent capture sparse. Skill should warn before proceeding. |
| `entity` | `entity-redesign/` | Ready (non-canonical) | `/entities` surface: Index + queue rail, `/hop`, `/columns`, `/concentration`, Editorial+Workbench detail, app-wide Cmd-K Finder. Folds `/contacts` into `/entities?has=contact`. Uses `README.md` instead of `IMPLEMENTATION.md`; per-page recipes under `prompts/00-07.md`; `DESIGN_LANGUAGE.md` lives under `reference/`. Skill must tolerate. |
| `secrets` | `secrets-redesign/` | Ready (non-canonical) | `/secrets` page: passport-book IA — left spine of all credentials (pinned `needs-hand`, CLI runtimes, System, User integrations); right editorial page with fingerprint + scopes + probe + *what breaks* evidence. Replaces today's 3-tab `SecretsPage`. Uses `README.md` + `HANDOFF.md` (no `IMPLEMENTATION.md`); per-surface recipes under `prompts/00-05.md`. Skill must tolerate. |
| `overview` | `overview/` (top level) | Reference | Cross-cutting design assets shared across redesigns. **Not a redesign**; do not pass as slug. |
| — | `dispatch-kit/` | **System (refuse)** | Design system / portable toolkit, not a redesign of a specific page. Skill refuses this slug. |
| — | `design-canvas.jsx`, `data.jsx`, etc. (top level) | **System (refuse)** | Cross-cutting primitives and canvases. Not redesigns. |

When adding a new bundle: add a row here in the same commit. The skill checks this file before falling back to fuzzy match.

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
