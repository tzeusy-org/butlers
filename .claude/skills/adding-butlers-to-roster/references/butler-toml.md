# butler.toml Reference

## Schema

```toml
[butler]
name = "<butler-name>"          # Required. Lowercase, no hyphens. Matches directory name.
port = <port-number>            # Required. Unique across all butlers.
description = "<description>"   # Required. One-line summary for registry/display.

[butler.db]
name = "butler_<butler-name>"   # Required. Database name. Convention: butler_ prefix.

[[butler.schedule]]             # Optional. Repeatable section for cron tasks.
name = "<task-name>"            # Kebab-case identifier for the scheduled task.
cron = "<cron-expression>"      # Standard 5-field cron expression.
prompt = """<prompt>"""         # Prompt sent to the runtime instance when triggered.

[modules.<module-name>]         # Optional. Module configuration.
mode = "polling"                # Module-specific settings.
```

## Port Allocation

| Butler       | Port |
|-------------|------|
| switchboard | 40100 |
| general     | 40101 |
| relationship| 40102 |
| health      | 40103 |
| heartbeat   | 40199 |
| *next*      | 40104+|

Heartbeat uses 40199 as a convention — it's an infrastructure butler, not a domain butler.

## Existing Examples

### Minimal (general)

```toml
[butler]
name = "general"
port = 40101
description = "Flexible catch-all assistant for freeform data"

[butler.db]
name = "butler_general"
```

### With Schedule (health)

```toml
[butler]
name = "health"
port = 40103
description = "Health tracking assistant for measurements, medications, diet, and symptoms"

[butler.db]
name = "butler_health"

[[butler.schedule]]
name = "medication-reminder-morning"
cron = "0 8 * * *"
prompt = """
Check for active medications with scheduled times between 8:00 AM and 10:00 AM.
For each medication, verify whether a dose has been logged for today covering that time window.
Report any medications that are due but not yet logged.
"""

[[butler.schedule]]
name = "weekly-health-summary"
cron = "0 9 * * 0"
prompt = """
Generate a comprehensive weekly health summary including:
- Weight trend over the past week (if weight measurements exist)
- Medication adherence rates for each active medication over the past week
- Symptom frequency and patterns over the past week
- Any notable changes or patterns identified from the data
Use the health_summary and trend_report tools to compile this data.
"""
```

### With Modules (switchboard)

```toml
[butler]
name = "switchboard"
port = 40100
description = "Routes incoming messages to specialist butlers"

[butler.db]
name = "butler_switchboard"

[modules.telegram]
mode = "polling"

[modules.email]
```

### Infrastructure (heartbeat)

```toml
[butler]
name = "heartbeat"
port = 40199
description = "System heartbeat — ticks all registered butlers on a schedule"

[butler.db]
name = "butler_heartbeat"

[[butler.schedule]]
name = "tick-all"
cron = "*/10 * * * *"
prompt = "Run tick_all_butlers to check in on every registered butler."
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
  "30 18 * * 1-5" → Weekdays at 6:30 PM
```
