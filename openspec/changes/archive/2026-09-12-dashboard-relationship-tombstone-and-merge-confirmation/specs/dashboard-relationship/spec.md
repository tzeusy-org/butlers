## ADDED Requirements

### Requirement: Tombstoned entity detail navigation is safe and explicit

When the canonical entity detail page receives a loaded entity whose `metadata.merged_into` is a nonempty string that differs from the loaded entity ID, the dashboard SHALL treat it as a frontend navigation fact. It SHALL replace-navigate to `/entities/<survivor>` using the existing client route and SHALL provide an accessible transient announcement. The merged-away record's source content and mutation actions SHALL NOT render while that replacement is pending.

If `merged_into` is absent, the entity detail page SHALL continue its normal behavior, including for archived records. If a present `merged_into` value is non-string, empty after trimming, or self-referential, the page SHALL not navigate and SHALL expose a named merge-metadata inconsistency with alert semantics.

#### Scenario: Valid tombstone redirects to the survivor
- **WHEN** `/entities/source-id` loads an entity whose `metadata.merged_into` is the nonempty string `survivor-id`
- **AND** `survivor-id` differs from the loaded entity ID
- **THEN** the client SHALL replace-navigate to `/entities/survivor-id`
- **AND** assistive technology SHALL receive a transient announcement that the merged record is opening
- **AND** source-record content and actions SHALL not remain available before the replacement completes

#### Scenario: Normal and archived entities do not redirect
- **WHEN** an entity detail page loads an entity with no `metadata.merged_into` value
- **THEN** the page SHALL render the entity detail normally without redirecting
- **AND** this SHALL remain true when the entity is archived

#### Scenario: Invalid tombstone metadata does not loop
- **WHEN** an entity detail page loads a present `metadata.merged_into` value that is non-string, empty after trimming, or equal to the loaded entity ID
- **THEN** the page SHALL not navigate away from the loaded record
- **AND** the page SHALL render a named merge-metadata inconsistency with `role="alert"`

### Requirement: Merge review requires final accessible confirmation

After the existing structural comparison has rendered and the operator selects a survivor, the Merge action in the merge-review dialog SHALL open a final alert-style confirmation before calling the existing merge mutation. The confirmation SHALL name the survivor and absorbed entity and shall be keyboard and screen-reader accessible through the shared dialog semantics.

Cancel, Escape, or closing the final confirmation SHALL make no merge request. Confirm SHALL invoke the existing merge mutation exactly once with the same entity IDs and `keepAs` selection that the direct action previously used. Existing successful-resolution and toast-based mutation error handling SHALL remain unchanged.

#### Scenario: Review opens a named final confirmation
- **WHEN** the merge comparison has rendered and the operator presses Merge after choosing a survivor
- **THEN** an alert-style confirmation SHALL open naming both the survivor and the absorbed entity
- **AND** the comparison SHALL remain the required prerequisite for reaching that confirmation

#### Scenario: Cancel or Escape does not commit a merge
- **WHEN** the final merge confirmation is open
- **AND** the operator presses Cancel or Escape
- **THEN** the confirmation SHALL close
- **AND** the merge mutation SHALL not be requested

#### Scenario: Confirm preserves the existing merge request and errors
- **WHEN** the final merge confirmation is open and the operator confirms
- **THEN** the merge mutation SHALL be requested once with the pre-existing `{ entityA, entityB, keepAs }` payload
- **AND** success SHALL retain the existing resolution and close behavior
- **AND** an error SHALL retain the existing merge error handling
