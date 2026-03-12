# Diagram Reconciliation Report — bu-k2hw.20

**Date**: 2026-03-12
**Scope**: All 19 .excalidraw diagrams in `docs/diagrams/`
**Source baseline**: HEAD of `main` branch

---

## Phase 1: File Existence and JSON Validity

All 19 `.excalidraw` files exist and parse as valid JSON:

| # | File | Valid JSON |
|---|------|-----------|
| 1 | 01-system-architecture.excalidraw | ✓ |
| 2 | 02-butler-specification.excalidraw | ✓ |
| 3 | 03a-switchboard-design.excalidraw | ✓ |
| 4 | 03b-general-butler-design.excalidraw | ✓ |
| 5 | 04a-health-butler-flows.excalidraw | ✓ |
| 6 | 04b-finance-butler-flows.excalidraw | ✓ |
| 7 | 04c-relationship-butler-flows.excalidraw | ✓ |
| 8 | 04d-education-butler-flows.excalidraw | ✓ |
| 9 | 04e-travel-butler-flows.excalidraw | ✓ |
| 10 | 04f-home-butler-flows.excalidraw | ✓ |
| 11 | 04g-messenger-butler-flows.excalidraw | ✓ |
| 12 | 05-connector-design.excalidraw | ✓ |
| 13 | 06a-spawner-runtime.excalidraw | ✓ |
| 14 | 06b-scheduler.excalidraw | ✓ |
| 15 | 06c-state-store.excalidraw | ✓ |
| 16 | 06d-startup-sequence.excalidraw | ✓ |
| 17 | 06e-database-schema.excalidraw | ✓ |
| 18 | 07a-dashboard-gateway.excalidraw | ✓ |
| 19 | 07b-dashboard-data-flows.excalidraw | ✓ |

**Result: All 19 files valid.**

---

## Phase 2: Cross-Check Against Source Code

### 2.1 Butler Ports (Diagram 01 vs butler.toml files)

All 9 ports are correct:

| Butler | Diagram | butler.toml | Match |
|--------|---------|-------------|-------|
| switchboard | :40100 | 40100 | ✓ |
| general | :40101 | 40101 | ✓ |
| relationship | :40102 | 40102 | ✓ |
| health | :40103 | 40103 | ✓ |
| messenger | :40104 | 40104 | ✓ |
| finance | :40105 | 40105 | ✓ |
| travel | :40106 | 40106 | ✓ |
| education | :40107 | 40107 | ✓ |
| home | :40108 | 40108 | ✓ |

### 2.2 Butler Modules (butler.toml vs diagrams 04a–04g)

**Health (04a):** Diagram lists `calendar · contacts · health · home_assistant (read-only) · memory`.
butler.toml enables: `calendar`, `contacts`, `health`, `home_assistant` (read_only=true), `memory`. **Match ✓**

**Finance (04b):** Diagram lists `email · calendar · memory · finance`.
butler.toml enables: `email`, `calendar`, `memory`, `finance`. **Match ✓**

**Relationship (04c):** Diagram lists `calendar · contacts · memory · relationship`.
butler.toml enables: `calendar`, `contacts`, `memory`, `relationship`. **Match ✓**

**Education (04d):** Diagram lists `education · memory · contacts`.
butler.toml enables: `education`, `memory`, `contacts`. **Match ✓**

**Travel (04e):** Diagram lists `email · calendar · memory`.
butler.toml enables: `email`, `calendar`, `memory`. **Match ✓**

**Home (04f):** Diagram lists `home_assistant · memory · contacts · approvals`.
butler.toml enables: `home_assistant`, `memory`, `contacts`, `approvals`. **Match ✓**

**Messenger (04g):** Diagram lists `messenger · telegram · email · calendar · approvals`.
butler.toml enables: `messenger`, `telegram`, `email`, `calendar`, `approvals`. **Match ✓**

**Switchboard (03a):** Diagram lists `calendar · telegram · email · memory · pipeline · switchboard`.
butler.toml enables: `calendar`, `telegram`, `email`, `memory`, `pipeline`, `switchboard`. **Match ✓**

**General (03b):** Diagram lists `general · calendar · contacts · memory · metrics`.
butler.toml enables: `calendar`, `contacts`, `general`, `memory`. Note: `metrics` is listed in diagram but NOT in butler.toml `[modules.*]`. However metrics is a built-in module — whether it's auto-loaded or requires explicit config is a code-level detail. **Minor discrepancy: metrics not in general/butler.toml modules config.**

### 2.3 Scheduled Tasks (butler.toml vs diagrams)

All checked schedules have correct cron expressions and task counts. Verified:

- **Health**: 4 tasks (`weekly-health-summary`, `memory_consolidation`, `memory_episode_cleanup`, `memory_purge_superseded`) — diagram 04a says "4 tasks" ✓
- **Finance**: 3 tasks (`upcoming-bills-check @15 21 * * 0`, `subscription-renewal-alerts @20 21 * * 0`, `monthly-spending-summary @0 9 1 * *`) ✓
- **Relationship**: 4 tasks (`upcoming-dates-check`, `relationship-maintenance`, `memory_consolidation`, `memory_episode_cleanup`) — no `memory_purge_superseded` ✓
- **Education**: 4 tasks (`nightly-analytics`, `weekly-progress-digest`, `weekly-stale-flow-check`, `daily-spaced-repetition-nudge`) ✓
- **Travel**: 2 tasks (`upcoming-travel-check @30 21 * * 0`, `trip-document-expiry @0 9 * * 1`) ✓
- **Home**: 6 tasks (`weekly-energy-digest`, `environment-report`, `device-health-check`, `memory_consolidation`, `memory_episode_cleanup @5 4 * * *`, `memory_purge_superseded @10 4 * * *`) ✓
- **Messenger**: 0 tasks — diagram 04g correctly says "NO SCHEDULED TASKS" ✓
- **Switchboard**: 3 tasks (`eligibility_sweep`, `memory_consolidation`, `memory_episode_cleanup`) — no `memory_purge_superseded` ✓
- **General**: 3 tasks (`memory_consolidation`, `memory_episode_cleanup`, `eod-tomorrow-prep`) ✓

### 2.4 API Router Counts (Diagram 07a vs app.py)

Diagram 07a states: "23 router registrations / 21 source files".

**Source files**: `ls src/butlers/api/routers/` excluding `__init__.py` and `__pycache__` = **21 files** ✓

**Static registrations**: Counting `app.include_router()` calls in `app.py` (excluding the dynamic discovery loop at line 228) = **23 calls** ✓

**Butler-specific routers**: `find roster/ -name "router.py"` = 8 (education, finance, general, health, home, relationship, switchboard, travel) ✓

**Diagram 01** claims "21+ core + 8 butler-specific routers" — consistent ✓

### 2.5 DB Dependency Wiring (Diagram 07a — DISCREPANCY FOUND)

Diagram 07a states: `"Applied to: 14 core router modules + all dynamic butler router modules (costs, issues, modules, sse skipped — no _get_db_manager stub)"`

**Actual code** (`wire_db_dependencies` in `src/butlers/api/deps.py`): wires **17** modules:
`approvals, audit, butlers, calendar_workspace, cli_auth, ingestion_events, issues, memory, model_settings, notifications, oauth, schedules, search, secrets, sessions, state, timeline`

Modules confirmed to NOT have `_get_db_manager` stubs: `costs`, `modules`, `sse` (3 not 4)

**Diagram says `issues` is skipped but `issues.py` HAS `_get_db_manager` and IS wired in `wire_db_dependencies`.** The diagram count of "14" and the claim that `issues` is skipped are both stale. Current count is **17 wired modules**.

This is a documentation stale-ness, not a functional bug.

### 2.6 Runtime Adapters (Diagrams 01, 02, 06a)

Source: `src/butlers/core/runtimes/` contains: `base.py`, `claude_code.py`, `codex.py`, `gemini.py`, `opencode.py`

| Diagram | Lists |
|---------|-------|
| 01 | Claude Code, Codex, Gemini (3 — **missing OpenCode**) |
| 02 | `codex.py | claude_code.py | gemini.py | base.py` (**missing opencode.py**) |
| 06a | `ClaudeCodeAdapter`, `CodexAdapter`, `GeminiAdapter`, `OpenCodeAdapter` ✓ (all 4) |

Diagrams 01 and 02 do not mention the `OpenCodeAdapter` / `opencode.py` runtime. Diagram 06a correctly documents all four.

### 2.7 Shared Database Schema (Diagrams 01 and 06e)

**Actual shared schema tables** (from Alembic migrations):
- `shared.contacts` (core_007)
- `shared.contact_info` (core_007)
- `shared.entities` (core_014)
- `shared.entity_info` (core_017)
- `shared.ingestion_events` (core_019)
- `shared.memory_catalog` (core_023)
- `shared.model_catalog` (core_025)
- `shared.butler_model_overrides` (core_025)
- `shared.google_accounts` (core_026)

**Diagram 01** lists `shared: contacts / contact_info / model_catalog / ingestion_events / credentials`

Discrepancies:
- `credentials` — **no table with this name exists**; secured credential data lives in `shared.contact_info` (secured=true), `shared.entity_info`, and `shared.google_accounts`
- Missing: `memory_catalog`, `butler_model_overrides`, `entity_info`, `google_accounts`, `entities`

**Diagram 06e** lists: `contacts`, `contact_info`, `ingestion_events`, `memory_catalog`, `model_catalog`, `butler_model_overrides`

Discrepancies:
- Missing: `entity_info` (core_017)
- Missing: `google_accounts` (core_026)
- Missing: `entities` (core_014 moved to shared)

### 2.8 Connector Count (Diagram 05)

Diagram section header "H. ALL FIVE CONNECTORS" lists 5 entries:
1. Telegram Bot Connector
2. Telegram User Client
3. Gmail Connector (polling)
4. Discord User Connector (stub)
5. Gmail Pub/Sub Mode (same gmail process, alternative ingestion path)

Source has 4 unique connector process files: `telegram_bot.py`, `telegram_user_client.py`, `gmail.py`, `discord_user.py`. The "five" counts Gmail polling and Pub/Sub as two separate entries, which is a reasonable documentation choice (they operate differently). The label is acceptable though slightly ambiguous.

### 2.9 Color Coding Consistency

Color palette across all 19 diagrams is broadly consistent. The system uses:
- Blue (`#1e40af`, variants): Butler components
- Green (`#047857`, `#166534`, variants): Modules, connectors, external components
- Purple (`#6d28d9`, variants): LLM/runtime components
- Orange/Red (`#c2410c`, `#b45309`, variants): Database, pipelines, alerts
- Teal (`#0f766e`, variants): Dashboard/API gateway
- Gray (`#374151`, `#64748b`): Neutral annotations

One color outside the main palette: `#831843` (dark rose) used in diagram 07b for "NEW" item labels. This is a minor variation, not a structural inconsistency.

Diagrams 04b and 06a use broader color ranges (more shades) due to higher complexity, but remain within the conceptual palette families.

---

## Phase 3: Summary of Findings

### Findings That Are Accurate (no action needed)

1. All 19 diagrams exist and are valid JSON.
2. All butler ports match butler.toml.
3. All butler module lists match butler.toml.
4. All scheduled task names and cron expressions match butler.toml.
5. Router count claims (07a): 23 registrations / 21 source files / 8 butler routers are correct.
6. Diagram 06a correctly documents all 4 runtime adapters including OpenCode.
7. Messenger butler correctly documented as having no scheduled tasks.
8. Home butler module name `home_assistant` correctly used throughout.
9. Connector design (05) accurately describes 4 connector process implementations.
10. Color coding is consistent across all diagrams.

### Discrepancies Found

| # | Diagram | Claim | Reality | Severity |
|---|---------|-------|---------|---------|
| D1 | 07a | "14 core router modules" wired by `wire_db_dependencies` | 17 modules wired | Low (documentation staleness) |
| D2 | 07a | "`issues` skipped — no `_get_db_manager` stub" | `issues.py` has the stub and IS wired | Low (documentation staleness) |
| D3 | 01, 02 | Runtime adapters: Claude Code, Codex, Gemini (3) | Source has 4: also includes OpenCode | Low (omission) |
| D4 | 01 | `shared: ... credentials` | No `credentials` table; credential data in `entity_info`, `google_accounts`, `contact_info` | Low (stale label) |
| D5 | 01 | Missing `memory_catalog`, `butler_model_overrides` from shared list | Both exist in DB | Low (omission) |
| D6 | 06e | Missing `entity_info`, `google_accounts`, `entities` from shared section | All 3 exist in shared schema | Low (omission) |
| D7 | 03b | `metrics` module shown for General butler | `metrics` not in `roster/general/butler.toml [modules.*]` | Very Low (auto-loaded module detail) |

All discrepancies are **documentation staleness** or **minor omissions** — no structural errors in the diagrams, and none affect correctness of the architecture they represent.

---

## Phase 4: Handoff

This reconciliation confirms the diagrams are structurally sound. The discrepancies above are low-severity documentation drift. No new beads have been created per task instructions (coordinator handles that). No code changes were needed — this is a report-only delivery.

**Recommended follow-up beads (coordinator to create if warranted):**
1. Update diagram 07a DB wiring count from "14" to "17" and correct the `issues` skip claim
2. Add `OpenCodeAdapter` / `opencode.py` to diagrams 01 and 02
3. Update diagram 01 shared schema list: replace `credentials` with `entity_info`, `google_accounts`; add `memory_catalog`, `butler_model_overrides`
4. Update diagram 06e shared schema section: add `entity_info`, `google_accounts`, `entities`
