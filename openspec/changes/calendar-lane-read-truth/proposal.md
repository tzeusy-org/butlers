## Why

The proposals and overlay lanes render a failed read as nothing to review or no domain context,
because the read fails open to an empty list and the API still returns 200 (bu-5ii4gj.4, slice C).

## What changes

- The proposals read reports `proposals_available` and a count of dropped malformed rows.
- The overlay read and the day briefing report `overlays_available`.
- The dashboard renders "Proposals unavailable" and "Domain context unavailable" for those cases.
- The "Proposals view is fail-open" scenario now states that a failed read is flagged unavailable
  rather than returned as an indistinguishable empty list.

## Deferred

- An overlay freshness stamp (`generated_at`) so stale envelopes stop wearing current authority.
