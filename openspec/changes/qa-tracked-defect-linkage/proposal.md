## Why

A finding already tracked by a bead or pull request is investigated again on every cooldown
expiry and breaker reset: a fixed publisher would open duplicate PRs racing a human-owned defect,
and recovery budget is spent on known work (bu-ejow5p.9, run 25 move 9).

## What changes

- New table `public.qa_known_issue_links` (core_372): one row links a fingerprint to a bead, GitHub
  PR or issue, with a lifecycle `state` and a required `expires_at` (at most 90 days after `linked_at`, a CHECK)
  so a link cannot suppress forever. Only `butler_qa_rw` can write it, and its ACL is converged
  exactly on every migration run.
- Triage gains a `tracked` gate between dismissal and cooldown. A finding with a live `tracked`
  link is persisted every patrol with `dedup_reason = 'tracked'` and dispatches nothing. A
  recurrence under a `fix_merged` link is dispatched with the link attached (the fix is not known
  live), and under an `awaiting_recovery` link it is admitted as a regression carrying
  `structured_evidence.regression_of`. An unreadable link store fails open.
- Owner-only dashboard API: `POST` and `DELETE /api/qa/known-issues/{fingerprint}/link`. A bead ref
  is an unverified owner assertion; a GitHub PR ref is refused when GitHub says it is closed or
  merged.

## Deferred

- S3: flip `fix_merged` to `awaiting_recovery` from deployment evidence (needs the deployment ledger).
