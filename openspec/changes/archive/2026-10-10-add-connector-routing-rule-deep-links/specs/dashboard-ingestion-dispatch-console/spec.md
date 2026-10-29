## ADDED Requirements

### Requirement: Connector routing-rule deep-link fidelity

Every connector-detail routing-rule row SHALL link to `/ingestion/filters?rule=<URL-encoded id>` for that exact rule. Filters SHALL interpret `rule` only as a navigational focus target and SHALL NOT mutate connector or rule state while resolving it. The target SHALL be compared by exact decoded id against complete successful non-archived (including disabled) and archived reads. Filters SHALL reveal, visibly highlight, scroll into view and accessibly focus only that exact row once per target change, following dashboard-design-language Interaction Affordances. This requirement composes with Connector Detail, Filters Pipeline and the active restore-ingestion-console-spec-coverage change.

#### Scenario: Active rule target is exact

- **WHEN** the owner follows a connector rule link and both rule reads complete successfully
- **THEN** Filters decodes the id once and reveals, highlights, scrolls to and focuses that exact non-archived rule row, including a disabled rule
- **AND** other rows do not receive the target highlight or focus
- **AND** the row has an accessible name and visible focus and selection affordances
- **AND** no rule or connector mutation is issued

#### Scenario: Archived target arrives asynchronously

- **WHEN** the exact target exists in an archived response that finishes after the active read
- **THEN** Filters waits for the complete successful reads, expands the archived section and highlights, scrolls to and focuses that exact row
- **AND** the rule remains archived and no restore is issued

#### Scenario: Target is verified absent

- **WHEN** both complete rule reads succeed and neither contains the target id
- **THEN** Filters announces that the rule no longer exists
- **AND** no different rule is selected or focused

#### Scenario: Rule reads are pending or unavailable

- **WHEN** either rule read is pending, incomplete or failed, including a failed refresh that retains stale rows
- **THEN** Filters does not declare the rule missing and does not focus a stale match
- **AND** pending reads show a loading status and failed or incomplete reads show an unavailable status with retry
- **AND** successful retry resolves the exact target or verified absence

#### Scenario: Focus follows target changes without interrupting the owner

- **WHEN** the owner changes the decoded target id, or removes and later re-adds it
- **THEN** Filters permits one reveal, scroll and focus move for the new target after complete successful reads
- **AND** repeated links to the same target, query refetches and archived re-expansion do not steal focus after that move
- **AND** no filter or server state is changed by navigation
