# Relationship temporal provenance candidate

`src/butlers/relationship_temporal_cutover.py` is a dormant feasibility
candidate for bu-h3b7t. It has no migration, daemon, CLI, bootstrap or generic
test-factory caller. It does not reserve G, advance the Relationship head,
install a production admission path, or complete unsigned cutover. Historical
`rel_035` and `rel_036` remain unchanged.

The adopted temporal behavior lives in
[relationship-fact-effective-time](../../openspec/changes/relationship-fact-effective-time/specs/relationship-facts/spec.md).
The complete receipt, fleet, mutator, isolation, lifecycle and database proof
obligations live in
[authorize-relationship-effective-time-cutover](../../openspec/changes/authorize-relationship-effective-time-cutover/specs/relationship-effective-time-cutover/spec.md).
All six current, ten historical and seven fresh-database terminal outcomes
remain mandatory and undelivered. This candidate is a prerequisite experiment
within that whole owner, not a substitute for those contracts.

## Candidate protocol

The existing privileged bootstrap identity actually creates a new disposable
database owned by the existing normal migration login. It commits a birth
record before ordinary bootstrap or migration traversal. The record is a
catalog comment on a bootstrap-owned table in the existing managed
`relationship` namespace, coupled to that table's owner, database OID/name,
normal migration owner OID, cluster system identifier and trigger body hash.
The controller refuses an existing database; empty tables, an absent index or
an asserted caller label are not a creation result. There is no post-hoc
installation interface.

The normal migration connection obtains its real backend PID/start, full
transaction ID and actual Relationship revision. A separate trusted bootstrap
connection verifies that online challenge and commits a short-lived complete
supplied binding in the protected trigger function's catalog comment. The
issuer closes before consumption. A `SECURITY INVOKER` trigger checks actual
object ownership, canonical namespace/function, body hash, database and cluster,
session identity, backend, full transaction ID, revision, purpose, expiry and
exact proof/fence/authorization equality. It takes the actual `NOWAIT`
access-exclusive facts lock and rejects another client session. Only then can
the authenticated normal migration login insert the unique consumption nonce
with its existing inherited `butler_relationship_rw` privileges on that caller's
transaction. No function commits or acquires another
pool connection on that caller's behalf.

The client check calls `pg_stat_clear_snapshot()` after the lock and before
reading activity. The earlier challenge's transaction-local activity snapshot
cannot represent clients that connected later. This refresh makes the actual
client check current; it does not establish the future managed lifecycle fence
or prevent every later connection by itself.

Consumption retains the normal migration identity and requires its existing
managed-role membership. PostgreSQL hides activity fields after a downshift to
the managed role because that role is not a member of the login role. The
session comparison rejects missing activity data, and another backend with an
unreadable type counts as a possible client. This conservative rule can also
refuse an unreadable internal backend; unknown activity is never evidence of
zero clients. The harness witnesses the genuine
role-visibility difference and a hidden privileged competing client; no
monitoring grant or privileged function is added to obtain these results.

No authority role, cross-schema read/write grant, privileged reader,
`SECURITY DEFINER` function or custodian is introduced. Existing bootstrap
creates the managed namespace and owns the protected objects; it grants the
existing managed role its existing schema/table privileges. Catalog comments
provide ordinary readback without a new private-schema reader grant. The
candidate adds a revoke-only narrowing hook for the protected table. The real
bootstrap deliberately widens managed-table privileges, so a future installed
hook must run in the **same transaction** as every replay's grants. The harness
does this explicitly; the actual production grant loop is not changed here.

## Evidence boundaries

The new integration species is
`tests/config/test_relationship_temporal_cutover_candidate.py`. It uses the
existing PostgreSQL 17 container and normal migration-login factory, actual
`init-db.sql`, and actual core plus Relationship chains. It plants all-validity
fact/evidence rows; tests ordinary database-owner and own-role catalog/DDL/DML
attacks; checks session, epoch, fence, expiry, revision, concurrent-client and
lock refusals; and composes consumption, the actual legacy index drop and a
**synthetic harness stamp** on one transaction. Positive commit and rollback
are read back through separate normal connections. The harness stamp is not G
and is restored only on its disposable target.

Supplied source/image/tree/configuration, inventory, test and isolation claims
are explicitly synthetic conformance inputs. The JSON canonicalization is a
candidate-local encoding, not the production receipt's RFC 8785 implementation.
Validation checks the candidate input shape and coupling; it does not enumerate
actual Compose services, collect an exhaustive mutator inventory, execute the
protected fixed test set, resolve an image or verify real isolation. Even a
genuine PostgreSQL PASS here proves only the supplied-input protocol. It cannot
be credited as actual collected origin, complete writer-cohort/fence proof,
unsigned G admission, registered caller traversal, or production bootstrap
installation. The independent outer collector and all required protocol and
mutator cases remain mandatory future work.

The grant replay has a causal negative control: replaying the unchanged grants
without narrowing must permit a real own-role update of a previously committed
consumption row. Replaying with narrowing must leave the ordinary actor denied
or blocked during the uncommitted grant and denied after commit. This control
does not make the omitted-hook path a supported advancement route.

Birth survives a real interrupted bootstrap transaction and a committed
bounded `rel_034` traversal followed by the actual `rel_035`/`rel_036` chain.
New session binding after that traversal is inner protocol resume evidence.
Actual independently collected unsigned G/bootstrap/committed-partial/resume
proof remains separate and undelivered. Existing generic head fixtures retain
their unchanged requested target; this candidate does not silently stamp or
lower them.

## Trust and recovery limits

The normal database owner and existing own role are adversarial in the
candidate controls. Their inability to rewrite, replace, drop, disable or forge
the protected objects must be established by actual SQL, not inferred from
their names or ACL text. A writable body/comment lookalike on a separate real
migrated target must refuse. Protected object absence must also refuse; it is
never freshness authority.

The existing migration/database owner can erase its own fact/history rows and
their real evidence cascades; the harness positively demonstrates that retained
authority after the atomicity assertions. The protected ledger is not a
fact-history integrity boundary and does not infer historical detection from
its own unchanged birth comment.

The existing privileged bootstrap actor and host remain trusted. That actor
can erase a committed ledger nonce and mint another session binding, drop and
recreate objects, or restore a database. The harness positively demonstrates
the nonce-erasure residual. Catalog ownership cannot establish history against
that same trusted authority. Full privileged erase/import/reset is outside the
existing adversarial boundary and is not claimed detectable here.

PostgreSQL 17 documents public control-data functions and catalog comments;
their actual visibility under the pinned image and post-bootstrap ordinary
roles is part of the real SQL species, not a reason to add a guessed grant.
See [control-data functions](https://www.postgresql.org/docs/17/functions-info.html#FUNCTIONS-CONTROLDATA),
[comments](https://www.postgresql.org/docs/17/sql-comment.html), and
[privileges](https://www.postgresql.org/docs/17/ddl-priv.html).
PostgreSQL's [activity visibility](https://www.postgresql.org/docs/17/monitoring-stats.html#MONITORING-PG-STAT-ACTIVITY-VIEW)
explains why role membership affects the activity fields available to an
ordinary invoker. The pinned-image assertions remain the execution evidence.

Local Docker access is unavailable in the authoring environment. Collection and
static checks do not prove SQL. The standard hosted disposable-PostgreSQL job
must execute this exact source and named cases before candidate feasibility
can be assessed. A failed control is a feasibility result requiring a concrete
repair or alternative; it cannot be converted into admission credit by mocks,
an injected receipt, a permanent unsigned refusal or a new trusted actor.
