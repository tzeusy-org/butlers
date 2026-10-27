## Why

Q1 (`bu-fvw4ap.1`) must keep QA's deterministic local patrol running when
only its remote Switchboard observation becomes stale, while an explicit
owner pause or quarantine still stops new local dispatch. The legacy
`eligibility_state` conflates TTL-derived quarantine with an owner hold.
Switchboard migration `sw_035` separates `policy_state` and
`policy_provenance`, but only the Switchboard runtime role can read the
control-plane table. QA's separate audit pool uses the migration login;
querying the table through it would bypass the runtime-role/RLS boundary.

Rule 3 requires inter-butler communication through Switchboard MCP. RFC 0010
does not already authorize a per-tick SQL read: its exception is scheduled,
batch-oriented, and justified by avoided LLM sessions. This proposed package
therefore asks for an explicit, narrower owner decision, including a paired
Rule 3 doctrine amendment, before any code or grant is changed.

## Proposed outcome if adopted

- Switchboard owns one no-argument, read-only database projection for the
  fixed roster identity `qa`. It returns only the validated
  `policy_state` and `policy_provenance` enums from the current committed
  row; it accepts no caller name, endpoint, observation, roster, or policy
  mutation.
- Only the effective `butler_qa_rw` runtime role may execute it, through
  QA's own role-scoped pool. PUBLIC, other butler roles, and the role-less
  migration-login audit pool cannot invoke it. A privileged database owner
  remains trusted under the existing single-owner threat model; this
  contract grants no new authority to that login.
- Q1 uses a fresh result at local job admission. `active` with proven
  `legacy_ttl` provenance remains runnable despite remote staleness;
  `paused`, `quarantined`, and `review_required` do not run; the
  provenance distinguishes an explicit owner review hold from ambiguous
  legacy history.
  Missing, malformed, or unreadable projection results deny new dispatch
  with a typed, diagnosable state, never legacy-eligibility or cached-active
  fallback.
- Switchboard's own eligibility sweep is a separate same-owner reader under
  its existing runtime role. This proposal does not give QA fleet-wide reads.

The owner choice also settles the narrow reading of
`REQ-staffer-qa-006`'s "only explicit" stop clause: derived remote staleness
is never a stop; a proven restrictive operator policy suppresses work.
Ambiguous legacy `review_required` and missing or unreadable *policy
authority* are separately typed safety holds, not remote-staleness stops or
claims that the owner paused QA. This distinction must be adopted with the new
read contract; it is not silently inferred from the current target wording.

## Scope and authority

This is proposed doctrine/RFC/OpenSpec text only. No canonical doctrine, RFC,
baseline spec, manifesto, function, migration, grant, scheduler, credential,
runtime, deployment, or live QA patrol changes are made here. Q1 and interface Bead
`bu-ch4p1v` remain blocked until the exact authority exception is
independently reviewed and explicitly adopted by the owner, then implemented
and separately reviewed. Declining it preserves current behavior and does
not authorize a fail-open scheduler shortcut.

## Exact owner choice after independent review

- **A — Adopt (recommended):** Approve the paired Rule 3 doctrine text, this
  fixed-QA RFC exception, and the separately typed unknown-policy and
  review-required safety holds. This permits a future, separately reviewed
  producer implementation packet; it does not authorize a grant, runtime
  change, merge, or activation by itself.
- **B — Decline:** Keep the MCP-only Rule 3 boundary and leave Q1's
  cross-boundary scheduler read blocked until a different, independently
  specified design can preserve both circular-outage and owner-stop
  guarantees. Do not use the migration-login audit pool or `allow_stale`.

If unanswered, default to **B/status quo**. The owner is deciding an
architectural exception, not choosing a SQL implementation detail.

Tests: +0 ~0 -0.
