# Deterministic job allocation on the reviewed base

The source snapshot is `d0574c729b6dbff776b2d076d8ca3e3354f37b78`.
The registry builder in `src/butlers/scheduled_jobs.py` resolves 209
daemon/name bindings across 12 owners, including registered jobs without a
TOML schedule. All 103 job-mode rows in the 12 roster TOML files resolve
within that daemon's map. This is a static source inventory. Live schedule
rows, dynamic deadlines and operational overrides keep their stored state
and are verified by bu-dss7he.10 before the old map is removed.

## Ownership of each current binding

The proposed owner follows the currently active handler, module and pool.
The roster lists below are the remaining distinct bindings after factoring
out Memory, QA and the retained core families. A roster wrapper can keep
calling its current module helper when the scheduling decision belongs to
that roster. Registration and authority stay tied to the daemon and preserve
each existing callback's interpretation of its job arguments.

| Roster | Current bindings | TOML job rows |
| --- | ---: | ---: |
| general | 14 | 2 |
| health | 16 | 7 |
| finance | 18 | 9 |
| relationship | 30 | 19 |
| travel | 18 | 9 |
| messenger | 3 | 1 |
| education | 12 | 3 |
| chronicler | 40 | 28 |
| home | 21 | 9 |
| lifestyle | 11 | 2 |
| switchboard | 21 | 11 |
| qa | 5 | 3 |

### Enabled Memory module

The seven existing handlers below resolve together for General, Health,
Finance, Relationship, Travel, Education, Chronicler, Home, Lifestyle and
Switchboard. Messenger and QA have no Memory module. Keep the loaded
module's owner binding and pool selection, including the configured
consolidation runtime path.

`memory_ann_observability`, `memory_catalog_backfill`, `memory_consolidation`,
`memory_decay_sweep`, `memory_episode_cleanup`, `memory_knowledge_gap_delivery`,
`memory_purge_superseded`.

The startup registration source is `_DEFAULT_MAINTENANCE_SCHEDULES` in
`src/butlers/modules/memory/__init__.py`. Core retains the eight definitions
and their cadence and arguments. TOML sync currently precedes the module
default registration. That precedence and the recovered-orphan behavior
remain intact when core takes over the persistence call.

| Default schedule name | Job name | Current cron |
| --- | --- | --- |
| `memory_decay_sweep` | `memory_decay_sweep` | `15 3 * * *` |
| `memory_consolidation` | `memory_consolidation` | `0 */6 * * *` |
| `memory_episode_cleanup` | `memory_episode_cleanup` | `0 4 * * *` |
| `memory_purge_superseded` | `memory_purge_superseded` | `10 4 * * *` |
| `memory_ann_observability` | `memory_ann_observability` | `25 4 * * *` |
| `memory_knowledge_gap_delivery` | `memory_knowledge_gap_delivery` | `*/10 * * * *` |
| `memory_consolidation_backfill` | `memory_consolidation` | `*/10 * * * *` |
| `memory_catalog_backfill` | `memory_catalog_backfill` | `*/5 * * * *` |

`memory_consolidation_backfill` passes the current `batch_size=500`.
The other rows keep their current arguments without adding defaults.

### Enabled QA module

The current QA wrappers dispatch through the active QA module.
Its declarations cover `qa_patrol`, `qa_pr_status_check`, `qa_evidence_cleanup`.
The QA daemon's administrative patrol gate, current infrastructure contract,
database role and owning pool remain binding. No QA declarations are added
to other daemons.

### Retained infrastructure in core

| Job name | Existing daemon scope |
| --- | --- |
| `domain_event_reconciliation_sweep` | switchboard |
| `filtered_events_partition_prune` | general |
| `fleet_case_lapse_sweep` | switchboard |
| `insight_candidates_prune` | general |
| `paperwork_deadline_notify` | All 12 owners in the table above |
| `secret_probe_log_prune` | general |
| `session_process_logs_prune` | general, health, finance, relationship, travel, messenger, education, home, lifestyle, switchboard, qa |

The registry currently supplies `session_process_logs_prune` for every
listed daemon except Chronicler. The core retention registrations remain
disabled by default. The three General-only pruners stay General-only.
The Switchboard event reconciliation and fleet-case lapse jobs keep their
existing shared control-plane authority under its current pool.
`paperwork_deadline_notify` is the native handler selected by
`core/temporal/document_deadlines.py`. Keep its per-daemon origin binding
and existing terminal delivery and replay rules.

### Roster declarations

**General**

`collect_briefing_contributions`, `context_producer_calendar`.

**Health**

`daily_briefing_contribution`, `calendar_overlay_contribution`, `insight_scan`,
`baseline_watch`, `health_visit_brief`, `atmosphere_advisory`,
`context_producer_sleep_window`.

**Finance**

`daily_briefing_contribution`, `calendar_overlay_contribution`, `insight_scan`,
`bill_reconciliation_sweep`, `cost_claim_reconciliation_sweep`,
`anomaly_insight_scan`, `custody_held_window_evidence`, `monthly_finance_digest`,
`simplefin_sync`.

**Relationship**

`daily_briefing_contribution`, `calendar_overlay_contribution`,
`calendar_prep_contribution`, `insight_scan`, `upcoming_dates_check`,
`relationship_maintenance`, `meeting_debrief`, `meeting_card_push`,
`interaction_sync`, `automated_evidence_sync`, `loan_cost_claim_backfill`,
`memory_curation`, `pending_actions_curation`, `fact_retraction_curation`,
`entity_dedup_curation`, `episodic_predicate_curation`, `email_identity_enrichment`,
`relationship_geocode_owner_addresses`, `commitment_candidate_scan`,
`public_mcp_proposal_import`, `prepared_action_orphan_sweep`.

**Travel**

`daily_briefing_contribution`, `calendar_overlay_contribution`,
`calendar_prep_contribution`, `insight_scan`, `trip_lifecycle_advance`,
`flight_status_check`, `destination_outlook`, `context_producer_travel`,
`context_producer_commuting_eta`.

**Messenger**

`calendar_prep_contribution`.

**Education**

`compute_analytics_snapshots`, `mind_map_staleness_abandonment`,
`daily_briefing_contribution`.

**Chronicler**

`chronicler_gap_interview`, `chronicler_location_retention`,
`chronicler_custody_device_fix_evidence`, `chronicler_project_sessions`,
`chronicler_project_calendar`, `chronicler_project_owntracks`,
`chronicler_project_owntracks_place_cluster`, `chronicler_project_owntracks_ssid`,
`chronicler_project_activitywatch`, `chronicler_project_calendar_revisions`,
`chronicler_project_owner_outbound`, `chronicler_project_steam`,
`chronicler_project_steam_status`, `chronicler_project_meals`,
`chronicler_project_home_assistant`,
`chronicler_project_home_assistant_sensor_activity`,
`chronicler_project_google_health_sleep`, `chronicler_project_google_health_workout`,
`chronicler_project_google_health_steps`,
`chronicler_project_google_health_heart_rate`, `chronicler_project_focus_inferred`,
`chronicler_project_reading_inferred`, `chronicler_project_spotify`,
`chronicler_project_exercise_inferred`, `chronicler_project_occupation_inferred`,
`chronicler_rebuild_daily_rollups`, `chronicler_project_situation_inferred`,
`chronicler_project_comms`, `chronicler_routines_mine`, `chronicler_rollup_daily`,
`chronicler_public_projection_sweep`, `chronicler_narrate_daily`.

**Home**

`device_health_check`, `environment_report`, `energy_digest`,
`maintenance_schedule_check`, `context_producer_home_presence`,
`atmosphere_feed_refresh`, `atmosphere_preconditioning`, `home_safety_sentinel`,
`home_departure_watch`, `home_arrival_watch`, `home_away_safety`,
`daily_briefing_contribution`.

**Lifestyle**

`daily_briefing_contribution`, `taste_ledger_project`.

**Switchboard**

`eligibility_sweep`, `insight_delivery_cycle`, `insight_urgent_subcycle`,
`insight_timed_subcycle`, `insight_outcome_sweep`, `commitment_escalation`,
`spend_rule_savings`, `rule_promotion_trigger`, `dropped_known_watch`,
`security_question_cycle`.

These bindings include Home's registered, unscheduled handlers. Keep their
current dark posture and manifest scope. Chronicler retains each current
projection job and its existing service call at this placement step.
bu-qozovd.5 independently owns adapter aggregation and isolation.

`contact_info_reconciler` is retired with the table removed by core_115.
The current roster and registry contain no Decision Desk job binding. Keep
retirements controlled by bu-g2zuv2 and bu-99t5s3.6 out of the new map. Existing
aliases and deadlines may resolve only names still registered for the
owning daemon.

## Dispatch and process consumers

- `lifecycle.py` invokes core TOML synchronization with the owning pool,
  existing timezone and staggering inputs. It preserves staffer briefing
  exclusion and the quiet-window advisory. Memory currently asks
  `core/scheduler/crud.py::ensure_module_default_schedule` to reconcile its
  default rows after TOML sync. Keep core's narrowly eligible, audited
  TOML-orphan recovery and the distinct operator-owned DB disable case.
- `background.py` resolves handler names for cron and job-mode deadlines,
  including the fixed paperwork handler. Move that dispatch reference to
  the validated daemon-local map. Remove the legacy rebuild-on-miss paths
  only after all existing sources resolve through the new map.
- `background.py` also compares the executed Chronicler projection or daily
  rollup handler with the trusted registry callable before calling the
  private post-commit continuation. Preserve that identity check in the
  validated declaration, plus the tick-tail replay and existing day-close
  signal. A same-name replacement or an ineligible job cannot trigger the
  post-commit hook.
- `core/break_through.py` calls the existing Switchboard notification
  builder. Preserve that helper's independent consumer and authority
  when moving the related declarations.
- The Dashboard API lifespan in `src/butlers/api/app.py` owns
  `fleet_shadow_observer`, `fleet_readiness`, `secrets_lifecycle`,
  `capability_health`, `model_verify`, `fleet_events_bridge`,
  `settings_console_delta`, `secrets_staleness`, `migration_drift`,
  `calendar_sync_deadman`, `calendar_overcommitment`, `day_sheet_push`,
  `backup_facts_snapshot` and `external_deadman`. These loops keep
  their own timers, health reporting, startup and shutdown handling.
  The registry migration preserves any shared pure service call and
  leaves these loop lifetimes with the API. Its one-shot stored-function
  drift probe also stays with API startup.
