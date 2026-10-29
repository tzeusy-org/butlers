## ADDED Requirements

### Requirement: Finance insights use the canonical whole-envelope inert renderer

Finance insight delivery MUST use the existing authenticated canonical Messenger rendering boundary. Any extracted or unknown contributing display value, or independent exposure, SHALL require all links in the final envelope to be inert with previews disabled; only owner-only independently unexposed output SHALL retain ordinary rendering.

ID: REQ-insight-delivery-001
Source: bu-svqpzo.19 proposed owner-door qualification; RFC 0038 D4/D6; canonical Messenger bu-vbdug9.1
Scope: v1-mandatory

#### Scenario: R03 Owner-only unexposed names retain ordinary rendering

- **WHEN** every contributing display value is owner-origin and the envelope is independently unexposed
- **THEN** raw stored values and ordinary delivered names MUST remain unchanged
- **AND** an owner source label alone MUST NOT satisfy the independent exposure condition

#### Scenario: R04 Mixed digests protect the whole message

- **WHEN** an owner-origin contribution and an extracted or unknown contribution share a digest
- **THEN** all links in the final digest MUST render inert, independent of contribution order
- **AND** both underlying raw records and their origins MUST remain unchanged

#### Scenario: R05 Independent exposure also protects owner text

- **WHEN** all Finance names are owner-origin but the delivery envelope is independently exposed
- **THEN** the canonical whole-envelope inert rule and preview suppression MUST still apply
- **AND** owner field authority MUST NOT declassify the envelope

#### Scenario: R10 Candidate readback cannot reactivate extracted labels

- **WHEN** the actual Switchboard candidate API returns message and metadata or a UI displays that projection
- **THEN** the projection MUST be safe to display without active extracted links, including metadata labels
- **AND** count-only aggregate insight endpoints MUST NOT stand in for this guarantee and a raw owner-authorized record read MUST NOT be silently rewritten

#### Scenario: R11 Verified cancellation door keeps authority under inert rendering

- **WHEN** an owner-sourced cancellation URL accompanies an extracted service name or an independently exposed envelope
- **THEN** the raw stored door and `door_verified: true` MUST remain authoritative as specified by REQ-finance-alerts-002
- **AND** its final rendered URL MUST nevertheless be inert as part of the whole message; only the owner-only independently unexposed case retains ordinary live rendering

#### Scenario: R12 The actual notification bridge cannot clear protection

- **WHEN** the registered insight-delivery job sends through its production notify closure with any candidate order, channel tie or caller false flag
- **THEN** the closure MUST carry the persisted union of protection into the authenticated canonical Messenger boundary
- **AND** the existing direct-delivery branch or an ignored metadata flag MUST NOT count as enforcement; recipient resolution, intent feedback, budgets and failure mapping MUST remain intact

#### Scenario: R13 Unsupported rendering or permission failure refuses truthfully

- **WHEN** the selected channel cannot enforce protection or current recipient permission is unavailable
- **THEN** canonical fail-closed or PARK behavior MUST prevent a raw fallback send
- **AND** the candidate MUST NOT be falsely marked sent; provider-start, at-most-once and ambiguous-outcome rules MUST remain unchanged

#### Scenario: R14 Any used temporal continuation restores trusted protection

- **WHEN** an implementation actually routes Finance delivery through the generic temporal queue
- **THEN** the stored envelope, digest coalescer and real scheduler/background replay MUST preserve the union of protection and restore the trusted private mark before canonical dispatch
- **AND** copying a public Boolean into storage alone MUST NOT count; the broker-owned delay/retry path MAY instead be retained without adding a second queue
