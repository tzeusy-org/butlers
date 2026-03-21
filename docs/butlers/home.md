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
| **Runtime** | codex (gpt-5.1) |

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
- `ha_get_statistics` -- Aggregated statistics (min, max, mean, sum) for sensor entities over configurable periods (5-minute, hourly, daily, weekly, monthly).
- `ha_render_template` -- Render Jinja2 templates server-side on the HA instance for computed values.
- `ha_call_service` -- Call any Home Assistant service: device control, automation triggers, scene creation. Takes domain, service, optional target (entity/area/device), and optional service-specific data.
- `ha_activate_scene` -- Activate a Home Assistant scene with optional transition time.

**Notification and Memory**
- `notify` -- Send messages via the user's preferred channel.
- `memory_store_fact / search / recall` -- Persist and retrieve home-related facts: comfort preferences, device issues, energy baselines, usage patterns, scene preferences.

## Key Behaviors

**Comfort Management.** The butler learns temperature, lighting, humidity, and air quality preferences per room and time of day. It stores these as `stable` memory facts and continuously applies them. When conditions drift outside the user's comfort zone, it notices and acts.

**Scene Composition.** Users build complex automations through conversation: "Create a bedtime scene that cools the bedroom to 68 and dims all lights." Scenes are composable and modifiable. The butler can also schedule scenes for automatic activation.

**Energy Awareness.** The weekly energy digest analyzes device usage patterns, identifies top consumers, highlights peak demand times, and suggests optimizations. The butler tracks energy baselines per device for anomaly detection.

**Device Health Monitoring.** The weekly health check surveys all connected devices for offline status, low batteries, and available firmware updates. Issues are recorded as volatile memory facts for trend tracking.

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

## Related Pages

- [Health Butler](health.md) -- has read-only access to Home Assistant sensors for health correlation
- [Switchboard Butler](switchboard.md) -- routes home automation messages here
- [Messenger Butler](messenger.md) -- delivers energy digests, environment reports, and device health alerts
