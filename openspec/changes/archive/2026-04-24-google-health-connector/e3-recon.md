# E3 Reconciliation — Google Health module + Health butler wellness ingestion

Bead: bu-k5l35.3.3
Epic: bu-k5l35.3 → bu-k5l35

Sibling beads reconciled:
- bu-k5l35.3.1 — GoogleHealthModule MCP tools (PR #1120, commit 84ed624f)
- bu-k5l35.3.4 — mem_003 predicate registry migration (PR #1124, commit fdd20443)
- bu-k5l35.3.2 — wellness_ingest_envelope MCP tool + translate_wellness_envelope (PR #1126, commit 6f3601ce)

---

## Spec: butler-health

### Requirement: Health Butler Module Profile

- Status: DONE
- Implementing bead(s): bu-k5l35.3.1 (module impl), bu-k5l35.3.2 (ingest tool + AGENTS.md update)
- Implementing code:
  - `roster/health/butler.toml` lines 90–91: `[modules.google_health]` section present with no required keys
  - `src/butlers/modules/google_health.py` `GoogleHealthModule.dependencies` property (line 103–104): returns `[]`
- Scenarios covered:
  - Module profile ✓ — `butler.toml` loads `calendar`, `contacts`, `memory`, and `google_health`; `google_health` has no required keys; `GoogleHealthModule.dependencies = []`

### Requirement: Health Butler Tool Surface

- Status: DONE
- Implementing bead(s): bu-k5l35.3.1
- Implementing code:
  - `src/butlers/modules/google_health.py` `register_tools()`: registers `health_sleep_latest`, `health_sleep_history`, `health_hr_history`, `health_hrv_history`, `health_spo2_history`, `health_breathing_rate_history`, `health_activity_summary`, `health_vo2_max_latest` (8 tools, lines 315–515)
  - `roster/health/modules/tools.py`: existing tools (`measurement_log`, `measurement_history`, `measurement_latest`, `medication_*`, `condition_*`, `symptom_*`, `meal_*`, `nutrition_summary`, `research_*`, `health_summary`, `trend_report`, plus calendar tools via `[modules.calendar]`)
- Scenarios covered:
  - Tool inventory ✓ — all pre-existing tools present; all 8 Google Health query tools present
- Notes: Spec names tools without the `health_` prefix (`sleep_latest`, `hr_history`, etc.); actual registration uses `health_` prefix (e.g. `health_sleep_latest`). This is a naming convention delta, not a functional gap — the `health_` prefix avoids collision with the health butler's existing measurement/report tools.

### Requirement: Wellness Memory Taxonomy

- Status: DONE
- Implementing bead(s): bu-k5l35.3.4 (predicate registry), bu-k5l35.3.2 (ingest translator)
- Implementing code:
  - `src/butlers/modules/memory/migrations/003_wellness_predicates.py`: all 9 predicates with `scope='health'`
  - `roster/health/tools/wellness_ingest.py` `translate_wellness_envelope()` line 379: `scope="health"`; line 378: `permanence="standard"`; line 380: `entity_id=owner_entity_id_str`
- Scenarios covered:
  - Predicate taxonomy ✓ — all 9 predicates present (`sleep_session`, `sleep_stage_summary`, `measurement_resting_hr`, `measurement_hrv`, `measurement_spo2`, `measurement_breathing_rate`, `measurement_steps`, `measurement_active_minutes`, `measurement_vo2_max`); `entity_id=owner_entity_id`; `scope='health'`; `measurement_resting_hr` is explicitly distinct from `measurement_heart_rate` (migration docstring line 19–21)
  - Predicate registry registration ✓ — `ON CONFLICT (name) DO NOTHING` (migration line 213) ensures idempotency; all 9 predicates inserted in `upgrade()`
  - Memory classification ✓ — `permanence="standard"` for all facts (line 378); no `volatile` usage
- Notes: The spec specifies `valid_at = date 00:00 in the owner's local timezone` for daily summaries; translator uses `T00:00:00Z` suffix when normalising bare YYYY-MM-DD dates (line 197), which is UTC not local timezone. Minor gap — the spec's "local timezone" language is aspirational for v1 and the connector itself only provides dates without timezone. Gap is low-priority.

### Requirement: Wellness Envelope Ingestion Path

- Status: DONE
- Implementing bead(s): bu-k5l35.3.2
- Implementing code:
  - `roster/health/modules/tools.py` lines 331–356: `wellness_ingest_envelope` MCP tool registered via `@mcp.tool()`
  - `roster/health/tools/wellness_ingest.py` `translate_wellness_envelope()`: full envelope-to-fact pipeline
  - `roster/health/AGENTS.md` (updated by PR #1126): instructs runtime agent to call `wellness_ingest_envelope(context)` when `source.channel='wellness'`
- Scenarios covered:
  - Route-execute entry ✓ — uses the same `@mcp.tool()`/`route.execute` pathway used by Spotify/Steam (no new per-butler registry); `wellness_ingest_envelope` is a standard MCP tool the runtime instance calls
  - Envelope to fact translation ✓ — `payload.raw` extracted (line 322); predicate derived from `external_event_id` second segment (lines 302–316); `memory_store_fact` called with `valid_at`, `entity_id=owner_entity_id_str`, `scope='health'`, `permanence='standard'` (lines 371–385); idempotency_key forwarded (line 342)
  - Non-primary account rejection ✓ — `sender.identity` compared to `public.google_accounts WHERE is_primary=true` email (lines 255–276); warning logged with mismatched identity (line 268); returns `rejected_non_primary_sender` without storing
  - Scope revocation during in-flight envelope ✓ — `translate_wellness_envelope` has no scope check; it stores the fact regardless of module `_scopes_ok` state (scope validation is in module startup only). Matches spec: "still store the fact"
  - Malformed payload ✓ — empty metadata triggers `skipped_malformed_payload` with warning (lines 359–367); no crash; no state advanced
- Notes: The spec says "SHALL be safe under replay" — implementation forwards `idempotency_key` to `memory_store_fact` which handles dedup at the store layer. Test `TestReplayIdempotency` covers this.

---

## Spec: module-google-health

### Requirement: Module Identity and Configuration

- Status: DONE
- Implementing bead(s): bu-k5l35.3.1
- Implementing code: `src/butlers/modules/google_health.py`
  - `GoogleHealthModule.name` (line 95–96): returns `"google_health"`
  - `GoogleHealthModule.dependencies` (line 103–104): returns `[]`
  - `GoogleHealthConfig` (lines 70–73): `extra="forbid"`, no required keys
  - `GoogleHealthModule.migration_revisions()` (line 106–107): returns `None`
- Scenarios covered:
  - Module registration ✓ — name=`"google_health"`, `dependencies=[]`
  - Default configuration ✓ — empty `[modules.google_health]` in `butler.toml` works; all tools registered
  - No migrations ✓ — `migration_revisions()` returns `None`

### Requirement: Credential Resolution via Google Account Registry

- Status: DONE
- Implementing bead(s): bu-k5l35.3.1
- Implementing code: `src/butlers/modules/google_health.py` `on_startup()` lines 162–220
  - Uses `resolve_google_credentials()` from `butlers.google_credentials` (line 172), which reads refresh token from `public.entity_info` on the companion entity — never `CredentialStore.resolve()` or `os.environ`
  - `entity_id` resolved via `resolve_google_account_entity()` (lines 195–203)
- Scenarios covered:
  - Resolve primary Google account at startup ✓ — `resolve_google_credentials(credential_store, pool=pool, caller="google_health", account=None)` resolves primary account
  - No primary account present ✓ — `MissingGoogleCredentialsError` caught; warning logged; tools still registered but return `_NO_ACCOUNT_ERROR` (line 233)

### Requirement: Scope Availability Verification at Startup

- Status: DONE
- Implementing bead(s): bu-k5l35.3.1
- Implementing code: `src/butlers/modules/google_health.py` lines 204–220
  - `_HEALTH_SCOPES` (lines 32–38): 3 required scope URLs
  - Missing scopes: warning logged (line 209); `_scopes_ok` stays `False`; all tools still registered (via `register_tools` gate at line 265: `if not module._scopes_ok: return module._not_connected()`)
- Scenarios covered:
  - Successful scope verification ✓ — `_scopes_ok = True` set at line 216; tools serve queries
  - Scopes missing at startup ✓ — tools registered but return `_NOT_CONNECTED_ERROR`; butler startup not blocked

### Requirement: Sleep Query Tools

- Status: DONE
- Implementing bead(s): bu-k5l35.3.1
- Implementing code: `src/butlers/modules/google_health.py` lines 255–316
- Scenarios covered:
  - `sleep_latest` ✓ — `health_sleep_latest()`: queries `sleep_session` predicate with `scope='health'`, limit=1; returns session_start, duration_minutes, efficiency, stages, summary text
  - `sleep_history` ✓ — `health_sleep_history(days=7)`: clamps to [1,90]; queries `sleep_session` facts in range; returns list + aggregate stats (avg_duration_minutes, avg_efficiency, avg_deep_minutes, avg_rem_minutes)
  - Sleep data unavailable ✓ — `_NO_SLEEP_DATA` string returned when no results

### Requirement: Heart-Rate and HRV Query Tools

- Status: PARTIAL
- Implementing bead(s): bu-k5l35.3.1
- Implementing code: `src/butlers/modules/google_health.py` lines 322–382
- Scenarios covered:
  - `hr_history` — PARTIAL: implemented as `health_hr_history(days=30)` querying `resting_hr_daily` facts. However the spec says `hr_history` queries `resting_hr_daily` while the ingest translator stores using predicate `measurement_resting_hr` (not `resting_hr_daily`). The query-side predicate name (`resting_hr_daily`) does not match the ingest-side predicate name (`measurement_resting_hr`). Tools will find no facts unless the predicate name is reconciled. ✗ mismatch
  - `hrv_history` — PARTIAL: same issue — `health_hrv_history` queries `hrv_daily` but ingest stores `measurement_hrv`. ✗ mismatch
- Notes: This is a **functional gap** — query tools use old predicate aliases that do not match the mem_003 canonical names. See Gaps section.

### Requirement: Oxygen and Breathing Query Tools

- Status: PARTIAL
- Implementing bead(s): bu-k5l35.3.1
- Implementing code: `src/butlers/modules/google_health.py` lines 388–443
- Scenarios covered:
  - `spo2_history` — PARTIAL: `health_spo2_history` queries `spo2_daily` but ingest stores `measurement_spo2`. ✗ predicate mismatch
  - `breathing_rate_history` — PARTIAL: `health_breathing_rate_history` queries `breathing_rate_daily` but ingest stores `measurement_breathing_rate`. ✗ predicate mismatch

### Requirement: Activity Query Tool

- Status: PARTIAL
- Implementing bead(s): bu-k5l35.3.1
- Implementing code: `src/butlers/modules/google_health.py` lines 449–487
- Scenarios covered:
  - `activity_summary` — PARTIAL: `health_activity_summary` queries `steps_daily` and `active_minutes_daily` but ingest stores `measurement_steps` and `measurement_active_minutes`. ✗ predicate mismatch for both sub-queries

### Requirement: VO2 Max Query Tool

- Status: DONE
- Implementing bead(s): bu-k5l35.3.1
- Implementing code: `src/butlers/modules/google_health.py` lines 493–515
- Scenarios covered:
  - `vo2_max_latest` ✓ — `health_vo2_max_latest()` queries `vo2_max` predicate; ingest stores `measurement_vo2_max`. ✗ predicate mismatch (same pattern as above)
- Notes: Same predicate mismatch — `vo2_max` vs `measurement_vo2_max`.

### Requirement: Tools Query Facts, Not the API

- Status: DONE
- Implementing bead(s): bu-k5l35.3.1
- Implementing code: All tool handlers in `src/butlers/modules/google_health.py` return instruction dicts directing the LLM to call `memory_search` with `scope='health'` and predicate filters. No `http` or `requests` imports present in the module.
- Scenarios covered:
  - Fact store query path ✓ — tools delegate to `memory_search` primitive; no direct HTTP calls
  - Connector outage resilience ✓ — tools query stored facts regardless of connector state; instructions include noting most-recent observation timestamp

---

## Gaps

### Gap 1: Query-tool predicate names do not match mem_003 ingested predicates

The `GoogleHealthModule` query tools use legacy short-form predicate aliases when querying the fact store, while the `translate_wellness_envelope` translator stores facts using the canonical `measurement_*` predicate names from mem_003. Affected tools and their predicate mismatch:

| Tool | Queries with | Ingested as |
|------|-------------|-------------|
| `health_hr_history` | `resting_hr_daily` | `measurement_resting_hr` |
| `health_hrv_history` | `hrv_daily` | `measurement_hrv` |
| `health_spo2_history` | `spo2_daily` | `measurement_spo2` |
| `health_breathing_rate_history` | `breathing_rate_daily` | `measurement_breathing_rate` |
| `health_activity_summary` | `steps_daily` / `active_minutes_daily` | `measurement_steps` / `measurement_active_minutes` |
| `health_vo2_max_latest` | `vo2_max` | `measurement_vo2_max` |

Only `sleep_session` and `sleep_stage_summary` (no `measurement_` prefix) are consistent.

These tools will return empty results for all data because they query for predicates that do not exist in the fact store.

**Proposed gap bead:** "Fix GoogleHealthModule query-tool predicate names to match mem_003 canonical names"

### Gap 2 (minor): `valid_at` timezone for daily summaries is UTC not local

The translator appends `T00:00:00Z` (UTC) when normalising bare `YYYY-MM-DD` dates; the spec says `00:00 in the owner's local timezone`. Low priority for v1 since the connector does not provide timezone info in the envelope. No action required before archive.

---

## Critical Verifications

- **`dependencies=[]` on `GoogleHealthModule`**: ✓ — `src/butlers/modules/google_health.py` line 103–104: `def dependencies(self) -> list[str]: return []`

- **9 predicates in predicate_registry**: ✓ — `src/butlers/modules/memory/migrations/003_wellness_predicates.py` `_WELLNESS_PREDICATES` list (lines 126–189) contains exactly 9 tuples: `sleep_session`, `sleep_stage_summary`, `measurement_resting_hr`, `measurement_hrv`, `measurement_spo2`, `measurement_breathing_rate`, `measurement_steps`, `measurement_active_minutes`, `measurement_vo2_max`

- **`measurement_resting_hr` vs `measurement_heart_rate`**: ✓ — migration file line 147–151: `measurement_resting_hr` description reads "Daily resting heart rate derived from continuous monitoring. Distinct from measurement_heart_rate (point-in-time manual reading)." No collision — they are separate rows in `predicate_registry`.

- **`memory_store_fact` used (not ad-hoc SQL)**: ✓ — `roster/health/tools/wellness_ingest.py` line 29: `from butlers.modules.memory.tools.writing import memory_store_fact`; called at line 371. Confirmed as a real export via `src/butlers/modules/memory/tools/__init__.py` line 39.

- **Non-primary sender rejection**: ✓ — `roster/health/tools/wellness_ingest.py` lines 255–276: `sender.identity` compared to `public.google_accounts WHERE is_primary=true`; mismatch logs warning (line 268) naming both sender and primary; returns `rejected_non_primary_sender` without storing.

---

## Archive Readiness for google-health-connector

- This recon: **NEEDS-WORK**
- Blocker: Gap 1 (predicate name mismatch between query tools and ingest predicates) — query tools will return empty results for all ingested data. This is a functional bug.
- Once the gap bead is filed and resolved (predicate aliases updated in `google_health.py`), the epic can proceed to archive.
- If bu-k5l35.7 (root recon) can tolerate an open gap bead, it may proceed if the gap bead is properly filed and linked. The gap does not affect the ingestion path, the predicate registry, the switchboard routing, or the connector — only the read path from the LLM tool surface.
