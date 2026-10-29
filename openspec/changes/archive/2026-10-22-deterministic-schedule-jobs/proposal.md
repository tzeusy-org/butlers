## Why

Three scheduled tasks spawn an LLM session for work that needs no model. Relationship
`upcoming-dates-check` and `relationship-maintenance` read an existing tool, apply a formula and
send one Telegram message. Chronicler `chronicler_gap_interview` computes yesterday and calls one
tool that already does the whole ask. Each run costs a session and a token budget, and under
RFC 0038 each would become an obligation to declare, admit and replay at the cutover (D2), even
though D2 leaves deterministic jobs unchanged. D18 also keeps model-composed owner text inside a
goal, so the honest home for fixed-shape reminders is a job with a template.

The weekly maintenance prompt also re-derived the urgency formula by hand, while the adopted
dunbar-tier-scoring formula (`compute_urgency`) had no production caller.

## What Changes

- `upcoming-dates-check` becomes a job: `upcoming_dates(days_ahead=7)`, silent on an empty read,
  no message on a failed read, otherwise one templated reminder.
- `relationship-maintenance` becomes a job: eligibility from the overdue evaluation, nothing sent
  when cadence is unmeasurable, the exact all-clear when nothing is overdue, otherwise the top 3
  eligible contacts by `compute_urgency` in a template. The prompt's days-since variant of the
  formula is retired in favour of the spec's days-overdue form.
- Both send through one shared owner-notify helper (the boundary Home and Health jobs already
  use): suppression drops rather than defers, one attention-ledger row per outcome, no retry.
- `chronicler_gap_interview` becomes a job that computes yesterday in the owner's general
  timezone and calls the ask service the MCP tool now shares.
- The two companion skills that existed only for the cron prompts are deleted.
- `generalist-lead-focused-delegation`'s butler-relationship delta is conformed: the two
  relationship schedules are deterministic jobs there too, not obligations.

## Capabilities

### New Capabilities
<!-- None. -->

### Modified Capabilities
- `butler-relationship`: "Relationship Butler Schedules" carries the two job contracts.
- `butler-chronicler`: adds "Day-Close Gap Interview Ask Job".

## Impact

- `roster/relationship/butler.toml`, `roster/chronicler/butler.toml`: three rows switch to
  `dispatch_mode = "job"`; names and crons are unchanged, and `sync_schedules` applies them on the
  next start.
- New `src/butlers/jobs/owner_notify.py`, `roster/relationship/tools/reminders.py`,
  `src/butlers/chronicler/gap_interview_job.py`; three registry entries in
  `src/butlers/scheduled_jobs.py`.
- Owner-visible: reminder wording becomes a fixed template instead of LLM drafting. What is sent,
  and when, is unchanged.
