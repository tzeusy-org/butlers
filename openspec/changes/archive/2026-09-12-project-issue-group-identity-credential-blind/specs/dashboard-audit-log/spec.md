## ADDED Requirements

### Requirement: Credential-Target Audit Groups Are Identified Without Free Text
Every surface that derives an audit-error **group** from `public.audit_log`
SHALL identify a credential-target group by a synthetic title composed only from
structured columns persisted on that row, and SHALL NOT use the row's free text
— its `error`, `note`, or `metadata` — as any part of that identity. A
credential target is the same namespace this capability's `Credential-Target
Audit Free Text Is Withheld On Read` requirement governs: any `target` whose
scope segment is a credential-key scope from `core-credentials` §Credential-Key
Normalisation Function (`u:`/`user:`, `s:`/`system:`, `c:`/`cli:`).

This requirement NARROWS `dashboard-api` §`Issues Aggregation` ("audit-log
errors are grouped by normalized error message"). Where the two speak about the
same row, this one governs. It is the group-identity counterpart of the
row-projection rule above: without it the text that requirement withholds
per row returns as the group's title, and the pair would give opposite rules for
the same stored string.

The rule SHALL be enforced in the shared grouping CTE
(`src/butlers/api/audit_grouping.py`), not in any one router, because the same
CTE feeds the Issues feed, the briefing attention items, the occurrences
drill-down, and the audit-row-to-group resolver. A per-surface fix would let a
group's title disagree with its own drill-down, which is a 404 on a group the
feed just showed.

Server-side evidence is untouched. Writers keep persisting the note and the
error; the free text still reaches `public.audit_log`,
`public.secret_probe_log`, and the `last_test_message` cache. The operator reads
the provider's words at the database, which is where they belong.

#### Scenario: A credential probe failure's provider text is not a group title
- **WHEN** a `result = 'error'` row whose `target` names a credential is grouped
  for `GET /api/issues`, `GET /api/issues/{issue_key}/occurrences`, the
  briefing's attention items, or `GET /api/issues/group-for-audit/{audit_id}`
- **THEN** the group's `error_summary` is the synthetic title, and no part of
  the row's `error` or `note` appears in it
- **AND** no part of that text appears anywhere else in the response body
  either — not in `Issue.error_message`, not in the composed `description`, not
  in `Issue.type`, and not in `issue_key`
- **BECAUSE** `_write_credential_audit` stores the raw probe message in `error`
  via `credential_lifecycle_outcome(action='failed')`, and `error_summary` is
  the single column the group is `GROUP BY`'d on.

#### Scenario: Credential groups stay distinguishable from one another
- **WHEN** two different credentials (e.g. `u:google` and `u:notion`) each have
  failure rows in the same window
- **THEN** they remain two groups, with two occurrence counts, two
  `issue_key`s, and two independent acknowledgements
- **AND** a blanket constant summary is NOT an acceptable implementation, since
  it would collapse every credential failure in the fleet into one group and
  make one acknowledgement silently cover unrelated broken credentials.

#### Scenario: Non-credential groups keep their normalized error verbatim
- **WHEN** a `result = 'error'` row whose `target` is absent or names a
  non-credential resource (e.g. `butler:qa`, `rule:7`, a request path) is
  grouped
- **THEN** its `error_summary` is the existing normalization — the first line of
  `error`, with `/tmp/tmp<random>/` collapsed to `/tmp/.../`, falling back to
  `"Unknown error"` — published verbatim
- **BECAUSE** this is a credential-namespace carve-out, not a blanket gag: an
  operator log that cannot say what failed is not an operator log.

#### Scenario: The drill-down resolves the group the feed published
- **WHEN** `GET /api/issues/{issue_key}/occurrences` re-derives a
  credential-target group by binding the `error_summary` the feed published for
  it
- **THEN** it returns that group's rows, and the total agrees with the
  occurrence count the feed reported for the same window
- **BECAUSE** the feed and the drill-down build on the same
  `normalized_errors` CTE and bind on `error_summary`: a credential branch
  present in one and absent in the other would 404 the drill-down on a group
  the feed had just rendered.

#### Scenario: One definition of "this target names a credential"
- **WHEN** the grouping CTE tests a row's `target` and the `AuditLogEntry` model
  tests the same row's `target`
- **THEN** both evaluate the same exported pattern
  (`butlers.api.models.audit.CREDENTIAL_TARGET_PATTERN`), matching every scope
  spelling that can appear in the column — the canonical `u:`/`s:`/`c:` and the
  long forms `user:`/`system:`/`cli:`, since `public.audit_log.target` is never
  normalised on write
- **AND** a group's title and its rows' withheld columns can therefore never
  disagree about whether a namespace is credential-scoped.
