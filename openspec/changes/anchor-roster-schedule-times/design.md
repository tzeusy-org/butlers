## Decisions

- **Pin with `Etc/UTC`.** `_effective_schedule_timezone` treats `UTC`, NULL and empty as
  "follow the owner", and the tick spec says a non-UTC stored value is an explicit override.
  `Etc/UTC` is an explicit override with identical behaviour, so no sentinel change and no
  migration is needed.
- **Sync honours the TOML zone.** Sync previously used only the owner zone. A schedule that
  declares `timezone` is computed and stored in that zone; schedules that do not are
  unchanged, and the stored sentinel stays `UTC`.
- **Lint is advisory and conservative.** It reads `public.approvals_policy`, checks both the
  nominal firing and the latest instant stagger can move it to, and stays silent for
  job-mode, non-notifying, irregular (more than 2000 firings in the week) or policy-less
  cases.
- **Effect on deployed firing times.** Pinning moves an infrastructure schedule's real
  firing time from the owner zone to the stated UTC time whenever the owner zone is not UTC.
