# Adopted doctrine amendment: generalist lead and focused delegation

**Status:** Adopted by the owner on 2026-10-14. Delivered by one hard cutover
specified in [RFC 0038](../rfcs/0038-goal-runtime-and-capability-authorization.md)
(proposed, awaiting owner signoff).
**Date:** 2026-10-14
**Baseline:** `3bde61199`
**Tracking:** `bu-rn8frx` (doctrine), `bu-7p9ll5` (successor contract)

## Decision

Butlers preserves durable domain responsibilities while one generalist lead
handles each whole goal across domains. The lead uses searchable, authorized
MCP capabilities directly and can delegate focused work to children with
dedicated prompts. Maximum hierarchy depth is one: lead 0, children 1.
Children cannot delegate. Parallelism and sequential child rounds are bounded
by finite goal budgets the runtime enforces.

The lead is a role per goal, not a butler. RFC 0038 places goal admission, the
goal runner and the capability broker in the Switchboard control plane and
gives the lead a versioned policy document; General gains no authority.

### Refinement: hard cutover and authorization in scope

On review of the first revision the owner directed two changes:

- "we should make it a hard cutover and not a multi-system-migration-process".
  Doctrine therefore describes one system. Topology pages and operational
  docs keep describing the running code until the cutover release rewrites
  them. The classify-and-route path is frozen to fixes; it is deleted, not
  bridged.
- The grant and authorization broker "should be in-scope for this proposed
  migration; i think we should go ahead with the design of this as well".
  RFC 0038 is that design.

## Before and after

Numbered doctrine rules keep their identifiers; existing citations do not move.

| Location | Previous text | Adopted principle |
|---|---|---|
| `vision.md`, What Butlers Is Not | "There is no single \"do everything\" agent. Domain specialization is fundamental." | Generalist reasoning is allowed; durable ownership and enforced permissions remain mandatory. |
| `vision.md`, Rule 3 | "Inter-butler communication is MCP-only through the Switchboard." | Cross-domain capability access is MCP-only through the control plane; a permitted call does not need another reasoning session. |
| `vision.md`, Rule 6 | "Every agent has a governing document that controls its scope." | Governing documents remain; children inherit policy and domain guidance plus a bounded brief, without a new roster identity. |
| `architecture.md`, Domain Specialization | "Context pollution", "Prompt bloat", "Scope creep", "Personality incoherence" described as inevitable for one general agent. | Retrieval, prompt loading and reasoning allocation are evolving mechanisms; custody and permissions are enforced independently. |
| `architecture.md`, The Core Loop | `trigger -> classify -> route -> spawn -> act -> log` | `admit -> establish authority -> reason -> act/delegate -> reconcile -> record`. |
| `security.md`, Session Sandboxing | "A session cannot spawn other sessions." (Configured, never enforced; see RFC 0038 Context.) | Sessions are sandboxed and reach only the broker; only a lead can start a session, and only a depth-1 child. |
| `security.md`, deployment control keys | One named key. | `GOAL_BROKER_SIGNING_KEY` is the second named key. |
| `v1.md`, success criterion 2 | Over 90% classification accuracy. | Goal runtime meets or beats recorded outcomes on replay; zero unauthorized effects in the adversarial suite. |

Rule 5 distinguishes versioned role guidance from per-invocation task
artifacts; Rule 7 removes mandatory domain classification. Rules 1, 2 and 4
and the named SQL exceptions keep their meaning.

## Evidence and limits

- **[Observed]** The repository already accepts native deferred discovery in
  [RFC 0027](../rfcs/0027-runtime-tool-surface-discovery.md), which confines
  discovery to presentation and grants no execution authority. Its native
  deferred mode is not yet implemented.
- **[Observed]** Current provider interfaces support deferred tool loading;
  [OpenAI's tool-search documentation](https://developers.openai.com/api/docs/guides/tools-tool-search)
  describes preserving cache state while loading definitions as needed.
  [SCOUT, August 2026](https://arxiv.org/abs/2608.23992) reports a production
  searchable catalog spanning more than 2,000 tools. These are capability
  reports, not proof of this design.
- **[Observed]** The [April 2026 scaling study](https://arxiv.org/html/2512.08296v3)
  finds task-dependent collaboration gains and diminishing benefits as
  single-agent capability rises. [Anthropic's August experiments](https://www.anthropic.com/research/multiagent-systems)
  show improving coordination and useful specialization. Neither establishes a
  universally optimal topology or proves a depth-one limit.
- **[Observed]** Today's per-butler session isolation is configuration, not
  enforcement: runtimes run with their permission systems bypassed, a shell and
  the network of the shared `butlers-up` container, MCP endpoints have no
  caller authentication, and several tools let a session start sessions or
  proxy calls to any butler. The goal runtime adds the boundary rather than
  giving one up (RFC 0038, Context).
- **[Observed]** Vendor safety reports for current small models show
  indirect-injection success rates several times those of frontier models.
  RFC 0038 therefore keeps every authorization check deterministic, so model
  choice never weakens the boundary.
- **[Inferred]** Future model improvement favors replaceable reasoning
  organization over mandatory domain handoffs. Domain obligations, custody,
  permissions and provenance stay useful under either generalist or specialist
  models.
- **[Unknown]** Butlers-specific quality, latency, cost, useful proactive work
  and owner correction rates for the goal runtime. The cutover gate measures
  them against recorded outcomes before the switch.

## Architectural boundaries

- The lead owns goal decomposition, shared constraints, reconciliation and the
  final outcome. It can complete a simple goal with no children.
- A child's brief includes the question, relevant context, governing guidance,
  scope and expected evidence. A prompt is never an authority grant.
- A child may perform only the reads and writes its grant allows. Approvals,
  credential custody, private-source restrictions and domain invariants stay
  outside model discretion.
- A child can ask its lead for further work; it cannot reach another reasoning
  agent, admit a new root or schedule work to escape depth, permissions,
  budget or cancellation.
- One goal has one active lead ownership slot with recoverable lineage. A
  replacement or escalated lead cannot reset spent budget or replay uncertain
  effects. Independent goals run concurrently.
- Depth one is a deliberate initial bound. Changing it requires an explicit
  amendment backed by outcome evidence.
- Domain schedules, subscriptions, obligations and governing documents survive
  agent completion. Proactivity remains a first-class product purpose.

## Hard cutover

There is one system in doctrine and, after the switch, one in code. RFC 0038
specifies the freeze on the classify-and-route path, the dark build on main,
the cutover gate in a dev namespace, the single switching release and
rollback by redeploying the previous release. Nothing runs both paths for the
owner at once.

Until the switch, RFCs 0001, 0002, 0003, 0008 and 0027 carry a one-line
supersession header naming exactly what RFC 0038 replaces; their bodies stay
as written. `about/lay-and-land/`, `docs/` and `CLAUDE.md` describe the running
code and are rewritten by the cutover release. Baseline OpenSpec deltas are
authored after owner signoff of RFC 0038 and applied by the same release.

## Downstream disposition

| Artifacts | Disposition |
|---|---|
| `about/README.md`, `heart-and-soul/{README,vision,architecture,security,v1}.md` | Describe the goal system only. `about/README.md` and `CLAUDE.md` carry the single cutover pointer. |
| `craft-and-care/{performance-discipline,observability-and-operations}.md` | Whole-goal outcome evaluation and goal-ledger evidence. |
| RFCs 0001, 0002, 0003, 0008, 0027 | One-line supersession header now; status and body updated by the cutover release. |
| RFCs 0005, 0006, 0009, 0010, 0011, 0014, 0017, 0020, 0021, 0026, 0028, 0030, 0033, 0037 | Remain authoritative; session attribution maps to invocation lineage. |
| `openspec/specs/*` baseline capabilities | MODIFIED/REMOVED deltas after RFC signoff (tasks 1.3). |
| `roster/switchboard/MANIFESTO.md`, domain `CLAUDE.md` files | Rewritten by the cutover release: control-plane contract; domain guidance. |
| `lay-and-land/*`, `docs/*` | Unchanged until the cutover release. |
| Local `doctrine`, `adding-butlers-to-roster`, `butler-tool-review`, `butler-relentless-jarvis-pursuit` skills | Point at the goal doctrine and RFC 0038; no transition text. |

## Review and adoption record

- An independent fresh-context reviewer accepted the first revision as a shape
  amendment (coherence, adversarial and cross-pillar lenses), with one wording
  fix applied.
- The owner reviewed draft PR #4509 at `fbe008d2af3bb40762a1c58dd753d5fbbd9d20b0`
  and the architecture Site, and on 2026-10-14 instructed: "Looks good. Let's
  proceed with implementing all relevant project-shape changes".
- On 2026-10-14 the owner confirmed the sign-off, kept the vision and
  architecture changes, and directed the hard cutover and in-scope broker
  design quoted above. RFC 0038 and the `capability-authorization` capability
  are the result; they await the owner's design and release signoff.
