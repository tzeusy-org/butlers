## ADDED Requirements

### Requirement: [TARGET-STATE] QA local scheduler reads only its own operator policy

Switchboard SHALL offer a read-only, no-argument projection of the exact
`qa` administrative policy to the effective `butler_qa_rw` runtime role.
The projection SHALL return only validated `policy_state` and
`policy_provenance` from the current Switchboard-owned row. It SHALL NOT
accept a caller name or expose registry, endpoint, observation, timestamp,
actor, reason, or boot data. PUBLIC, other roles, and the role-less
migration-login audit pool SHALL have no read authority through this seam.
QA SHALL use its own runtime-role pool for local scheduler admission and
SHALL NOT infer policy from remote liveness or cached positive evidence.
The "derived staleness cannot stop QA" guarantee of `REQ-staffer-qa-006`
remains absolute; ambiguous legacy `review_required` and typed missing,
malformed, or unreadable policy results are distinct safety refusals, not
derived-staleness gates or attributed owner stops. An
`operator`-provenance `review_required` row retains its owner attribution.
This target contract requires explicit owner adoption of the proposed Rule 3
exception before implementation.

ID: REQ-staffer-qa-009
Source: heart-and-soul/vision.md Rule 3; RFC 0001 local scheduler amendment;
RFC 0003 Switchboard policy ownership; proposed-rfc-0010-amendment.md;
REQ-staffer-qa-006
Scope: v1-mandatory

#### Scenario: TTL-derived remote staleness does not stop a local patrol
- **WHEN** QA's own runtime and database are operational, its remote observation is stale, and the fixed projection returns `active/legacy_ttl`
- **THEN** the due local QA patrol remains eligible
- **AND** it does not treat the legacy `eligibility_state` quarantine as an owner pause

#### Scenario: Switchboard sweep keeps its separate authority
- **WHEN** Switchboard's own policy is active but its remote observation is stale
- **THEN** its local eligibility sweep remains eligible under the existing Switchboard runtime role
- **AND** it does not use QA's fixed projection or acquire QA's read grant

#### Scenario: Explicit and ambiguous holds remain distinct
- **WHEN** the projection returns `paused` or `quarantined` with valid operator provenance
- **THEN** QA suppresses new local cron/deadline dispatch while preserving due-work continuity
- **AND** `review_required/legacy_ambiguous` suppresses dispatch as unresolved history without owner attribution, while `review_required/operator` remains an explicit owner review hold

#### Scenario: Exact role and name cannot be chosen by a caller
- **WHEN** a caller invokes the projection without effective `butler_qa_rw`, from another runtime role or the role-less audit login, or attempts to select another butler name
- **THEN** the read is denied without another daemon's policy or a raw registry row
- **AND** the function has no name or endpoint parameter, no PUBLIC EXECUTE, and grants no direct QA SELECT on Switchboard tables

#### Scenario: Missing or malformed policy cannot authorize dispatch
- **WHEN** the exact QA row is absent, an enum or provenance pair is inconsistent, role enforcement is missing, or the projection is unreadable
- **THEN** QA records a typed missing, malformed, denied, or unavailable policy result and admits no new local job
- **AND** it does not fall back to legacy eligibility, the migration-login audit pool, or a cached `active` result, nor present the refusal as an owner pause or healthy patrol

#### Scenario: Owner change is rechecked before new admission
- **WHEN** an authenticated owner pause commits after one local admission but before a later due task is admitted
- **THEN** the later admission reads the current committed policy and does not dispatch that task
- **AND** an already launched task is not falsely described as retroactively cancelled

#### Scenario: Bootstrap and rollback preserve the authority fence
- **WHEN** `init-db.sql` grants are replayed or code/migrations roll back
- **THEN** the exact projection remains role-scoped and existing Switchboard RLS/owner-policy history remains effective
- **AND** rollback does not clear a restrictive hold or reactivate a legacy fail-open scheduler path
