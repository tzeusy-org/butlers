# System Architecture

Status: Normative
Last updated: 2026-02-18

## 1. Overview

Butlers is a multi-agent system where each **butler** is a long-running MCP server daemon with isolated state, opt-in modules, and ephemeral LLM runtime sessions. Butlers communicate exclusively through MCP tool calls routed by a central **Switchboard**.

---

## 2. System Topology

```mermaid
graph TB
    subgraph External["External Sources"]
        TG_API["Telegram API"]
        GMAIL_API["Gmail API"]
        GCAL_API["Google Calendar API"]
        DASH["Dashboard<br/>(Vite + FastAPI)"]
    end

    subgraph Connectors["Connectors (Data Ingestion)"]
        TG_BOT["Telegram Bot<br/>Connector"]
        TG_USER["Telegram User<br/>Connector"]
        GMAIL_CONN["Gmail<br/>Connector"]
        HB_CONN["Heartbeat<br/>Connector"]
    end

    subgraph Switchboard["Switchboard Butler :40100"]
        direction TB
        SB_MCP["MCP Server"]
        SB_INGEST["ingest tool"]
        SB_PIPELINE["MessagePipeline<br/>(classify + route)"]
        SB_ROUTE["route.execute tool"]
        SB_SPAWNER["Spawner<br/>(serial lock)"]
        SB_DB[("butler_switchboard<br/>PostgreSQL")]

        SB_MCP --> SB_INGEST
        SB_INGEST --> SB_PIPELINE
        SB_PIPELINE --> SB_SPAWNER
        SB_SPAWNER --> SB_ROUTE
        SB_MCP -.-> SB_DB
    end

    subgraph Heartbeat["Heartbeat Butler :40199"]
        HB_MCP["MCP Server"]
        HB_TICK["tick() dispatcher"]
        HB_DB[("butler_heartbeat<br/>PostgreSQL")]
        HB_MCP --> HB_TICK
        HB_MCP -.-> HB_DB
    end

    subgraph Specialists["Domain Butlers"]
        subgraph General["General Butler :40101"]
            GEN_MCP["MCP Server"]
            GEN_CORE["Core:<br/>State · Scheduler · Sessions"]
            GEN_MOD["Modules:<br/>Calendar · Memory"]
            GEN_DB[("butler_general<br/>PostgreSQL")]
            GEN_MCP --> GEN_CORE
            GEN_MCP --> GEN_MOD
            GEN_MCP -.-> GEN_DB
        end

        subgraph Relationship["Relationship Butler :40102"]
            REL_MCP["MCP Server"]
            REL_CORE["Core:<br/>State · Scheduler · Sessions"]
            REL_MOD["Modules:<br/>Memory"]
            REL_DB[("butler_relationship<br/>PostgreSQL")]
            REL_MCP --> REL_CORE
            REL_MCP --> REL_MOD
            REL_MCP -.-> REL_DB
        end

        subgraph Health["Health Butler :40103"]
            HEALTH_MCP["MCP Server"]
            HEALTH_CORE["Core:<br/>State · Scheduler · Sessions"]
            HEALTH_MOD["Modules:<br/>Memory"]
            HEALTH_DB[("butler_health<br/>PostgreSQL")]
            HEALTH_MCP --> HEALTH_CORE
            HEALTH_MCP --> HEALTH_MOD
            HEALTH_MCP -.-> HEALTH_DB
        end
    end

    subgraph Messenger["Messenger Butler :40104"]
        MSG_MCP["MCP Server"]
        MSG_DELIVERY["Delivery Engine"]
        MSG_TG["bot_telegram_send_message<br/>bot_telegram_reply_to_message"]
        MSG_EMAIL["bot_email_send_message<br/>bot_email_reply_to_thread"]
        MSG_DB[("butler_messenger<br/>PostgreSQL")]
        MSG_MCP --> MSG_DELIVERY
        MSG_DELIVERY --> MSG_TG
        MSG_DELIVERY --> MSG_EMAIL
        MSG_MCP -.-> MSG_DB
    end

    %% Ingress: External → Connectors
    TG_API --> TG_BOT
    TG_API --> TG_USER
    GMAIL_API --> GMAIL_CONN

    %% Connectors → Switchboard
    TG_BOT -- "ingest.v1" --> SB_MCP
    TG_USER -- "ingest.v1" --> SB_MCP
    GMAIL_CONN -- "ingest.v1" --> SB_MCP
    HB_CONN -- "tick()" --> HB_MCP

    %% Dashboard → API → Butlers
    DASH -- "REST API" --> SB_MCP

    %% Switchboard routes to specialists
    SB_ROUTE -- "route.execute" --> GEN_MCP
    SB_ROUTE -- "route.execute" --> REL_MCP
    SB_ROUTE -- "route.execute" --> HEALTH_MCP

    %% Heartbeat ticks all butlers
    HB_TICK -. "tick()" .-> SB_MCP
    HB_TICK -. "tick()" .-> GEN_MCP
    HB_TICK -. "tick()" .-> REL_MCP
    HB_TICK -. "tick()" .-> HEALTH_MCP
    HB_TICK -. "tick()" .-> MSG_MCP

    %% Specialist butlers → Messenger (egress)
    GEN_MCP -- "notify.v1" --> SB_ROUTE
    REL_MCP -- "notify.v1" --> SB_ROUTE
    HEALTH_MCP -- "notify.v1" --> SB_ROUTE
    SB_ROUTE -- "notify.v1" --> MSG_MCP

    %% Messenger → External (egress)
    MSG_TG -- "Bot API" --> TG_API
    MSG_EMAIL -- "SMTP" --> GMAIL_API

    %% Calendar module → external
    GEN_MOD -- "Calendar API" --> GCAL_API

    classDef external fill:#f9f,stroke:#333,stroke-width:1px
    classDef connector fill:#bbf,stroke:#333,stroke-width:1px
    classDef switchboard fill:#ffd,stroke:#333,stroke-width:2px
    classDef specialist fill:#dfd,stroke:#333,stroke-width:1px
    classDef messenger fill:#fdb,stroke:#333,stroke-width:2px
    classDef heartbeat fill:#ddd,stroke:#333,stroke-width:1px
    classDef database fill:#eee,stroke:#666,stroke-width:1px

    class TG_API,GMAIL_API,GCAL_API,DASH external
    class TG_BOT,TG_USER,GMAIL_CONN,HB_CONN connector
    class SB_MCP,SB_INGEST,SB_PIPELINE,SB_ROUTE,SB_SPAWNER switchboard
    class HB_MCP,HB_TICK heartbeat
    class GEN_MCP,GEN_CORE,GEN_MOD,REL_MCP,REL_CORE,REL_MOD,HEALTH_MCP,HEALTH_CORE,HEALTH_MOD specialist
    class MSG_MCP,MSG_DELIVERY,MSG_TG,MSG_EMAIL messenger
    class SB_DB,HB_DB,GEN_DB,REL_DB,HEALTH_DB,MSG_DB database
```

### Key constraints

- **Database isolation**: Each butler owns a dedicated PostgreSQL database. No cross-butler DB access.
- **MCP-only communication**: Butlers interact exclusively through MCP tool calls routed via the Switchboard.
- **Serial dispatch**: Each butler processes one LLM session at a time (configurable concurrency planned).
- **Channel egress ownership**: Only the Messenger butler holds bot-scoped send/reply tools.

---

## 3. Butler Internal Architecture

Every butler shares the same two-layer design: **core** (always present) and **modules** (opt-in).

```mermaid
graph TB
    subgraph Butler["Butler Daemon"]
        direction TB

        subgraph Core["Core (always present)"]
            MCP["FastMCP Server"]
            SPAWNER["LLM CLI Spawner<br/>(asyncio.Lock)"]
            SCHEDULER["Task Scheduler<br/>(croniter)"]
            STATE["State Store<br/>(KV JSONB)"]
            SESSIONS["Session Log<br/>(append-only)"]
            TICK["Tick Handler"]
            STATUS["Status Tool"]
        end

        subgraph Modules["Modules (opt-in via butler.toml)"]
            MEMORY["Memory Module"]
            EMAIL["Email Module"]
            TELEGRAM["Telegram Module"]
            CALENDAR["Calendar Module"]
            APPROVALS["Approvals Module"]
            PIPELINE["Pipeline Module"]
            MAILBOX["Mailbox Module"]
        end

        DB[("butler_<name><br/>PostgreSQL")]
        CONFIG["roster/<name>/butler.toml"]
        PERSONALITY["roster/<name>/CLAUDE.md"]
        SKILLS["roster/<name>/skills/"]

        %% Core wiring
        MCP --> SPAWNER
        MCP --> SCHEDULER
        MCP --> STATE
        MCP --> SESSIONS
        MCP --> TICK
        MCP --> STATUS
        TICK --> SCHEDULER
        SCHEDULER --> SPAWNER

        %% Module registration
        Modules -. "register_tools()" .-> MCP
        Modules -. "migrations()" .-> DB

        %% Spawner creates sessions
        SPAWNER -- "spawn ephemeral<br/>LLM CLI" --> RUNTIME["Claude Code<br/>Runtime Instance"]
        RUNTIME -- "calls tools via" --> MCP

        %% Spawner reads config
        SPAWNER -. "reads" .-> CONFIG
        SPAWNER -. "reads" .-> PERSONALITY
        SPAWNER -. "reads" .-> SKILLS

        %% Data layer
        Core -.-> DB
        Modules -.-> DB
    end

    classDef core fill:#e8f4fd,stroke:#2196F3,stroke-width:1px
    classDef module fill:#e8f5e9,stroke:#4CAF50,stroke-width:1px
    classDef runtime fill:#fff3e0,stroke:#FF9800,stroke-width:2px
    classDef db fill:#f3e5f5,stroke:#9C27B0,stroke-width:1px
    classDef config fill:#fafafa,stroke:#999,stroke-width:1px,stroke-dasharray: 5 5

    class MCP,SPAWNER,SCHEDULER,STATE,SESSIONS,TICK,STATUS core
    class MEMORY,EMAIL,TELEGRAM,CALENDAR,APPROVALS,PIPELINE,MAILBOX module
    class RUNTIME runtime
    class DB db
    class CONFIG,PERSONALITY,SKILLS config
```

### Trigger flow

```
1. Trigger arrives (MCP call, cron task, or heartbeat tick)
2. Spawner acquires lock (serial dispatch)
3. Creates session record
4. Loads config + personality (CLAUDE.md) + skills
5. Fetches memory context (if memory module enabled)
6. Generates locked-down MCP config (this butler only)
7. Spawns ephemeral Claude Code instance via SDK
8. Runtime instance calls butler's MCP tools
9. Session completes → stores episode (if memory enabled)
10. Logs session, releases lock
```

---

## 4. Ingress and Egress

### Ingress paths (data enters the system)

| Source | Connector | Entry Point | Protocol |
|--------|-----------|-------------|----------|
| Telegram messages | `telegram_bot.py` | Switchboard `ingest` | `ingest.v1` via MCP |
| Telegram (user account) | `telegram_user_client.py` | Switchboard `ingest` | `ingest.v1` via MCP |
| Gmail | `gmail.py` | Switchboard `ingest` | `ingest.v1` via MCP |
| Heartbeat | `heartbeat.py` | Each butler `tick()` | MCP tool call |
| Dashboard | Vite frontend | FastAPI REST API | HTTP |
| External MCP clients | Direct | Butler `trigger()` tool | MCP |

Connectors are semaphore-bounded (default 8 concurrent fetches). The Switchboard accepts and persists messages in <10ms, then classifies asynchronously.

### Egress paths (data leaves the system)

| Destination | Owner | Tools | Protocol |
|-------------|-------|-------|----------|
| Telegram (bot sends) | Messenger | `bot_telegram_send_message`, `bot_telegram_reply_to_message` | Bot API |
| Email (bot sends) | Messenger | `bot_email_send_message`, `bot_email_reply_to_thread` | SMTP |
| Google Calendar | General | `calendar_create_event`, `calendar_update_event` | Calendar API |

All user-facing egress flows through the Messenger butler via the `notify.v1` contract. Domain butlers call `notify()` which routes through the Switchboard to the Messenger for delivery. This ensures a single egress control plane with audit, approval gates, and identity-scoped tooling.

### Module internet access

Modules can access external services directly using their configured credentials:

- **Calendar module**: Google Calendar API (list, create, update events)
- **Email module**: IMAP/SMTP for inbox reads (ingress is via connector, but runtime search tools also access IMAP)
- **Telegram module**: Telegram API for get_updates calls during runtime sessions

These are read-oriented or bidirectional operations. Outbound **sending** of messages is exclusively owned by the Messenger butler.

---

## 5. Inter-Butler Communication

```mermaid
sequenceDiagram
    participant C as Connector
    participant SW as Switchboard
    participant DB as Domain Butler
    participant MSG as Messenger
    participant EXT as External Channel

    C->>SW: ingest.v1 (message)
    SW->>SW: Persist to message_inbox
    SW->>SW: Classify via pipeline
    SW->>DB: route.execute (routed prompt)
    DB->>DB: Spawner → Runtime session
    DB->>SW: notify.v1 (response)
    SW->>MSG: route.execute (delivery intent)
    MSG->>EXT: bot_send (Telegram/Email)
```

### Routing rules

The Switchboard's `MessagePipeline` classifies messages by domain:

| Domain | Keywords / Signals | Target |
|--------|-------------------|--------|
| Relationship | person, contact, gift, social | `relationship` butler |
| Health | medication, symptoms, exercise, diet | `health` butler |
| General | ambiguous or unclassifiable | `general` butler |

Multi-domain messages are decomposed into focused sub-prompts, each routed to the appropriate specialist.

### `route.execute` contract

```
Request:  schema_version, request_context, input, target, source, trace
Response: status (success|error), output, tool_calls, delivery_ids
Errors:   validation_error, target_unavailable, timeout, overload_rejected, internal_error
Retry:    Only target_unavailable, timeout, overload_rejected are retryable
```

---

## 6. Memory System

The memory module is a reusable module loaded by each butler that enables it. Memory data lives in the butler's own database — there is no shared memory service.

```mermaid
graph TB
    subgraph Runtime["LLM Runtime Session"]
        LLM["Claude Code Instance"]
    end

    subgraph MemoryModule["Memory Module"]
        direction TB

        subgraph Tools["MCP Tools (12 total)"]
            WRITE_TOOLS["Writing:<br/>store_episode<br/>store_fact<br/>store_rule"]
            READ_TOOLS["Reading:<br/>search · recall · get"]
            FEEDBACK["Feedback:<br/>confirm<br/>mark_helpful<br/>mark_harmful"]
            MGMT["Management:<br/>forget · stats"]
            CTX["Context:<br/>memory_context"]
        end

        subgraph Engine["Processing Engine"]
            EMBED["Embedding Engine<br/>(MiniLM-L6, local)"]
            SEARCH["Search Engine<br/>(vector + keyword + hybrid)"]
            SCORING["Composite Scoring<br/>relevance·importance·recency·confidence"]
            CONSOLIDATION["Consolidation Engine<br/>(episodes → facts/rules)"]
            DECAY["Confidence Decay<br/>(exponential, per permanence class)"]
        end
    end

    subgraph DB["Butler Database (PostgreSQL + pgvector)"]
        direction TB
        EPISODES[("episodes<br/>(what happened)")]
        FACTS[("facts<br/>(what is true)")]
        RULES[("rules<br/>(how to behave)")]
        LINKS[("memory_links<br/>(provenance)")]
        EVENTS[("memory_events<br/>(audit trail)")]
        RULE_APP[("rule_applications<br/>(outcome tracking)")]
    end

    subgraph Spawner["Butler Spawner (pre-session)"]
        CONTEXT_INJECT["Inject memory context<br/>into system prompt"]
    end

    subgraph Scheduler["Scheduled Jobs"]
        CONSOLIDATE_JOB["Consolidation<br/>(every 6h)"]
        CLEANUP_JOB["Episode Cleanup<br/>(daily 4am)"]
        DECAY_JOB["Decay Sweep<br/>(daily 3am)"]
    end

    %% Runtime ↔ Tools
    LLM -- "calls during session" --> Tools

    %% Tool → Engine → DB
    WRITE_TOOLS --> EMBED
    EMBED --> EPISODES
    EMBED --> FACTS
    EMBED --> RULES
    READ_TOOLS --> SEARCH
    SEARCH --> SCORING
    SCORING --> EPISODES
    SCORING --> FACTS
    SCORING --> RULES
    FEEDBACK --> RULE_APP
    FEEDBACK --> FACTS
    CTX --> SEARCH

    %% Provenance
    WRITE_TOOLS -.-> LINKS
    WRITE_TOOLS -.-> EVENTS

    %% Pre-session context injection
    Spawner -- "memory_context()" --> CTX
    CTX -- "scored memories" --> CONTEXT_INJECT
    CONTEXT_INJECT -- "system prompt suffix" --> LLM

    %% Session → Episode pipeline
    LLM -. "session completes" .-> WRITE_TOOLS

    %% Scheduled jobs
    CONSOLIDATE_JOB --> CONSOLIDATION
    CONSOLIDATION -- "extract facts/rules" --> EPISODES
    CONSOLIDATION -- "create" --> FACTS
    CONSOLIDATION -- "create" --> RULES
    CONSOLIDATION -.-> LINKS
    CLEANUP_JOB -- "TTL enforcement" --> EPISODES
    DECAY_JOB -- "confidence decay" --> FACTS
    DECAY_JOB -- "confidence decay" --> RULES

    classDef tool fill:#e3f2fd,stroke:#1565C0,stroke-width:1px
    classDef engine fill:#fff8e1,stroke:#F9A825,stroke-width:1px
    classDef db fill:#f3e5f5,stroke:#7B1FA2,stroke-width:1px
    classDef job fill:#e8f5e9,stroke:#2E7D32,stroke-width:1px
    classDef runtime fill:#fff3e0,stroke:#E65100,stroke-width:2px
    classDef spawner fill:#fce4ec,stroke:#C62828,stroke-width:1px

    class WRITE_TOOLS,READ_TOOLS,FEEDBACK,MGMT,CTX tool
    class EMBED,SEARCH,SCORING,CONSOLIDATION,DECAY engine
    class EPISODES,FACTS,RULES,LINKS,EVENTS,RULE_APP db
    class CONSOLIDATE_JOB,CLEANUP_JOB,DECAY_JOB job
    class LLM runtime
    class CONTEXT_INJECT spawner
```

### Three memory types

| Type | Purpose | Lifecycle | Retention |
|------|---------|-----------|-----------|
| **Episodes** | Raw session observations ("what happened") | Created → TTL expiry | Default 7 days, max 10K entries |
| **Facts** | Distilled knowledge ("what is true") | `active → fading → expired` or `superseded`/`retracted` | Confidence-decay based |
| **Rules** | Procedural guidance ("how to behave") | `candidate → established → proven` or `anti_pattern` | Effectiveness-based maturity |

### Confidence decay

```
effective_confidence = confidence × exp(−decay_rate × days_since_last_confirmed)
```

| Permanence Class | Half-life | Use Case |
|------------------|-----------|----------|
| `permanent` | Never decays | Identity, biographical constants |
| `stable` | ~1 year | Long-term preferences |
| `standard` | ~3 months | Ongoing interests, projects |
| `volatile` | ~weeks | Temporary states, plans |
| `ephemeral` | ~days | Transient observations |

### Context assembly (pre-session)

Before each LLM session, the spawner calls `memory_context()` which:

1. Runs hybrid search (vector + keyword) against the trigger prompt
2. Scores results: `0.4×relevance + 0.3×importance + 0.2×recency + 0.1×confidence`
3. Applies section quotas (facts, rules, episodes)
4. Token-budgets the output (default 3000 tokens)
5. Injects the result as a system prompt suffix after `CLAUDE.md`

### Consolidation pipeline

Every 6 hours, the consolidation engine processes unconsolidated episodes:

```
episodes (batch) → LLM extraction → candidate facts/rules
  → dedup against existing facts (supersession if conflict)
  → persist with provenance links back to source episodes
  → mark episodes as consolidated
```

---

## 7. Database Architecture

Each butler has a dedicated PostgreSQL database. There is no shared database or cross-butler queries.

```mermaid
graph LR
    subgraph PG["PostgreSQL Server"]
        DB_SW[("butler_switchboard")]
        DB_GEN[("butler_general")]
        DB_REL[("butler_relationship")]
        DB_HEALTH[("butler_health")]
        DB_HB[("butler_heartbeat")]
        DB_MSG[("butler_messenger")]
    end

    SW["Switchboard :40100"] --> DB_SW
    GEN["General :40101"] --> DB_GEN
    REL["Relationship :40102"] --> DB_REL
    HEALTH["Health :40103"] --> DB_HEALTH
    HB["Heartbeat :40199"] --> DB_HB
    MSG["Messenger :40104"] --> DB_MSG
```

### Shared schema (core tables, all butlers)

| Table | Purpose |
|-------|---------|
| `state` | Key-value JSONB store |
| `scheduled_tasks` | Cron-driven task definitions |
| `sessions` | Append-only session audit log |

### Module-specific tables (present when module enabled)

| Module | Tables |
|--------|--------|
| Memory | `episodes`, `facts`, `rules`, `memory_links`, `memory_events`, `rule_applications`, `embedding_versions` |
| Approvals | `approval_rules`, `approval_events`, `approval_constraints` |
| Mailbox | `message_inbox` (Switchboard only) |
| Pipeline | Uses `message_inbox` + routing metadata |

### Connection management

- **Pool**: asyncpg, min 2 / max 10 connections per butler
- **Provisioning**: `Database.provision()` creates the DB if absent
- **Migrations**: Alembic with multi-chain architecture (core + module chains)
- **SSL**: Configurable (`disable`, `prefer`, `require`, `verify-ca`, `verify-full`)

---

## 8. Module System

Modules are pluggable units that add MCP tools to a butler. They implement the `Module` abstract base class and are registered via topological sort based on declared dependencies.

### Module lifecycle

```
1. Load butler.toml → identify enabled modules
2. Resolve dependencies (topological sort)
3. Run module migrations (per-module Alembic chains)
4. Call on_startup() for each module
5. Call register_tools() → adds tools to FastMCP server
6. Apply approval gates (if approvals module enabled)
7. Butler serves requests...
8. Call on_shutdown() on teardown
```

### Identity-scoped I/O

Channel modules (Telegram, Email) expose tools with explicit identity prefixes:

- **`user_*` tools**: Act through the user's personal account (e.g., user's Gmail). Send/reply tools are approval-gated by default.
- **`bot_*` tools**: Act through butler-owned service accounts. Approval is policy-configurable.

This ensures clear identity accountability, audit clarity, and safety-by-default for user impersonation.

---

## 9. Related Documents

- [Concurrency Architecture](concurrency.md) — Spawner serialization, backpressure, planned concurrency pool
- [Memory Project Plan](../MEMORY_PROJECT_PLAN.md) — Full memory module design, schemas, milestones
- [Module I/O Model](../modules/io_model.md) — Identity-scoped tooling contract
- [Connector Interface](../connectors/interface.md) — `ingest.v1` contract
- [Switchboard Operator Runbook](../operations/switchboard_operator_runbook.md)
- [Messenger Butler Role](../roles/messenger_butler.md) — `notify.v1` delivery contract
