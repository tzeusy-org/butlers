## ADDED Requirements

### Requirement: Credential-Target Audit Group Identity Includes The Persisted Failure Category
A credential-target audit-error group SHALL be identified by the credential
**and** the cause of the failure, where the cause is a `failure_category` value
persisted on the row at write time. The synthetic group title SHALL be composed
only from `action`, `target`, and `failure_category`, and SHALL NOT be derived
from the row's `error`, `note`, or `metadata` at read time.

`failure_category` SHALL hold only a member of
`butlers.api.models.audit.PROBE_FAILURE_VOCABULARY` — `not_set`, `expired`,
`rejected`, `rate_limited`, `provider_error`, `malformed`, `unverified`,
`other` — or `NULL`. It SHALL NEVER hold a raw probe-status token, a provider
HTTP status code, a provider string, or any audit free text. The value is
*selected* out of that closed set, never *derived* from an input string, so a
new provider message cannot widen what the column can contain.

This requirement REVISES this capability's `Credential-Target Audit Groups Are
Identified Without Free Text`, and specifically supersedes its scenario
**"Identity is the credential, not the cause"**. That scenario's reasoning —
"the only per-cause signal on the row is the withheld text itself" — described
the row as it was then written, not the failure: the category was already being
derived for `TestResult.message` and discarded instead of stored. Where the two
speak about the same row, this requirement governs. Everything else in that
requirement stands unchanged, including its content-blindness rule, its
distinguishability rule, its non-credential carve-out, and its single-predicate
rule.

The rule SHALL be enforced in the shared grouping CTE
(`src/butlers/api/audit_grouping.py`), for the same reason its predecessor is:
one definition for the Issues feed, the briefing attention items, the
occurrences drill-down, and the audit-row-to-group resolver.

#### Scenario: Two causes on one credential are two groups
- **WHEN** one credential (e.g. `u:google`) has a failure row categorised
  `rejected` and another categorised `rate_limited` in the same window
- **THEN** they are two groups, with two occurrence counts, two `issue_key`s,
  and two independent acknowledgements
- **AND** the published title names the cause, so an operator can tell the two
  rows apart
- **BECAUSE** acknowledging a transient throttle must not silently acknowledge a
  credential the provider has stopped accepting.

#### Scenario: Repeats of one cause stay one group
- **WHEN** one credential fails three times with the same `failure_category`
- **THEN** all three fall in one group whose `occurrences` count reports three
- **BECAUSE** the identity is the cause, not the occurrence: a feed of
  singletons would replace an over-broad group with an unreadable one.

#### Scenario: The category is stored, never recovered from withheld text
- **WHEN** a credential-audit producer writes a `result = 'error'` row
- **THEN** it passes an already-selected vocabulary member, which is stored in
  `public.audit_log.failure_category`
- **AND** no grouping surface parses `note`, `error`, or `metadata` to obtain a
  cause
- **BECAUSE** parsing the withheld text at read time would put the provider's
  own words back into a group title, which is the inversion this capability's
  withholding requirements exist to prevent.

#### Scenario: The database refuses anything outside the vocabulary
- **WHEN** any writer inserts into `public.audit_log`, including one that
  bypasses `audit.append()`
- **THEN** a `failure_category` that is neither `NULL` nor a vocabulary member
  is rejected by a CHECK constraint
- **AND** a non-member handed to `audit.append()` is clamped to `other` and the
  audit row is still written
- **BECAUSE** an audit write is fire-and-forget: a mislabelled category must
  cost the label, never the record of the failure.

#### Scenario: Every producer that can write a failure names a category
- **WHEN** a credential-audit writer can emit `action = 'failed'`
- **THEN** it passes a `failure_category`
- **AND** producers that can only emit success actions pass none, and their
  rows' `NULL` is correct rather than a gap, since the grouping CTE reads only
  `result = 'error'` rows
- **BECAUSE** one uncategorised failure path silently reopens the coarse group,
  so the guarantee is a property of the whole producer set and is verified by
  enumerating it rather than by exercising one endpoint.

#### Scenario: Rows written before the category existed keep their group
- **WHEN** a credential-target error row has `failure_category IS NULL`
- **THEN** its group title is byte-identical to the title it had before this
  change, so its `error_summary`, its `group_key`, and any acknowledgement
  already attached to it are unchanged
- **AND** such rows SHALL NOT be backfilled from `note`, `error`, or `metadata`
- **AND** a still-failing credential may therefore show a frozen uncategorised
  group beside its new categorised one until the old one ages out of the window
- **BECAUSE** the only surviving per-cause signal on a historic row is the
  withheld text, and one bounded transitional duplicate is cheaper than either
  parsing that text or orphaning every existing acknowledgement.

#### Scenario: The wire projection does not widen
- **WHEN** a credential-target row is serialized by `GET /api/audit-log`,
  `GET /api/audit-log/{id}`, or `GET /api/issues/{issue_key}/occurrences`
- **THEN** `failure_category` is not among the published fields
- **BECAUSE** `butlers.api.models.audit` remains the single enforcement point
  for what a credential row discloses; persisting the cause changes how rows
  group, and is not licence to change what each row says.
