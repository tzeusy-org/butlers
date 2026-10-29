# Proposed RFC 0010 amendment: fixed QA local-policy read

**Status: proposed, not adopted.** This text would be added only after an
explicit owner decision. It does not reinterpret RFC 0010's existing daily
briefing exception as permission for real-time reads.

## Paired Rule 3 doctrine amendment required

The existing `about/heart-and-soul/vision.md` Rule 3 permits a narrow
read-only SQL exception only for deterministic batch aggregation justified
by avoided LLM-session cost. QA's per-admission availability need does not
meet that criterion. Adopting this RFC without a paired owner-approved
doctrine amendment would leave a higher-layer contradiction. Proposed
additional Rule 3 wording, **not applied here**:

> One separately reviewed exception permits only the QA daemon's
> deterministic local scheduler, under its matching runtime database role,
> to read its own explicit administrative-policy state and provenance
> through a fixed, no-argument Switchboard-owned read projection. The
> exception exists solely to keep local patrol independent of Switchboard
> process reachability without bypassing an owner stop. It conveys no
> cross-butler data, endpoint, observation, policy-write, or general-purpose
> SQL authority. Its exact grants, failure behavior, and rollback are
> governed by RFC 0010's QA-specific amendment; expansion requires a new
> owner-approved doctrine and RFC decision.

## Narrow second Rule 3 exception

The QA daemon's deterministic local scheduler MAY read its own administrative
stop state from a Switchboard-owned, no-argument database function when its
effective runtime role is exactly `butler_qa_rw`. The function is bound to the
literal Git-roster identity `qa`; it returns only validated
`policy_state` and `policy_provenance`. It neither returns nor accepts a
roster name, endpoint, liveness observation, boot epoch, policy actor/reason,
timestamp, or arbitrary SQL predicate. Switchboard remains the sole producer
of the underlying policy and its authenticated owner API remains the only
policy writer.

This exception exists because a synchronous Switchboard MCP dependency at
each local tick would recreate the QA circular outage when Switchboard is
unreachable. Reusing a last-known `active` result would instead bypass a
later owner stop; reusing a restrictive result could keep QA stopped after an
owner release. The database is already required by the local patrol, so a
fresh fixed projection can distinguish remote staleness from owner policy
without requiring the Switchboard process to be reachable.

The exception has all of these guardrails:

1. A migration-reviewed Switchboard-owned `SECURITY DEFINER` function has a
   fixed `pg_catalog, switchboard, pg_temp` search path, fully qualified
   table references, no caller arguments, and an in-body check of the active
   `SET ROLE` value. It refuses `role=none`, wrong roles, missing rows,
   invalid enum values, and inconsistent policy/provenance pairs. It returns
   no raw registry row or observation.
2. Revoke function EXECUTE from PUBLIC and grant it only to
   `butler_qa_rw`; do not grant QA SELECT or USAGE on the Switchboard
   control table/schema. Existing table RLS remains enabled. Replayed
   `init-db.sql` grants, migration-login ownership, and role fallback
   cannot turn the audit pool into an accepted caller.
3. QA calls only through its own runtime-role pool. Missing role enforcement
   or an unreadable/malformed response is a typed unknown policy and denies
   new effectful local dispatch. No legacy eligibility, stale positive cache,
   or arbitrary MCP/HTTP fallback may authorize a run.
4. An explicit owner pause or quarantine blocks new QA local admissions;
   `review_required/operator` is an explicit owner review hold, while
   `review_required/legacy_ambiguous` is unresolved history and blocks
   without being represented as owner intent. A proven TTL-derived stale observation
   with `policy_state=active` does not block local patrol. Already admitted
   work is not retrospectively described as cancelled.
5. The function and grants remain during rollback until every active reader
   has a reviewed replacement. A code downgrade must not resurrect a legacy
   fail-open path or clear a retained owner hold.

This is not an RFC 0010 reuse instance: it is a separately adopted exception
to Rule 3. It does not authorize other roles, per-butler generic policy reads,
cross-schema application SQL, shared policy caches, arbitrary public views,
or a general-purpose scheduler-policy service. A future expansion needs its
own authority review and owner choice. RFC 0001's local scheduler remains
independent of remote liveness; RFC 0003's Switchboard policy ownership and
RFC 0006's runtime-role isolation remain otherwise unchanged.

The `REQ-staffer-qa-006` statement that derived staleness cannot stop QA
remains absolute. An ambiguous legacy `review_required` hold and missing,
malformed, or unreadable policy authority are separately typed safety
refusals, not fabricated owner stops; they cannot count as completed or
healthy patrols. This limited clarification is part of owner adoption.
