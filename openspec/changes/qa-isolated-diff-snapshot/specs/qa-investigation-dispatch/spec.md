## ADDED Requirements

### Requirement: Isolated Attempts Derive The Diff Snapshot From The Sealed Export

For an attempt that ran in an isolated Job, the dispatcher SHALL derive the diff snapshot from the
sealed export of the agent's branch and the trusted base objects of the dispatcher's own
repository, and SHALL NOT run git over the agent-written clone. The snapshot SHALL be the net
`base..head` diff in the same line model as the commit-time snapshot, bounded in file size, file
count and line count; a file that is binary or over the size bound SHALL render as a placeholder
line, not content. When the export cannot be sealed or the diff cannot be derived, the dispatcher
SHALL record an empty snapshot and SHALL NOT fail the attempt.

ID: REQ-qa-investigation-dispatch-016
Source: bu-ejow5p.20 (run 25, follow-up from bu-ejow5p.11 PR-C)
Scope: v1-mandatory

#### Scenario: An isolated attempt shows its diff

- **WHEN** an isolated attempt commits a change and its export seals
- **THEN** the dossier diff shows the same added and removed lines `git diff` shows for `base..head`
- **AND** no git process runs over the pod-written clone

#### Scenario: A refused export gives an empty snapshot

- **WHEN** the sealer refuses the branch or the base objects are unavailable
- **THEN** the snapshot is `[]` and the attempt continues to the publisher's decision
