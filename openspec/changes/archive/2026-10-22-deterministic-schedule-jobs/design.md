## Context

Bead bu-idc5mx.4. RFC 0038 D2 leaves deterministic jobs unchanged by the switch and turns only
prompt-carrying schedules into obligations; D18 keeps model-composed owner text inside a goal.
The three schedules here need no model, so they become jobs now, with templates.

## Decisions

### Relationship reminders

- Domain logic lives in `roster/relationship/tools/reminders.py`
  (`butlers.tools.relationship.reminders`), with injected read, rank and notify seams that default
  to production. The registry wrappers import it directly.
- Dates: `upcoming_dates(pool, days_ahead=7)` is the only read. A raised read propagates, so the
  scheduler records a failed job and nothing is sent. Lines are sorted by date:
  `- {name}: {label}, {Mon D} ({today|tomorrow|in N days})` under "Upcoming dates this week:".
- Maintenance: `contacts_overdue_evaluation` is the eligibility gate (active, listed, effective
  cadence, tier-1500 exclusion, single-producer measurability). `compute_tier_ranking` is filtered
  to the eligible contact ids and passed to `compute_urgency(top_n=3)`. Eligible contacts absent
  from the ranking are counted and logged. If eligible contacts exist but none ranks, nothing is
  sent: an all-clear would be false. Lines: `{n}. {name}: {d} days since you last talked (every
  {cadence} days)`, plus "a date is coming up" / "gift pending" when `compute_urgency`'s context
  bonus carries those terms.

### Owner-notify helper

`butlers.jobs.owner_notify.send_owner_telegram(pool, *, origin_butler, message, log_context)` is
the boundary `health_visit_brief.send_owner_brief` and the Home jobs use, parameterised by origin.
Suppression drops and records; it does not defer the way `notify()` does. The daily dates job
re-evaluates the next morning; a suppressed weekly maintenance run is not resent. Existing senders
are not migrated here (bu-dss7he.12 owns that consolidation).

### Gap interview

- The tool body moves verbatim into `butlers.chronicler.gap_interview_job.ask_gap_interview`;
  the MCP tool is a one-line wrapper with the same signature and statuses.
- `run_gap_interview_job` rejects any job_args, resolves the general timezone with
  `resolve_general_timezone` (the zone the scheduler evaluates a TOML cron without its own
  timezone in) and takes yesterday from `day_close_writer._compute_day_window`, the DST-correct
  helper day-close already uses. A per-schedule timezone override is not read.
- The dedupe stays check-then-deliver-then-mark: sequential, not an atomic claim. An owner tool
  call racing the 09:00 job can still ask twice for one day, as two sessions could before. A fix
  belongs to bu-jjy9p's transport rework.

### Spec placement

This change archives in the same PR, so the canonical specs match shipped behaviour and no
pending collision remains. The generalist delta is conformed in place.

## Open question (not blocking)

butler-chronicler "Retrospective-Only Scope" names the day-close summary as the single sanctioned
owner-facing message. The gap interview ask (bu-whhll.12) has shipped since July as a separate,
opted-in correction prompt, and its answer path is already specified. This change specifies the
ask schedule as it runs and does not amend the scope requirement; reconciling the two is an owner
decision.

## Rollback

Revert the PR. The rows return to prompt mode on the next `sync_schedules`, and KV dedupe state,
attention-ledger rows and answer state are kept, so nothing is resent.
