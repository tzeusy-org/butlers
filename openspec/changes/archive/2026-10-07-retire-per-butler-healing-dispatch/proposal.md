## Why

Accepted RFC 0015 and QA's manifesto assign investigation to QA. The old
per-butler module still dispatches directly when routing fails, while none of
thirteen rosters declares the required report relay. Registry discovery also
admits undeclared schema-less modules, and actual startup ignores unknown names.
Universal declarations must ship together with relay-only source and guidance.

## What Changes

- Admit only explicit startup declarations; reject unknown names before DB work.
- Declare self_healing and link its shared skill in all thirteen rosters.
- Register report_error and read-only get_healing_status; normalize actual MCP
  target acceptance within one two-second deadline, using Switchboard's existing
  MCP route tools and a scoped local MCP adapter for Switchboard self-reports.
- Remove local active-history admission, fallback dispatch, retry, watchdog,
  recovery and reaper behavior; retain six-key configuration compatibility.
- Remove dead Spawner wiring, preserving ordinary error/process/reset/cleanup
  evidence and delivery accounting. Preserve dashboard retry history/409 and
  injected legacy dispatch with truthful missing-tool dispatched=false.
- Rebuild the five modified core requirements and add the relay contract with
  complete original heading/body disposition and mandatory central survivors.

## Capabilities

### Modified Capabilities
- `core-spawner`: ordinary failure evidence without per-butler investigation.
- `core-modules`: explicit startup admission and the centralized QA error relay.

## Impact

Runtime, all roster declarations/skill links, tool inventory, shared relay skill,
owned docs and existing behavior species change together. No database migration,
new role/grant, provider activation, Metrics/DocumentRenderer retirement, family
spec move, QA publication authority or automatic ambiguous retry is included.

## Authority and Retained Outcomes

Source: accepted about/legends-and-lore/rfcs/0015-qa-staffer-discovery-investigation-pipeline.md;
roster/qa/MANIFESTO.md; bu-1fe7xv rounds 2-4; closed bu-lsxqb0.6 delegation.
The complete original 36 requirements/147 scenario bodies remain in the review
matrix with explicit unchanged/superseded dispositions. Historical heading names
remain for native carryover; fallback outcomes survive as ordinary evidence and
central QA contracts. Foreign active deltas and archive/signature gates remain.
Current v1.md already describes the RFC 0015 relay and is preserved unchanged.
