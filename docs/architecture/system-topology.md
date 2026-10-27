# System Topology

> **Purpose:** The one-picture view of how Butlers fits together, with pointers to the
> authoritative topology.
> **Audience:** New developers and operators.

![System Topology](./system-overview.svg)

Butlers is a personal AI agent system in a hub-and-spoke shape. Each butler is a long-running
MCP server daemon; the Switchboard is the single ingress and routes work to domain butlers over
MCP, and outbound messages flow back through the Switchboard to Messenger. All butlers share one
PostgreSQL database, each confined to its own schema plus `public`, so cross-butler data moves
only through MCP tool calls. Each butler is configured by its git-backed
`roster/<butler>/butler.toml`.

The authoritative topology lives in `about/lay-and-land/`:

- [Components](../../about/lay-and-land/components.md) — every runtime piece and what it owns
- [Deployment](../../about/lay-and-land/deployment.md) — process model, ports, Docker Compose,
  database topology, and deployment modes
- [Integration](../../about/lay-and-land/integration.md) — the wire protocols between components
- [Data Flow](../../about/lay-and-land/data-flow.md) — ingestion, scheduled, and outbound flows

## Related Pages

- [Switchboard Routing](../concepts/switchboard-routing.md) — how a message is routed, end to end
- [Butler Daemon](butler-daemon.md) — daemon internals and startup sequence
- [Database Design](database-design.md) — schema isolation and migration strategy
- [Observability](observability.md) — tracing and metrics infrastructure
