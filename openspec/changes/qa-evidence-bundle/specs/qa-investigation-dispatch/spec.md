## ADDED Requirements

### Requirement: The Investigator Receives A Dispatcher-Built Evidence Bundle

Before launching an investigation the dispatcher SHALL build an evidence bundle from data it can
observe: the failing sessions and tool-call failures of the finding, log lines matching its
fingerprint in its window, the call-site file's recent git history, and recent dispatch outcomes
for the source butler. Every body SHALL pass the anonymizer and its validator, each item SHALL
carry a stable id and a content digest, and the bundle SHALL carry a revision and a total digest.
A source that cannot be read SHALL be marked unavailable and never invented; items and bytes SHALL
be capped with an explicit truncated status. The dispatcher SHALL record the bundle on the
finding before launch and SHALL write only a read-only copy to the agent's input area. Git history
SHALL run on the dispatcher's own repository with a pinned argument vector, no external diff or
textconv, and no repository, global or system configuration. If any body cannot be made clean the
launch SHALL be refused, and that refusal SHALL NOT count as a launched execution for the circuit
breaker.

ID: REQ-qa-investigation-dispatch-017
Source: bu-ejow5p.13 (run 25)
Scope: v1-mandatory

#### Scenario: A source is unreadable

- **WHEN** the tool-call failure view cannot be read
- **THEN** that section is marked unavailable and the investigation still launches with the rest

#### Scenario: A body cannot be anonymized

- **WHEN** an evidence item still carries sensitive content after anonymization
- **THEN** no agent is launched and the attempt is not counted toward the breaker
