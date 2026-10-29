## 1. Owner-notify helper

- [x] 1.1 Add `butlers.jobs.owner_notify.send_owner_telegram` with one attention-ledger row per outcome and no retry
- [x] 1.2 Cover suppressed, no recipient, no client, deliver error and delivered in `tests/jobs/test_owner_notify.py`

## 2. Relationship reminder jobs

- [x] 2.1 Add `run_upcoming_dates_check` and `run_relationship_maintenance` in `roster/relationship/tools/reminders.py`
- [x] 2.2 Register `upcoming_dates_check` and `relationship_maintenance` in the deterministic job registry
- [x] 2.3 Convert both `roster/relationship/butler.toml` rows to job mode with unchanged names and crons
- [x] 2.4 Delete the `upcoming-dates` and `relationship-maintenance` companion skills and drop the scheduled bullet from `reconnect-planner`
- [x] 2.5 Cover the templates, silence rules, eligibility and ranking in `roster/relationship/tests/test_scheduled_reminder_jobs.py`

## 3. Chronicler gap interview job

- [x] 3.1 Move the tool body into `butlers.chronicler.gap_interview_job.ask_gap_interview` and make the tool a wrapper
- [x] 3.2 Add `run_gap_interview_job` and register `chronicler_gap_interview`
- [x] 3.3 Convert the `roster/chronicler/butler.toml` row to job mode
- [x] 3.4 Cover yesterday across DST, job_args rejection and the shared ask in `tests/chronicler/test_gap_interview_job.py`

## 4. Spawn-nothing gate and specs

- [x] 4.1 Dispatch all three rows through `dispatch_scheduled_task` with a spawner sentinel in `tests/daemon/test_deterministic_schedule_jobs_spawn_nothing.py`
- [x] 4.2 Conform the `generalist-lead-focused-delegation` butler-relationship delta to jobs
- [x] 4.3 Update `docs/butlers/relationship.md` and `docs/runtime/scheduler-execution.md`
