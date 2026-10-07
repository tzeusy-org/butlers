## ADDED Requirements

### Requirement: Candidate channel reports are not resolved identity

Single/bulk/normalized inbound lookup, recipient selection and owner-channel corroboration SHALL consume eligible active handles only. Third-party known-person candidates SHALL never become sender/recipient identity before actual owner adoption. Legacy NULL active handles and deterministic transitory dedup SHALL retain existing resolution; active ambiguity, canonicalization, entity liveness and temporal reader semantics SHALL remain unchanged.

ID: REQ-entity-identity-003
Source: bu-s11n0s.2 original Outcome/S1–S4; heart-and-soul/security.md; adopted owner-auth request admission; existing relationship/entity contracts
Scope: v1-mandatory

#### Scenario: Candidate exclusion has a reachable positive

- **WHEN** actual resolver SQL sees a candidate plus a distinct planted active/legacy-NULL handle
- **THEN** candidate SHALL not resolve and the active companion SHALL resolve under its real role
- **AND** genuine owner adoption SHALL make only its intended compatible survivor eligible
