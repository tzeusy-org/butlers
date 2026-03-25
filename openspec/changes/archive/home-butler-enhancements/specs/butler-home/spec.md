# Home Butler Role — Schedule Enhancements

## MODIFIED Requirements

### Requirement: Home Butler Schedules

The home butler runs periodic monitoring and reporting jobs. Monitoring tasks use deterministic job-based dispatch to avoid LLM costs for formulaic work.

#### Scenario: Scheduled task inventory

- **WHEN** the home butler daemon is running
- **THEN** it SHALL execute:
  - `device-health-check` (0 4 * * *, job-based, job_name=`device_health_check`): read entity states from connector-populated `ha_entity_snapshot`, classify offline status and low battery using configurable thresholds from state store (`home:thresholds:battery`, `home:thresholds:offline_hours`), store findings in memory, and notify the owner via Telegram
  - `environment-report` (0 8 * * *, job-based, job_name=`environment_report`): read environmental sensors per area from `ha_entity_snapshot`, compare against stored comfort preferences with configurable deviation thresholds from state store (`home:thresholds:comfort_defaults`, `home:thresholds:comfort_deviation`), and send a room-by-room report via Telegram
  - `weekly-energy-digest` (0 9 * * 0, job-based, job_name=`energy_digest`): discover energy sensors from `ha_entity_snapshot`, fetch weekly historical statistics via HA REST API (`recorder/get_statistics_during_period`), compute top consumers and trends vs. baselines using configurable anomaly thresholds from state store (`home:thresholds:energy`), and send a structured digest via Telegram
  - `maintenance-schedule-check` (0 10 * * 1, job-based, job_name=`maintenance_schedule_check`): check all maintenance items for due/overdue status and send reminders via Telegram
  - `memory-consolidation` (0 */6 * * *, job-based, job_name=`memory_consolidation`)
  - `memory-episode-cleanup` (5 4 * * *, job-based, job_name=`memory_episode_cleanup`)
  - `memory-purge-superseded` (10 4 * * *, job-based, job_name=`memory_purge_superseded`)

## ADDED Requirements

### Requirement: Home Butler Maintenance Tools

The home butler provides MCP tools for managing recurring maintenance items.

#### Scenario: Maintenance tool inventory

- **WHEN** a runtime instance is spawned for the home butler
- **THEN** it SHALL have access to: `ha_maintenance_create`, `ha_maintenance_complete`, `ha_maintenance_list`, `ha_maintenance_remove` in addition to existing HA tools, memory tools, and contact tools
