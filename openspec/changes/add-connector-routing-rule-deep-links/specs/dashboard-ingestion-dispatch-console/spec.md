## ADDED Requirements

### Requirement: Connector routing-rule deep-link fidelity

Connector-detail routing-rule rows SHALL link to `/ingestion/filters?rule=<URL-encoded id>` using the full rule id. Filters SHALL interpret `rule` as an exact navigation focus target, not URL-backed filter state, and SHALL never silently substitute another rule. The target SHALL be highlighted, scrolled into view, and visibly focused with an accessible name once per decoded target change after its authoritative read completes. Navigation SHALL invoke no server mutation or editor action.

This requirement extends the baseline Connector Detail and Filters Pipeline requirements and composes with `restore-ingestion-console-spec-coverage` without changing its filter-control contract. Highlighting and focus SHALL preserve the `dashboard-design-language` Interaction Affordances global outline, use the existing focus token, include a non-color linked-rule cue, and respect reduced motion through instant scrolling.

#### Scenario: Exact active target is revealed

- **WHEN** the owner follows a connector routing-rule link and the complete nonarchived read succeeds with that exact id
- **THEN** Filters highlights, scrolls to, and focuses only that rule row, including when the rule is disabled
- **AND** reserved characters in the id survive encoding and decoding without truncation or selector interpretation
- **AND** no rule mutation or editor is invoked

#### Scenario: Archived target waits for its read

- **WHEN** the complete active read contains no exact target and the archived read is still loading
- **THEN** Filters announces that the linked rule is loading without declaring it absent or focusing another row
- **WHEN** the complete archived read succeeds with the exact target
- **THEN** the archived section expands and highlights, scrolls to, and accessibly focuses that row
- **AND** no restore action is invoked

#### Scenario: Confirmed absent target is announced

- **WHEN** complete successful active and archived reads both contain no exact target
- **THEN** Filters announces that the linked rule no longer exists in an accessible status region
- **AND** it does not select a similarly named or partially matching rule

#### Scenario: Unavailable reads never establish absence

- **WHEN** a read required to resolve the target fails, has degraded-source metadata, lacks a complete rule list, or reports more total rows than returned
- **THEN** Filters announces the linked rule as unavailable with a retry control
- **AND** it does not announce the rule as missing or focus cached target data from a failed read
- **WHEN** retry completes the required reads successfully
- **THEN** Filters resolves the exact target or announces confirmed absence

#### Scenario: Focus follows target changes without refetch disruption

- **WHEN** the decoded target changes, including returning to a previous id after another target or removing and restoring it
- **THEN** the newly resolved exact row receives one focus transfer
- **WHEN** data refetches, retry succeeds after a prior focus, an unrelated query parameter changes, or the same target link is followed repeatedly
- **THEN** focus is not stolen again and no server state is written
