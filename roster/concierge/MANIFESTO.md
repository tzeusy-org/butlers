# Concierge Staffer: Infrastructure Contract

**Service type:** Staffer (infrastructure)
**Port:** 41112
**DB:** `butlers` / **Schema:** `concierge`

---

## Purpose

The Concierge staffer answers **system-plane** questions about the butler
fleet itself: what is running, what a session cost, which sessions failed,
what the current spend looks like. It exists so the dashboard chat's question
lane (bu-0ynlk.2) can answer these questions from typed, versioned read
models instead of either fabricating an answer or routing a purely
operational question to a domain butler that has no authority over it.

Concierge is read-only. It owns no write tools, no Operator-style controls,
and no domain data. It never answers a **domain** question ("what did I spend
on groceries", "when is my next appointment") stay with Finance,
General/Relationship/Health/etc. Concierge's jurisdiction is the fleet's own
operational telemetry: sessions, spend, fleet status, and the other
dashboard read-model surfaces enumerated in `about/legends-and-lore/rfcs/
0030-system-plane-read-exception.md`.

---

## Responsibilities

- **Fleet status reads:** board rows, per-butler detail, module health;
  mirrors `GET /api/butlers/board` and `GET /api/butlers/{name}` semantics via
  the same `src/butlers/api/read_models/*_v1.py` DTOs the dashboard uses.
- **Session reads:** recent sessions (fan-out, keyset-paged), a single
  session's detail, cross-butler aggregate counts, trigger-source breakdown.
- **Spend reads:** aggregate summary, daily series, top-costing sessions,
  breakdown by butler/model, computed the same way the `/api/spend/*` routes
  compute it (token counts × `pricing.toml`, never a stored dollar column).
- **Timeline / activity / insights reads:** the cross-butler timeline feed,
  a single butler's activity feed, and the insight-delivery aggregate.
- **Source attribution:** every tool result carries a `source: {kind, ref,
  as_of}` envelope so a downstream answer can cite exactly what was read and
  when.

## Non-Responsibilities

- Concierge does **not** respond to user messages directly (staffer type):
  it is invoked by the dashboard chat's question lane via Switchboard tool
  routing, not classified as a message target.
- Concierge does **not** register daily briefing contributions.
- Concierge does **not** expose a single write tool. No Operator-style
  fleet control (trigger, tick, mutate runtime config) lives here; that
  remains on the existing `/api/butlers/*` admin surface, gated separately.
- Concierge does **not** answer domain questions. A question about a user's
  own finances, health, or relationships is out of scope even if it
  superficially resembles a "how much" or "how many" question; see the
  Behavior Matrix in RFC 0030 for the boundary.
- Concierge does **not** query another butler's schema directly. All
  cross-schema reads go through the column-allowlisted, migration-tracked
  UNION views `concierge.v_fleet_sessions` / `concierge.v_fleet_spend`
  (RFC 0030) or through this butler's own MCP tool calls to the dashboard
  read-model layer for `public`-schema data (e.g. `public.insight_candidates`).

## Retention Policy

Concierge holds no data of its own beyond the standard core session log in
its own schema (`concierge.sessions`); no domain retention policy applies.

---

## SLAs

| Metric | Target |
|---|---|
| Tool call latency | Sub-second for keyset/aggregate reads (same query budget as the dashboard API routes they mirror) |
| Availability | Best-effort; a degraded/unreachable source returns the documented degraded envelope, never a silent empty result |
| Tool surface | 30-50 tools total (asserted by `roster/concierge/tests/test_dashboard_read.py`) |

---

## Permissions Model

| Resource | Access |
|---|---|
| `concierge.v_fleet_sessions` | Read only (sanctioned UNION view, RFC 0030) |
| `concierge.v_fleet_spend` | Read only (sanctioned UNION view, RFC 0030) |
| `public.insight_candidates` | Read only |
| `public.memory_catalog` | Read (catalog routing seed, standard grant) |
| Butler-owned schemas (direct) | No access |

`cross_butler_access = ["*"]` reflects that Concierge's *views* fan out across
every butler schema at the database level (each view executes with its
owner's privileges, not `butler_concierge_rw`'s own; that role holds no
grant on any other butler's tables at all), not that Concierge issues live
MCP calls to other butlers. See RFC 0030 for the full guardrail set.

---

## Failure Modes and Recovery

| Failure | Symptom | Recovery |
|---|---|---|
| A per-butler fan-out query fails | Tool result's `source` still returns, but the payload includes the documented degraded-source marker instead of a silently partial/empty list | Caller retries; the failing pool's own health is diagnosed separately |
| `concierge.v_fleet_sessions` / `v_fleet_spend` missing or grant revoked | Tool raises naming the view; `on_startup` fails loudly rather than degrading to empty | Re-run the `concierge` migration chain (`alembic upgrade concierge@head`) |
| Pricing config missing a model's rate | Spend tools return unpriced usage explicitly (never a fabricated `$0.00`) | Add the model to `pricing.toml` |

## Dependency Graph

### Depends On

- **PostgreSQL (`butlers.concierge` schema):** core session log, state store
- **PostgreSQL (`concierge.v_fleet_sessions` / `v_fleet_spend`):** the
  sanctioned cross-schema read views (RFC 0030)
- **PostgreSQL (`public.insight_candidates`, `public.memory_catalog`):**
  fleet-wide read-only tables already granted to every butler role
- **Switchboard:** registration, liveness, and tool-call routing from the
  dashboard chat's question lane

### Depends On Concierge

- **Dashboard chat question lane (bu-0ynlk.2):** routes system-plane
  questions here via `resolve_target_via_catalog` once the `public.
  memory_catalog` seed (RFC 0030) is in place.
