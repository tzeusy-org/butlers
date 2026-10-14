# Adopted doctrine amendment: generalist lead and focused delegation

**Status:** Adopted by the owner on 2026-10-14; runtime transition not activated.
**Date:** 2026-10-14
**Baseline:** `3bde61199`
**Tracking:** `bu-rn8frx`
**Scope:** Project shape and reserved target capability contract only.

## Decision

Butlers will preserve durable domain responsibilities while allowing one
generalist lead to handle a whole goal across domains. The lead can use
searchable, authorized MCP capabilities directly and can delegate focused work
to children with dedicated prompts. Maximum hierarchy depth is one: lead 0,
children 1. Children cannot delegate. Parallelism and sequential child rounds
remain subject to finite goal budgets and runtime enforcement.

The owner requested this direction on 2026-10-14 after reviewing research on
tool discovery and agent architectures. After reviewing the draft and its
interactive architecture brief, the owner adopted the direction and authorized
all relevant project-shape changes. The receipt below identifies the reviewed
revision. Runtime implementation and activation require the successor contract.

The lead is a role per goal, not automatically the existing General butler or
Switchboard staffer. Both have narrower current manifestos. Selecting the
hosting component and amending its identity belong to the successor design.

## Before and after

Numbered doctrine rules keep their identifiers; existing citations do not move.

| Location | Previous text | Adopted replacement principle |
|---|---|---|
| `vision.md`, What Butlers Is Not | "There is no single \"do everything\" agent. Domain specialization is fundamental." | Generalist reasoning is allowed; durable ownership and enforced permissions remain mandatory. |
| `vision.md`, Rule 3 | "Inter-butler communication is MCP-only through the Switchboard." | Cross-domain capability access is MCP-only through the control plane; permitted access does not require another reasoning session. |
| `vision.md`, Rule 6 | "Every agent has a governing document that controls its scope." | Retain governing documents; ephemeral children inherit policy and domain guidance plus a bounded task brief, without requiring a new roster identity. |
| `architecture.md`, Domain Specialization | "Context pollution", "Prompt bloat", "Scope creep", "Personality incoherence" are described as inevitable for one general agent. | Treat retrieval, prompt loading, and reasoning allocation as evolving mechanisms; enforce custody and permissions independently. |
| `security.md`, Session Sandboxing | "A session cannot spawn other sessions." | Preserve that current guarantee; a future admitted lead can request depth-1 children through a new runtime-enforced contract. Children cannot spawn sessions. |

The accompanying edits to `vision.md`, `architecture.md`, and `security.md`
contain the complete adopted wording. Rule 5 additionally distinguishes
versioned role guidance from per-invocation task artifacts; Rule 7 removes
mandatory domain classification from the target transport contract. Rules 1,
2, and 4 and the named SQL exceptions retain their meaning.

## Evidence and limits

- **[Observed]** The repository already accepts native deferred discovery in
  [RFC 0027](../rfcs/0027-runtime-tool-surface-discovery.md). It deliberately
  confines discovery to per-butler presentation and does not provide new
  execution authority. This amendment extends reasoning organization, not the
  assertion that search itself authorizes cross-domain access.
- **[Observed]** Current provider interfaces support deferred tool loading;
  [OpenAI's tool-search documentation](https://developers.openai.com/api/docs/guides/tools-tool-search)
  describes preserving cache state while loading definitions as needed.
  [SCOUT, August 2026](https://arxiv.org/abs/2608.23992) reports a production
  searchable catalog spanning more than 2,000 tools. These are capability and
  production reports, not comparative proof of this personal-assistant design.
- **[Observed]** The [April 2026 scaling study](https://arxiv.org/html/2512.08296v3)
  finds task-dependent collaboration gains and diminishing benefits as
  single-agent capability rises. [Anthropic's August experiments](https://www.anthropic.com/research/multiagent-systems)
  also show improving coordination and useful specialization. Neither result
  establishes a universally optimal topology or proves a depth-one limit.
- **[Inferred]** Future model improvement favors replaceable reasoning
  organization over mandatory domain handoffs. Domain obligations, custody,
  permissions, and provenance remain useful under either generalist or
  specialist models. This is the basis for the change, not one feature needing
  an exception to the constitution.
- **[Unknown]** Butlers-specific comparative quality, latency, cost, useful
  proactive work, and owner correction rates have not been measured for the
  target hierarchy. Falling inference prices do not settle the topology:
  they benefit monitoring, generalists, and additional workers alike.

## Architectural boundaries

- The lead owns goal decomposition, shared constraints, reconciliation, and the
  final outcome. It can complete a simple goal with no children.
- A focused child's dedicated prompt includes the question, relevant context,
  governing guidance, scope, and expected evidence/result. A prompt is never
  an authority grant. All relevant domain policies apply to cross-domain work.
- A child may perform only explicitly authorized reads or writes. Execution
  checks, approvals, credential custody, private-source restrictions, and
  domain invariants remain outside model discretion.
- A child can request that its lead investigate further; it cannot route work
  to another reasoning agent, create a new root, or schedule work to bypass
  depth, permissions, budget, or cancellation. Existing independently
  authorized standing schedules are not descendants merely because they fire.
- One goal has one active lead ownership slot with recoverable lineage. A
  replacement lead cannot reset spent budget or replay uncertain effects.
  Multiple independent goals may run concurrently.
- Depth one is a deliberate initial bound, not a claim about the permanent
  limits of future agents. Changing it requires an explicit amendment.
- Domain schedules, subscriptions, obligations, and governing documents
  survive agent completion. Proactivity remains a first-class product purpose.

## Transition and precedence

The target direction is adopted. Current executable contracts continue to
govern deployed behavior under the transition boundary in that doctrine. The
[goal-orchestration requirements](../../../openspec/changes/generalist-lead-focused-delegation/specs/goal-orchestration/spec.md)
are an ADDED, reserved capability, not permission to replace existing
requirements. Adoption of the shape alone does not authorize activation.

Before implementation, a successor RFC and explicit MODIFIED deltas must
reconcile the exact baseline contracts listed below, resolve the lead's host
and identity, and define enforceable authorization, delegation lifecycle,
budgets, cancellation, result/effect receipts, and recovery. A depth-one label
or a model instruction is insufficient. No accepted RFC is silently repealed,
no baseline spec is rewritten, and no deployed topology is relabeled as target.

This preserves current restrictions during transition without requiring the
new system to retain today's routing machinery after an explicit cutover.
The [v1 scope](../../heart-and-soul/v1.md) remains the current release boundary;
the adopted target does not silently add an implementation milestone to it.

## Downstream sweep and disposition

| Artifacts | Impact and disposition |
|---|---|
| `about/README.md`, `heart-and-soul/{README,vision,architecture,security,v1}.md` | Adopted in this change: target vocabulary, depth bound, current/target precedence, and release boundary. |
| `heart-and-soul/development.md`, `craft-and-care/*` | Preserve manifesto-led scope, spec-before-code, independent review and verification. Performance and operations standards now specify comparative whole-goal outcomes and goal-level enforcement evidence. |
| `legends-and-lore/README.md`, `lay-and-land/README.md` | Link adoption and the target responsibility map. Existing RFCs and current topology remain current-contract material. |
| RFCs `0001`, `0002`, `0003` | Successor lifecycle, exclusive MCP binding, registration, classification and route-dispatch contracts require explicit amendments before implementation. Existing contracts remain binding; each RFC now links the adoption boundary and names its successor obligation. |
| RFCs `0005`, `0006`, `0008` | Preserve observability, schema ownership and security. Successor design must add parent/goal attribution, constrained caller authority and execution isolation without losing these guarantees; RFC 0008 explicitly retains current restrictions. |
| RFCs `0009`, `0011`, `0026` | Preserve context, proactive delivery and commitment ownership across reasoning-session changes; no new unsolicited-delivery permission. |
| RFCs `0010`, `0014`, `0020`, `0030`, `0031` | Preserve narrowly scoped read/projection and retrospective responsibilities; cross-domain planning is no general SQL or memory-write exception. |
| RFCs `0012`, `0021`, `0024`, `0028`, `0033`, `0037` | Preserve financial truth, approval/decision authority, correspondence custody, physical-actuation rules, spend accounting and private-source boundaries. |
| RFCs `0004`, `0007`, `0015`, `0017`, `0019`, `0027`, `0036` | Identity, dashboard, QA, routing safety, parked proactivity, tool presentation and runtime-fit contracts remain authoritative; no implicit capability, routing or policy activation. RFC 0027 explicitly distinguishes its presentation scope from successor grants. |
| `openspec/specs/{butler-base-spec,core-spawner,core-daemon,core-sessions,core-modules,butler-switchboard,cross-butler-delegation,staffer-archetype}/spec.md` | Record successor amendment need. Existing ledger-backed cross-butler delegation is not a depth-bounded child runtime. Own-butler MCP binding remains mandatory today. |
| `openspec/specs/{core-scheduler,core-notify,core-state,core-telemetry,core-credentials,core-fleet-events}/spec.md` | Preserve durable triggers/state, outbound ownership, attribution and credential rules; add goal-level semantics through explicit future deltas. |
| `openspec/specs/butler-*/spec.md`, `roster/*/MANIFESTO.md` | Preserve domain responsibility and restrictions. The lead is not an automatic expansion of General or Switchboard. Role-specific amendments depend on host selection. |
| `openspec/changes/add-runtime-tool-surface-discovery/` | Keep its per-butler catalog and presentation-only contract; no cross-domain authority inferred. |
| `openspec/changes/define-dashboard-fast-answer-runtime/` | Separate unapproved proposal, with narrow Concierge reads and domain routing. Not precedent or a prerequisite assumed complete. |
| `openspec/changes/consolidate-platform-family/` | Proposed capability-home consolidation; resolve actual homes at future implementation time rather than assuming moves have landed. |
| `lay-and-land/{components,data-flow,integration,module-vs-butler,deployment,dependencies,frontend}.md` | Retain current snapshots. The directory entry point and core maps link `target-reasoning.md`; it shows adopted logical responsibility without inventing deployed components. Rewrite actual topology only with implementation. |
| `docs/concepts/{mcp-model,switchboard-routing}.md`, `docs/runtime/spawner.md`, root `CLAUDE.md` | Retain current operational guidance; each entry point links the adopted direction and explicitly preserves current execution restrictions. |
| Local `doctrine` navigator | Record adoption and link the canonical seven rules instead of copying a stale summary; preserve current runtime restrictions until successor cutover. |
| Local `adding-butlers-to-roster`, `butler-tool-review`, `butler-relentless-jarvis-pursuit` skills | Entry points now distinguish durable daemon scaffolding, actual registered/searchable/loaded surfaces, and target reasoning ideation. Focused invocations do not create roster entries; actual permissions and manifesto alignment remain binding. |

The sweep covered doctrine, RFCs, baseline specs, active changes, topology,
manifestos, operational entry points and local skills using specialist-routing,
MCP-only, exclusive-tool, schema-ownership and session-spawning references.
It found no accepted runtime-child contract that already enforces this model.
The shape/adoption change is tracked by `bu-rn8frx`. The remaining successor
RFC, explicit baseline deltas and release/cutover design are tracked by
`bu-7p9ll5`, dependent on the shape change. This is design work, not released
runtime implementation. No artifact in the sweep is silently treated as
superseded; preserved contracts and deferred amendments have explicit homes.

## Review and adoption record

An independent fresh-context reviewer applied coherence, adversarial and
cross-pillar lenses on 2026-10-14 and returned **ACCEPT as a proposed shape
amendment**, with no blocking findings. The review checked product purpose,
zero-child completion, concurrent goals, delegated authority, indirect spawn
paths, finite sibling budgets, cancellation, recovery, proactive ownership,
release reservation and preservation of current contracts. It also checked
the cited SCOUT, scaling-study and Anthropic research sources.

One low-severity wording finding was corrected: the local navigator now says
current runtime restrictions remain until explicit successor cutover, rather
than suggesting doctrine adoption alone ends them. The separate read-only
impact investigation supplied the downstream inventory above.

The owner reviewed draft PR #4509 at
`fbe008d2af3bb40762a1c58dd753d5fbbd9d20b0` and the accompanying architecture
Site. On 2026-10-14 the owner instructed: "Looks good. Let's proceed with
implementing all relevant project-shape changes". This adopts the reviewed
direction and authorizes its cross-pillar shape sweep. It does not claim that
the runtime is implemented, select a lead host, or admit it into v1.

The adoption sweep records this status in doctrine, navigators, and the reserved
OpenSpec change; identifies current-contract scope at the affected RFC entry
points; adds a target responsibility map beside the current topology; and makes
outcome-based evaluation and goal-level operational evidence explicit in the
engineering standards. Numbered doctrine rules and existing runtime requirements
retain their identities.

## Draft verification receipt (reviewed revision)

- `git diff --check`: passed.
- Relative Markdown link check: all 84 links in changed/new documents resolve.
- `openspec validate generalist-lead-focused-delegation --strict`: passed.
- `make check-guards`: passed, including all 269 OpenSpec items validated by
  the repository-wide strict gate.
- Shape scan: all five pillars and navigators remain present and valid;
  `SHAPE_LEVEL=SHAPED`, `DOCTRINE_LAYOUT=SUPERSKILL`. The repository's existing
  maturity/traceability gap remains; this is not a claim of a mature corpus.
- Global `spec-trace-check.py`: failed. Against exact baseline `3bde61199`,
  there are 304 existing errors and 2,499 warnings. This change adds only one
  diagnostic: the helper rejects `## Purpose` in a delta, while the installed
  OpenSpec artifact instructions require it for a new capability and strict
  OpenSpec validation accepts it. No baseline finding changed. A standalone
  main-spec projection of the eight new requirements passed the helper's
  authoring check with eight IDs, zero errors and zero warnings. That projection
  validates requirement shape; it does not make the global trace check pass.
- `make test-plan BASE=origin/main`: executed after dependency setup; it
  escalated on documentation/unknown paths and the generated Beads lock file.
  No runtime code or tests changed. No Python test-body or live-stack result is
  claimed; repository static gates and document validation are the relevant
  local checks for this draft. Hosted checks, when available, remain separate.


## Adoption verification receipt (2026-10-14)

- Independent fresh-context coherence, adversarial and cross-pillar review:
  **ACCEPT**. One stale proposed-status paragraph was corrected and rechecked;
  no remaining blocking findings. The review included the target topology,
  RFC scope notes, engineering criteria, operational docs and local skills.
- `make check-guards`: passed, including all 269 strict OpenSpec items.
- Targeted strict OpenSpec validation and `git diff --check`: passed.
- Relative Markdown prose links: 187 checked, zero missing; example links in
  inline code are templates rather than file references.
- Mechanical audits of the three changed development skills: all passed with
  zero errors and warnings. Shape scan retained five valid pillar navigators
  and `SHAPE_LEVEL=SHAPED`; the existing maturity/traceability gap remains.
- Global external spec-trace check: still 305 errors and 2,499 warnings, the
  same totals as the reviewed draft. The baseline debt and new-capability
  Purpose-heading incompatibility described above remain unresolved; this is
  not a clean global traceability claim.
- The test planner escalated on documentation/unknown paths. No executable
  code or tests changed, so no Python test bodies or live runtime evaluation
  were run. Hosted CI must verify the pushed revision separately.
