# Butlers — Project Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** An AI agent framework where each "butler" is a long-running MCP server daemon with core infrastructure (state, scheduler, LLM CLI spawner, session log) and opt-in modules (email, telegram, calendar, etc.). When triggered, a butler spawns an ephemeral LLM CLI instance wired exclusively to itself. Claude Code is the universal executor — it reasons about what to do and uses whatever tools (MCP tools, bash, scripts) it needs.

**Architecture:** Each butler is a persistent MCP server daemon with two layers of functionality: **core components** (state store, task scheduler, LLM CLI spawner, session log) that every butler gets automatically, and **modules** (email, telegram, calendar, etc.) that are opt-in per butler. When triggered — by the scheduler, heartbeat, or an external MCP call — the butler spawns an ephemeral LLM CLI instance via the Claude Code SDK. That instance receives a locked-down MCP config pointing exclusively to the butler's own MCP server, plus the butler's CLAUDE.md and skills. Claude Code runs, calls tools as needed, and exits. A **Switchboard Butler** routes external MCP requests to the correct butler. A **Heartbeat Butler** periodically calls each butler's `tick` tool, triggering the scheduler. Each butler owns a dedicated PostgreSQL database (strict isolation). Butler definitions are git-based directories.

**Tech Stack:** Python 3.12+, FastMCP (MCP server), Claude Code SDK (ephemeral runtimes), PostgreSQL (JSONB-heavy, one DB per butler), Docker, asyncio

---

## System Architecture

```
                    ┌─────────────────────┐
                    │   External Clients   │
                    │  (MCP-compatible)    │
                    └─────────┬───────────┘
                              │ MCP
                    ┌─────────▼───────────┐
                    │  Switchboard Butler  │
                    │  (ingress + routing) │
                    │  [MCP server daemon] │
                    └──┬──────┬────────┬──┘
                 MCP   │      │        │   MCP
            ┌──────────▼┐  ┌──▼─────┐ ┌▼──────────┐
            │  Butler A  │  │Butler B│ │  Butler N  │
            │  [daemon]  │  │[daemon]│ │  [daemon]  │
            │            │  │        │ │            │
            │ core:      │  │core:   │ │ core:      │
            │ - state    │  │- state │ │ - state    │
            │ - scheduler│  │- sched │ │ - scheduler│
            │ - spawner  │  │- spawn │ │ - spawner  │
            │ - sessions │  │- sess  │ │ - sessions │
            │            │  │        │ │            │
            │ modules:   │  │modules:│ │ modules:   │
            │ - email    │  │  (none)│ │ - telegram │
            │ - calendar │  │        │ │ - email    │
            └──────┬─────┘  └───┬────┘ └─────┬─────┘
                   │            │             │
              on trigger:  on trigger:   on trigger:
            ┌──────▼─────┐┌────▼───┐ ┌───────▼────┐
            │Claude Code ││runtime ││ │ runtime    │
            │(ephemeral) ││        ││ │            │
            │locked-down ││        ││ │            │
            │MCP config  ││        ││ │            │
            └──────┬─────┘└────┬───┘ └───────┬────┘
                   │MCP        │MCP          │MCP
                   ▼           ▼             ▼
              back to      back to       back to
              Butler A     Butler B      Butler N

            ┌───────────────────────────────────┐
            │        Heartbeat Butler           │
            │  [calls tick on each butler       │
            │   every 10 minutes]               │
            └───────────────────────────────────┘
```

### Trigger Flow (the core loop)

```
1. Trigger arrives (external MCP call, scheduler due task, or heartbeat tick)
2. Butler's LLM CLI Spawner:
   a. Generates ephemeral MCP config → /tmp/butler_<name>_<uuid>/mcp.json
      { "mcpServers": { "<butler>": { "url": "http://localhost:<port>/sse" } } }
   b. Spawns Claude Code via SDK with:
      - The prompt
      - --mcp-config pointing to the generated config
      - System prompt from CLAUDE.md
      - Working directory = butler's config dir (skills, scripts accessible)
3. Claude Code runs:
   - Calls butler's MCP tools (core + module tools) as needed
   - Can use built-in bash to run skill scripts from skills/
   - Reasons about what to do, executes, returns
4. Claude Code exits
5. Butler logs the session (prompt, tool calls, outcome) and returns result
```

### Scheduler Flow

```
1. Heartbeat Butler calls tick() on Butler A every 10 min
2. Butler A's scheduler checks DB for due tasks (next_run_at <= now())
3. For each due task:
   a. Calls LLM CLI Spawner with the task's prompt
   b. runtime instance has access to all butler tools + bash + skills
   c. Runtime instance decides what to do, executes, returns
   d. Scheduler updates task (last_run_at, next_run_at, status)
4. Returns summary of executed tasks
```

---

## Core Components vs Modules

Every butler has two layers:

### Core Components (always present, not opt-in)

These are the shared infrastructure every butler needs. They are built into the daemon directly and provide the foundation that modules build on.

| Component          | What it does                                                                                                   | MCP Tools it provides                                                    |
| ------------------ | -------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| **State Store**    | Key-value JSONB persistence in butler's DB                                                                     | `state_get`, `state_set`, `state_delete`, `state_list`                   |
| **Task Scheduler** | Cron-driven task dispatch. TOML for bootstrap, DB for runtime. Always dispatches prompts to LLM CLI.                | `schedule_list`, `schedule_create`, `schedule_update`, `schedule_delete` |
| **LLM CLI Spawner**     | Spawns locked-down LLM CLI instances via SDK. Generates ephemeral MCP config pointing only to this butler. | `trigger` (spawn a runtime instance with a prompt)                                       |
| **Session Log**    | Logs every LLM CLI invocation — prompt, tool calls, outcome, duration.                                              | `sessions_list`, `sessions_get`                                          |
| **Tick Handler**   | Entry point for heartbeat. Calls the scheduler, returns summary.                                               | `tick`                                                                   |
| **Status**         | Butler identity, loaded modules, health, uptime.                                                               | `status`                                                                 |

**Core MCP tools (every butler exposes these):**

```
Core Tools
├── status()                                → butler identity, modules, health
├── tick()                                  → trigger scheduler, return summary
├── trigger(prompt, context?)               → spawn runtime instance, return result
├── state_get(key)                          → JSONB value
├── state_set(key, value)                   → void
├── state_delete(key)                       → void
├── state_list(prefix?)                     → list of keys
├── schedule_list()                         → all scheduled tasks
├── schedule_create(name, cron, prompt)     → task id
├── schedule_update(id, ...)               → void
├── schedule_delete(id)                     → void
├── sessions_list(limit?, offset?)          → recent runtime sessions
└── sessions_get(id)                        → full session detail (tools called, outcome)
```

### Modules (opt-in capabilities)

Modules are pluggable units that add domain-specific MCP tools on top of the core. Each module contributes tools, config, and optionally DB tables.

```
Module Tools (examples)
├── email:    bot_email_send_message, bot_email_search_inbox, bot_email_read_message
├── telegram: bot_telegram_send_message, bot_telegram_get_updates, user_telegram_send_message
├── calendar: calendar_list_events, calendar_create_event, calendar_update_event
├── slack:    bot_slack_send_message, bot_slack_list_channels, bot_slack_react
└── github:   create_issue, list_prs, review_pr
```

The Module interface remains the same:

```
Module Interface
┌─────────────────────────────────────────────────┐
│  name: str                                      │
│  config_schema: type[BaseModel]                 │
│  dependencies: list[str]                        │
│                                                 │
│  register_tools(mcp, config, db) → void         │
│  migrations() → list[str]                       │
│  on_startup(config, db) → void                  │
│  on_shutdown() → void                           │
└─────────────────────────────────────────────────┘
```

Modules only add tools. They never touch core infrastructure (scheduler, spawner, state store). They can *read* state via the state store if needed.

---

### Butler Anatomy (git-based config directory)

```
butler-name/
├── CLAUDE.md            # Butler personality, instructions, constraints
├── AGENTS.md            # Agent-specific notes (populated at runtime)
├── skills/              # Skills available to runtime instances
│   ├── morning-briefing/
│   │   ├── SKILL.md     # Prompt template / instructions
│   │   └── run.py       # Script Runtime instance can invoke via bash
│   └── inbox-triage/
│       └── SKILL.md     # Prompt-only skill
└── butler.toml          # Butler config: identity, schedule, modules
```

### Example `butler.toml`

```toml
[butler]
name = "assistant"
description = "Personal assistant with email and calendar access"
port = 40101

[butler.db]
name = "butler_assistant"

# --- Static Scheduled Tasks ---
# Synced to DB on startup. Runtime-created tasks live only in DB.
# The scheduler always dispatches prompts to Claude Code.

[[butler.schedule]]
name = "morning-briefing"
cron = "0 8 * * *"
prompt = """
Run the morning-briefing skill. Check my calendar for today,
summarize upcoming meetings, and send me a briefing via email.
"""

[[butler.schedule]]
name = "inbox-triage"
cron = "*/30 * * * *"
prompt = "Check for new emails. Flag anything urgent and draft replies for routine items."

# --- Modules ---
# Each [modules.<name>] section enables that module.
# The table contents are module-specific configuration.

[modules.email]

[modules.email.user]
enabled = false

[modules.email.bot]
address_env = "BUTLER_EMAIL_ADDRESS"
password_env = "BUTLER_EMAIL_PASSWORD"

[modules.calendar]
provider = "google"
calendar_id = "primary"
credentials_env = "GCAL_ASSISTANT_CREDS"
```

**Minimal butler (core only, no modules, no schedule):**

```toml
[butler]
name = "minimal"
description = "A butler with only core tools"
port = 40100
```

### Core Concepts

| Concept              | Description                                                                                                                         |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| **Butler**           | A long-running MCP server daemon. Has core components (state, scheduler, spawner, sessions) + opt-in modules. Owns a dedicated DB.  |
| **Core Component**   | Shared infrastructure built into every butler: state store, task scheduler, LLM CLI spawner, session log. Not opt-in.                    |
| **Module**           | A pluggable unit that adds domain-specific MCP tools to a butler. Many-to-many with butlers. Opt-in via butler.toml.                |
| **Trigger**          | Spawning a runtime instance with a prompt. Can be initiated by: scheduler, heartbeat tick, external MCP call.                            |
| **LLM CLI Spawner**       | Generates a locked-down MCP config and spawns Claude Code via SDK. Runtime instance only sees this butler's tools.                                |
| **Scheduler**        | Cron-driven. Checks for due tasks on `tick()`. Always dispatches prompts to LLM CLI Spawner. Tasks from TOML (bootstrap) + DB (runtime). |
| **Skill**            | A directory in `skills/` with SKILL.md (prompt template) and optionally scripts. Runtime instance reads the skill and decides what to do.         |
| **Switchboard**      | The hub butler that routes external MCP requests to the correct butler.                                                             |
| **Heartbeat Butler** | Calls `tick()` on every registered butler every N minutes (default 10).                                                             |
| **Butler DB**        | Each butler's dedicated PostgreSQL database — strict isolation, MCP-only access between butlers.                                    |

### Database Isolation

Each butler gets its own PostgreSQL database. Butlers **cannot** access each other's databases. All inter-butler data exchange happens via MCP tool calls through the Switchboard. This is a hard architectural constraint, not a guideline.

Core components create their tables automatically on startup. Modules declare additional migrations.

```
PostgreSQL instance
├── db: butler_switchboard    ← Switchboard's DB (registry, routing)
├── db: butler_heartbeat      ← Heartbeat Butler's DB (run log)
├── db: butler_assistant      ← core tables + email module tables + calendar module tables
└── db: butler_researcher     ← core tables only (no modules)
```

### Core Database Schema (applied to every butler's DB)

```sql
-- Key-value state store
CREATE TABLE state (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Scheduled tasks (cron-driven prompt dispatch)
CREATE TABLE scheduled_tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT UNIQUE NOT NULL,
    cron TEXT NOT NULL,
    prompt TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'db',   -- 'toml' or 'db'
    enabled BOOLEAN NOT NULL DEFAULT true,
    last_run_at TIMESTAMPTZ,
    next_run_at TIMESTAMPTZ,
    last_result JSONB,                   -- summary of last runtime session
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- runtime session log
CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trigger_source TEXT NOT NULL,         -- 'schedule:<task-name>', 'tick', 'external', 'trigger'
    prompt TEXT NOT NULL,
    result TEXT,
    tool_calls JSONB NOT NULL DEFAULT '[]',
    success BOOLEAN,
    error TEXT,
    duration_ms INT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);
```

---

## LLM CLI Spawner: Locked-Down Claude Code Instances

When the LLM CLI Spawner is invoked (via `trigger`, scheduler, or `tick`), it:

1. **Generates an ephemeral MCP config:**

```json
{
  "mcpServers": {
    "assistant": {
      "url": "http://localhost:40101/sse"
    }
  }
}
```

Written to a temp directory: `/tmp/butler_assistant_<uuid>/mcp.json`

2. **Spawns Claude Code via SDK:**

```python
from claude_code_sdk import query

result = await query(
    prompt=task_prompt,
    options={
        "system_prompt": butler_claude_md,
        "mcp_config": "/tmp/butler_assistant_<uuid>/mcp.json",
        "cwd": str(butler_config_dir),  # skills/ accessible
        "max_turns": 20,
    }
)
```

3. **runtime instance capabilities:**
   - Can call any MCP tool on the butler (core + modules)
   - Has built-in bash → can run skill scripts from `skills/`
   - Has file reading/writing within the butler's config dir
   - **Cannot** access other butlers' MCP servers (config is locked down)
   - **Cannot** access the host network beyond the butler's port

4. **Logs the session** to the `sessions` table when complete.

5. **Cleans up** the temp directory.

---

## MVP Butlers (v1)

The MVP ships 4 butlers + the Heartbeat infrastructure butler:

```
                     Telegram Bot / Email
                            │
                    ┌───────▼────────┐
                    │  Switchboard   │ ← public ingress + routing
                    │  port 40100     │
                    └──┬──┬──┬──────┘
                 MCP   │  │  │  MCP
          ┌────────────▼┐ │ ┌▼───────────┐
          │ Relationship│ │ │   Health    │
          │ (CRM)       │ │ │ (tracking) │
          │ port 40102   │ │ │ port 40103  │
          └─────────────┘ │ └────────────┘
                    ┌─────▼──────┐
                    │  General   │ ← catch-all, freeform JSON
                    │  port 40101 │
                    └────────────┘

          ┌──────────────────────────┐
          │  Heartbeat (port 40199)   │ ← ticks all butlers every 10m
          └──────────────────────────┘
```

### 1. Switchboard Butler

The **public-facing ingress**. Listens on Telegram (bot) and Email (IMAP/webhook), determines which butler should handle each message, and routes via MCP.

**Role:** Not just an MCP router — it's the front door. When a Telegram message or email arrives, the Switchboard spawns a runtime instance to classify the request and route it.

**Modules:** `telegram`, `email`

**Special MCP tools (beyond core):**
- `route(butler_name, tool_name, args)` → forward a tool call to a butler via MCP client
- `list_butlers()` → registry of all butlers, their modules, and endpoints
- `discover()` → re-scan config directories, update registry

**Routing flow:**
```
1. Telegram message arrives → telegram module receives it
2. Switchboard triggers runtime instance with: "Classify this message and route it:
   Available butlers: [relationship, health, general]
   Message: {text}"
3. Runtime instance decides: this is a health question → calls route("health", "trigger", {prompt: ...})
4. Health butler handles it, returns result
5. Switchboard sends response back via telegram module
```

**Config: `butlers/switchboard/butler.toml`**
```toml
[butler]
name = "switchboard"
description = "Public ingress. Listens on Telegram and Email, routes to specialist butlers."
port = 40100

[butler.db]
name = "butler_switchboard"

[modules.telegram]
mode = "polling"

[modules.telegram.user]
enabled = false

[modules.telegram.bot]
token_env = "BUTLER_TELEGRAM_TOKEN"

[modules.email]

[modules.email.user]
enabled = false

[modules.email.bot]
address_env = "BUTLER_EMAIL_ADDRESS"
password_env = "BUTLER_EMAIL_PASSWORD"
```

**Switchboard DB schema (in addition to core tables):**
```sql
CREATE TABLE butler_registry (
    name TEXT PRIMARY KEY,
    endpoint_url TEXT NOT NULL,
    description TEXT,
    modules JSONB NOT NULL DEFAULT '[]',
    last_seen_at TIMESTAMPTZ,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE routing_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_channel TEXT NOT NULL,       -- 'telegram', 'email', 'mcp'
    source_id TEXT,                     -- chat_id, email address, etc.
    routed_to TEXT NOT NULL,            -- butler name
    prompt_summary TEXT,
    trace_id TEXT,                      -- OpenTelemetry trace ID
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

### 2. Relationship Butler (Personal CRM)

A personal relationship manager inspired by [Monica CRM](https://www.monicahq.com/). Tracks contacts, relationships, interactions, important dates, reminders, gifts, loans, and more.

**Modules:** none initially (all functionality is in its dedicated schema/tools)

**MCP tools (butler-specific, beyond core):**

| Category            | Tools                                                                                  |
| ------------------- | -------------------------------------------------------------------------------------- |
| **Contacts**        | `contact_create`, `contact_update`, `contact_get`, `contact_search`, `contact_archive` |
| **Relationships**   | `relationship_add`, `relationship_list`, `relationship_remove`                         |
| **Important Dates** | `date_add`, `date_list`, `upcoming_dates`                                              |
| **Notes**           | `note_create`, `note_list`, `note_search`                                              |
| **Interactions**    | `interaction_log`, `interaction_list` (calls, meetings, messages)                      |
| **Reminders**       | `reminder_create`, `reminder_list`, `reminder_dismiss`                                 |
| **Gifts**           | `gift_add`, `gift_update_status`, `gift_list`                                          |
| **Loans**           | `loan_create`, `loan_settle`, `loan_list`                                              |
| **Groups**          | `group_create`, `group_add_member`, `group_list`                                       |
| **Labels**          | `label_create`, `label_assign`, `contact_search_by_label`                              |
| **Quick Facts**     | `fact_set`, `fact_list`                                                                |
| **Activity Feed**   | `feed_get` (per contact, chronological)                                                |

**Config: `butlers/relationship/butler.toml`**
```toml
[butler]
name = "relationship"
description = "Personal CRM. Manages contacts, relationships, important dates, interactions, gifts, and reminders."
port = 40102

[butler.db]
name = "butler_relationship"

[[butler.schedule]]
name = "upcoming-dates-check"
cron = "0 8 * * *"
prompt = """
Check for important dates in the next 7 days (birthdays, anniversaries).
For each, draft a reminder message and store it in state for the Switchboard
to deliver via Telegram.
"""

[[butler.schedule]]
name = "relationship-maintenance"
cron = "0 9 * * 1"
prompt = """
Review contacts I haven't interacted with in 30+ days.
Suggest 3 people I should reach out to this week, with context
on our last interaction and any upcoming dates.
"""
```

**Relationship DB schema (dedicated `butler_relationship` database):**
```sql
-- Core tables (from framework: state, scheduled_tasks, sessions)

-- Contacts
CREATE TABLE contacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    first_name TEXT,
    last_name TEXT,
    nickname TEXT,
    company TEXT,
    job_title TEXT,
    gender TEXT,
    pronouns TEXT,
    avatar_url TEXT,
    listed BOOLEAN NOT NULL DEFAULT true,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Contact information (email, phone, social, etc.)
CREATE TABLE contact_info (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    type TEXT NOT NULL,             -- 'email', 'phone', 'telegram', 'linkedin', etc.
    value TEXT NOT NULL,
    label TEXT,                     -- 'work', 'personal', 'home'
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Typed, bidirectional relationships between contacts
CREATE TABLE relationships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    related_contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    group_type TEXT NOT NULL,       -- 'love', 'family', 'friend', 'work'
    type TEXT NOT NULL,             -- 'spouse', 'parent', 'child', 'colleague', etc.
    reverse_type TEXT NOT NULL,     -- the other direction: 'child' if type is 'parent'
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(contact_id, related_contact_id, type)
);

-- Important dates (birthdays, anniversaries, etc.)
CREATE TABLE important_dates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    label TEXT NOT NULL,            -- 'birthday', 'anniversary', 'deceased', custom
    day INT,                        -- nullable for partial dates
    month INT,
    year INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Notes per contact
CREATE TABLE notes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    title TEXT,
    body TEXT NOT NULL,
    emotion TEXT,                   -- 'positive', 'neutral', 'negative'
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Interaction log (calls, meetings, messages)
CREATE TABLE interactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    type TEXT NOT NULL,             -- 'call', 'video', 'meeting', 'message', 'email'
    direction TEXT,                 -- 'inbound', 'outbound'
    summary TEXT,
    duration_minutes INT,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata JSONB NOT NULL DEFAULT '{}'
);

-- Reminders (one-time or recurring)
CREATE TABLE reminders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID REFERENCES contacts(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'one_time',  -- 'one_time', 'recurring_yearly', 'recurring_monthly'
    next_trigger_at TIMESTAMPTZ,
    last_triggered_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Gift tracking (idea → bought → given pipeline)
CREATE TABLE gifts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'idea',  -- 'idea', 'searched', 'found', 'bought', 'given'
    occasion TEXT,                         -- 'birthday', 'christmas', 'just_because'
    estimated_price_cents INT,
    url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Loans and debts
CREATE TABLE loans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lender_contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    borrower_contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    amount_cents INT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    loaned_at TIMESTAMPTZ,
    settled BOOLEAN NOT NULL DEFAULT false,
    settled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Groups (families, friend circles, teams)
CREATE TABLE groups (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    type TEXT,                      -- 'family', 'couple', 'friends', 'team'
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE group_members (
    group_id UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    role TEXT,                      -- 'parent', 'child', 'partner', etc.
    PRIMARY KEY (group_id, contact_id)
);

-- Labels (color-coded tags)
CREATE TABLE labels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT UNIQUE NOT NULL,
    color TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE contact_labels (
    contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    label_id UUID NOT NULL REFERENCES labels(id) ON DELETE CASCADE,
    PRIMARY KEY (contact_id, label_id)
);

-- Quick facts (key-value per contact: hobbies, food preferences, etc.)
CREATE TABLE quick_facts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    category TEXT NOT NULL,         -- 'hobbies', 'food', 'custom'
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Addresses
CREATE TABLE addresses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    type TEXT,                      -- 'home', 'work', 'other'
    line_1 TEXT,
    line_2 TEXT,
    city TEXT,
    province TEXT,
    postal_code TEXT,
    country TEXT,
    is_current BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Activity feed (polymorphic log of all changes per contact)
CREATE TABLE contact_feed (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    action TEXT NOT NULL,           -- 'note_created', 'interaction_logged', 'gift_added', etc.
    entity_type TEXT,               -- 'note', 'interaction', 'gift', etc.
    entity_id UUID,
    summary TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_contacts_name ON contacts(first_name, last_name);
CREATE INDEX idx_contact_info_type ON contact_info(contact_id, type);
CREATE INDEX idx_important_dates_month ON important_dates(month, day);
CREATE INDEX idx_interactions_contact ON interactions(contact_id, occurred_at DESC);
CREATE INDEX idx_notes_contact ON notes(contact_id, created_at DESC);
CREATE INDEX idx_contact_feed_contact ON contact_feed(contact_id, created_at DESC);
```

---

### 3. Health Butler

Tracks health data, medications, conditions, diet, and aggregates research. Designed around the fact that health data is highly personal and benefits from longitudinal tracking.

**Modules:** none initially

**MCP tools (butler-specific):**

| Category         | Tools                                                                            |
| ---------------- | -------------------------------------------------------------------------------- |
| **Measurements** | `measurement_log`, `measurement_history`, `measurement_latest`                   |
| **Medications**  | `medication_add`, `medication_list`, `medication_log_dose`, `medication_history` |
| **Conditions**   | `condition_add`, `condition_list`, `condition_update`                            |
| **Diet**         | `meal_log`, `meal_history`, `nutrition_summary`                                  |
| **Symptoms**     | `symptom_log`, `symptom_history`, `symptom_search`                               |
| **Research**     | `research_save`, `research_search`, `research_summarize`                         |
| **Reports**      | `health_summary`, `trend_report`                                                 |

**Config: `butlers/health/butler.toml`**
```toml
[butler]
name = "health"
description = "Health tracking and management. Medications, measurements, diet, conditions, symptoms, and research aggregation."
port = 40103

[butler.db]
name = "butler_health"

[[butler.schedule]]
name = "medication-reminder-check"
cron = "0 8,12,20 * * *"
prompt = """
Check for medications due in the next 2 hours that haven't been logged today.
For each, prepare a reminder message to store in state for Switchboard delivery.
"""

[[butler.schedule]]
name = "weekly-health-summary"
cron = "0 9 * * 0"
prompt = """
Generate a weekly health summary: weight trend, medication adherence,
symptom frequency, and any notable patterns. Store as a report.
"""
```

**Health DB schema:**
```sql
-- Measurements (weight, blood pressure, heart rate, etc.)
CREATE TABLE measurements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type TEXT NOT NULL,             -- 'weight', 'blood_pressure', 'heart_rate', 'blood_sugar', 'temperature'
    value JSONB NOT NULL,           -- {"kg": 75.5} or {"systolic": 120, "diastolic": 80}
    unit TEXT,
    notes TEXT,
    measured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Medications
CREATE TABLE medications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    dosage TEXT,                    -- '500mg', '10ml'
    frequency TEXT,                 -- 'daily', 'twice_daily', 'as_needed'
    schedule JSONB,                 -- {"times": ["08:00", "20:00"]}
    prescriber TEXT,
    purpose TEXT,
    active BOOLEAN NOT NULL DEFAULT true,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Medication dose log
CREATE TABLE medication_doses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    medication_id UUID NOT NULL REFERENCES medications(id),
    taken_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    skipped BOOLEAN NOT NULL DEFAULT false,
    notes TEXT
);

-- Health conditions
CREATE TABLE conditions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',  -- 'active', 'managed', 'resolved'
    diagnosed_at TIMESTAMPTZ,
    notes TEXT,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Diet / meal logging
CREATE TABLE meals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type TEXT,                      -- 'breakfast', 'lunch', 'dinner', 'snack'
    description TEXT NOT NULL,
    nutrition JSONB,                -- {"calories": 500, "protein_g": 30, ...}
    eaten_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Symptom tracking
CREATE TABLE symptoms (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    severity INT,                   -- 1-10
    notes TEXT,
    condition_id UUID REFERENCES conditions(id),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Research notes (articles, findings, summaries)
CREATE TABLE research (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    topic TEXT NOT NULL,
    title TEXT,
    content TEXT NOT NULL,
    source_url TEXT,
    tags JSONB NOT NULL DEFAULT '[]',
    condition_id UUID REFERENCES conditions(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_measurements_type ON measurements(type, measured_at DESC);
CREATE INDEX idx_medication_doses_med ON medication_doses(medication_id, taken_at DESC);
CREATE INDEX idx_symptoms_name ON symptoms(name, occurred_at DESC);
CREATE INDEX idx_meals_date ON meals(eaten_at DESC);
CREATE INDEX idx_research_topic ON research(topic);
```

---

### 4. General Butler (catch-all)

The **catch-all** for requests that don't fit other butlers. Uses a **freeform JSONB data structure** so it can accumulate any kind of data. As patterns emerge, data can be migrated to a new specialized butler.

**Modules:** none initially

**MCP tools (butler-specific):**

| Category        | Tools                                                                            |
| --------------- | -------------------------------------------------------------------------------- |
| **Entities**    | `entity_create`, `entity_get`, `entity_update`, `entity_search`, `entity_delete` |
| **Collections** | `collection_create`, `collection_list`, `collection_get`                         |
| **Migration**   | `export_collection`, `export_by_tag`                                             |

**Config: `butlers/general/butler.toml`**
```toml
[butler]
name = "general"
description = "General-purpose catch-all butler. Freeform JSON data for anything that doesn't fit a specialist butler. Data migrates to new butlers over time."
port = 40101

[butler.db]
name = "butler_general"
```

**General DB schema:**
```sql
-- Collections (logical groupings, like future butler categories)
CREATE TABLE collections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    schema_hint JSONB,             -- optional: suggested shape of entities in this collection
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Freeform entities (the core storage — anything goes)
CREATE TABLE entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_id UUID REFERENCES collections(id),
    title TEXT,
    data JSONB NOT NULL DEFAULT '{}',
    tags JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_entities_collection ON entities(collection_id);
CREATE INDEX idx_entities_tags ON entities USING GIN(tags);
CREATE INDEX idx_entities_data ON entities USING GIN(data);
CREATE INDEX idx_entities_title ON entities(title);
```

The General butler is intentionally schema-light. runtime instances can store anything:
```json
{"type": "recipe", "name": "Pasta Carbonara", "ingredients": [...], "source": "..."}
{"type": "travel_idea", "destination": "Kyoto", "notes": "cherry blossom season", "budget": 3000}
{"type": "book_note", "title": "Thinking Fast and Slow", "highlights": [...]}
```

When a collection grows enough to warrant its own butler, `export_collection` extracts all entities for migration.

---

### 5. Heartbeat Butler

Infrastructure butler. Calls `tick()` on every registered butler every 10 minutes.

**Config: `butlers/heartbeat/butler.toml`**
```toml
[butler]
name = "heartbeat"
description = "Infrastructure butler. Calls tick() on all registered butlers every 10 minutes."
port = 40199

[butler.db]
name = "butler_heartbeat"

[[butler.schedule]]
name = "heartbeat-cycle"
cron = "*/10 * * * *"
prompt = """
Query the Switchboard for all registered butlers via list_butlers().
Call tick() on each one. Log results.
"""
```

---

## Observability: OpenTelemetry

All inter-butler communication is instrumented with OpenTelemetry for distributed tracing. Trace context propagates from Switchboard → target butler → runtime instance, giving end-to-end visibility for every request.

### Instrumentation Points

```
Telegram message arrives
  └─ [Span: switchboard.receive] channel=telegram, chat_id=...
      └─ [Span: switchboard.classify] → Runtime instance decides routing
          └─ [Span: switchboard.route] target=health
              └─ [Span: health.trigger] prompt=...          ← trace context passed via MCP
                  └─ [Span: health.llm_session] session_id=...
                      ├─ [Span: health.tool.measurement_log]
                      ├─ [Span: health.tool.state_set]
                      └─ [Span: health.tool.sessions_log]
```

### Trace Context Propagation

1. **Switchboard → Butler:** Trace context (`traceparent` header) is propagated via MCP tool call metadata. When the Switchboard calls `route()`, it includes the current trace context. The target butler extracts it and creates a child span.

2. **Butler → Runtime Instance:** The LLM CLI spawner passes trace context via environment variable (`TRACEPARENT`) or MCP config metadata. The session log stores the `trace_id` for correlation.

3. **Runtime Instance → Butler MCP tools:** Each MCP tool call from the runtime instance back to the butler creates a child span under the runtime session span.

### Core Instrumentation

```python
# src/butlers/core/telemetry.py
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

tracer = trace.get_tracer("butlers")

def init_telemetry(service_name: str):
    """Initialize OTel for a butler daemon."""
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
```

**Every MCP tool handler is wrapped with a span:**
```python
@self._mcp.tool(name="trigger", description="Spawn runtime instance")
async def trigger(prompt: str) -> dict:
    with tracer.start_as_current_span("butler.trigger", attributes={"butler.name": self.config.name}) as span:
        # ... spawn runtime instance ...
        span.set_attribute("cc.session_id", session_id)
        return result
```

**Inter-butler calls carry trace context:**
```python
async def route(butler_name: str, tool_name: str, args: dict) -> dict:
    with tracer.start_as_current_span("switchboard.route", attributes={"target": butler_name}):
        ctx = extract_trace_context()  # W3C traceparent
        result = await mcp_client.call_tool(tool_name, {**args, "_trace_context": ctx})
        return result
```

### Local Dev Export

Docker Compose includes the LGTM stack (Alloy/Tempo/Grafana) for local trace visualization:
```yaml
  alloy:
    image: grafana/alloy:latest
    ports:
      - "4317:4317"     # OTLP gRPC receiver
    volumes:
      - ./alloy-config.yaml:/etc/alloy/config.yaml:ro
    command: run /etc/alloy/config.yaml

  tempo:
    image: grafana/tempo:latest
    ports:
      - "3200:3200"     # tempo API

  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"     # UI
    environment:
      GF_SECURITY_ADMIN_PASSWORD: admin
    volumes:
      - ./grafana-datasources.yaml:/etc/grafana/provisioning/datasources/datasources.yaml:ro
```

### What Gets Traced

| Event             | Span Name              | Key Attributes                |
| ----------------- | ---------------------- | ----------------------------- |
| Message received  | `switchboard.receive`  | `channel`, `source_id`        |
| Routing decision  | `switchboard.classify` | `routed_to`                   |
| Inter-butler call | `switchboard.route`    | `target`, `tool_name`         |
| Runtime spawned        | `butler.llm_session`    | `session_id`, `prompt_length` |
| MCP tool called   | `butler.tool.<name>`   | tool-specific attributes      |
| Scheduler tick    | `butler.tick`          | `tasks_due`, `tasks_run`      |
| Heartbeat cycle   | `heartbeat.cycle`      | `butlers_ticked`, `failures`  |

---

## Testing Strategy

### Principle: Mock the LLM CLI Executable

LLM CLI instances are the most expensive part of the system. **All unit tests mock the LLM CLI spawner.** The mock records what prompts were sent and what tools the runtime instance would have called, without invoking a real LLM.

### MockRuntime

```python
# tests/conftest.py
from dataclasses import dataclass, field
from butlers.core.spawner import SpawnerResult


@dataclass
class MockSpawner:
    """Mock LLM CLI spawner that records invocations and returns canned results."""

    calls: list[dict] = field(default_factory=list)
    responses: dict[str, SpawnerResult] = field(default_factory=dict)
    default_response: SpawnerResult = field(default_factory=lambda: SpawnerResult(
        output="mock response",
        tool_calls=[],
        success=True,
    ))

    async def trigger(self, prompt: str, context: dict | None = None) -> SpawnerResult:
        self.calls.append({"prompt": prompt, "context": context})
        # Match on prompt substring for canned responses
        for pattern, response in self.responses.items():
            if pattern in prompt:
                return response
        return self.default_response

    def assert_triggered(self, times: int | None = None):
        if times is not None:
            assert len(self.calls) == times, f"Expected {times} triggers, got {len(self.calls)}"

    def assert_prompted_with(self, substring: str):
        assert any(substring in c["prompt"] for c in self.calls), \
            f"No trigger prompt contained '{substring}'"
```

### Test Layers

| Layer                           | What                                     | DB                       | LLM CLI            | How                              |
| ------------------------------- | ---------------------------------------- | ------------------------ | ------------- | -------------------------------- |
| **Unit: Config**                | Config loading, validation               | No                       | No            | `tmp_path` fixtures              |
| **Unit: Modules**               | Module ABC, registry, tool registration  | No                       | No            | FastMCP in-memory                |
| **Unit: Core tools**            | State store, scheduler, session log      | Mock or testcontainer    | `MockSpawner` | Async tests                      |
| **Unit: Butler-specific tools** | Relationship CRUD, Health tracking, etc. | Testcontainer PostgreSQL | No            | Test each tool function directly |
| **Unit: Switchboard routing**   | Registry, route logic                    | Mock                     | `MockSpawner` | Test routing decisions           |
| **Unit: Heartbeat**             | Tick cycle, butler enumeration           | Mock                     | No            | Mock MCP client                  |
| **Integration**                 | Full butler startup, tool call, trigger  | Testcontainer PostgreSQL | `MockSpawner` | End-to-end with mocked LLM CLI        |
| **Integration: Tracing**        | Span creation, context propagation       | No                       | `MockSpawner` | InMemorySpanExporter             |

### DB Tests

Use `testcontainers` for real PostgreSQL in tests:
```python
# tests/conftest.py
import pytest
from testcontainers.postgres import PostgresContainer

@pytest.fixture(scope="session")
def pg_container():
    with PostgresContainer("postgres:17") as pg:
        yield pg

@pytest.fixture
async def butler_db(pg_container):
    """Create a fresh per-butler database for each test."""
    # Create a unique DB, apply core + module migrations, return connection
    ...
```

### Example: Testing Relationship Butler Tools

```python
# tests/test_relationship_tools.py
import pytest

async def test_create_and_get_contact(butler_db):
    tools = RelationshipTools(db=butler_db)

    result = await tools.contact_create(
        first_name="Alice", last_name="Smith", company="Acme"
    )
    assert result["id"]

    contact = await tools.contact_get(id=result["id"])
    assert contact["first_name"] == "Alice"
    assert contact["company"] == "Acme"


async def test_add_relationship(butler_db):
    tools = RelationshipTools(db=butler_db)

    alice = await tools.contact_create(first_name="Alice")
    bob = await tools.contact_create(first_name="Bob")

    await tools.relationship_add(
        contact_id=alice["id"],
        related_contact_id=bob["id"],
        group_type="friend",
        type="best friend",
    )

    rels = await tools.relationship_list(contact_id=alice["id"])
    assert len(rels) == 1
    assert rels[0]["related_contact_id"] == bob["id"]


async def test_upcoming_dates(butler_db):
    tools = RelationshipTools(db=butler_db)

    alice = await tools.contact_create(first_name="Alice")
    await tools.date_add(
        contact_id=alice["id"], label="birthday", month=2, day=14
    )

    upcoming = await tools.upcoming_dates(days=7)
    # Test depends on current date — use freezegun or similar
    assert isinstance(upcoming, list)
```

### Example: Testing Scheduler with MockSpawner

```python
# tests/test_scheduler_integration.py
async def test_tick_dispatches_due_tasks(butler_db, mock_spawner):
    scheduler = Scheduler(db=butler_db, spawner=mock_spawner)

    # Insert a due task
    await scheduler.create_task(name="test", cron="* * * * *", prompt="Do the thing")
    await scheduler.sync_next_run_times()

    # Tick
    result = await scheduler.tick()

    assert result["tasks_run"] == 1
    mock_spawner.assert_triggered(times=1)
    mock_spawner.assert_prompted_with("Do the thing")
```

### Example: Testing Trace Propagation

```python
# tests/test_telemetry.py
from opentelemetry.sdk.trace.export.in_memory import InMemorySpanExporter

async def test_route_propagates_trace_context(in_memory_exporter):
    # Call switchboard.route()
    # Assert child span created on target butler
    # Assert trace_id matches across spans
    spans = in_memory_exporter.get_finished_spans()
    route_span = next(s for s in spans if s.name == "switchboard.route")
    trigger_span = next(s for s in spans if s.name == "butler.trigger")
    assert route_span.context.trace_id == trigger_span.context.trace_id
```

---

## Deployment

Two modes from the same codebase: **single-process for development**, **docker-compose for production**. The `butlers` CLI supports both.

### CLI Modes

```bash
# Dev mode: single process, all butlers in one asyncio event loop
butlers up                                    # discover all butlers/ dirs, start all
butlers up --only switchboard,health          # start a subset

# Production mode: one butler per invocation (used by docker-compose)
butlers run --config butlers/health           # start one butler daemon
butlers run --config butlers/switchboard

# Utilities
butlers list                                  # list discovered butlers and their ports
butlers init <name> --port 40104               # scaffold a new butler directory
```

Both modes use MCP-over-HTTP for inter-butler communication, so behavior is identical regardless of process topology. The only difference is process isolation.

### Dev Mode: `butlers up`

One Python process, one asyncio event loop, all butler MCP servers bound to different ports.

```
$ butlers up
[2026-02-09 08:00:00] switchboard   listening on :40100
[2026-02-09 08:00:00] general       listening on :40101
[2026-02-09 08:00:00] relationship  listening on :40102
[2026-02-09 08:00:00] health        listening on :40103
[2026-02-09 08:00:00] heartbeat     listening on :40199
```

Requires PostgreSQL running locally (or via `docker compose up postgres`). Reads all `butlers/*/butler.toml` configs, creates per-butler databases if they don't exist, applies migrations, and starts all MCP servers concurrently.

### Production Mode: docker-compose

Each butler runs as a separate container from the same base image. PostgreSQL and LGTM stack (Alloy/Tempo/Grafana) as companion services.

```yaml
# docker-compose.yml
services:
  postgres:
    image: postgres:17
    volumes:
      - pgdata:/var/lib/postgresql/data
    environment:
      POSTGRES_USER: butlers
      POSTGRES_PASSWORD: butlers
    ports:
      - "5432:5432"
    healthcheck:
      test: pg_isready -U butlers
      interval: 5s
      retries: 5

  alloy:
    image: grafana/alloy:latest
    ports:
      - "4317:4317"      # OTLP gRPC receiver
    volumes:
      - ./alloy-config.yaml:/etc/alloy/config.yaml:ro
    command: run /etc/alloy/config.yaml

  tempo:
    image: grafana/tempo:latest
    ports:
      - "3200:3200"      # Tempo API

  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"      # UI
    environment:
      GF_SECURITY_ADMIN_PASSWORD: admin
    volumes:
      - ./grafana-datasources.yaml:/etc/grafana/provisioning/datasources/datasources.yaml:ro
    depends_on:
      - tempo

  switchboard:
    image: butlers:latest
    command: ["butlers", "run", "--config", "/etc/butler"]
    volumes:
      - ./butlers/switchboard:/etc/butler:ro
    ports:
      - "40100:40100"
    environment:
      DATABASE_URL: postgres://butlers:butlers@postgres/butler_switchboard
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
      OTEL_EXPORTER_OTLP_ENDPOINT: http://alloy:4317
    depends_on:
      postgres:
        condition: service_healthy

  general:
    image: butlers:latest
    command: ["butlers", "run", "--config", "/etc/butler"]
    volumes:
      - ./butlers/general:/etc/butler:ro
    ports:
      - "40101:40101"
    environment:
      DATABASE_URL: postgres://butlers:butlers@postgres/butler_general
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
      OTEL_EXPORTER_OTLP_ENDPOINT: http://alloy:4317
    depends_on:
      postgres:
        condition: service_healthy

  relationship:
    image: butlers:latest
    command: ["butlers", "run", "--config", "/etc/butler"]
    volumes:
      - ./butlers/relationship:/etc/butler:ro
    ports:
      - "40102:40102"
    environment:
      DATABASE_URL: postgres://butlers:butlers@postgres/butler_relationship
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
      OTEL_EXPORTER_OTLP_ENDPOINT: http://alloy:4317
    depends_on:
      postgres:
        condition: service_healthy

  health:
    image: butlers:latest
    command: ["butlers", "run", "--config", "/etc/butler"]
    volumes:
      - ./butlers/health:/etc/butler:ro
    ports:
      - "40103:40103"
    environment:
      DATABASE_URL: postgres://butlers:butlers@postgres/butler_health
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
      OTEL_EXPORTER_OTLP_ENDPOINT: http://alloy:4317
    depends_on:
      postgres:
        condition: service_healthy

  heartbeat:
    image: butlers:latest
    command: ["butlers", "run", "--config", "/etc/butler"]
    volumes:
      - ./butlers/heartbeat:/etc/butler:ro
    ports:
      - "40199:40199"
    environment:
      DATABASE_URL: postgres://butlers:butlers@postgres/butler_heartbeat
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
      OTEL_EXPORTER_OTLP_ENDPOINT: http://alloy:4317
    depends_on:
      postgres:
        condition: service_healthy

volumes:
  pgdata:
```

### Dockerfile

```dockerfile
FROM python:3.12-slim

# Install Claude Code (Node.js runtime + claude-code npm package)
RUN apt-get update && apt-get install -y curl && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    npm install -g @anthropic-ai/claude-code && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync --no-dev

COPY src/ src/
COPY migrations/ migrations/

ENTRYPOINT ["uv", "run"]
CMD ["butlers", "up"]
```

### Database Provisioning

Each butler auto-provisions its database on startup:

1. Connect to PostgreSQL as the `butlers` superuser
2. `CREATE DATABASE butler_<name> IF NOT EXISTS`
3. Apply core migrations (`migrations/core/`)
4. Apply butler-specific migrations (`migrations/<name>/`) if they exist
5. Sync TOML scheduled tasks to DB

This happens identically in both dev and production modes. No separate init script needed.

### Quick Start

```bash
# Dev (local Python, containerized Postgres + LGTM stack)
docker compose up -d postgres alloy tempo grafana
butlers up

# Production (everything containerized)
docker compose up -d
```

---

## Milestones

### Milestone 0: Project Skeleton
Python project with uv, ruff, pytest, CI. MockSpawner fixture. Testcontainers setup.

### Milestone 1: Core Daemon + Module System
Module ABC, registry with dependency resolution, config loading (with `[[butler.schedule]]`), daemon with core tool stubs + module composition. Butler class.

### Milestone 2: OpenTelemetry Foundation
Telemetry init, tracer setup, span wrappers for MCP tool handlers. InMemorySpanExporter for tests. LGTM stack (Alloy/Tempo/Grafana) in docker-compose.

### Milestone 3: Per-Butler PostgreSQL + State Store
DB connection layer, core schema migration on startup, state store tools live. Session log tools live.

### Milestone 4: Claude Code Spawner
MockSpawner for tests. Real LLM CLI spawner via SDK. Locked-down MCP config generation. `trigger` tool wired. Session logging with trace context.

### Milestone 5: Task Scheduler
TOML → DB sync. Cron evaluation. `tick()` handler dispatches to LLM CLI spawner. Schedule CRUD tools. Tests with MockSpawner.

### Milestone 6: Telegram + Email Modules
Telegram bot listener (polling or webhook). Email listener (IMAP or webhook). Both register tools on the butler's MCP server. Tests with mocked APIs.

### Milestone 7: Switchboard Butler
Butler registry. Routing logic. `route()`, `list_butlers()`, `discover()`. Trace context propagation on route. Tests with MockSpawner for classification.

### Milestone 8: Heartbeat Butler
Tick cycle. Butler enumeration from Switchboard. Logging. Tests with mocked MCP client.

### Milestone 9: Relationship Butler
Dedicated schema migration. All CRM tools (contacts, relationships, dates, notes, interactions, reminders, gifts, loans, groups, labels, facts, feed). Scheduled tasks. Full unit test coverage against testcontainer PostgreSQL.

### Milestone 10: Health Butler
Dedicated schema migration. All health tools (measurements, medications, conditions, meals, symptoms, research, reports). Scheduled tasks. Full unit test coverage.

### Milestone 11: General Butler
Dedicated schema with freeform entities + collections. CRUD + search + export tools. Tests.

### Milestone 12: Deployment + CLI
`butlers up` (dev mode, single process) and `butlers run` (production mode, one butler). Dockerfile with Claude Code installed. docker-compose.yml with all 5 butlers + PostgreSQL + LGTM stack (Alloy/Tempo/Grafana). Auto-provisioning of per-butler databases on startup. End-to-end smoke test.

---

## Milestone 0: Project Skeleton

### Task 0.1: Initialize Python project with uv

**Files:**
- Create: `pyproject.toml`
- Create: `src/butlers/__init__.py`
- Create: `src/butlers/py.typed`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `.python-version`

**Step 1: Initialize project**

```toml
# pyproject.toml
[project]
name = "butlers"
version = "0.1.0"
description = "AI agent framework — MCP server daemons with core infrastructure and modular capabilities"
requires-python = ">=3.12"
dependencies = []

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "ruff>=0.8",
    "testcontainers[postgres]>=4.0",
]

[tool.ruff]
target-version = "py312"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/butlers"]
```

**Step 2: Create package files and test fixtures**

```python
# src/butlers/__init__.py
"""Butlers — MCP server daemons with core infrastructure and modular capabilities."""

__version__ = "0.1.0"
```

```python
# tests/conftest.py
"""Shared test fixtures including MockSpawner."""

import pytest
from dataclasses import dataclass, field


@dataclass
class SpawnerResult:
    output: str = "mock response"
    tool_calls: list = field(default_factory=list)
    success: bool = True
    error: str | None = None


@dataclass
class MockSpawner:
    """Mock LLM CLI spawner — records invocations, returns canned results."""

    calls: list[dict] = field(default_factory=list)
    responses: dict[str, SpawnerResult] = field(default_factory=dict)
    default_response: SpawnerResult = field(default_factory=SpawnerResult)

    async def trigger(self, prompt: str, context: dict | None = None) -> SpawnerResult:
        self.calls.append({"prompt": prompt, "context": context})
        for pattern, response in self.responses.items():
            if pattern in prompt:
                return response
        return self.default_response

    def assert_triggered(self, times: int | None = None):
        if times is not None:
            assert len(self.calls) == times, f"Expected {times} triggers, got {len(self.calls)}"

    def assert_prompted_with(self, substring: str):
        assert any(substring in c["prompt"] for c in self.calls), \
            f"No trigger prompt contained '{substring}'"


@pytest.fixture
def mock_spawner():
    return MockSpawner()
```

**Step 3: Set Python version**

```
# .python-version
3.12
```

**Step 4: Install and verify**

Run: `cd /home/tze/GitHub/butlers && uv sync --dev`
Expected: Dependencies install successfully

**Step 5: Commit**

```bash
git add pyproject.toml src/ tests/ .python-version
git commit -m "chore: initialize python project with uv and MockSpawner fixture"
```

---

### Task 0.2: Add linting and basic CI

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `Makefile`

**Step 1: Create Makefile**

```makefile
.PHONY: lint test format check

lint:
	uv run ruff check src/ tests/

format:
	uv run ruff format src/ tests/

test:
	uv run pytest -v

check: lint test
```

**Step 2: Create CI workflow**

```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]
jobs:
  check:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:17
        env:
          POSTGRES_USER: butlers
          POSTGRES_PASSWORD: butlers
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - run: uv sync --dev
      - run: make check
```

**Step 3: Verify locally**

Run: `make check`
Expected: Lint passes, 0 tests collected, exits 0

**Step 4: Commit**

```bash
git add Makefile .github/
git commit -m "chore: add Makefile and CI with PostgreSQL service"
```

---

(Milestones 1–5 task breakdowns remain as specified above. Milestones 6–12 task breakdowns to be detailed when earlier milestones are complete.)

---

## Project Structure (target)

```
butlers/
├── src/butlers/
│   ├── __init__.py
│   ├── butler.py               # Butler class
│   ├── config.py               # ButlerConfig (TOML loading)
│   ├── daemon.py               # ButlerDaemon (MCP server, core tools, module loading)
│   ├── db.py                   # Database connection layer
│   ├── core/                   # Core component implementations
│   │   ├── __init__.py
│   │   ├── state.py            # State store (KV persistence)
│   │   ├── scheduler.py        # Task scheduler (cron, TOML sync, tick)
│   │   ├── spawner.py          # LLM CLI Spawner (locked-down LLM CLI instances)
│   │   ├── sessions.py         # Session log (LLM CLI invocation tracking)
│   │   └── telemetry.py        # OpenTelemetry init + tracer
│   ├── modules/                # Module system
│   │   ├── __init__.py
│   │   ├── base.py             # Module ABC
│   │   ├── registry.py         # Module registry + dependency resolution
│   │   ├── email.py            # Email module
│   │   └── telegram.py         # Telegram module
│   ├── tools/                  # Butler-specific tool implementations
│   │   ├── __init__.py
│   │   ├── relationship.py     # Relationship butler CRUD tools
│   │   ├── health.py           # Health butler tracking tools
│   │   ├── general.py          # General butler freeform tools
│   │   └── switchboard.py      # Switchboard routing tools
│   ├── heartbeat_butler.py     # Heartbeat Butler
│   └── cli.py                  # CLI: `butlers up` (dev) / `butlers run` (prod)
├── tests/
│   ├── conftest.py             # MockSpawner, DB fixtures, OTel test exporter
│   ├── test_config.py
│   ├── test_daemon.py
│   ├── test_butler.py
│   ├── test_module_base.py
│   ├── test_module_registry.py
│   ├── test_module_telegram.py
│   ├── test_module_email.py
│   ├── test_core_state.py
│   ├── test_core_scheduler.py
│   ├── test_core_spawner.py
│   ├── test_core_sessions.py
│   ├── test_core_telemetry.py
│   ├── test_tools_relationship.py
│   ├── test_tools_health.py
│   ├── test_tools_general.py
│   ├── test_tools_switchboard.py
│   ├── test_heartbeat.py
│   └── ...
├── butlers/                    # Butler config directories (git-based)
│   ├── switchboard/
│   │   ├── CLAUDE.md
│   │   └── butler.toml
│   ├── heartbeat/
│   │   ├── CLAUDE.md
│   │   └── butler.toml
│   ├── relationship/
│   │   ├── CLAUDE.md
│   │   ├── butler.toml
│   │   └── skills/
│   │       └── relationship-maintenance/
│   │           └── SKILL.md
│   ├── health/
│   │   ├── CLAUDE.md
│   │   ├── butler.toml
│   │   └── skills/
│   │       └── weekly-summary/
│   │           └── SKILL.md
│   └── general/
│       ├── CLAUDE.md
│       └── butler.toml
├── migrations/
│   ├── core/
│   │   └── 001_core.sql
│   ├── relationship/
│   │   └── 001_relationship.sql
│   ├── health/
│   │   └── 001_health.sql
│   ├── general/
│   │   └── 001_general.sql
│   └── switchboard/
│       └── 001_switchboard.sql
├── pyproject.toml
├── Makefile
├── Dockerfile
├── docker-compose.yml
└── PROJECT_PLAN.md
```

---

## Open Questions

| Question                                                | Status  | Notes                                                                                                       |
| ------------------------------------------------------- | ------- | ----------------------------------------------------------------------------------------------------------- |
| Claude Code SDK API for passing MCP config?             | TBD     | Need to investigate exact SDK options for `--mcp-config` equivalent                                         |
| MCP transport between butlers (SSE vs streamable HTTP)? | TBD     | SSE likely for daemon-to-daemon; need to test FastMCP SSE transport                                         |
| Cron expression library?                                | TBD     | `croniter` is the standard Python choice                                                                    |
| How are new butler databases provisioned?               | Decided | Auto-provision on startup: `CREATE DATABASE IF NOT EXISTS`, then apply migrations. No separate init script. |
| Auth/security between butlers on Docker network?        | TBD     | Not needed for v0.1                                                                                         |
| Concurrent runtime instances per butler?                     | TBD     | Serial initially, queue later                                                                               |
| How do integration modules handle OAuth?                | TBD     | Out-of-band setup, credentials as env vars                                                                  |
| runtime instance timeout?                                    | TBD     | Configurable per-trigger, default ~5 min                                                                    |
| Can runtime instances create new scheduled tasks?            | Yes     | Via `schedule_create` MCP tool — Runtime instance can self-schedule follow-up work                                        |
| How does the runtime instance know about skills?                          | TBD     | System prompt lists available skills, or Runtime instance reads `skills/` directory via bash                              |
| Telegram: polling vs webhook?                           | TBD     | Polling simpler for dev, webhook for production                                                             |
| How does Switchboard send responses back?               | TBD     | Likely stores in state, Telegram module polls or callback                                                   |
| OTel sampling strategy?                                 | TBD     | Sample 100% in dev, configurable in prod                                                                    |
| Butler-specific migrations: auto-apply or explicit?     | TBD     | Auto-apply on startup for simplicity                                                                        |
| How to handle Switchboard routing when the runtime instance is unsure?    | TBD     | Default to General butler                                                                                   |
