# butler.toml Reference

## Schema

```toml
[butler]
name = "<butler-name>"          # Required. Lowercase, no hyphens. Matches directory name.
port = <port-number>            # Required. Unique across all butlers.
description = "<description>"   # Required. One-line summary for registry/display.

[butler.runtime]                # Optional. Runtime model and concurrency.
model = "gpt-5.3-codex-spark"  # Model for runtime invocations.
max_concurrent_sessions = 3     # Default 1 (serial). Set higher for throughput.
max_queued_sessions = 100       # Default 100.

[runtime]                       # Optional. Runtime type.
type = "codex"                  # Runtime adapter type.

[butler.db]
name = "butlers"                # Required. Always "butlers" (shared database).
schema = "<butler-name>"        # Required. Per-butler schema for isolation.

[[butler.schedule]]             # Optional. Repeatable section for cron tasks.
name = "<task-name>"            # Kebab-case identifier for the scheduled task.
cron = "<cron-expression>"      # Standard 5-field cron expression.
prompt = """<prompt>"""         # Prompt sent to runtime instance (prompt mode, default).
# dispatch_mode = "prompt"      # Default. Explicit only when needed.

[[butler.schedule]]             # Optional. Job-mode scheduled task.
name = "<job-name>"             # Kebab-case identifier.
cron = "<cron-expression>"      # Standard 5-field cron expression.
dispatch_mode = "job"           # Calls a Python function directly (no LLM spawn).
job_name = "<function_name>"    # Python function to invoke.

[modules.calendar]              # Optional. Google Calendar integration.
provider = "google"
calendar_id = "<calendar-id>"   # Shared butler calendar (not user's primary).

[modules.calendar.conflicts]
policy = "suggest"              # "suggest" = propose alternatives on overlap.

[modules.contacts]              # Optional. Google Contacts integration.
provider = "google"
include_other_contacts = false

[modules.contacts.sync]
enabled = true
run_on_startup = true
interval_minutes = 15
full_sync_interval_days = 6

[modules.memory]                # Optional. Memory subsystem (episodes, facts, rules).

[modules.telegram]              # Optional. Telegram channel.
mode = "polling"                # Polling mode for bot.

[modules.telegram.user]
enabled = false                 # User-scoped Telegram (usually disabled).

[modules.telegram.bot]
token_env = "BUTLER_TELEGRAM_TOKEN"  # Env var reference for bot token.

[modules.email]                 # Optional. Email channel.

[modules.email.user]
enabled = false                 # User-scoped email (usually disabled).

[modules.email.bot]
address_env = "BUTLER_EMAIL_ADDRESS"
password_env = "BUTLER_EMAIL_PASSWORD"
```

## Port Allocation

| Butler       | Port  | Type           | Status  |
|-------------|-------|----------------|---------|
| switchboard | 40100 | Infrastructure | Active  |
| general     | 40101 | Domain         | Active  |
| relationship| 40102 | Domain         | Active  |
| health      | 40103 | Domain         | Active  |
| messenger   | 40104 | Infrastructure | Active  |
| *next*      | 40105+| Domain         | —       |
| *(reserved)*| 40199 | Infrastructure | Reserved|

Port 40199 is reserved for infrastructure butlers. New domain butlers should use 40105+.

## Database Isolation

All butlers share a single PostgreSQL database named `butlers`. Each butler gets its own schema:

```toml
[butler.db]
name = "butlers"           # Always the shared DB
schema = "health"          # This butler's isolated schema
```

Direct cross-butler DB access is prohibited. Inter-butler communication happens only via MCP/Switchboard.

## Schedule Dispatch Modes

### Prompt mode (default)

Spawns an ephemeral LLM runtime instance with the given prompt:

```toml
[[butler.schedule]]
name = "weekly-summary"
cron = "0 9 * * 0"
prompt = """
Generate a weekly summary...
"""
```

### Job mode

Calls a Python function directly without spawning an LLM:

```toml
[[butler.schedule]]
name = "memory-consolidation"
cron = "0 */6 * * *"
dispatch_mode = "job"
job_name = "memory_consolidation"
```

## Common Module Profiles

### Domain butler (user-facing)

Most domain butlers (health, general, relationship) enable:
- `calendar` — appointment scheduling on shared butler calendar
- `contacts` — Google Contacts access with sync
- `memory` — episode/fact/rule memory with consolidation

### Infrastructure butler (messenger)

Channel-owning butlers enable the channel modules:
- `telegram` — with bot token config
- `email` — with bot address/password config
- `calendar` — for delivery scheduling context

### Minimal butler

Butlers without external integrations may have no modules at all.

## Existing Examples

### Domain butler with full modules (health)

```toml
[butler]
name = "health"
port = 40103
description = "Health tracking assistant for measurements, medications, diet, food preferences, nutrition, meals, and symptoms"

[butler.runtime]
model = "gpt-5.3-codex-spark"
max_concurrent_sessions = 3

[runtime]
type = "codex"

[butler.db]
name = "butlers"
schema = "health"

[[butler.schedule]]
name = "medication-reminder-morning"
cron = "0 8 * * *"
prompt = """
Check for active medications with scheduled times between 8:00 AM and 10:00 AM.
For each medication, verify whether a dose has been logged for today.
Report any medications that are due but not yet logged.

ONLY use tools available via your MCP server.
"""

[[butler.schedule]]
name = "weekly-health-summary"
cron = "0 9 * * 0"
prompt = """
Generate a comprehensive weekly health summary including:
- Weight trend over the past week
- Medication adherence rates
- Symptom frequency and patterns
- Any notable changes or patterns
"""

[[butler.schedule]]
name = "memory-consolidation"
cron = "0 */6 * * *"
dispatch_mode = "job"
job_name = "memory_consolidation"

[[butler.schedule]]
name = "memory-episode-cleanup"
cron = "0 4 * * *"
dispatch_mode = "job"
job_name = "memory_episode_cleanup"

[modules.calendar]
provider = "google"
calendar_id = "<shared-butler-calendar-id>"

[modules.calendar.conflicts]
policy = "suggest"

[modules.contacts]
provider = "google"
include_other_contacts = false

[modules.contacts.sync]
enabled = true
run_on_startup = true
interval_minutes = 15
full_sync_interval_days = 6

[modules.memory]
```

### Infrastructure butler (messenger)

```toml
[butler]
name = "messenger"
port = 40104
description = "Outbound delivery execution plane for Telegram and Email"

[butler.runtime]
model = "gpt-5.3-codex-spark"
max_concurrent_sessions = 3

[runtime]
type = "codex"

[butler.db]
name = "butlers"
schema = "messenger"

[modules.calendar]
provider = "google"
calendar_id = "<shared-butler-calendar-id>"

[modules.calendar.conflicts]
policy = "suggest"

[modules.telegram]

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

### Minimal domain butler (general)

```toml
[butler]
name = "general"
port = 40101
description = "Flexible catch-all assistant for freeform data"

[butler.runtime]
model = "gpt-5.3-codex-spark"
max_concurrent_sessions = 3

[runtime]
type = "codex"

[butler.db]
name = "butlers"
schema = "general"

[modules.calendar]
provider = "google"
calendar_id = "<shared-butler-calendar-id>"

[modules.calendar.conflicts]
policy = "suggest"

[modules.contacts]
provider = "google"
include_other_contacts = false

[modules.contacts.sync]
enabled = true
run_on_startup = true
interval_minutes = 15
full_sync_interval_days = 6

[[butler.schedule]]
name = "memory-consolidation"
cron = "0 */6 * * *"
dispatch_mode = "job"
job_name = "memory_consolidation"

[[butler.schedule]]
name = "memory-episode-cleanup"
cron = "0 4 * * *"
dispatch_mode = "job"
job_name = "memory_episode_cleanup"

[modules.memory]
```

## Cron Expression Quick Reference

```
* * * * *
│ │ │ │ │
│ │ │ │ └── Day of week (0-7, 0 and 7 are Sunday)
│ │ │ └──── Month (1-12)
│ │ └────── Day of month (1-31)
│ └──────── Hour (0-23)
└────────── Minute (0-59)

Common patterns:
  "0 8 * * *"     → Every day at 8:00 AM
  "0 9 * * 0"     → Every Sunday at 9:00 AM
  "0 9 * * 1"     → Every Monday at 9:00 AM
  "*/10 * * * *"  → Every 10 minutes
  "0 */4 * * *"   → Every 4 hours
  "0 */6 * * *"   → Every 6 hours
  "30 18 * * 1-5" → Weekdays at 6:30 PM
  "0 4 * * *"     → Every day at 4:00 AM (maintenance window)
  "0 15 * * *"    → Every day at 3:00 PM
```

## Environment Variable References

Config values can reference environment variables with `${VAR_NAME}`:

```toml
[modules.telegram.bot]
token_env = "BUTLER_TELEGRAM_TOKEN"   # Module validates this env var exists at startup
```

- Unresolved required references are startup-blocking errors.
- Inline secrets in config values are prohibited — always use env var references.
