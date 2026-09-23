# Dependency Map

Internal and external dependencies, startup ordering constraints, and failure
blast radius analysis.

The fleet-observer edges below are the approved target contract in
`restore-butler-control-plane-liveness`. Until its cutover, deployed daemons
still send the legacy heartbeat POST and the observer does not exist.

---

## Internal Dependency Graph

### Module Dependencies (topological sort at startup)

```mermaid
graph TD
    Pipeline["pipeline"]
    Memory["memory"]
    Email["email"]
    Telegram["telegram"]
    Calendar["calendar"]
    Contacts["contacts"]
    Approvals["approvals"]
    Mailbox["mailbox"]
    Metrics["metrics (module)"]
    SelfHealing["self_healing"]

    Pipeline --> Memory
    Pipeline --> Email
    Pipeline --> Telegram
    Approvals --> Pipeline
```

Module dependencies are declared via the `dependencies` property on each Module
subclass. The `ModuleRegistry` resolves them via topological sort before
calling `on_startup()`. Shutdown happens in reverse topological order.

Modules that declare no dependencies (memory, email, telegram, calendar,
contacts, mailbox, metrics, self_healing) can start in any order relative to
each other.

### Butler-to-Switchboard Dependency

```mermaid
graph LR
    GEN["general :41101"] -- "MCP client" --> SW["switchboard :41100"]
    REL["relationship :41102"] -- "MCP client" --> SW
    HLT["health :41103"] -- "MCP client" --> SW
    MSG["messenger :41104"] -- "MCP client" --> SW
    FIN["finance :41105"] -- "MCP client" --> SW

    OBS["supervised dashboard fleet observer"] -- "bounded internal GET" --> GEN
    OBS -- "bounded internal GET" --> REL
    OBS -- "bounded internal GET" --> HLT
    OBS -- "bounded internal GET" --> SW
    OBS -- "reserved probe + DB-server observation" --> REG["Switchboard registry"]
    SW -- "one stale-route probe + conditional observation" --> REG
```

In the approved target state, every non-switchboard butler:

1. Opens an MCP client connection to the Switchboard during startup phase 12.
2. Exposes bounded identity and route-readiness facts on its existing internal
   port. The supervised observer probes the exact Git-roster endpoint. The
   Switchboard daemon is observed by the same mechanism.
   Each boot obtains a server-allocated durable epoch before advertising route
   acceptance. The Dashboard observer and Switchboard stale-route recheck
   share the exact-response verifier and reserve probe sequences in the
   database; conditional writes fence old boots and overlapping receivers.

The observer's DB-server timestamp is liveness evidence; daemon-authored
heartbeat POSTs are retired after the approved cutover. Connector MCP
heartbeats remain independent. Routing requires observed health, administrative
policy, and route compatibility. A stale target gets one bounded on-demand
probe before refusal; no probe, route success, or registration clears an
administrative quarantine.

**Failure mode**: If the Switchboard is down, domain butlers cannot receive
routed messages. Their local deterministic schedules and direct MCP triggers
continue unless a separate administrative policy disables them. If the
observer stops, the fleet becomes observer-unknown and semantic readiness
fails; a failed observation cannot assert recovery.

### Candidate voice-egress dependency chain (not implemented)

```mermaid
graph LR
    SIGN["isolated voice signers"] -. "fixed-purpose non-inheritable handles" .-> SW
    SIGN -. "fixed-purpose non-inheritable handles" .-> MSG
    SIGN -. "fixed-purpose non-inheritable handles" .-> HOME
    Origin["origin butler"] -- "notify.v1" --> SW["Switchboard"]
    SW -- "signed voice_origin.v1" --> MSG["Messenger"]
    MSG -- "signed attestation request" --> SW
    SW -- "signed broker request" --> HOME["Home"]
    HOME -- "signed categorical result" --> SW
    SW -- "signed nested relay" --> MSG
    MSG -- "one admitted handoff" --> VP["local-first voice provider"]
    MSG -- "one text-only fallback intent" --> SW
```

Voice fails closed if a required signer/keyring or durable nonce store,
Switchboard, Messenger's replay store, Home attestation, or the exact provider
profile is unavailable. Current RFC 0028 does not admit Home/HA voice. A
failure does not redirect
Messenger to another room/provider, infer a device from identity data, or make
Live Listener an egress component. Telegram/email text fallback is separately keyed
and resolved once by Switchboard.

### Connector-to-Switchboard Dependency

```mermaid
graph LR
    TGBot["telegram-bot connector"] -- "MCP: ingest()" --> SW["switchboard :41100"]
    Gmail["gmail connector"] -- "MCP: ingest()" --> SW
    LiveL["live-listener connector"] -- "MCP: ingest()" --> SW

    TGBot -- "readiness probe" --> SW
    Gmail -- "readiness probe" --> SW
```

Connectors call `wait_for_switchboard_ready()` on startup, polling the
Switchboard health endpoint with exponential backoff (up to ~5 minutes).

**Failure mode**: If the Switchboard is unreachable, connectors block at startup
and cannot ingest events.

### Dashboard-to-Database Dependency

```mermaid
graph LR
    API["FastAPI :41200"] -- "asyncpg pools" --> PG["PostgreSQL"]
    API -- "reads all schemas" --> PG
```

The dashboard backend creates connection pools to each butler's schema on
startup. It reads directly from butler databases -- it does not go through
butler MCP servers.

The approved control-plane observer is a separate, supervised Dashboard
dependency on exact backend-network daemon identity/readiness endpoints. It
uses the database for durable observations and condition evidence, and an
independent patrol-age check so QA cannot be the only watcher of its own
scheduler. Only a completed successful patrol with all enabled discovery
sources completed renews that age. The canonical `/ready` requires this observer, expected fleet,
patrol freshness, supervised loops, and an effect-free route canary in addition
to PostgreSQL and roster discovery; `/health` remains process-only.

**Failure mode**: If PostgreSQL is down, the dashboard returns 500 errors.
Butler daemons also fail to start.

---

## Startup Order Constraints

The following must start before dependents can function:

```
1. PostgreSQL
   └── 2. MinIO (+ bucket setup)
       ├── 3. Switchboard butler
       │   ├── 4a. Domain butlers (general, relationship, health, ...)
       │   └── 4b. Connectors (telegram-bot, gmail, live-listener, ...)
       └── 3'. Dashboard API
```

PostgreSQL is the hard dependency for everything. MinIO / S3 is required for
blob storage, but butler daemons start without a usable blob store when the S3
configuration is absent or the configured endpoint/bucket fails validation
(blob operations fail at runtime). The Switchboard must be up before connectors
attempt ingestion, enforced by the readiness probe.

---

## External Dependencies

### Runtime Services (required)

| Dependency | Used By | Purpose | Failure Impact |
|---|---|---|---|
| **PostgreSQL** (pgvector/pg17) | All butlers, dashboard | Schema-isolated data storage, vector search | Total system failure -- nothing starts |
| **LLM API** (Anthropic/OpenAI/Google) | Spawner (all butlers) | LLM CLI session execution | Sessions cannot spawn; scheduled tasks queue indefinitely |

### Runtime Services (optional, degraded without)

| Dependency | Used By | Purpose | Failure Impact |
|---|---|---|---|
| **MinIO / S3** | Blob storage (attachments) | Attachment storage and retrieval | Attachment operations fail; non-blob daemon startup and core text messaging continue |
| **Grafana Alloy** | Telemetry | OTLP trace/metric collection | No observability; no-op tracer/meter used instead |
| **Tempo** | Trace queries | Trace storage backend | Cannot query traces; collection unaffected |
| **Prometheus** | Metric queries | Metric storage backend | Cannot query metrics; emission unaffected |

### External APIs (per-connector/module)

| Dependency | Used By | Purpose | Failure Impact |
|---|---|---|---|
| **Telegram Bot API** | telegram-bot connector, telegram module | Message polling/sending | Telegram ingestion and responses stop |
| **Telegram MTProto** | telegram-user-client connector | Userbot message access | Userbot ingestion stops |
| **Gmail API** | gmail connector | Watch/history delta email ingestion | Email ingestion stops |
| **Google Calendar API** | calendar module | Calendar CRUD | Calendar tools return errors |
| **Google Contacts API** | contacts module | Contact sync | Contact sync pauses |
| **Discord Gateway** | discord connector | WebSocket event stream | Discord ingestion stops (Draft status) |

### Build-Time Dependencies

| Dependency | Purpose |
|---|---|
| **Python 3.12+** | Runtime language |
| **uv** | Package management and virtual environments |
| **Node.js 22+** | Frontend build toolchain |
| **Docker** | Container runtime for infrastructure services |
| **Ruff** | Linting and formatting |
| **pytest** | Test execution with pytest-asyncio |
| **Alembic** | Database schema migrations |
| **Hatchling** | Python package build backend |

---

## Failure Blast Radius

### PostgreSQL down

Everything stops. No butler can start or continue operating. Dashboard returns
errors. Connectors cannot resolve credentials.

### Switchboard down

- Domain butlers continue running (local schedulers work, direct MCP calls work).
- No new external messages are routed to domain butlers.
- Connectors block or fail at ingestion.
- The observer records Switchboard unavailability; related target impacts
  correlate into one fleet condition rather than one QA investigation per
  butler.

### Single domain butler down

- Switchboard probes a stale target once; a proven pre-accept no-effect
  attempt remains durably retryable for ordinary ingestion-to-domain delivery.
  Ambiguous and policy-terminal outcomes remain visible for review.
- Other butlers are unaffected.
- Dashboard shows the observer-derived unavailable state, distinct from
  administrative quarantine or route incompatibility.

### QA patrol or control-plane observer down

- The independently supervised observer checks the age of completed QA
  patrols, so a missed patrol remains visible without QA executing.
- A stopped observer yields unknown fleet observation and failed semantic
  readiness. A process-health 200 cannot close the condition.

### Connector down

- The specific channel stops ingesting (e.g., no new Telegram messages).
- All other channels continue.
- Switchboard connector registry marks it stale after heartbeat TTL.

### LLM API down

- No new sessions can spawn across the fleet.
- Scheduled prompt-mode tasks queue behind the spawner semaphore.
- Job-mode scheduled tasks (Python functions) continue running.
- Existing data (state, memory, sessions) remains accessible via MCP tools.

### MinIO/S3 down

- Daemon startup continues with `daemon.blob_store = None` after the S3
  validation warning.
- Attachment upload/download fails at runtime.
- Core messaging continues for text-based ingestion and routing.
- Gmail connector attachment lazy-fetch fails.
