# Adopted doctrine amendment: generalist lead and focused delegation

**Status:** Adopted by the owner on 2026-10-14. Delivered by one hard cutover
specified in [RFC 0038](../rfcs/0038-goal-runtime-and-capability-authorization.md)
(accepted 2026-10-14 with four owner amendments).
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

The lead is a role per goal, not a butler. Goal admission, the goal runner and
the capability broker live in the Switchboard control plane, which moves into
its own process; the lead has a versioned policy document; General gains no
authority (RFC 0038 D1, owner decision 1).

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
| `security.md`, Session Sandboxing | "A session cannot spawn other sessions." (Configured, never enforced; see RFC 0038 Context.) | Sessions are sandboxed and reach only the broker; only the goal runner starts sessions, and a lead can obtain only depth-1 children. |
| `security.md`, deployment control keys | One named key. | `GOAL_BROKER_SIGNING_KEY` is the second named key (owner decision 6). |
| `v1.md`, success criterion 2 | Over 90% classification accuracy. | Goal runtime meets or beats recorded outcomes on replay; zero unauthorized effects in the adversarial suite (owner decision 2). |
| `v1.md`, success criterion 6 | "The dashboard shows butler status, sessions, contacts, and ingestion flow in real time." | The dashboard shows goals with lineage, effects and approvals, and each domain region (owner decision 11). |
| `security.md`, Goal and Delegation Authority; Approval Gates | "A third-party goal can read, record attributed facts, notify the owner, and at most prepare an action for owner approval; it can never itself send, spend, actuate, or administer." Non-owner communication approval was enforced by `core-notify`, the approval-gated channel tools and Messenger's delivery gate; calendar invitations were not gated. | Communication with anyone other than the owner needs owner approval for every goal; no goal holds `transact` (owner decisions 8 and 9). |

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
- **[Reported by vendors]** Current small models resist indirect prompt
  injection less well than frontier models: the
  [Claude Haiku 5.5 system card](https://www.anthropic.com/document/claude-haiku-5-5-system-card)
  reports a 7.1% attacker success rate within 15 attempts against 1.0% for
  Opus 5.5, and [OpenAI's October 2026 GPT-6 update](https://cdn.openai.com/pdf/gpt-6-october.pdf)
  reports lower injection robustness for GPT-6 Luna than for Sol.
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

Until the switch, RFCs 0001, 0002, 0003, 0007, 0008, 0027 and 0037 carry a
one-line supersession header naming exactly what RFC 0038 replaces; their
bodies stay as written. `about/lay-and-land/`, `docs/` and `CLAUDE.md` describe
the running code and are rewritten by the cutover release. Baseline OpenSpec
deltas were authored in this change after owner signoff of RFC 0038 and are
applied by the same release.

## Downstream disposition

| Artifacts | Disposition |
|---|---|
| `about/README.md`, `heart-and-soul/{README,vision,architecture,security,v1}.md` | Describe the goal system only. `about/README.md`, `CLAUDE.md` and the local `doctrine` skill each carry one cutover pointer. |
| `craft-and-care/{performance-discipline,observability-and-operations}.md` | Whole-goal outcome evaluation and goal-ledger evidence. |
| RFCs 0001, 0002, 0003, 0007, 0008, 0027, 0037 | One-line supersession header now; status and body updated by the cutover release. |
| All other RFCs | Disposition per RFC 0038's table: most remain authoritative with session attribution mapped to invocation lineage; 0021 is amended. After the switch the whole corpus is rewritten and reorganized (owner decision 10). |
| `openspec/specs/*` baseline capabilities | MODIFIED/REMOVED/ADDED deltas authored in this change (tasks 1.4); applied by the cutover release. |
| `heart-and-soul/design-language.md` | Carries a D19 pointer now; rewritten by the signed D19 dashboard change before the switch. |
| `roster/switchboard/MANIFESTO.md`, domain `CLAUDE.md` files | Rewritten by the cutover release: control-plane contract; domain guidance. |
| `lay-and-land/*`, `docs/*` | Unchanged until the cutover release. |
| Local `doctrine`, `adding-butlers-to-roster`, `butler-tool-review`, `butler-relentless-jarvis-pursuit` skills | Point at the goal doctrine and RFC 0038; only the `doctrine` skill mentions the pending cutover. |

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
  are the result.
- An independent fresh-context review of that revision returned ACCEPT WITH
  FIXES: six blocking text fixes (pending owner decisions written as settled,
  signing key sharing the domains' process, indirect root admission through
  domain reactions and runtime obligations, exposure laundering through stored
  data, undispositioned model paths and RFCs, child-token contradiction) plus
  security and wording items. All were addressed in RFC 0038 and the specs.
- On 2026-10-14 the owner answered RFC 0038's open decisions, choosing:
  "Switchboard, own process"; "Yes, v1 ships on goals"; "Narrow exemptions";
  and approving both `GOAL_BROKER_SIGNING_KEY` and "Third-party park-only".
- On 2026-10-14 the owner signed off the full design with four amendments:
  "`transact` should not be enabled, I don't trust the systems that far yet";
  "The existing rule of 'all communication with all entities that are NOT
  myself (owner) require approval' must still stand"; "We should do a
  rewrite/reorganization of our RFCs after this rearchitecture"; and design
  work for the dashboard, because "specialized butlers are going to be more
  akin to specialized regions of the same brain". "The proposal is otherwise
  signed off." RFC 0038 records them as owner decisions 8 to 11 and D19; the
  baseline OpenSpec deltas were authored in the same pull request.
- Detail the author added while applying the amendments stands pending owner
  confirmation, as RFC 0038's status line lists: `recipient_params` and the D4
  recipient rules, the reclassification of calendar-invitation answers to
  `communicate`, the Messenger delivery carve-out in D7, the D19 direction
  bullets, the proposed RFC organization, and the gaps closed while writing
  the baseline deltas.
