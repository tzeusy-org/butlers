# Home Butler

> **Purpose:** Smart home automation orchestrator for comfort management, energy awareness, device coordination, and scene composition via Home Assistant.
> **Audience:** Contributors and operators.
> **Prerequisites:** [Concepts](../concepts/butler-lifecycle.md), [Architecture](../architecture/butler-daemon.md).

## Overview

The Home Butler transforms scattered smart home devices into a cohesive system that learns the user's preferences, adapts to behavior patterns, and keeps the home comfortable and efficient. It integrates with Home Assistant to read sensor data, control devices, activate scenes, and monitor device health -- all through natural language via Telegram.

The butler's philosophy is that smart home technology should feel invisible: responsive, adaptive, and always aligned with the user's actual needs. Not a collection of disconnected apps, but a coherent system that handles the details so the user can focus on living.

## Profile

| Property | Value |
|----------|-------|
| **Port** | 41108 |
| **Schema** | `home` |
| **Modules** | home_assistant, memory, contacts, approvals |
| **Runtime** | codex (gpt-5.4-mini) |

## Schedule

| Task | Cron | Description |
|------|------|-------------|
| `weekly-energy-digest` | `0 21 * * 0` | Weekly energy efficiency digest: device usage patterns, consumption trends, peak demand, optimization recommendations. Delivered via Telegram. |
| `environment-report` | `5 21 * * 0` | Weekly home environment report: temperature, humidity, air quality, lighting levels vs. user comfort preferences, with actionable recommendations. Delivered via Telegram. |
| `device-health-check` | `10 21 * * 0` | Weekly device health check: query all connected devices for status, battery levels, last communication, firmware updates. Always sends a summary -- alert if issues found, all-clear if healthy. |
| `memory_consolidation` | `0 */6 * * *` | Consolidate episodic memory into durable facts |
| `memory_episode_cleanup` | `5 4 * * *` | Prune expired episodic memory entries |
| `memory_purge_superseded` | `10 4 * * *` | Purge facts that have been superseded by newer data |

## Tools

**Home Assistant Integration**
- `ha_get_entity_state` -- Current state of a single entity (sensor, light, switch, climate device).
- `ha_list_entities` -- List entities filtered by domain (light, sensor, switch, climate) and/or area (bedroom, kitchen, living room).
- `ha_list_areas` -- Discover all configured rooms and areas.
- `ha_list_services` -- Discover available services by domain.
- `ha_get_history` -- State history for entities over a time window for trend analysis.
- `ha_get_statistics` -- Aggregated statistics (min, max, mean, cumulative sum, state, and per-period change) for sensor entities over configurable periods (5-minute, hourly, daily, weekly, monthly).
- `ha_render_template` -- Render Jinja2 templates server-side on the HA instance for computed values.
- `ha_call_service` -- Call a Home Assistant service through RFC 0028's physical-risk boundary. Consequential/protected calls park for owner approval; every execution attempt is receipted and must pass live post-condition verification before reporting success.

### Physical actuation receipts

`home.ha_command_log` is the authoritative actuation ledger. New rows carry a
unique attempt id, declared risk, server-derived actor and session, approval
lineage, requested and observed state, status, and rollback hint. The status is
`succeeded` only after live read-back. A definitive connection failure or HA
rejection is `failed`; post-send timeout, reset, response-parse uncertainty,
missing proof, or mismatched proof is `unverified` and shown as requiring
attention in Recent commands. An approved attempt left `attempting` by a crash
or settlement failure, or settled `unverified`, cannot be blindly retried with
the same approval; the owner reconciles the device and issues a new approved
action. A durable `succeeded` receipt can be replayed for approval bookkeeping
without another HA call. Each permitted retry creates a new attempt row.

Home also publishes the minimized `home.actuation_executed` event after a
terminal attempt. The event deliberately excludes requested/observed home state
and never supersedes the Home-owned receipt.
- `ha_activate_scene` -- Activate a Home Assistant scene with optional transition time.

**Notification and Memory**
- `notify` -- Send messages via the user's preferred channel.
- `memory_store_fact / search / recall` -- Persist and retrieve home-related facts: comfort preferences, device issues, energy baselines, usage patterns, scene preferences.

## Key Behaviors

**Comfort Management.** The butler learns temperature, lighting, humidity, and air quality preferences per room and time of day. It stores these as `stable` memory facts and continuously applies them. When conditions drift outside the user's comfort zone, it notices and acts.

**Scene Composition.** Users build complex automations through conversation: "Create a bedtime scene that cools the bedroom to 68 and dims all lights." Scenes are composable and modifiable. The butler can also schedule scenes for automatic activation.

**Energy Awareness.** The weekly energy digest analyzes device usage patterns, identifies top consumers, highlights peak demand times, and suggests optimizations. The butler tracks energy baselines per device for anomaly detection.

**Device Health Monitoring.** The weekly health check surveys all connected devices for offline status, low batteries, and available firmware updates. Issues are recorded as volatile memory facts for trend tracking.

### Home Assistant source health and cached reads

`home.ha_entity_snapshot` is a last-known-state cache, not proof that Home
Assistant is reachable. The Home module therefore maintains the single
`home.ha_source_health` row for `home_assistant`. Successful REST contact or a
WebSocket pong renews a five-minute health lease; connection, keepalive,
polling, or post-authentication setup failures mark the row `error`
immediately. PostgreSQL evaluates lease age against its own timestamp. A
missing row, an error row, or an expired healthy lease makes snapshot-backed
reads unmeasurable.

Dashboard list/count endpoints retain useful last-known rows where their
response envelope can carry `ha_source_available=false`. Missing single-item
reads, empty bare area lists, and the energy endpoints return 503 during that
state because they cannot honestly distinguish absence from an outage. The
presence producer leaves `at_home` untouched, and the Home briefing emits an
explicit high-priority unmeasurable highlight instead of a nominal all-clear.

Operators should verify both the source ledger and a reader response without
selecting the stored error text:

```bash
psql -h "$POSTGRES_HOST" -p "${POSTGRES_PORT:-5432}" \
  -U "${POSTGRES_USER:-butlers}" -d "${POSTGRES_DB:-butlers}" \
  -c "SELECT source, status, last_success_at, last_error_at, updated_at
      FROM home.ha_source_health WHERE source = 'home_assistant';"

curl -s http://localhost:41200/api/home/snapshot-status | \
  jq '{ha_source_available, newest_captured_at, total_entities}'
```

Do not treat HTTP 200 or a stored `status='healthy'` alone as recovery. The
reader response must report `ha_source_available=true`, the health timestamp
must be within the lease, and snapshot timestamps should advance after live HA
contact resumes.

**Destructive Action Confirmation.** The butler always asks for explicit confirmation before deleting scenes, disabling automations, or disarming security systems. It never automatically executes potentially destructive changes.

**Discover Before Acting.** The butler uses `ha_list_entities` and `ha_list_services` to confirm entity IDs before calling services, since Home Assistant entity IDs are case-sensitive and vary by installation.

## Interaction Patterns

**Conversational control.** Users say "Turn off the living room lights" or "What's the temperature in the bedroom?" via Telegram. The butler translates natural language into Home Assistant service calls and returns the result.

**Scene management.** Users create, modify, and trigger scenes through conversation. The butler stores scene preferences in memory and can suggest scheduling automations.

**Environmental queries.** Users ask about current conditions, energy usage, or device status and receive data-backed answers from Home Assistant sensors and statistics.

**Proactive alerts.** The butler sends weekly digests on energy, environment, and device health. It also stores and monitors comfort preferences, alerting when readings drift outside acceptable ranges.

## Memory Classification

The Home Butler uses a domain-specific memory taxonomy:

- **Room and device subjects** (bedroom, thermostat, front-door-lock) are internal identifiers that do not require entity resolution.
- **Service providers** (plumber, electrician, cleaning company) must be resolved to shared entities before storing facts.
- **Permanence**: `stable` for long-term preferences (temperature, lighting), `standard` for current patterns (scene usage, energy baselines), `volatile` for alerts and device issues (low battery, firmware updates).

## Verification

To confirm the Home Butler's Home Assistant integration, memory taxonomy, and scheduled tasks are operating as described:

```bash
# 1. Confirm the butler is listening on the expected port
curl -s http://localhost:41108/health | python3 -m json.tool
# Expected: {"status": "ok", ...}

# 2. Verify Home Assistant entity state is reachable via the butler's HA tool
# (requires a running Home Assistant instance connected to the butler)
psql -h localhost -U butlers -d butlers -c \
  "SELECT key, value FROM home.state
   WHERE key LIKE 'ha_%' OR key LIKE 'home_assistant_%'
   ORDER BY key LIMIT 5;"
# Expected: HA connection config or last-probe timestamp present

# 3. Confirm ha_call_service is registered (Home Butler owns write access, unlike Health Butler)
curl -s http://localhost:41108/sse 2>/dev/null | head -5 || \
  echo "Inspect tool list via MCP client: ha_call_service and ha_activate_scene should be present"
# Expected: ha_call_service and ha_activate_scene available -- these are absent from Health Butler

# 4. Verify scheduled tasks are seeded from butler.toml
psql -h localhost -U butlers -d butlers -c \
  "SELECT name, cron, enabled FROM home.scheduled_tasks ORDER BY name;"
# Expected: device-health-check, environment-report, memory_consolidation,
# memory_episode_cleanup, memory_purge_superseded, weekly-energy-digest all present

# 5. Confirm memory permanence taxonomy is in use (stable/standard/volatile)
psql -h localhost -U butlers -d butlers -c \
  "SELECT permanence, COUNT(*) FROM home.memory_facts
   GROUP BY permanence ORDER BY permanence;"
# Expected: entries under 'stable' (preferences), 'standard' (baselines), 'volatile' (alerts)

# 6. Verify destructive action confirmation is enforced (approvals module present)
psql -h localhost -U butlers -d butlers -c \
  "SELECT table_name FROM information_schema.tables
   WHERE table_schema = 'home' AND table_name LIKE '%approval%';"
# Expected: approvals-related table present (Home Butler uses the approvals module)
```

## Related Pages

- [Health Butler](health.md) -- has read-only access to Home Assistant sensors for health correlation
- [Switchboard Butler](switchboard.md) -- routes home automation messages here
- [Messenger Butler](messenger.md) -- delivers energy digests, environment reports, and device health alerts
