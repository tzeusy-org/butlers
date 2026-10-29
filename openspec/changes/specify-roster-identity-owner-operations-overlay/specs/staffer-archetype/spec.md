## ADDED Requirements

### Requirement: Staffer Shared-Instruction Participation Contract
Each staffer's infrastructure-contract `MANIFESTO.md` SHALL explicitly declare whether its versioned
guidance composes `roster/shared/AGENTS.md`, and the concrete staffer capability spec SHALL expose the
same opt-in or opt-out as an observable contract. The choice SHALL be based on compatibility with
the staffer's infrastructure responsibilities, not inferred from another staffer's file shape.
A newly added or substantively changed staffer with no declaration SHALL fail roster validation.
Existing staffer opt-ins SHALL remain in force unless their own infrastructure contract is separately
amended. The domain-butler first-line shared-include rule SHALL NOT be broadened to staffers.
This declaration SHALL govern guidance participation, not a domain-daemon session prompt. Staffer
triage SHALL use the lead/child goal contract; only the separately governed D18 engineering
executor retains its isolated executor contract, without extending that exception to other sessions.

ID: REQ-staffer-archetype-001
Source: heart-and-soul/vision.md agent types and Rule 6; specify-roster-identity-owner-operations-overlay design D3
Scope: v1-mandatory

#### Scenario: Staffer explicitly opts in
- **WHEN** a staffer's infrastructure contract declares shared-instruction participation
- **THEN** its versioned guidance resolves `roster/shared/AGENTS.md` before staffer-specific instructions
- **AND** its capability spec records the same opt-in

#### Scenario: Staffer explicitly opts out
- **WHEN** a staffer's infrastructure responsibilities conflict with domain shared instructions and its contract declares an opt-out
- **THEN** its versioned guidance omits `roster/shared/AGENTS.md`
- **AND** its capability spec records the same opt-out and rationale
- **AND** the opt-out does not weaken the staffer's own identity or security contract

#### Scenario: Missing staffer declaration is invalid
- **WHEN** a new or substantively changed staffer has no shared-instruction participation declaration in its infrastructure contract or capability spec
- **THEN** guidance governance validation fails before that required guidance is used
- **AND** file layout alone is not treated as an opt-in or opt-out

#### Scenario: Domain-butler rule remains separate
- **WHEN** shared-instruction conformance is evaluated for a domain butler
- **THEN** the domain-butler first-line include rule applies directly
- **AND** no staffer opt-out can exempt that domain butler
