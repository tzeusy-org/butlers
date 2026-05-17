---
name: butlers-redesign-prompt
description: Orchestrate a UX redesign of a Butlers dashboard page (or sub-page set) using /project-direction as the spec+beads engine, with redesign-specific upfront phases for vision capture, asset ingestion, impact analysis, backend-contract derivation, LLM-cost feasibility, and manifesto/identity preservation. Use when handed a redesign bundle under pr/overview/SLUG-redesign/ (for example ingestion, qa, settings, butler-detail) and asked to plan integration into the live Butlers stack. Triggers on "redesign the X page", "plan the Y redesign", "integrate the redesign in pr/overview/...", "what would it take to ship the SLUG redesign", "design language integration for AREA".
---

# Butlers Redesign Orchestrator

Plan the integration of a Claude Design redesign bundle into the live Butlers stack. This skill is a thin orchestrator that captures user vision, runs four redesign-specific phases via independent subagents, synthesises a brief, and then hands the brief to `/project-direction` so its existing Phase 1–3 (+ R1–R4+ reconciliation) machinery does the spec and beads work. The reason this skill exists is that `/project-direction` is generic; a Butlers redesign has a fixed input (`pr/overview/SLUG-redesign/`), a fixed risk profile (LLM cost blowouts, manifesto drift, vision loss in mechanical porting), and a fixed output shape that benefit from pre-baked scaffolding.

## Hard Rules

1. **Vision is the source of truth.** Section 0 of the brief — "Design intent" — is binding. Every spec section, every component decision, every backend contract must trace back to it. Phase D treats violations of intent as automatic red regardless of cost math.
2. **Specifications are the source of truth for behaviour.** Once Section 0 is locked, every work item must link to a spec section. `/project-direction` enforces this — do not bypass it.
3. **No coding during this skill.** Output is vision capture, briefs, and beads only. Implementation is owned by `/beads-coordinator` later.
4. **Flag infeasible features early.** If an LLM-driven affordance would blow the token budget at expected user volume, surface it during Phase D — not during Phase 2 of `/project-direction`, and not during implementation.
5. **One subagent per phase.** Use independent subagents for Phases A–D so each gets a clean context window and the orchestrator window stays small. Pass each subagent the slug, the relevant paths, the brief's Section 0 (when applicable), and the phase's reference file from `references/`.
6. **Doctrine before details.** Read `about/heart-and-soul/` (or invoke the `heart-and-soul` skill) before declaring any feature acceptable — manifesto/identity drift is the failure mode that most often forces a redesign-of-a-redesign.

## Argument shape

Single positional argument: **the redesign slug**. Examples: `ingestion`, `qa`, `settings`, `butler-detail`.

Resolution source-of-truth: **`pr/overview/README.md`**. That file maintains the canonical slug-to-folder map. Read it first.

Resolution rules (in order, first match wins):

1. **Slug map hit** in `pr/overview/README.md` — use that folder.
2. **Canonical match** `pr/overview/SLUG-redesign/`.
3. **Bare match** `pr/overview/SLUG/`.
4. **Fuzzy match** — any `pr/overview/` folder containing `SLUG`. Require user confirmation before proceeding.

**Toolkit refusal.** If `pr/overview/README.md` marks the resolved folder as `System (refuse)` (e.g. `dispatch-kit/`), stop and tell the user: "This is a design system / portable toolkit, not a redesign of a specific page. The skill cannot process it. Pick a redesign slug instead." Do not run any phase.

**Optional override.** A user may pass `--bundle=PATH` to bypass the slug resolver entirely (for unlisted or in-flight bundles).

## Workflow

The skill runs in two acts:

- **Act 1 (Phases 0 → 0.5 → A → B → C → D)** — vision capture + four redesign-specific phases, each via its own subagent. Output is a synthesised **redesign brief** doc.
- **Act 2 (Phases E → F → G → H)** — synthesise the brief, hand it to `/project-direction`, post-process the resulting beads graph, deliver a final handoff message.

### Phase 0 — Resolve, gate, detect prior runs

Before anything else:

1. **Resolve the slug** per the rules above. State the resolved path back to the user in one line. If toolkit-refused, stop.
2. **Tolerate file variants.** Check the bundle for these files (in this order):
   - Handoff: `IMPLEMENTATION.md` **or** `PLAN.md` (either is acceptable).
   - Recipe TL;DR: `*_HANDOFF.md` (preferred, optional).
   - Design language: `DESIGN_LANGUAGE.md` (strongly recommended; warn if missing).
   - Vision: `VISION.md` (optional; controls Phase 0.5 behaviour).
3. **Read handoff headers only** — first 30 lines of `IMPLEMENTATION.md` / `PLAN.md` / `*_HANDOFF.md`. Confirm sub-page count and route map. Do not load full bodies into the orchestrator window — that is Phase A's job.
4. **Detect prior runs.** Check for:
   - Prior brief: `ls docs/redesigns/*-SLUG-brief*.md` (any version).
   - Prior beads epics: `bd list --json | jq '.[] | select(.title | test("SLUG redesign"; "i"))'`.
5. **If prior found**, ask the user via `AskUserQuestion`:
   - `fresh` — ignore prior artifacts; create new brief at `-vN+1` and new epics.
   - `diff` — generate `-vN+1` brief that explicitly diffs against the prior brief and only re-runs phases whose inputs changed.
   - `amend` — edit the prior brief in place and update existing beads.
   Default to `fresh` if no prior found.
6. **Confirm scope.** State back to the user: resolved path, mode, target brief filename, and any warnings (missing `DESIGN_LANGUAGE.md`, sparse intent, etc.). Do not proceed without acknowledgement.

### Phase 0.5 — Vision capture

Goal: capture the WHY behind the design before any mechanical analysis runs. This is what makes the skill a vision-to-implementation bridge instead of a port-and-pray tool.

1. **If `BUNDLE/VISION.md` exists**, read it. Use its content as the draft of Section 0. Confirm with the user that it is current ("Should I use this verbatim, or do you want to refine any of the bullets?").
2. **Otherwise**, prompt the user via `AskUserQuestion` with these five questions, one at a time:
   - **Problem being solved** — "What's wrong with the current `/SLUG` page; what specific user pain does this redesign address?"
   - **Primary audience** — "Who is this for in v1? (owner / team / operator / external user). If multiple, who ranks highest?"
   - **Deliberate design moves** — "Name the 2–5 specific choices that define the redesign, with the reason you made each during the Claude Design session."
   - **Things deliberately rejected** — "What did Claude Design and you explicitly choose NOT to do? Why? The implementation must resist these temptations."
   - **Success criteria** — "What user-observable behaviours will tell you the integration worked? (Not 'tests pass' — 'owner can do X in N seconds' style.)"
3. **Write Section 0 to the in-memory brief draft.** It becomes Phase D's intent gate input.
4. **Offer to persist back to `BUNDLE/VISION.md`** if it didn't already exist. Storing it in the bundle lets the next iteration skip Phase 0.5.

This phase is the most important difference between this skill and a generic `/project-direction` invocation. Do not skip it.

### Phase A — Input gathering (subagent)

Goal: produce an asset inventory + sub-page enumeration + design-token extraction.

Read `references/input-gathering.md`. Dispatch with `subagent_type: Explore`. Pass it the bundle path. Output: sub-pages, components, design tokens, open questions.

### Phase B — Impact analysis (subagent)

Goal: classify every component as `reuse / adapt / new / replace`, locate the current implementation, flag stack changes, and **emit a `## Butlers touched` table** that Phase D will use to scope its manifesto pass.

Read `references/impact-analysis.md`. Dispatch with `subagent_type: Explore`. Pass it Phase A's output. Output: current implementation map, component classification, stack delta, butlers touched, risks.

### Phase C — Backend-contract derivation (subagent)

Goal: derive the API contract each new affordance needs, reconcile against existing routers, and mark every row with its **evidence basis** (`live-endpoint / spec / fixture`). Fixture-only rows are automatically `unclear`.

Read `references/backend-contract.md`. Dispatch with `subagent_type: Explore`. Output: affordance inventory, API delta with evidence column, schema migration impact, proposed backend epic outline.

### Phase D — Butlers guardrails (subagent)

Goal: two passes in one subagent — (1) LLM-cost feasibility audit grounded in `references/llm-pricing.md` and the intent gate, (2) manifesto/identity preservation scoped to Phase B's `## Butlers touched` table.

Read `references/butlers-guardrails.md`. Dispatch with `subagent_type: general-purpose` (needs to read manifestos and reason about cost). Pass it **Section 0 of the brief draft** plus Phases A–C reports. Output: cost findings table, manifesto findings table, intent-compliance check, Phase D verdict.

### Phase E — Synthesise the brief

1. Determine the target filename:
   - Base: `docs/redesigns/YYYY-MM-DD-SLUG-brief.md`.
   - If a brief already exists at that path (same-day collision) or prior versions exist, append `-vN` where N = next integer.
2. **Copy** `assets/brief-template.md` to the target path. Do not re-type the template — `cp` it, then `sed`-substitute placeholders (`SLUG`, `YYYY-MM-DD`, `RESOLVED_BUNDLE_PATH`, `PATH_OR_NONE`, version, mode).
3. **Fill the body** by quoting structured tables verbatim from the four subagent reports. Do not paraphrase. Phase 0.5's Section 0 goes in first.
4. **Pause and let the user read the brief.** If they request changes, re-run the relevant phase's subagent with their feedback appended; do not edit the brief in place without re-running.

### Phase F — Hand off to `/project-direction`

Invoke `/project-direction` with **feature evaluation focus**, using this concrete invocation pattern:

```
/project-direction --focus=feature \
  --brief=docs/redesigns/YYYY-MM-DD-SLUG-brief.md \
  --bundle=RESOLVED_BUNDLE_PATH \
  --binding-design-language=RESOLVED_BUNDLE_PATH/DESIGN_LANGUAGE.md \
  --binding-design-intent=docs/redesigns/YYYY-MM-DD-SLUG-brief.md#0-design-intent \
  --red-flag-policy=descope-or-escalate
```

If `/project-direction` does not accept these flags literally, paste the equivalent paragraph but **list every binding artifact path** so its Phase 1 doctrine reconciliation can cite them.

Capture the OpenSpec changeset path that `/project-direction` Phase 2 (`/opsx:ff`) emits — Phase H needs it.

### Phase G — Split out backend epic (with existing-work detection)

After `/project-direction` Phase 3 produces the beads graph, post-process:

1. **Detect existing epics.** Query `bd list --json` for any open epic whose title contains `SLUG redesign — backend contracts` (or its frontend sibling). If found and mode is `amend`, update the existing epics in place. If found and mode is `fresh`, ask the user before creating duplicates.
2. Identify every bead whose work is entirely backend (new API contract, schema migration, butler/api router change) by cross-referencing against the Phase C delta.
3. Create (or amend) the **backend epic** titled `SLUG redesign — backend contracts` and re-parent the backend beads under it.
4. Wire `blocked-by` from the frontend epic to the backend epic so the frontend cannot start before backend contracts land.
5. Add `discovered-from` links from each backend bead to the brief doc path.
6. Verify Phase D red verdicts: every red-verdict feature in the brief must either be missing from the bead graph or carry a `descope-decision-link` annotation pointing to where the user de-scoped it. Flag any leak as a bead-graph error before Phase H.

### Phase H — Final handoff message

End the orchestrator with a fixed message giving the user every artifact path and the literal next command. Template:

```
✓ Redesign plan ready for SLUG.

Brief:               docs/redesigns/YYYY-MM-DD-SLUG-brief.md (vN)
Vision (Section 0):  <persisted to BUNDLE/VISION.md? yes/no>
OpenSpec changeset:  openspec/changes/<change-id>/  (created by /project-direction Phase 2)
Frontend epic:       <bd-id>  (N beads)
Backend epic:        <bd-id>  (M beads, blocks frontend epic)

Red-flag verification:
  - <K> red verdicts in Phase D
  - All <K> have de-scope decisions linked in the bead graph ✓ / ✗

Next:
  /beads-coordinator    # start parallel worker dispatch on the two epics
```

Do not run `/beads-coordinator`. The skill's contract is planning + handoff, not execution.

## Reference and asset files

| Path | Type | When to read | Purpose |
|------|------|--------------|---------|
| `references/input-gathering.md` | reference | Phase A | Subagent prompt for asset ingestion + sub-page enumeration + design-token extraction. |
| `references/impact-analysis.md` | reference | Phase B | Subagent prompt for current-state baseline + per-component classification + stack delta + `## Butlers touched` table. |
| `references/backend-contract.md` | reference | Phase C | Subagent prompt for deriving API contracts with `evidence` column. Fixture-only rows → `unclear`. |
| `references/butlers-guardrails.md` | reference | Phase D | Two-pass subagent prompt: LLM-cost feasibility (intent gate + pricing-grounded) + manifesto/identity preservation (scoped via Phase B). |
| `references/llm-pricing.md` | reference | Phase D | Per-MTok rate table, cadence reference, sanity-default per-affordance rows, verdict thresholds. Re-verified date inside. |
| `assets/brief-template.md` | asset | Phase E | Raw markdown template. `cp` to `docs/redesigns/YYYY-MM-DD-SLUG-brief[-vN].md` and substitute placeholders. **Never load into context; copy to disk.** |
| `pr/overview/README.md` (repo-level) | external | Phase 0 | Slug-to-folder map + bundle contract. Skill source-of-truth for resolution + toolkit refusal. |

## Common failure modes

- **Skipping Phase 0.5.** The mechanical phases will produce a competent port plan with the vision lost. Section 0 is the spine of every downstream phase.
- **Skipping Phase D's cost audit.** Easy because the design looks delightful and cost only appears at the token-counting step. The brief is the right place to surface a $50/user/day feature — not a post-launch incident.
- **Letting `/project-direction` re-derive Phase A.** Pass the brief in so the spec phase grounds in it. Otherwise R1–R4+ reconciliation passes rediscover sub-pages.
- **Merging frontend and backend beads into one epic.** Backend contracts have different worker pools and dependencies; keep them separate so `/beads-coordinator` can dispatch in parallel.
- **Trusting `data.jsx` fixtures.** Phase C's evidence column is the guardrail. Fixture-only rows must go to `unclear` and resolve before spec phase.
- **Treating the brief as final.** It is the input to `/project-direction`, not the spec. Specs live in `openspec/`; the brief primes the spec phase.
- **Re-running without `--mode`.** Phase 0's iteration detection catches this — don't skip it, or you will silently overwrite a previous run's brief and duplicate beads epics.
