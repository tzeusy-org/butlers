# Inter-Butler Communication

> **Purpose:** Show how a butler reaches another butler in practice: which core tool to call for
> which intent.
> **Audience:** Butler developers designing cross-butler workflows.
> **Prerequisites:** [MCP Tools](mcp-tools.md).

## The Rule

Butlers never talk to each other directly — no cross-schema reads, no peer MCP clients, no
shared memory. Every cross-butler interaction goes through the Switchboard's `route()` primitive.
The rule, its rationale, and the one exception (the Switchboard's own client connections to
domain butlers) are defined in
[about/lay-and-land/integration.md](../../about/lay-and-land/integration.md) §6 and
[RFC 0003](../../about/legends-and-lore/rfcs/0003-switchboard-routing-and-ingestion.md); schema
isolation is described in [Schema Topology](../data_and_storage/schema-topology.md).

## Which Tool to Call

All of these are core tools registered fleet-wide on non-staffer butlers. Each calls a
Switchboard tool through `daemon.switchboard_client` (`deliver` for `notify`, `route` for the
others); the Switchboard calls the underlying function in-process for itself.

| Intent | Tool(s) | Source |
| --- | --- | --- |
| Tell the owner something (send, reply, react, insight) | `notify` | `src/butlers/core_tools/_notifications.py` |
| Ask whichever butler's domain covers a question, and get the answer back later | `delegate_ask` → target's `delegate_receive` / `delegate_answer` → asker's `delegate_wake` | `src/butlers/core_tools/_delegation.py` |
| Announce a standing domain event other butlers may care about | `publish_event`, `subscribe_to_event`, `unsubscribe_from_event` | `src/butlers/core_tools/_domain_events.py` |

- **`notify`** hands a `notify.v1` request to the Switchboard's `deliver`, which dispatches it to
  the messenger butler via `route.execute`. The caller never picks a transport adapter; it names
  a channel or an entity and the recipient's preferred channel is resolved.
- **`delegate_ask`** takes a self-contained question (the target has no access to your session
  context). The owning butler is resolved from `public.memory_catalog`, every outcome is recorded
  in `public.delegation_ledger`, and the call returns `routed`, `unroutable`, or `failed` with a
  `ledger_id`. The answer arrives asynchronously as a wake callback, not as the return value.
- **Domain events** fan out to subscribers through the same route path and wake each subscriber
  to create its own bounded task.

Deterministic jobs (not only LLM sessions) can use the same paths through `dispatch_delegated_ask`
and `fan_out_event`; see `publish_domain_event` for a convenience wrapper.

## Verification

```bash
# 1. Schema isolation: an unqualified name from another butler's schema is not visible
psql -h localhost -U butlers -d butlers \
  -c "SET search_path TO general,public; SELECT COUNT(*) FROM entity_facts;" 2>&1
# Expected: ERROR: relation "entity_facts" does not exist

# 2. Cross-butler hops are recorded by the Switchboard
psql -h localhost -U butlers -d butlers -c \
  "SELECT source_butler, target_butler, created_at
   FROM switchboard.routing_log ORDER BY created_at DESC LIMIT 5;"

# 3. Delegations are durably recorded, whatever their outcome
psql -h localhost -U butlers -d butlers -c \
  "SELECT * FROM public.delegation_ledger ORDER BY 1 DESC LIMIT 5;"
```

## Related Pages

- [MCP Tools](mcp-tools.md) -- Tool registration and naming
- [Ingestion Envelope](ingestion-envelope.md) -- How external events enter the system
- [Integration Points](../../about/lay-and-land/integration.md) -- Every protocol boundary
