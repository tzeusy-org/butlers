## Proposed modified requirement

### Requirement: One Exact-Artifact Recovery Verdict

The dedicated protected writer SHALL be the sole authority for new recovery
attempts after the approved CronJob cutover. Existing executor-owner ledger
rows SHALL retain their original provenance. One writer-attested immutable row
and one database transaction SHALL bind the overall result to the admitted Job
generation, exact artifact, verified completion time and byte size, every
required scoped verdict, cleanup and policy digest. A public audit, attention
row, filesystem timestamp, filename, Job status, run receipt or manifest alone
SHALL NOT authorize or manufacture a recovery pass.

ID: REQ-backup-recovery-truth-003
Source: Non-Negotiable Rule 4; RFC 0005 Workflow and Recovery Telemetry
Scope: v1-mandatory

#### Scenario: Separately adopted scoped manifest binds only its named scope

- **WHEN** the owner has separately adopted the exact proposed filtered-event
  artifact and a selected backup carries its implemented manifest
- **THEN** its exact basename, byte length, SHA-256, manifest SHA-256, and
  capture completion bind the filtered-event verdict as required by
  `REQ-deployment-hardening-008` and `REQ-deployment-hardening-009`
- **AND** the manifest cannot substitute for direct artifact integrity,
  application-data restoration, ownership/ACL verification, or cleanup

#### Scenario: Unapproved sibling supplies no implementation authority

- **WHEN** `artifact-bound-filtered-event-restore-verification` has not received
  exact-artifact independent review and explicit owner adoption
- **THEN** no implementation or recovery proof may rely on its manifest,
  checker, protected transition, or requirement IDs as adopted behavior
- **AND** review, CI, PR state, merge, or adoption of this umbrella alone cannot
  satisfy that sibling gate

#### Scenario: Projected artifact facts share the attempt binding

- **WHEN** a protected attempt records artifact completion time or byte size
- **THEN** those fields are persisted in the same immutable row as the verified
  artifact digest, manifest digest, scoped results, and overall result
- **AND** the protected writer accepts them only with the exact verified digest,
  and `latest_result()` returns them from that row rather than filesystem recency
- **AND** a digest-verified internal join may resolve the artifact but cannot
  combine an older pass with a newer artifact's time or size

#### Scenario: Full proof is one atomic attempt

- **WHEN** an attempt reaches a terminal result
- **THEN** the same protected row contains its FK-closure result,
  credential-coverage result, filtered-event result, ownership/ACL result and
  policy digest, cleanup result, fixed scope, and artifact bindings
- **AND** `proven` is impossible unless every required result in that row passes

#### Scenario: Partial or cross-artifact evidence cannot pass

- **WHEN** a required manifest is missing, malformed, stale, incomplete,
  mismatched, or belongs to another artifact, or any required checker is
  unavailable or fails
- **THEN** the attempt is `failed` or `unproven` according to its completed
  stage and never `proven`
- **AND** no last-known pass, live-database comparison, filename, mtime, or
  successful subset fills the missing evidence

#### Scenario: Crash and retry preserve attempt identity

- **WHEN** the admitted drill or protected writer crashes before or after authoritative persistence or
  retries the same artifact
- **THEN** a pre-commit crash leaves no authoritative attempt and a post-commit
  crash leaves one complete immutable attempt
- **AND** every retry recomputes its own verdict and cannot copy or associate an
  earlier scoped pass or binding with a later attempt

#### Scenario: Concurrent execution remains serialized

- **WHEN** another restore attempt is requested while an attempt for the same
  backup catalog owns the scratch lifecycle or its retirement is uncertain
- **THEN** admission refuses the second generation before artifact access
- **AND** neither a second release, manual Job nor retry can bypass the guard

#### Scenario: Submission is bound to the admitted attempt

- **WHEN** the protected writer receives evidence
- **THEN** it validates the approved Job generation and artifact identity before
  persisting the one allowed result
- **AND** an authenticated retry after an acknowledgement loss returns the
  existing receipt for the same identity and content
- **AND** a retired generation cannot submit. After retirement only the
  protected control plane can look up the exact receipt without writing
- **AND** conflicting content is refused
- **AND** an uncommitted attempt or unavailable writer never advances proof

## Source References

- Non-Negotiable Rule 4, Vision, and RFC 0005, Workflow and Recovery Telemetry.
- Owner-adopted `REQ-backup-recovery-truth-003`, PR #4170 at `f2e59fc6`.
  The authoritative main landing is pending under `bu-h40h2b.3`.
