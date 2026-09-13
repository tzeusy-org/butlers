## ADDED Requirements

### Requirement: QA Shared-Instruction Exception
The QA Staffer's infrastructure contract SHALL explicitly opt out of composing
`roster/shared/AGENTS.md` because those domain-facing instructions can conflict with QA's isolated
investigation and infrastructure responsibilities. QA's `CLAUDE.md` SHALL continue to resolve its
QA-specific `AGENTS.md`; the approved opt-out SHALL require no prompt-byte change.
The opt-out SHALL be represented consistently in QA's infrastructure-contract MANIFESTO and this
capability. It SHALL NOT exempt QA from its own identity, sandbox, privacy, MCP isolation, or
human-in-the-merge-seat obligations, and SHALL NOT establish an opt-out for another staffer or any
domain butler.

ID: REQ-staffer-qa-005
Source: [Observed] PR #3110 and roster/qa; specify-roster-identity-owner-operations-overlay design D3
Scope: v1-mandatory

#### Scenario: QA keeps its approved opt-out
- **WHEN** QA's roster identity is resolved
- **THEN** `CLAUDE.md` resolves the QA-specific `AGENTS.md` without composing `roster/shared/AGENTS.md`
- **AND** QA's existing `CLAUDE.md` and `AGENTS.md` bytes remain unchanged by adoption of this contract

#### Scenario: QA infrastructure contract records the exception
- **WHEN** the QA shared-instruction policy is validated
- **THEN** QA's MANIFESTO and capability spec both declare the opt-out and its infrastructure-conflict rationale
- **AND** either declaration missing causes governance validation to fail

#### Scenario: QA-specific safeguards remain binding
- **WHEN** QA operates under the shared-instruction opt-out
- **THEN** its existing investigation sandbox, privacy, credential-isolation, MCP-isolation, and no-merge guarantees remain unchanged
- **AND** the opt-out grants no new prompt, database, deployment, or repository-write authority

#### Scenario: QA exception does not spread
- **WHEN** another staffer or a domain butler is validated
- **THEN** QA's opt-out supplies no default or exemption for that agent
- **AND** the other agent follows its own staffer contract or the domain-butler shared-include rule
