## Context

Concierge's roster integration test boots its real core and module registration
path against PostgreSQL, enumerates canonical FastMCP `tools/list`, verifies the
exact dashboard-read names and docstrings, and invokes each dashboard reader to
check its source envelope. Its additional assertion that the total registered
count remains between 30 and 50 predates the accepted RFC 0027 layered surface
model.

RFC 0002 Amendment 1 and RFC 0027 now distinguish the complete
registered/callable set from the LLM-presentable, initially loaded, and later
loaded sets. The 30-50 target constrains full definitions initially loaded per
session. Canonical `tools/list` remains complete, and registration-time role,
group, and manifesto boundaries remain authoritative.

## Goals / Non-Goals

**Goals:**

- Remove the false numeric ceiling from Concierge's canonical registration
  contract.
- Keep the existing behavior-executing integration coverage at the real
  registration and PostgreSQL seams.
- Preserve exact dashboard-read surface, docstring, and source-envelope
  assertions.
- State clearly that canonical handler counts and initial-schema-byte evidence
  answer different questions.

**Non-Goals:**

- Remove a legitimate core or module tool, widen Concierge's role, or change
  its configured modules or groups.
- Raise or replace the 30-50 initial-working-set target.
- Activate native discovery, change presentation policy, or add measurement or
  admission logic owned by `bu-ondtw`.
- Amend RFC 0002, RFC 0027, runtime behavior, persistence, concurrency,
  authorization, or deployment.

## Decisions

- Continue enumerating the complete canonical FastMCP registry in the existing
  integration test, because that is the correct seam for proving registration.
- Assert the exact `dashboard_read` names rather than a total count. Exact
  module membership detects missing or unintended dashboard capabilities while
  allowing the complete role-fit registry to evolve independently of initial
  model presentation.
- Keep the existing argument-free and detail-reader invocations. Their source
  envelope assertions prove behavior at the tool boundary rather than merely
  scanning registration source.
- Treat initial serialized schema bytes as separate RFC 0027 conformance
  evidence. This change neither manufactures that evidence from a handler
  count nor duplicates the `bu-ondtw` implementation lane.

## Risks / Trade-offs

- [Future unrelated tools could expand Concierge's registered surface] -> Core
  groups, module groups, type/name gates, roster role fit, and manifesto scope
  remain the registration controls; RFC 0027 presentation evidence is measured
  separately.
- [Dropping the count assertion could weaken dashboard module coverage] -> The
  test retains exact-name equality for the dashboard-read surface, non-empty
  descriptions, and behavior-executing source-envelope checks.

## Migration Plan

No runtime or data migration is required. The delta explicitly removes the
obsolete requirement and adds its RFC-aligned successor, avoiding a whole-body
`MODIFIED` replacement or an overwrite-baseline refreeze. Syncing or archiving
the delta later applies that replacement to the canonical Concierge spec.
Reverting the test and change directory restores the previous documentation-only
ceiling.
