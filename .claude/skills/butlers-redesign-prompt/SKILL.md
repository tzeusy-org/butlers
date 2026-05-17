---
name: butlers-redesign-prompt
description: Orchestrate a UX redesign of a Butlers dashboard page (or sub-page set) using /project-direction as the spec+beads engine, with redesign-specific upfront phases for asset ingestion, impact analysis, backend-contract derivation, LLM-cost feasibility, and manifesto/identity preservation. Use when handed a redesign bundle under pr/overview/SLUG-redesign/ (for example ingestion, qa, settings, butler-detail) and asked to plan integration into the live Butlers stack. Triggers on "redesign the X page", "plan the Y redesign", "integrate the redesign in pr/overview/...", "what would it take to ship the SLUG redesign", "design language integration for AREA".
---

# Butlers Redesign Orchestrator

Plan the integration of a Claude Design redesign bundle into the live Butlers stack. This skill is a thin orchestrator that runs four redesign-specific phases up front, then hands their outputs to `/project-direction` so its existing Phase 1–3 (+ R1–R4+ reconciliation) machinery does the spec and beads work. The reason this skill exists is that `/project-direction` is generic; a Butlers redesign has fixed inputs (a `pr/overview/<slug>-redesign/` folder), fixed risks (LLM cost blowouts, manifesto drift), and a fixed output shape (a redesign brief + a beads graph split between frontend and backend epics) that benefit from pre-baked scaffolding.

## Hard Rules

1. **Specifications are still the source of truth.** Every UI affordance in the redesign must map to a spec section before any bead is generated. `/project-direction` enforces this — do not bypass it.
2. **No coding during this skill.** Output is plans, briefs, and beads only. Implementation is owned by `/beads-coordinator` later.
3. **Flag infeasible features early.** If an LLM-driven affordance in the design would blow the token budget at expected user volume, surface it during Phase D — not during Phase 2 of `/project-direction`, and not during implementation. The cost of catching it late compounds.
4. **One subagent per phase.** Use independent subagents for Phases A–D so each gets a clean context window and the main orchestrator window stays small. Pass each subagent the slug, the relative paths it needs, and the phase's reference file from `references/`.
5. **Doctrine before details.** Read `about/heart-and-soul/` (or invoke the `heart-and-soul` skill) before declaring any feature acceptable — manifesto/identity drift is the failure mode that most often forces a redesign-of-a-redesign.

## Argument shape

Single positional argument: **the redesign slug**. Examples: `ingestion`, `qa`, `settings`, `butler-detail`, `specific-butler-page`. The skill resolves all other paths from the slug.

Resolution rules (apply in order; first match wins):

1. `pr/overview/<slug>-redesign/` (canonical, e.g. `pr/overview/ingestion-redesign/`).
2. `pr/overview/<slug>/` (some bundles drop the `-redesign` suffix, e.g. `pr/overview/dispatch-kit/`).
3. `pr/overview/` matches containing `<slug>` (last-resort fuzzy match; require user confirmation before proceeding).

If no folder matches, stop and ask the user for the path. Do not proceed with assumptions.

Once resolved, expect (but tolerate missing) these files in the bundle:

- `DESIGN_LANGUAGE.md` — design tokens, motion, typography rules. **Treat as binding.**
- `IMPLEMENTATION.md` or `*_HANDOFF.md` — porting recipe, route map, component inventory.
- `*.jsx` mocks — one file per sub-page or component.
- `*.html` exports — standalone browser-openable previews.

## Workflow

The skill runs in two acts:

- **Act 1 (Phases A–D)** — redesign-specific phases, each via its own subagent. Output is a synthesised **redesign brief** doc.
- **Act 2 (Phases E–G)** — hand the brief to `/project-direction`, then post-process the resulting beads graph to split out a backend-only epic.

### Phase 0 — Resolve & confirm

Before dispatching any subagent:

1. Resolve the slug to a folder per the rules above. State the resolved path back to the user in one line.
2. Read `IMPLEMENTATION.md` / `*_HANDOFF.md` headers only (first 30 lines) to confirm sub-page count and route map. Do not load the full bodies into the orchestrator window — that is the input-gathering subagent's job.
3. If the folder is empty or missing `DESIGN_LANGUAGE.md`, stop and ask. A redesign without a design language doc is not actionable by this skill.

### Phase A — Input gathering (subagent)

Goal: produce an asset inventory + sub-page enumeration + design-token extraction the rest of the skill can rely on.

Read `references/input-gathering.md` for the full subagent prompt template. Dispatch with `subagent_type: Explore` (read-only is sufficient). Output expected: a single structured markdown report with sections `## Sub-pages`, `## Components`, `## Design tokens`, `## Open questions`.

### Phase B — Impact analysis (subagent)

Goal: classify every component in the redesign as `reuse / adapt / new`, locate the current page's implementation, and flag any frontend-stack changes required (new deps, state library, routing changes).

Read `references/impact-analysis.md`. Dispatch with `subagent_type: Explore`. Pass it Phase A's output as context. Output expected: a component-by-component table + a stack-delta section.

### Phase C — Backend-contract derivation (subagent)

Goal: for every new UI affordance from Phase B, derive the API contract it needs (path, method, request/response shape) and reconcile against existing routes under `roster/*/api/router.py` and `src/butlers/api/`. Missing endpoints become explicit backend work; this is the input to the backend-only epic in Phase G.

Read `references/backend-contract.md`. Dispatch with `subagent_type: Explore`. Output expected: API delta table with `status: exists / extend / new`, plus per-new-endpoint draft schema.

### Phase D — Butlers guardrails (subagent)

Goal: two passes in one subagent — (1) LLM-cost feasibility audit, (2) manifesto/identity preservation check. Both are explicit project mandates.

Read `references/butlers-guardrails.md`. Dispatch with `subagent_type: general-purpose` (needs to load manifestos + estimate cost; not pure read-only). Output expected: a verdict per LLM-driven feature (`green / yellow / red` with reasoning) and a verdict per butler-touching surface (`identity preserved / drift flagged`).

### Phase E — Synthesise the redesign brief

Once Phases A–D have returned, write the brief to `docs/redesigns/YYYY-MM-DD-<slug>-brief.md` using the template at `references/brief-template.md`. Fill every section from the subagent outputs — do not paraphrase, quote the structured tables verbatim. The brief is the single artifact the user reviews before `/project-direction` is invoked.

Pause and let the user read the brief. If they request changes, re-run the relevant phase's subagent with their feedback appended; do not edit the brief in place without re-running.

### Phase F — Hand off to `/project-direction`

Invoke `/project-direction` with **feature evaluation focus**, scoping the request to the redesign slug, and supplying:

- The path to the brief from Phase E.
- The resolved redesign-bundle folder path.
- An explicit instruction that the design language in `DESIGN_LANGUAGE.md` is **binding** and any spec must preserve it.
- An explicit instruction that LLM-cost `red` flags from Phase D must be either de-scoped or escalated to the user before being specced.

`/project-direction` then runs Phases 1–3 with R1–R4+ reconciliation per its own contract. Do not duplicate that work here.

### Phase G — Split out backend-only epic

After `/project-direction` Phase 3 has produced the beads graph, post-process:

1. Identify every bead whose work is entirely backend (new API contract, schema migration, butler/api router change) by cross-referencing against the Phase C delta.
2. Create a sibling beads **epic** titled `<slug> redesign — backend contracts` and re-parent the backend beads under it.
3. Wire `blocked-by` from the frontend epic to the backend epic so any worker picking up the frontend cannot start before the backend contracts land.
4. Add `discovered-from` links from each backend bead to the brief doc (use the brief's path as the rationale).

Do not run `/beads-coordinator`. Hand off explicitly to the user with the two epic IDs + brief path.

## Reference files

| File | When to read | What it contains |
|------|--------------|------------------|
| `references/input-gathering.md` | Phase A | Full subagent prompt for asset ingestion + sub-page enumeration + design-token extraction. |
| `references/impact-analysis.md` | Phase B | Subagent prompt for current-state baseline + per-component reuse/adapt/new classification + stack delta. |
| `references/backend-contract.md` | Phase C | Subagent prompt for deriving API contracts from new affordances and reconciling against existing routers. |
| `references/butlers-guardrails.md` | Phase D | Two-pass subagent prompt: LLM-cost feasibility audit + manifesto/identity preservation check. |
| `references/brief-template.md` | Phase E | Markdown template for `docs/redesigns/YYYY-MM-DD-<slug>-brief.md`. |

## Common failure modes

- **Skipping Phase D's cost audit.** Easy to do because the design looks delightful and the cost only appears at the token-counting step. Resist. A redesign that costs $50/user/day is not shippable, and the brief is the right place to surface that — not a post-launch incident.
- **Letting `/project-direction` re-derive the asset inventory.** That work is already done in Phase A; pass the brief in so the spec phase grounds in it. Otherwise R1–R4+ reconciliation passes will repeatedly rediscover the same sub-pages and waste cycles.
- **Merging frontend and backend beads into one epic.** Backend contracts have different worker pools and different dependencies; keep them separate so `/beads-coordinator` can dispatch them in parallel where possible.
- **Trusting the redesign mocks blindly.** The mocks ship with `data.jsx` fixtures that look plausible but may not reflect real API responses. Always verify shape against the actual butler's `/api/...` endpoints in Phase C.
- **Treating the brief as final.** It is the input to `/project-direction`, not the spec. Specs live in `openspec/`; the brief just primes the spec phase.
