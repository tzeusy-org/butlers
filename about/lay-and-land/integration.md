# Integration Points

How subsystems connect at their boundaries: wire protocols, envelope schemas,
and transport details.

The observer and per-target intent edges in the overview are approved target
contracts in `restore-butler-control-plane-liveness` and
`recover-ingestion-target-deliveries`; they are not current runtime claims.

---

## Overview

```mermaid
graph TB
    subgraph Connectors
        C["Connector Process"]
    end

    subgraph Switchboard
        Ingest["ingest() tool"]
        Classify["classify()"]
        Intent["per-target delivery intents"]
        Route["route.execute()"]
        Preflight["internal read-only route preflight"]
    end

    subgraph DomainButler["Domain Butler"]
        RouteInbox["route_inbox"]
        Identity["internal identity/readiness facts"]
        Spawner["Spawner"]
        MCP["FastMCP Server"]
    end

    subgraph LLMSession["Ephemeral LLM Session"]
        CLI["LLM CLI"]
    end

    subgraph Dashboard
        FastAPI["FastAPI Backend"]
        Observer["supervised fleet observer"]
    end

    subgraph DB["PostgreSQL"]
        Schema["Per-butler schema"]
        Shared["public schema (cross-butler tables)"]
    end

    C -- "ingest.v1 / MCP SSE" --> Ingest
    Ingest --> Classify
    Classify -- "durable classified targets" --> Intent
    Intent -- "fenced route.v1 attempt" --> Route
    Route -- "route.v1 / MCP SSE" --> RouteInbox
    Observer -- "exact roster endpoint / bounded GET" --> Identity
    Observer -- "bounded internal GET" --> Preflight
    Preflight -- "identity GET only" --> Identity
    Observer -- "DB-server observation" --> Shared
    RouteInbox --> Spawner
    Spawner -- "ephemeral MCP config / subprocess" --> CLI
    CLI -- "MCP tool calls / SSE or HTTP" --> MCP

    FastAPI -- "SQL / asyncpg" --> Schema
    FastAPI -- "SQL / asyncpg" --> Shared
    MCP -- "SQL / asyncpg" --> Schema
    MCP -- "SQL / asyncpg" --> Shared
```

---

## 1. Connector to Switchboard: `ingest.v1`

**Transport**: MCP tool call over SSE (via `CachedMCPClient`)

**Endpoint**: Switchboard's `ingest()` MCP tool

**Envelope schema** (`ingest.v1`, defined in `roster/switchboard/tools/routing/contracts.py`):

| Field | Type | Required | Description |
|---|---|---|---|
| `schema_version` | `"ingest.v1"` | Yes | Fixed version string |
| `source_channel` | SourceChannel enum | Yes | `telegram_bot`, `telegram_user_client`, `email`, `voice`, `api`, `mcp`, `slack` |
| `source_provider` | SourceProvider enum | Yes | `telegram`, `gmail`, `imap`, `internal`, `live-listener`, `slack` |
| `source_endpoint_identity` | string | Yes | Connector instance identity (e.g., bot username) |
| `source_sender_identity` | string | Yes | Sender identifier within the channel |
| `source_thread_identity` | string | No | Thread/conversation identifier for reply targeting |
| `received_at` | RFC3339 datetime | Yes | Timestamp of event reception |
| `content` | string | Yes | Normalized message content |
| `content_type` | string | No | MIME type hint |
| `metadata` | dict | No | Channel-specific metadata |
| `attachments` | list | No | Attachment descriptors |
| `ingestion_tier` | `"full"` / `"metadata"` | No | Ingestion depth |
| `policy_tier` | `"default"` / `"interactive"` / `"high_priority"` | No | Processing priority |

**Validation**: Pydantic model enforces channel-provider compatibility (e.g.,
`telegram_bot` channel requires `telegram` provider).

**Readiness**: Connectors call `wait_for_switchboard_ready()` before entering
their main loop, polling `GET /health` with exponential backoff.

---

## 2. Switchboard to Domain Butler: `route.v1`

**Transport**: MCP tool call over SSE (Switchboard acts as MCP client to the
target butler's MCP server)

**Endpoint**: Target butler's `route.execute()` MCP tool

**Envelope schema** (`route.v1`):

| Field | Type | Required | Description |
|---|---|---|---|
| `schema_version` | `"route.v1"` | Yes | Fixed version string |
| `request_id` | UUID | Yes | Unique request identifier |
| `source_channel` | SourceChannel | Yes | Preserved from ingest envelope |
| `source_endpoint_identity` | string | Yes | Preserved from ingest envelope |
| `source_sender_identity` | string | Yes | Preserved from ingest envelope |
| `source_thread_identity` | string | No | Preserved from ingest envelope |
| `target_butler` | string | Yes | Target butler name |
| `prompt` | string | Yes | Classified message content |
| `conversation_history` | string | No | Recent conversation context |
| `input_context` | dict/string | No | Additional routing context |
| `attachments` | list | No | Attachment references |

**Authorization**: The target butler checks `trusted_route_callers` (default:
`["switchboard"]`) to verify the caller identity. Untrusted callers are
rejected.

**Durability**: On acceptance, the target butler inserts the envelope into
`route_inbox` before returning `{"status": "accepted"}`. Processing happens
asynchronously under a fenced processing-claim lease. Startup recovery can
claim and re-dispatch ordinary accepted work (and recoverable stale work), but
an already-processing dashboard turn first reconciles its durable predecessor.
If that predecessor is not provably terminal, recovery marks the route row for
operator attention and does not automatically replay a second runtime.

**Approved ingestion recovery target (2026-09-23):** Ordinary non-dashboard
ingestion-to-domain routing has a Switchboard-owned durable per-target intent
before the first `route.execute` call. Its stable acceptance identity is
`(ingestion_event_id, target_butler, segment_id)`; the target atomically
upserts this identity with the canonical immutable payload digest and returns
the same receipt on exact duplicates. Receipt lookup compares both receiving
target and digest; changed work conflicts rather than appearing accepted. The
Switchboard retries only proven pre-acceptance no-effect attempts and settles
at `accepted` when the target owns its inbox row. Ambiguous attempts reconcile
by the same key and never create a second target row. Its per-segment intent
state is authoritative; the legacy per-butler dispatch outcome cannot collapse
two segments to one success. Source `ingested` status does not imply universal
target acceptance. Connector ingress replay,
dashboard turn recovery, Messenger delivery, and domain-event subscriptions
keep their distinct ownership and stores. Historic failed rows need a
content-blind dry run and exact owner-reviewed recovery policy.

---

## 3. Spawner to LLM CLI: Ephemeral MCP Config

**Transport**: Subprocess invocation with environment injection

**Mechanism**:

1. Spawner generates a temporary MCP config (JSON) pointing exclusively at
   this butler's MCP endpoint URL.
2. Spawner selects a runtime adapter (Claude Code, Codex, Gemini, OpenCode)
   based on model catalog resolution.
3. Spawner builds CLI arguments via the adapter's `build_args()` method.
4. Spawner injects environment variables:
   - `TRACEPARENT` -- OpenTelemetry trace context propagation
   - Declared credentials from the butler's `env_required`/`env_optional` lists
   - Model-specific API keys
5. Spawner invokes the CLI as a subprocess.

**MCP endpoint URL**: `http://localhost:{port}/mcp` (streamable HTTP) or
`http://localhost:{port}/sse` (legacy SSE).

**Concurrency control**: Two-level semaphore.
- Per-butler: `max_concurrent_sessions` (from `butler.toml`, default 1).
- Global: `BUTLERS_MAX_GLOBAL_SESSIONS` (default 3, across all butlers in-process).

**Session lifecycle**:
- `session_create()` -- DB INSERT before CLI invocation.
- CLI runs, calls MCP tools, returns.
- `session_complete()` -- DB UPDATE with exit code, token counts, cost, tool
  calls, and duration.

---

## 4. LLM Session to Butler: MCP Tool Calls

**Transport**: MCP over streamable HTTP (`/mcp`) or SSE (`/sse`)

**Server**: Butler's FastMCP instance (created with core tools in daemon startup phase 13; its SSE server starts in phase 15)

**Available tools**: Core tools (status, trigger, state_*, schedule_*,
sessions_*, notify, remind, get_attachment, module.states, module.set_enabled)
plus all tools registered by enabled modules.

**Approval gating**: Configured tools pass through the approvals module gate
before execution. The gate checks existing approval rules, risk tiers, and
expiry. If no valid approval exists, the tool call is blocked pending human
approval.

**Tool call capture**: All tool calls during a session are captured via
`ContextVar`-based tracking and persisted in the session log.

---

## 5. Dashboard to Database: Direct SQL

**Transport**: asyncpg connection pools (one pool per butler schema)

**Manager**: `src/butlers/api/db.py::DatabaseManager` -- initialized at FastAPI
startup, creates pools for each discovered butler config.

**Access pattern**: The dashboard reads directly from butler databases. It does
not proxy through butler MCP servers. This means:
- Dashboard queries can span multiple butler schemas in a single request.
- Dashboard has read access to shared identity tables.
- Dashboard writes are limited to admin operations (secrets, model catalog,
  provider settings).

**Auto-wiring**: `wire_db_dependencies()` patches FastAPI dependency injection
so butler-specific routers (from `roster/{butler}/api/router.py`) receive the
correct DatabaseManager instance.

---

### Owner authentication before dashboard domain access

The browser first completes the dedicated WebAuthn/session contract over the
canonical HTTPS origin. Authentication-store access is bounded and separate
from domain-pool acquisition: the central boundary must establish the owner
before reading request bodies, domain records, caches or owner/contact rows.
Unsafe cookie-backed requests additionally pass synchronizer CSRF and exact
Origin validation. The resulting principal does not skip domain-specific
approval, privacy, idempotency or owner-integrity checks.

Host CLI operations use trusted administrative access for initial authorization,
recovery and mode reconciliation. The API can complete only an already-authorized
browser-bound ceremony through restricted persistence operations. No MCP tool,
connector or runtime child receives host authorization authority. See
[owner authentication](../../docs/identity_and_secrets/dashboard-owner-auth.md).

## 6. Butler to Butler: MCP via Switchboard

**Rule**: Butlers never communicate directly. All inter-butler communication
flows through the Switchboard.

**Mechanism**: A butler's LLM session can call `notify()` which routes through
the Switchboard's notification tools. There is no direct MCP client connection
between domain butlers.

**Exception**: The Switchboard itself holds MCP client connections to all
registered domain butlers for route dispatch.

### Candidate voice-egress control plane (not implemented)

RFC 0034 keeps the same MCP-only rule while adding two versioned envelopes:

| Envelope | Authenticated producer | Consumer | Content boundary |
|---|---|---|---|
| `voice_origin.v1` | Switchboard's isolated Ed25519 service principal, matched to its durable route record | Messenger | Switchboard-signed canonical request/service/intent/message digest plus verified reply-lineage ref or one explicit opaque endpoint ref; no raw mic/room/device id |
| `voice_presence_attest.v1` request | Messenger signature verified by Switchboard; separately signed broker hop to Home | Home | Opaque room ref, binding version, single-use nonce; no message content |
| `voice_presence_attest.v1` result | Home signature verified/relayed under a Switchboard signature | Messenger | Nested signatures over categorical result/freshness/age bound to version and nonce; no raw presence evidence |

Messenger never connects directly to Home. Dedicated immutable service
keyrings and durable nonce receipts authenticate every broker hop; generic MCP
reachability and caller fields do not. Switchboard does not manufacture
presence or choose a physical endpoint. Home does not receive speech content
and is presence-only under this candidate: current RFC 0028 cannot admit a
Home/HA voice provider. A separate accepted amendment must define its approval,
speech, receipt, freshness, and persistence seam. The provider adapter is
Messenger-owned and returns only the exact no-start/start/confirm/fail/unknown
categories. This is a target contract only; it provisions no keys and exposes
no live endpoint until separate implementation and activation authority exists.

---

## 7. Non-Switchboard Butler to Switchboard: Registration

**Transport**: MCP client connection for dispatch; backend-network HTTP GET
from the control-plane observer to each daemon's existing port for liveness

On startup, each non-switchboard butler opens an MCP client to
`{switchboard_url}/mcp` during daemon startup phase 12 and advertises its
configured endpoint. Every daemon, including Switchboard, exposes
`GET /internal/control-plane/identity` on its existing port. Its bounded
`butler.control.v1` response carries `butler_name`, UUIDv7
`boot_instance_id`, a server-allocated durable `boot_epoch`, `route_contract` minimum/maximum, and
`accepting_routes`. The separately supervised Dashboard/control-plane
observer probes only exact Git-roster host:port/path entries, checks the
response against expected identity, generation, and compatibility, and
records DB-server observation time. Switchboard may perform one bounded
stale-route recheck using the same verifier. Both receivers reserve a shared
per-daemon probe sequence in the database and conditionally write against the
latest boot epoch; neither gains owner-auth or administrative-policy authority.

The registry keeps observed health, administrative policy, and route
compatibility separately. A stale target receives one bounded on-demand
probe before a typed `not_attempted` refusal. A healthy probe or restart never
clears administrative quarantine. The old dashboard heartbeat POST is retired
after cutover; owner auth does not make it anonymous. This is an approved
target contract, not a claim that the current runtime has cut over.

L3's separate Switchboard-owned internal route preflight chooses the fixed
domain target from Git roster and traverses the pure production resolver,
then performs only a bounded identity GET. It makes no target MCP call or
durable evidence write and returns a content-blind result to the Dashboard
controller. Q4's public `/ready` reads the controller's cached result without
calling Switchboard per request; k3s and Compose consume the one Q4-owned
route and owner-auth exception. A positive preflight does not certify target
inbox acceptance.

### L1 registry representation and rollback

Switchboard migration `sw_035` adds
`switchboard.butler_registry_control_plane` beside the existing registry.
It stores administrative policy and its provenance separately from observed
health, route compatibility, the latest committed boot UUID/epoch, reserved and
recorded probe sequences, last probe attempt, and last verified healthy time.
The migration copies original legacy quarantine fields and the matching
eligibility-log receipt into `legacy_evidence`. A matching TTL transition
becomes stale observation; a matching owner transition becomes restrictive
policy. Missing or contradictory evidence becomes `review_required`, never
an inferred owner release. The same classification applies to legacy manual
and TTL-derived `stale` rows.

The new table has runtime RLS with no direct write policy, including after an
`init-db.sql` grant replay. Fixed `public.register_butler_boot`,
`reserve_butler_probe`, and `record_butler_probe` operations allocate epochs
and sequences under database locks and stamp attempts with database time.
New legacy registry inserts initialize an unknown control row without
claiming health or releasing a retained policy for a reused name.
`public.set_butler_registry_policy` is reserved for the authenticated owner
API's database login; the Switchboard runtime role cannot invoke it. A legacy
registry trigger retains `eligibility_state='quarantined'` for paused,
quarantined, or review-required policy despite automatic registrations, sweeps,
heartbeat writes, or confirmed-route touches. A failed probe advances its
attempt/failure state without advancing `healthy_observed_at`.
The existing eligibility endpoint accepts explicit `paused` and
`review_required`; its older operator `stale` request means a manual pause
and maps to `paused` policy. Its response reports the actual restrictive
legacy `quarantined` projection, not an invented receiver-stale observation.

L1 does not switch route authority. The legacy writers still include the
`register_butler` upsert, `run_eligibility_sweep`,
`_reconcile_eligibility_state`, the heartbeat API, and routing's
confirmed-success touch; only the owner eligibility API now writes protected
policy. `resolve_routing_target`, `list_butlers`, the Switchboard registry
API, the Dashboard butler status read, and the QA heartbeat view still consume
the legacy registry/projection. L3 must audit and move each reader and writer
before cutover; L4 retires the heartbeat writer. On code rollback,
disable any new observer first and retain the `sw_035` table, RLS, trigger,
functions, provenance and highest boot epoch; its Alembic downgrade is
intentionally non-destructive. An operator must release a restrictive policy
through the authenticated eligibility action, never by editing the old
`eligibility_state` column. Do not drop the new representation while a
reader, rollback path, or old-process fence still relies on it.

---

## 8. Connector Heartbeat: Liveness Reporting

**Transport**: MCP tool call (`connector.heartbeat`)

**Payload**:
- `instance_id` -- stable UUID generated at process startup
- `connector_type` -- e.g., `telegram_bot`, `gmail`
- `endpoint_identity` -- e.g., bot username, email address
- `version` -- connector version string
- `counters` -- current metric values (messages ingested, errors, etc.)
- `health` -- derived health state

**Interval**: `CONNECTOR_HEARTBEAT_INTERVAL_S` (default 120s, min 30s, max 300s)

**Failure behavior**: Heartbeat failures are logged but never crash or block
ingestion.

---

## 9. Observability: Trace Propagation

**Protocol**: W3C Trace Context (`TRACEPARENT` header/env var)

**Propagation path**:
1. Connector creates a root span for each ingested event.
2. Switchboard receives the trace context via MCP call metadata.
3. Switchboard creates child spans for triage, classification, and routing.
4. Route dispatch to target butler carries trace context.
5. Target butler's route.execute creates a linked span.
6. Spawner injects `TRACEPARENT` into the LLM CLI subprocess environment.
7. The LLM CLI (if instrumented) joins the trace.

This produces an end-to-end trace from external event arrival through
classification, routing, and session execution.

---

## Protocol Summary

| Boundary | Protocol | Envelope | Transport |
|---|---|---|---|
| Connector -> Switchboard | MCP | `ingest.v1` | SSE |
| Switchboard -> Domain Butler | MCP | `route.v1` | SSE |
| Spawner -> LLM CLI | Subprocess | Ephemeral MCP config + env vars | stdin/env |
| LLM CLI -> Butler | MCP | Tool calls | Streamable HTTP or SSE |
| Dashboard -> Database | SQL | asyncpg queries | TCP |
| Butler -> Database | SQL | asyncpg queries | TCP |
| Connector -> Switchboard (heartbeat) | MCP | `connector.heartbeat` | SSE |
| Control plane -> daemon (liveness) | backend-network HTTP GET | `butler.control.v1` bounded facts | HTTP |
| Switchboard -> target (ingestion delivery) | MCP | stable per-target acceptance identity and receipt | Streamable HTTP or SSE |
| All -> OTel | OTLP | Traces + metrics | gRPC |
