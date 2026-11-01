# Vision

## What Butlers Is

Butlers absorbs the recurring mental labor of one person's life. It maintains
health, relationships, finance, education, travel, home, and other declared
responsibilities, acts on incoming requests, and notices useful work before the
owner asks. One user owns the instance, data, credentials, and model choices.

The owner can express a whole goal across life domains. A generalist lead keeps
that goal intact, discovers relevant capabilities and context, and handles it
directly or delegates bounded work to focused subagents. The owner does not
have to choose a department or reconcile separate specialists' answers.

Three concepts have distinct lifetimes:

- **Domain responsibilities (butlers)** persist across sessions. Each has a
  manifesto, owned data and capabilities, schedules or event subscriptions,
  and obligations that survive any individual model invocation. Domain names
  express what the system cares for; they do not require one reasoning agent
  or one model per domain.
- **Reasoning agents** are ephemeral. One lead is accountable for each goal.
  It may create focused children with dedicated task prompts, selected context,
  and explicit permissions. The initial hierarchy has maximum depth one: lead
  at depth 0, children at depth 1. Children cannot delegate further. Different
  goals may have concurrent leads; there is no single global conversation.
- **Staffers and the control plane** own deterministic infrastructure such as
  ingress, identity, scheduling, execution admission, recovery, and delivery.
  Infrastructure responsibilities remain distinct from user life domains.
  Reasoning needed by a staffer still runs in an ephemeral session.

Specialization lives in domain knowledge, governing guidance, capability
contracts, and focused reasoning where it helps. The number of reasoning
agents is an execution choice, not the product's identity.

## What Butlers Is Not

**Not a SaaS or hosted product.** There is no multi-tenant architecture, no
shared database, no user accounts. Every instance belongs to exactly one person.
If someone else wants Butlers, they run their own.

**Not a chatbot.** Butlers act autonomously on cron schedules --- morning
briefings, inbox triage, health check-ins, memory consolidation. A user may
never send a message directly and still get value. Conversation is one input
channel, not the primary interface.

**Not an unrestricted agent.** A generalist may reason across domains, but
cannot grant itself access, bypass domain rules, or replace durable services
with an ever-growing conversation. Focused prompts are guidance; permissions
and approval gates are enforced by the runtime and capability owners.

**Not a framework for building other products.** Butlers is the product. It is
not a library, not a toolkit, not a platform for third-party developers. It
exists to serve one user's life, not to be packaged and resold.

**Not an experiment.** It is vibe-coded and early, but the intent is a system
that runs continuously, handles real data, and is trusted with real decisions.
The bar is reliability, not novelty.

## Non-Negotiable Rules

These are the load-bearing constraints. Violating any of them means the change
does not ship.

1. **User-federated: one user, one instance, full sovereignty.** The user owns
   the database, the credentials, the LLM API keys, and all data. There is no
   shared infrastructure. Design decisions must never assume or enable
   multi-tenancy.

2. **Modules only add tools --- they never touch core infrastructure.** A module
   registers MCP tools, declares database migrations, and hooks into the daemon
   lifecycle. It must not modify the state store, the scheduler, the spawner, or
   the session log. If a capability requires core changes, it belongs in core,
   not in a module.

3. **Cross-domain capability access is MCP-only through the control plane.**
   Switchboard remains the broker for domain-owned capabilities. A permitted
   read or action need not start another LLM session. A lead may combine
   authorized results across domains without direct schema access, shared
   database connections, or imports of another domain's implementation.
   Discovery and prompt instructions never grant authority. Invocation checks
   bind the caller, goal, permitted operation, and applicable approval to the
   action; delegated authority can only narrow. Durable facts retain their
   owner and provenance when retrieved into a goal's working context.

   [RFC 0038](../legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md)
   defines the grants, broker and call verification. Narrowly scoped
   read-only SQL exceptions still require an RFC with explicit guardrails and
   reuse criteria (see RFC 0010); RFC 0038 grants none.

   One separately reviewed exception permits only the QA daemon's
   deterministic local scheduler, under its matching runtime database role,
   to read its own explicit administrative-policy state and provenance
   through a fixed, no-argument Switchboard-owned read projection. The
   exception exists solely to keep local patrol independent of Switchboard
   process reachability without bypassing an owner stop. It conveys no
   cross-butler data, endpoint, observation, policy-write, or general-purpose
   SQL authority. Its exact grants, failure behavior, and rollback are
   governed by RFC 0010's QA-specific amendment; expansion requires a new
   owner-approved doctrine and RFC decision.

   A second separately reviewed exception permits only the Concierge
   staffer to answer system-plane questions on demand by reading fixed,
   migration-tracked, read-only views of fleet operational telemetry
   (sessions, spend, fleet status), reached through Switchboard-routed tool
   calls. It conveys no domain data and no general-purpose SQL authority.
   Its views, grants, and failure behavior are governed by RFC 0030;
   expansion requires a new owner-approved doctrine and RFC decision. These
   two named carve-outs do not create a general class of on-demand
   exceptions.

4. **The daemon is deterministic infrastructure; intelligence is in ephemeral LLM
   sessions.** The daemon manages state, runs migrations, enforces schedules, and
   registers tools. It must be testable, debuggable, and predictable. The goal
   invocations the control plane launches are where reasoning and judgment
   happen. Mixing LLM logic into the daemon is a defect.

5. **Git-based config is the source of truth for butler identity; operational
   tuning lives in the database.** A butler's personality, schedule, module
   selection, and manifesto live in git-tracked files under `roster/`. These
   define *who the butler is* --- its name, purpose, domain, and governing
   document. Operational tuning lives in the database, not git. Concurrency
   limits and tool surface configuration (`core_groups`) live in a per-schema
   `runtime_config` table, seeded from git on first boot and managed via the
   dashboard thereafter. Model selection and session timeouts are resolved per
   complexity tier from the shared `public.model_catalog` table and edited via
   the dashboard Models tab (see `src/butlers/core/model_routing.py`). The
   distinction: identity answers
   "what is this butler?"; operational tuning answers "how should it behave
   right now?" Reusable lead policies, domain guidance, and focus templates are
   also versioned identity configuration.
   Per-task prompts and selected context are invocation artifacts, not new
   roster identities. Runtime tuning cannot widen policy-granted authority.
   If someone changes a butler's name, manifesto, or module list,
   that is an identity change and belongs in git. If someone changes which
   model it uses or how many concurrent sessions it runs, that is operational
   tuning and belongs in the database.

6. **Every agent has a governing document that controls its scope.** For
   butlers, this is a manifesto: it defines what the butler cares about, what
   it promises, what it refuses, and the conceptual frameworks it uses to
   structure and prioritize knowledge within its domain. For staffers, this is
   an infrastructure contract: it defines the service's responsibilities, SLAs,
   failure modes, dependency graph, and escalation procedures. In both cases the
   document is not decoration --- it is binding. Features, tools, and UX
   decisions must be deeply aligned with the governing document. A capability
   that contradicts it must not be added without a formal amendment.
   A lead inherits the system's governing policy; a focused child inherits
   that policy and the applicable domain guidance plus a bounded task brief.
   Each invocation records which versions govern it. A generated task prompt
   cannot override these documents. The runtime enforces the depth-one limit,
   finite work budgets, and child scope; a child must return requests for
   further work to its lead rather than spawn or route another reasoning agent.

7. **Transport is connector responsibility; butlers never know about transport
   details.** Connectors normalize external events into a canonical ingestion
   format and submit them to the Switchboard. Reasoning sessions receive
   structured events and goals; domain classification is not a mandatory
   decomposition of the owner's intent. A butler must never contain Telegram
   polling logic, Gmail
   API calls, or Discord websocket handling. If a butler knows how a message
   arrived, something is wrong.

## What Success Looks Like

Butlers succeeds when it runs for weeks without intervention, handles the
owner's daily information flow, and the owner trusts it with progressively more
autonomy. The measure is not feature count --- it is the amount of mental labor
the system reliably absorbs.

Concrete markers:

- The owner expresses a goal from any channel, including a cross-domain goal,
  and receives one coherent outcome without manual routing or reconciliation.
- Focused workers improve the outcome when used; the lead remains accountable
  for resolving conflicting evidence and reporting partial or unknown outcomes.
- Domain obligations remain active when no conversation or lead is running.
- Scheduled tasks fire on time, produce useful output, and recover from
  transient failures.
- The owner's health data, relationship context, financial signals, and calendar
  are maintained without manual entry.
- Butlers surface timely, relevant insights without being asked --- and
  automatically reduce frequency when the owner disengages.
- The system is boring. It works. The owner stops thinking about it.

## Anti-Patterns

- Requiring a new reasoning agent merely to access another domain's capability.
- Giving a generalist unrestricted authority or treating a focus prompt as a sandbox.
- Replacing proactive responsibilities with a purely request-driven assistant.
- Encoding today's model limits or prices as permanent agent boundaries.
- Building admin features for managing multiple users.
- Embedding transport-specific logic inside butler code.
- Making the daemon "smart" instead of keeping intelligence in sessions.
- Treating manifestos as optional documentation rather than binding contracts.
- Designing features that require an always-on internet connection to function
  at the daemon level (LLM calls are the exception, not the rule).
