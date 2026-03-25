## 1. Database Migration

- [ ] 1.1 Create Alembic migration that adds `general.v_briefing_contributions` SQL view unioning `butler, key, value` from `health.state`, `finance.state`, `relationship.state`, `travel.state`, `education.state`, `home.state` filtered to `key LIKE 'briefing/daily/%'`, with an explicit `butler` column as a string literal per UNION term (e.g., `SELECT 'health' AS butler, key, value FROM health.state WHERE ...`)
- [ ] 1.2 Add SELECT grants on each specialist schema's `state` table to the General butler's database role within the same migration
- [ ] 1.3 Add reversible downgrade that drops the view and revokes grants
- [ ] 1.4 Test migration upgrade/downgrade against a local database

## 2. Contribution Schema and Shared Utilities

- [ ] 2.1 Create `src/butlers/jobs/briefing.py` with the contribution envelope dataclass/TypedDict (`butler`, `date`, `has_updates`, `highlights`, `summary`) and validation helper
- [ ] 2.2 Add shared helper for contribution state key generation (`briefing/daily/<YYYY-MM-DD>` using SGT timezone) and cleanup of entries older than 7 days
- [ ] 2.3 Write unit tests for the contribution schema validation and key generation helpers

## 3. Specialist Contribution Jobs

- [ ] 3.1 Implement Health butler `daily_briefing_contribution` job: query medication adherence, missed doses, latest weight, next appointment; write contribution to state store
- [ ] 3.2 Implement Finance butler `daily_briefing_contribution` job: query bills due in 48h, spending anomalies (2x rolling average), subscription renewals this week
- [ ] 3.3 Implement Relationship butler `daily_briefing_contribution` job: query birthdays in 7 days, follow-ups due/overdue, interaction gaps exceeding threshold
- [ ] 3.4 Implement Travel butler `daily_briefing_contribution` job: query departures in 48h, check-in windows opening today, missing travel documents
- [ ] 3.5 Implement Education butler `daily_briefing_contribution` job: query pending review count, streak status (at-risk if >= 3 day streak and no review today), current topic
- [ ] 3.6 Implement Home butler `daily_briefing_contribution` job: query active device alerts, environment sensor outliers, energy consumption anomalies
- [ ] 3.7 Write unit tests for each specialist contribution job (mock domain table queries, verify contribution envelope structure)

## 4. Job Registration

- [ ] 4.1 Register `daily_briefing_contribution` job handler in `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY` for each specialist butler (health, finance, relationship, travel, education, home) in `daemon.py`
- [ ] 4.2 Register `collect_briefing_contributions` job handler in `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY` for `general` butler in `daemon.py`
- [ ] 4.3 Write test verifying all 7 new job handlers are resolvable via `_resolve_deterministic_schedule_job_name`

## 5. Aggregation Job

- [ ] 5.1 Implement `collect_briefing_contributions` job in `src/butlers/jobs/briefing.py`: query `general.v_briefing_contributions` view for today's date, validate each contribution, assemble combined payload with `contributions` and `missing_butlers` fields, write to `briefing/combined/<date>`
- [ ] 5.2 Write unit tests for aggregation: all specialists present, partial contributions, no contributions, malformed contribution handling

## 6. Butler TOML Schedule Entries

- [ ] 6.1 Add `[[butler.schedule]]` entry for `daily_briefing_contribution` (cron `55 6 * * *`, dispatch_mode `job`, job_name `daily_briefing_contribution`) to `roster/health/butler.toml`
- [ ] 6.2 Add same schedule entry to `roster/finance/butler.toml`
- [ ] 6.3 Add same schedule entry to `roster/relationship/butler.toml`
- [ ] 6.4 Add same schedule entry to `roster/travel/butler.toml`
- [ ] 6.5 Add same schedule entry to `roster/education/butler.toml`
- [ ] 6.6 Add same schedule entry to `roster/home/butler.toml`
- [ ] 6.7 Add `[[butler.schedule]]` entry for `collect_briefing_contributions` (cron `58 6 * * *`, dispatch_mode `job`, job_name `collect_briefing_contributions`) to `roster/general/butler.toml`

## 7. EOD Prompt Update

- [ ] 7.1 Update General's `eod-tomorrow-prep` prompt in `roster/general/butler.toml` to include step: read `state_get('briefing/combined/<today-SGT>')` and incorporate specialist highlights into the briefing
- [ ] 7.2 Update the prompt to use the multi-domain message format (calendar timeline + Today's Highlights sections + optional Heads-up section) with <500 word target
- [ ] 7.3 Ensure graceful degradation: prompt instructions must handle the case where combined briefing state is absent (fall back to calendar-only)

## 8. Spec Updates

- [ ] 8.1 Archive the delta specs into the main spec tree via `openspec archive` after implementation is complete
