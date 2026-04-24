# System Boundaries

Estimated smart-human study time: 8 hours

## Why This Module Matters

Butlers only makes sense if you separate stable orchestration from LLM reasoning, in-process capability from out-of-process transport, and direct tool access from routed inter-butler communication. Many unsafe changes come from putting logic in the wrong layer.

## Learning Goals

- Explain MCP, FastMCP, tool registration, and scoped runtime access.
- Distinguish daemons, sessions, modules, connectors, and the Switchboard.
- Trace a message or trigger from ingress to a target butler session.
- Identify why this repo is not a generic plugin marketplace or normal web app.

## Subsection: MCP Tool Servers And Ephemeral Runtime Sessions

### Why This Matters Here

Every butler is a long-running FastMCP server, but LLM reasoning happens in short-lived CLI subprocess sessions. The subprocess receives a temporary MCP config pointing only at its own butler.

### Technical Deep Dive

MCP is a tool-calling protocol: a host process exposes named tools with typed inputs and outputs, and a client calls those tools during reasoning. The key idea is capability scoping. A runtime should not receive every tool in the whole system; it should receive exactly the tools it is allowed to use.

In Butlers, the daemon is the durable authority. It owns tool registration, module state, scheduler state, session records, and database access. The LLM runtime is ephemeral and nondeterministic. It can reason, decide, and call tools, but it should not own durable infrastructure. This split lets the system audit sessions, constrain tool access, and preserve a deterministic boundary around side effects.

### Where It Appears In The Repo

- `docs/concepts/mcp-model.md`
- `docs/runtime/spawner.md`
- `src/butlers/core/spawner.py`
- `src/butlers/core/runtimes/`
- `tests/core/test_spawner_mcp_config.py`

### Sample Q&A

- Q: Why should a spawned LLM session receive only its butler's MCP endpoint?
  A: Because tool access is the capability boundary; broad MCP configs would bypass the Switchboard and leak cross-butler authority.
- Q: Which process should own durable state: the daemon or the LLM CLI?
  A: The daemon. The LLM CLI is a supervised, temporary reasoning worker.

### Progress

- [ ] Exposed: I can define MCP, FastMCP, tool, runtime adapter, daemon, and session.
- [ ] Working: I can explain why an ephemeral runtime should not own durable state.
- [ ] Working: I can point to the spawner and MCP model docs.

### Mastery Check

Target level: `working`

You should be able to describe how a butler starts a runtime session and why the generated MCP config is intentionally narrow.

## Subsection: Modules, Connectors, And The Switchboard

### Why This Matters Here

Modules and connectors sound similar, but they have opposite responsibilities. Mixing them up is a core architecture bug.

### Technical Deep Dive

A module is an in-process capability unit. It runs inside a daemon, validates config, may run migrations, starts background tasks, and registers tools. Use a module when the LLM needs a capability such as calendar, email, memory, or contacts.

A connector is an out-of-process transport adapter. It polls or listens to an external service, normalizes the event into a canonical envelope, checkpoints progress, and submits the event to the Switchboard. It should not classify, route, or perform domain actions.

The Switchboard is the routing boundary. It accepts ingress, preserves request context, performs deterministic triage or classification, and forwards route envelopes to target butlers. Inter-butler communication should flow through this path rather than ad hoc cross-schema reads or direct tool calls.

### Where It Appears In The Repo

- `docs/concepts/modules-and-connectors.md`
- `docs/concepts/switchboard-routing.md`
- `docs/architecture/routing.md`
- `src/butlers/modules/`
- `src/butlers/connectors/`
- `src/butlers/core_tools/_routing.py`

### Sample Q&A

- Q: You are adding Gmail polling. Is that a module or a connector?
  A: A connector for ingress polling and checkpointing; Gmail tools used by a butler can be module capability.
- Q: Why should a connector not decide the final target butler?
  A: Routing policy belongs at the Switchboard boundary so ingress stays transport-only and auditable.

### Progress

- [ ] Exposed: I can define module, connector, switchboard, route envelope, and ingress.
- [ ] Working: I can classify a new capability as module or connector.
- [ ] Contribution-ready: I can explain one bug caused by putting routing logic in a connector.

### Mastery Check

Target level: `contribution-ready`

You should be able to review a proposed integration and identify whether each responsibility belongs in a connector, module, Switchboard route path, or target butler.

## Module Mastery Gate

- [ ] I can summarize MCP capability scoping and ephemeral runtime sessions.
- [ ] I can trace connector ingress to Switchboard routing to a butler session.
- [ ] I can explain why modules, connectors, and the Switchboard are separate.
- [ ] I can point to the main repo docs and code surfaces for these boundaries.
