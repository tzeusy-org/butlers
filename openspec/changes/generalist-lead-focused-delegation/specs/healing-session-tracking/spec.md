## MODIFIED Requirements

### Requirement: Session ID Accumulation
When a goal invocation fails with a fingerprint that already has an active (non-terminal) healing attempt, the failing invocation's `invocation_id` SHALL be appended to the existing attempt's `session_ids` array. The column keeps its name; rows written before cutover hold session IDs.

Source: RFC 0038 D17
Scope: v1-mandatory

#### Scenario: Additional session linked to existing attempt
- **WHEN** invocation `i2` fails with fingerprint `fp1` and an `investigating` attempt exists for `fp1` with `session_ids = [i1]`
- **THEN** the attempt's `session_ids` is updated to `[i1, i2]`

#### Scenario: Duplicate session ID is idempotent
- **WHEN** the same session ID is appended twice (e.g. due to retry logic)
- **THEN** the `session_ids` array does not contain duplicates (use `array_append` only if not already present)
