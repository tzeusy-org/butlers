## Why

No QA publisher is injected, so every investigation that commits a fix ends `failed` with
`publication_unavailable`. Five in a row trip the circuit breaker, a manual reset buys five more
discarded investigations, and the patch is thrown away at teardown. A valid proposal is not an
execution failure, and its sealed export needs no credential and no publisher to keep.

The owner approved this direction on `bu-l34qcx` (2026-11-22): amend the publication-unavailable
scenario of `REQ-qa-investigation-dispatch-001`, hold window 7 days, exports flagged as sealed before
isolation is proven and never auto-published. This supersedes `bu-mc01y5.12` and closed PR #4749.

## What changes

- A committed investigation with no publisher is sealed with the publisher's validator and retained
  in `public.qa_publication_exports`; the dossier shows it as `unpublished`.
- The circuit breaker counts such an attempt as an execution success. Dispatch admission and the
  dashboard share one SQL definition, so they cannot drift.
- A new Gate 5.6 `publication_paused` holds a fingerprint with a live retained proposal for the
  hold window, so each fingerprint costs at most one launched session while the publisher is
  absent. The breaker change and the hold ship together.
- Modifies `REQ-qa-investigation-dispatch-001` (Publication unavailable scenario) and adds two
  scenarios to it. No new attempt status, table, migration or endpoint.

## Not decided here

The owner apply door and mbox download (`bu-ejow5p.7`), the publisher's resume from a retained
export (`bu-mc01y5.7`), canonical finding identity (`bu-ejow5p.4`) and breaker v2 (`bu-ejow5p.3`).

Tracking: `bu-ejow5p.2`. Spec wording lands in a PR marked `(needs owner approval)`.
