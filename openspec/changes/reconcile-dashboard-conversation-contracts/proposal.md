## Goal-runtime disposition

Under adopted RFC 0038 D1, D2 and D19 and the generalist change's
collision-dispositions row, dashboard reasoning ingress SHALL admit an owner
goal through Switchboard rather than classify a message and route it to a
target-butler session. This amendment preserves the immutable,
server-authenticated conversation envelope, deduplication, retry identity,
direct-ingress provenance and connector-specific boundaries. Dashboard bodies
belong to `dashboard-one-mind-goal-surface` under D19. No RFC decision changes.

The two delta requirements distinguish the serving pre-cutover route contract
from its goal-admission successor. Legacy source/session records remain
readable; route dispatch is not a second post-cutover admission path. This is a
contract amendment, not runtime implementation, activation, task completion or
archive. The original reconciliation scope below is historical; it grants no
extra edit or execution authority for this amendment.

## Why

The pre-cutover dashboard conversation path is now real enough that stale planning language and
an incomplete canonical-source list can misdirect the next reliability work.  The
current contract already uses `dashboard` / `internal`; RFC 0003 omits that pair,
and the anchor/resume change still says live wiring is absent even though its task
record says it landed.

## What Changes

- Replace the future target-session routing contract with goal admission,
  retaining authenticated immutable ingress and request-ID lineage. Legacy
  routing-pin cases are explicitly scoped to pre-cutover behavior.
- Preserve all task identities and checkbox states as historical evidence or
  outstanding work; no roadmap mutation is executed here.

- Amend RFC 0003's channel vocabulary, issuer wording, canonical source-pair
  list, and endpoint-identity rule to explicitly include the owner-operated
  `dashboard` / `internal` ingress and `dashboard:web:{conversation_id}`
  identity, scoped to direct dashboard conversations rather than connectors.
- Make the dashboard-conversation envelope requirement cite that canonical
  vocabulary, so the RFC and the executable contract agree.
- Correct the ingestion-event registry's connector-only purpose wording: it is
  a unified canonical record for connector-originated and direct internal
  dashboard ingress, while retaining connector-specific status semantics where
  those actually apply.
- Correct the active anchor/resume change's status narrative and retain only its
  genuinely unfinished work: first-token streaming and a unified conversation
  read/action-receipt surface.
- Only after owner approval, make a content-only correction to the one mixed,
  stale conversational-roadmap Bead (`bu-27dxl.9`) with links to the canonical
  work packets; this change does not create, release, or lifecycle-mutate
  execution work.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-conversations`: make the `dashboard` / `internal` envelope origin
  explicitly canonical and traceable to RFC 0003.
- `ingestion-event-registry`: correct the registry's connector-only purpose and
  table wording without changing connector-specific filtered-event behavior.

## Impact

- `about/legends-and-lore/rfcs/0003-switchboard-routing-and-ingestion.md`
- `openspec/specs/dashboard-conversations/spec.md`
- `openspec/specs/ingestion-event-registry/spec.md`
- `openspec/changes/conversation-anchor-provider-resume-ledger/`
- `bu-27dxl.9`, after owner approval and only as a content-only edit.

This is a documentation and requirements-reconciliation change. It adopts no
new routing policy: goal admission is already required by RFC 0038. It does not
add a generic question lane or alter serving runtime behavior.
