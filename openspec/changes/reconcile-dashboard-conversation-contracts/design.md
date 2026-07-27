## Context

Dashboard conversations deliberately enter the standard Switchboard spine as
`source.channel = "dashboard"` and `source.provider = "internal"`.  That exact
pair is already normative in the dashboard-conversations capability, but RFC 0003
lists canonical pairs without it.  Separately, the anchor/provider-resume change
contains an implementation-status contradiction: its proposal says live wiring is
out of scope while its tasks record that wiring as complete.

These are source-of-truth defects.  They are small individually, but they make a
reliability plan look less certain than the code and can cause a future change to
duplicate already-landed work.

## Goals / Non-Goals

**Goals:**

- Establish one traceable, non-contradictory authority for dashboard conversation
  source vocabulary and the current anchor/resume boundary.
- Preserve the existing specialist-routing policy and make its remaining work
  discoverable as distinct future slices.
- Replace stale roadmap references only after their successors are approved.

**Non-Goals:**

- Change message classification, Switchboard ownership, General's authority, or
  the dashboard's runtime behavior.
- Add first-token streaming, a unified conversation reader, or a question lane.
- Create executable Beads or supersede an in-flight pull request before owner
  approval.

## Decisions

### 1. Treat `dashboard` / `internal` as a scoped canonical ingress pair

RFC 0003 will add `dashboard` to its channel vocabulary, add the pair alongside
other canonical source/provider pairs, distinguish direct owner-dashboard ingress
from connector submission, and exempt the dashboard API's
`dashboard:web:{conversation_id}` endpoint identity from connector-startup
auto-resolution. The dashboard-conversations envelope requirement will link to
that RFC entry.

**Why this over a local exception:** a local exception leaves code technically
correct but the canonical routing contract false.  Widening `internal` into a
catch-all would weaken source provenance, so the exception remains explicit and
scoped.

### 2. Preserve history while correcting active change status

The anchor/resume change will receive a concise status correction that distinguishes
landed wiring from its remaining first-token/read-surface work.  It will not be
archived or rewritten as a new implementation proposal.

**Why this over a second replacement change:** the existing change already owns the
history and outstanding tasks.  A replacement would create two competing sources
for the same provider-resume contract.

### 3. Narrow the one mixed roadmap record without releasing work

Only `bu-27dxl.9` is in scope for a future owner-approved roadmap edit. Its
description may remove the already-separated widget Stop/route-truth work
(#3624), truthful dispatch-receipt work (#3618), and durable terminal-action
recovery (`bu-s3qvp`), replacing each with a link to its canonical packet. It
will retain Telegram parity, the explicitly owner-decided question-lane topic,
and the future unified cross-channel reader. This changeset SHALL NOT alter
`bu-27dxl.9` status, priority, owner, dependencies, or parent; create a
successor Bead; or mutate any other roadmap record.

**Why this over preserving one umbrella roadmap:** a composite roadmap hides
dependencies and makes ready work ambiguous. A content-only correction retains
history without silently releasing a new fleet-executable slice.

## Risks / Trade-offs

- **Historical wording is altered after work landed** → add a dated status note
  rather than erasing the original scope claim.
- **RFC vocabulary becomes over-broad** → name only `dashboard` / `internal` and
  state its operator-ingress scope.
- **Roadmap cleanup accidentally unblocks fleet work** → perform Bead mutations
  only after owner approval and keep any successors gated.

## Migration Plan

1. Record the exact merged commit/PR and targeted verification evidence for
   anchor/resume tasks 4.1-4.2 and for the durable Stop/dispatch prerequisites.
2. Update the RFC, the dashboard-conversations delta, and the active
   anchor/resume status text in one documentation-only pull request.
3. Validate OpenSpec and links. Only after owner approval, make the scoped
   content-only `bu-27dxl.9` edit described above; do not create successors or
   mutate any Bead lifecycle/dependency field in this reconciliation change.

Rollback is a normal documentation revert; no database or runtime migration is
involved.

## Open Questions

None for the contract correction itself.  The separate question-intent taxonomy
remains an owner decision and is deliberately not inferred here.
