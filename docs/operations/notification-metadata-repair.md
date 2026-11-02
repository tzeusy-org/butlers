# Notification metadata repair: serving-writer evidence

This read-only procedure prepares Section 2 of
`openspec/changes/repair-notification-metadata-read/tasks.md`. It implements the evidence interface
for REQ-core-notify-002; it does not authorize live execution, a historical data mutation,
deployment/restart, migration source in Section 3.1, archive, or production access. An execution
needs its existing environment authorization and a separately bounded invocation. Missing access
or evidence leaves the original outcome open.

## Gate and current limitation

Every actual process able to write `switchboard.notifications` must be proven to serve writer fix
#3458, commit `7d2bea3bc9c4fe64b8697b4017e946a83d71ec71`, or a Git descendant. This ancestry
condition is required in addition to the actual image/process/source relationship. A branch, tag,
checkout, merged PR, source file hash, passing regression or deployment-template inventory alone
cannot satisfy the gate.

Source preparation at `1751d8a0cfb248e85ce4c01dcb5d507909527b3d` found the complete
`log_notification` function text/AST identical to the fix, but that source is not a descendant of
#3458 in the complete Git graph. That proves the fixed function is present in that source; it
does not satisfy the required ancestry or prove that any running process loaded it. A process
bound to that non-descendant source therefore cannot qualify under the unchanged requirement.
Do not substitute semantic equivalence or an image containing a copied function. Unknown or
incomplete ancestry is UNPROVEN; proven non-descendant or unfixed source is NONPASS. This
procedure does not amend that rule or supply a workaround.

## Before any observation

Bind one explicitly authorized environment/context/namespace, the actual Switchboard database and
schema, the serving deployment, and the requested operation. A dev receipt certifies only its
named dev target and time window, never an unobserved production deployment. The procedure grants
no production authority and includes no credential discovery.

Before execution, record a finite envelope: allowed metadata-query classes, maximum inventory
queries, per-command and database-statement bounds, observation start/end and sample schedule,
maximum samples, and total elapsed bound. The governing contract requires a bounded clean window
but supplies no numeric duration or sampling minimum. Do not invent one, report an unspecified
window as clean, or silently extend bounds/retry until evidence appears. Preserve every scheduled
sample, missed/late sample, exception and completion status. Use only existing authorized client
and credential custody; do not print or copy credentials, DSNs, environment values, Secret/config
contents or unrestricted command arguments.

## 1. Enumerate every actual serving writer

Use read-only workload/container/process metadata for the named target. Inspect the actual running
set, including replicas, jobs and ad-hoc or long-lived processes; Helm/Compose templates are only
starting points. Bind each writer to its current process/container correlation, image digest or
runtime revision, recognized entrypoint identity, start/restart identity and source-mount class.
Do not dump raw argv, environment, mount tables or unrestricted process listings. Parse permitted
metadata internally and emit only the required non-secret identity/binding fields into a private
receipt. Unclassified processes capable of reaching the writer are a completeness gap.

Known source paths that the actual inventory must account for include:

- Switchboard's `log_notification` and delivery entry points, whether Switchboard runs inside
  `butlers-up` or in a separate service under the actual active profile.
- `dashboard-api`: notification retry/escalate redelivery calls `deliver` in-process and reaches
  `log_notification`. The API is a writer consumer, not merely a notification reader.
- The typed review-notification writer path and any other actual process able to use it. Retain
  its approval and best-effort behavior; absence from a static service list does not prove absence
  from the running set.

Other daemons may reach the Switchboard service rather than run its writer locally. Classify the
actual relationship; do not equate every client with a local writer or omit an in-process API
writer. Inventory completeness must be supported by the actual target and allowed commands, not
by a guessed process list.

## 2. Bind process, image, loaded source and ancestry

Join every current writer identity to its image/build receipt, recognized command, interpreter and
package root, and all source mounts. The baked `GIT_SHA` and existing deployment provenance API
(`src/butlers/api/routers/deployments.py`) are supporting interfaces. The latter can legitimately
report unknown or a hotreload worktree; neither is proof of a fixed loaded writer.

For an immutable image, prove the source/build-to-image-digest relationship and the actual running
process's image relationship. For mounted or hotreload source, prove the exact source and loaded
revision; an on-disk hash cannot attest a long-lived process's loaded module. Changing source,
missing build provenance, unknown loaded revision or ambiguous mount relationships leave that
writer UNPROVEN. No restart/redeploy is authorized to make qualification easier.

For each proven served source revision, use a complete source graph to verify equality with the
#3458 merge anchor or its ancestry. Record the anchor, served revision, graph completeness and
actual ancestry result. A missing object, shallow/incomplete graph or failed read is UNPROVEN;
a proven non-descendant is NONPASS. Preserve body/codec identity as additional source facts only.
Also bind the actual registered JSONB codec and the production writer, without invoking a test
notification or treating a source regression as a serving probe.

## 3. Verify the actual migration frontier and target

Through an already authorized owning read-only connection, qualify the exact Switchboard database,
schema and `notifications` relation. Use finite statement bounds and read-only session/transaction
settings; these settings are not a repair. Read the schema's actual `alembic_version` rows and
resolve their chain-qualified IDs against the exact reviewed migration source. The version table
can contain multiple chains; do not guess a singleton revision, select an arbitrary row or use a
core head as the Switchboard frontier. Source preparation observes `sw_051` as its Switchboard
source head, not an actual applied frontier or a reservation for the later repair revision.

Record only qualified revision IDs, source bindings and relation/qualification booleans. An
absent, inaccessible or wrong target, unknown chain, or source/applied-frontier mismatch blocks
repair. No credential value or unrestricted query result belongs in the receipt. Recheck the
current source/applied namespace only after successful Section 2 evidence before Section 3.1 is
allocated; held foreign migration revisions must not be adopted or guessed here.

## 4. Capture aggregate bands and the bounded clean window

Use the bound target's database clock to capture one observation start `t0`. This is an evidence
boundary, not the later migration's own repair-start cutoff. Define the historical candidate band
once using `created_at < t0` and `jsonb_typeof(metadata) = 'string'`. Retain only aggregate counts
and aggregate time bounds for that same fixed band. Separately count string-shaped rows created
at or after `t0` through each sample's captured database time. Never select or emit metadata,
message text, recipients, row identifiers or sample rows.

At every predeclared sample, record only database-clock bounds, the historical band's count/time
bounds, the post-start string count, qualification status and safe exception class/SQLSTATE. A
new string row or historical candidate-band growth is NONPASS. A missing/late/failed sample,
clock/band ambiguity or incomplete schedule is UNPROVEN. Do not reinterpret missing output as
zero or exclude a failed sample from the window. Polling is sampled observation; retain its
cadence and limits rather than claiming to rule out unobserved transients. No synthetic write is
authorized to generate traffic, and an idle window does not replace the real writer regression.

Re-read the actual writer inventory/source bindings and frontier after observation. Process,
image, restart, mount, source or frontier drift invalidates the affected binding and prevents a
clean Section 2 verdict; do not silently restart the observation clock. The receipt concerns only
the qualified target, fixed band and fully completed bounded window.

## 5. Retain a content-free receipt and stop at the boundary

Store the receipt outside the repository with mode 0600. Record the exact authorized target and
finite command identities/bounds, every writer's required process/image/source/ancestry
qualification, the source/applied frontier, all aggregate samples and gaps, observation limits,
and independent overall status. Sanitize exceptions to class/SQLSTATE; do not retain raw process
logs, raw query rows, health/notification content, account identifiers or secrets. Keep unknown,
non-pass and successful facts distinct. A script exit or absence of an error is not a complete
receipt.

Section 2 is successful only when the complete actual serving set qualifies under the unchanged
ancestry condition, frontier/target and all scheduled aggregate observations are complete, no new
strings or candidate-band growth is observed, and the required real-Postgres writer behavior is
proven by named non-skipped execution. A passing local API test or source-text identity cannot
supply any missing serving evidence. Otherwise retain the exact external blocker and stop before
historical mutation; do not issue manual SQL or a runtime workaround.

After actual Section 2 success, Section 3.1 may separately recheck the migration frontier and
prepare the next Switchboard migration. Its existing one-cutoff/one-transaction/one-layer parser,
22P02-only fallback, nonstring/post-cutoff preservation, absent-table no-op, rollback/replay,
aggregate-only evidence and data-no-op downgrade requirements remain unchanged. The normal
deployment runner is the only future repair path. Section 3.4 still separately requires aggregate
post-repair proof of zero pre-cutoff strings and no new strings over its actual bounded window
before archive; this preparatory procedure supplies none of that execution credit.

## Existing executing owners

- `tests/api/test_notifications.py`: shared normalizer and all three API paths, including decoder
  limits/non-object fallback, no recursive parse, effective status, pagination, degraded-source
  and mark-read behavior.
- `roster/switchboard/tests/test_tools.py`:
  `test_log_notification_persists_metadata_as_jsonb_object`,
  `test_log_notification_normalizes_non_json_metadata_as_jsonb_object`, and both parameters of
  `test_log_notification_persists_empty_metadata_as_jsonb_object`. These use the real registered
  JSONB codec and need actual named non-skipped PostgreSQL execution; no static fixture is that
  proof.
- `roster/switchboard/tests/test_deliver_search_path_integration.py`: separate cross-schema and
  public-only-search-path delivery placement. Keep this owner; it does not replace the writer's
  normalization/None/empty cases.
- `tests/config/test_switchboard_notifications_migration.py`: existing schema/index/replay owner;
  the future historical-conversion atomicity/parser/cutoff/replay controls remain unimplemented
  until Section 3 is actually allocated. Do not label the existing migration test as that proof.

No task checkbox, migration, runtime process or provider state is changed by this document.
