## ADDED Requirements

### Requirement: Owner-private visit brief document and PDF

Health SHALL generate a document or PDF from a visit brief only after the owner adopts the exact contract and the policy for each copy involved. An authorized generation SHALL begin with an explicit server-authenticated owner request pinned to one eligible Health brief and one immutable source snapshot. The current scheduled plain-text brief SHALL retain its existing contract. Every copied figure SHALL preserve its attribution and the brief SHALL retain truthful missing-data language and its record-of-health disclaimer.

Before creating or exporting a Doc, Health SHALL verify that the connected account, target folder, and applicable Doc satisfy the existing owner-private guard. Export SHALL bind to the inspected Doc version and the Health creation mark. PDF delivery SHALL use only a verified storage reference, size, and digest through the existing owner-only notification route. Health SHALL send no share link or grant and SHALL make no request to a clinic, third party, or fallback route.

Health SHALL bind all stages to one durable request and reuse confirmed effects only while current access permits. It SHALL give no delivery credit for an export or an ambiguous or partial send. An ambiguous create or send SHALL stop automatic replay. Receipts SHALL report safe stages and outcomes while keeping confidential content and exact references in their restricted store. Retention and cleanup SHALL follow the adopted policy for each copy and disclose incomplete or unverified cleanup truthfully.

ID: REQ-butler-health-007
Source: bu-jv1moa.35, bu-jv1moa.23, REQ-module-google-drive-001, REQ-module-google-drive-008
Scope: v1-reserved

#### Scenario: Owner authority and approved copies are required

- **WHEN** a schedule fires, a third party requests a file, or the policy for a requested copy is unresolved
- **THEN** Health SHALL create no Doc, store no PDF, and make no attachment send for that request
- **AND** the ordinary text brief SHALL retain its existing eligibility and delivery behavior

#### Scenario: The explicit request pins the attributed snapshot

- **WHEN** the owner requests an authorized file for a current snapshot with a stale reading
- **THEN** the artifact SHALL preserve the stale-data wording and the provenance of each figure
- **AND** if that exact snapshot is unavailable or superseded before creating the artifact, Health SHALL refuse it without contacting Drive

#### Scenario: Changed or unreadable privacy and source checks stop further effects

- **WHEN** a target folder fails its private check, the Doc's ACL or creation mark fails inspection, or an edit makes the exported bytes' version uncertain
- **THEN** Health SHALL refuse the affected stage and make no attachment request
- **AND** a failed preflight SHALL make no export request
- **AND** any Doc already confirmed SHALL remain accounted for with a content-free outcome

#### Scenario: Overlapping requests reuse the same confirmed generation

- **WHEN** two submissions carry the same authorized request identity and snapshot
- **THEN** at most one Doc SHALL be generated, at most one PDF SHALL be stored, and at most one attachment send SHALL be admitted
- **AND** a replay SHALL recheck current access before returning an existing artifact

#### Scenario: Export failure and ambiguous delivery remain distinct

- **WHEN** exporting the private Doc fails or the attachment outcome is partial or uncertain
- **THEN** the receipt SHALL show the furthest confirmed stage, without a delivered marker
- **AND** an uncertain create or delivery SHALL authorize no automatic repeat

#### Scenario: Confirmed attachment and copy-specific cleanup

- **WHEN** the approved Telegram route acknowledges the entire verified attachment
- **THEN** the restricted receipt SHALL record delivery once
- **AND** later cleanup SHALL apply only to the copy identities covered by the adopted policy and expose only an outcome for each copy it can verify
