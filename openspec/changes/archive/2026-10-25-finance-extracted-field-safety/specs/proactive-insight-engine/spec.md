## ADDED Requirements

### Requirement: Finance contribution protection survives durable insight processing

The insight broker MUST preserve a Finance contribution protection floor bound to the exact subject, display value, revision and message through persistence and every derived message. Only the trusted deterministic producer SHALL supply an owner-only proof; public JSON labels, current session context and derived text MUST NOT upgrade stored provenance.

ID: REQ-proactive-insight-engine-035
Source: bu-svqpzo.19 proposed persisted contribution contract; RFC 0038 D6; canonical Messenger bu-vbdug9.1
Scope: v1-mandatory

#### Scenario: R01 Extracted subscription names remain protected through the actual producer

- **WHEN** an extracted subscription service contains a bare host, an HTTPS URL or hostile markup
- **THEN** the real registered writer, stored row and Finance scan MUST preserve its extracted origin through candidate persistence to the actual final delivery consumer
- **AND** raw stored names MUST remain unchanged while text and metadata readback MUST NOT create an active extracted link or preview

#### Scenario: R02 Extracted payees use the same protection

- **WHEN** a bill due, reconciliation or predicted-bill candidate incorporates an extracted payee
- **THEN** the same origin and revision protection MUST survive the actual bill writer and name-bearing producer branch
- **AND** payment destination, bill settlement and reconciliation authority MUST remain unchanged

#### Scenario: R06 Unprovable contribution origin stays conservative

- **WHEN** a candidate is legacy, lacks a valid producer binding or carries forged, malformed or conflicting provenance
- **THEN** its Finance display contribution MUST require inert rendering
- **AND** public MCP JSON and a later owner session MUST NOT construct an owner-only proof, clear the protection floor or rebind it to different content

#### Scenario: R07 Broker delay and restart retain the protection floor

- **WHEN** a candidate is held for quiet hours, attention policy, day boundary or process restart
- **THEN** the persisted row and its later replay MUST retain protection with the original subject, value and revision bindings
- **AND** current quiet/context checks, expiry, priorities and budgets MUST still apply; re-reading current owner context MUST NOT upgrade the stored origin

#### Scenario: R08 Failed delivery and replay do not lose protection

- **WHEN** a protected delivery fails or is replayed by the existing broker retry path
- **THEN** all subsequent attempts MUST retain its protection and exact contribution bindings
- **AND** existing failure and attempt accounting, three-failure policy and unknown-outcome semantics MUST remain; no automatic duplicate-send or safe-retry claim is added

#### Scenario: R09 Derived summaries and amendments inherit all contributors

- **WHEN** synthesis, digest formatting, folded amendments or prepared-action doors derive text from protected contributions
- **THEN** the resulting text MUST require the union of every contributor protection floor
- **AND** paraphrase, ordering or a newly generated summary MUST NOT clear protection; separately bound server-created controls MUST keep their own authority without making extracted text active

#### Scenario: R15 Successful in-place amendments retain the complete protection floor

- **WHEN** a stored Finance insight is amended in place through `apply_pending_amendments`, the production `_build_switchboard_insight_amend_fn`, `amend_delivery` and the registered Telegram edit consumer
- **THEN** the complete replacement MUST inherit the union of the persisted original message's and every new contributor's protection, bound to their exact values and revisions; a current session, public flag or resolution note MUST NOT clear it
- **AND** a successful protected edit MUST use the same canonical rendering primitive for all replacement text, with every link inert and previews disabled, while preserving authenticated target/message identity and current edit permission
- **AND** if that trusted edit path cannot enforce protection, it MUST refuse before the provider call and truthfully use the existing rejected-edit/fold path with the full persisted protection union; it MUST NOT mark the raw edit applied or lose protection in the later digest
- **AND** owner-only independently unexposed edits MUST retain ordinary rendering, and existing amendment CAS, attempt limits, cancellation, ambiguous outcomes and replay accounting MUST remain; no unknown edit becomes a claimed success, duplicate send or invented safe retry
