# Lay of the Land -- Topology Maps

This directory answers **WHERE**: where components live, how they connect, what
boundaries exist, and how the system is deployed.

## Documents

| Document | Question it answers |
|---|---|
| [components.md](components.md) | What are the moving parts and what does each one own? |
| [data-flow.md](data-flow.md) | How does data travel through the system end to end? |
| [deployment.md](deployment.md) | How is the system deployed and what ports/services exist? |
| [dependencies.md](dependencies.md) | What depends on what -- internally and externally? |
| [integration.md](integration.md) | How do subsystems connect at their boundaries? |

## Reading Order

1. **components.md** -- start here for the inventory of every major piece.
2. **data-flow.md** -- follow a message from the outside world through the system.
3. **integration.md** -- zoom into each boundary and understand the wire protocol.
4. **dependencies.md** -- understand the dependency graph for startup order and failure blast radius.
5. **deployment.md** -- understand the runtime topology: processes, ports, and infrastructure.

## Stability Legend

Throughout these documents, components are marked with a stability level:

- **Stable** -- API surface and behavior are production-tested and unlikely to change.
- **Maturing** -- Core behavior works; edge cases and API surface still evolving.
- **Evolving** -- Active development; expect breaking changes.
- **Draft** -- Skeleton or spec only; not production-ready.
