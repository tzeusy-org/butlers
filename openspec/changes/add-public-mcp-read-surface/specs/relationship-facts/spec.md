## ADDED Requirements

### Requirement: Approved external proposals write third-party facts
When the proposal importer parks an external proposal, the writer SHALL record in `relationship.fact_approval_context`, server-side and never from tool arguments, `src = 'external_mcp:<proposal_id>'`, the proposal's receipt time as `observed_at` and a frozen report with `content_authority = 'third_party'`. On the owner's approval, replay SHALL follow REQ-relationship-facts-005 unchanged and SHALL write the fact with `assert_origin = 'approved'`, `content_authority = 'third_party'`, `confirmed_at` set and `confirmation_source = 'approved_action'`, so the fact is verified, owner-endorsed and still attributed to an outside source. The owner's approval of a third-party frozen report SHALL satisfy the admitted-decision check without changing the authority to an owner class. Such a fact SHALL never become an owner-profile fact, an identity channel, an override or a standing rule.

ID: REQ-relationship-facts-011
Source: RFC 0039 D13 steps 3 and 5; REQ-relationship-facts-005; heart-and-soul/security.md untrusted senders (explicit owner endorsement)
Scope: v1-mandatory

#### Scenario: Approved proposal keeps third-party authority
- **WHEN** the owner approves a parked external proposal `(A, "works-at", B)`
- **THEN** the written fact SHALL have `content_authority = 'third_party'`, `src = 'external_mcp:<proposal_id>'`, `assert_origin = 'approved'` and a non-null `confirmed_at`

#### Scenario: Owner approval is not refused for third-party authority
- **WHEN** the approval executes with the bound executor context for the original stored call
- **THEN** the admitted-decision check SHALL pass and the fact SHALL be written rather than re-parked or refused

#### Scenario: Provenance parameters in stored arguments are refused
- **WHEN** the stored tool arguments of an importer-parked action carry `src`, `content_authority` or `observed_at`
- **THEN** the write SHALL raise for the unknown parameters before any row is written

#### Scenario: Provenance comes only from the recorded context
- **WHEN** the stored tool arguments are valid and the owner approves
- **THEN** the written fact's `src`, `content_authority` and `observed_at` SHALL equal the values in `relationship.fact_approval_context`

#### Scenario: Owner edit keeps outside attribution
- **WHEN** the owner edits the object entity before approving
- **THEN** the edited triple SHALL be written, still with `content_authority = 'third_party'` and the original `src`

## Source References

- Non-Negotiable Rule 1 (owner sovereignty) and Rule 4 (deterministic infrastructure), `about/heart-and-soul/vision.md`.
- `about/heart-and-soul/security.md`, untrusted senders and steering memory: outside content never becomes owner authority.
- RFC 0039 (`about/legends-and-lore/rfcs/0039-public-mcp-read-surface.md`), D13; RFC 0038 D6 (stored data keeps its authority), as amended by RFC 0039.
- `openspec/specs/relationship-facts/spec.md`, REQ-relationship-facts-002 (central writer) and REQ-relationship-facts-005 (approved writes under server-recorded provenance).
