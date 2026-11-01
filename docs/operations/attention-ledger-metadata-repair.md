# Attention ledger metadata repair

The delivered writer binds JSON text through `$11::text::jsonb`, which works
with raw asyncpg and the production JSONB codec. It prevents new double encoding.
It does not repair historical rows. This operator only changes explicitly
selected `public.attention_ledger.metadata` cells. It never changes the reader,
outcome, timestamps, notification references, another table, or database grants.

Source delivery is preparation. Actual live planning needs its own operational
allocation; actual data mutation needs the existing owner's signoff for that
environment, population and exact plan hash. `--apply-plan` selects a mode and
does not supply that authority. No repair has been executed merely because this
runbook, a test, or a PR exists.

## Selection and private transport

Use the reviewed source and an existing connecting identity admitted to
`butler_switchboard_rw`. The normal `Database.connect()` path verifies that role
and establishes it on every acquisition. The operator never calls `provision`,
discovers credentials, reads database environment defaults, or falls back to an
administrator. Missing permissions refuse.

Supply a complete PostgreSQL URL on stdin through an approved private secret
injection process. Do not put it in argv, shell history, reports or logs. Host,
port, username, password, database and explicit `sslmode` are required; accepted
modes are `disable`, `require`, `verify-ca`, and `verify-full`. Choose the actual
environment's approved TLS mode; there is no automatic SSL downgrade.

Selection is **1–128 explicit unique UUIDs**, sorted deterministically. The tool
does not discover a population. Each metadata value is bounded to 16 KiB and a
repairable inner JSON object to 32 nesting levels. Overbounds refuse, without
truncating data. The issue's old 334-row observation is not a current target or
proof of completeness. Larger approved populations require separately reviewed
finite selections; a failure in one batch must not be represented as global
atomicity across batches.

For an allocated planning operation, set `umask 077` and redirect stdout to a
new private plan outside every checkout:

```sh
# The approved injector supplies the URL as one stdin line; no literal URL here.
approved-private-injector | uv run --no-sync python scripts/repair_attention_ledger_metadata.py \
  --row-id <approved-row-uuid> > /private/outside-checkout/plan.json
sha256sum /private/outside-checkout/plan.json
```

`approved-private-injector` is a placeholder for that environment's allocated
secret transport, not a provided executable. The default mode uses a READ ONLY
REPEATABLE READ transaction. A plan records only version/source/target hashes,
row UUIDs, closed classifications and original/intended/nonmetadata hashes.
It contains no metadata, session/message/recipient value, DSN or database name.
The target hash binds the explicit connection tuple (excluding password) and
observed database/table OIDs, server endpoint, login and effective normal role.
It does not claim a globally unique cluster identifier or confer authority.

`repair` means exactly one JSONB string layer contains a strict JSON object.
Duplicate keys, nonfinite numbers, malformed JSON, another encoded string,
arrays, scalar values, decoded null and unsupported/oversized values refuse.
Existing objects and SQL NULL remain unchanged. Arbitrary legitimate keys and
nested values survive. PostgreSQL receives the **original inner JSON text**;
Python never reserializes its parsed numbers for an update.

## Exact apply, rollback and readback

After the existing owner has approved the exact live plan hash, use that hash
and a new private beforeimage destination:

```sh
approved-private-injector | uv run --no-sync python scripts/repair_attention_ledger_metadata.py \
  --apply-plan /private/outside-checkout/plan.json \
  --plan-sha256 <approved-sha256> \
  --beforeimages /private/outside-checkout/new-beforeimages.json
```

Plans must be owned private regular UTF-8 files; symlinks, FIFOs/devices,
duplicate/extra keys, truncation, conflicting identities, overbounds and an
incorrect approved hash refuse. The operator reads one bounded file descriptor,
not a later pathname. The supplied hash enforces reviewed-byte identity; it is
not an approval credential.

Apply opens one SERIALIZABLE transaction, locks the exact selected rows in UUID
order, and rechecks all classifications, source/target/nonmetadata fingerprints
and original metadata hashes before the first update. A refused or missing row,
drift, SQL error, cancellation or serialization failure aborts the transaction.
There is no automatic retry or expanded selection. Each query is bounded to 30
seconds and the CLI work budget, including stdin, to 120 seconds, with at most
five additional seconds for cleanup. An owned process watchdog enforces the
125-second ceiling even if transaction, pool reset or cancellation finalizers
stall. It exits 124 with `timeout_outcome_unknown`, closes the process's sockets,
and does not claim rollback when a COMMIT may already have reached PostgreSQL.
Ordinary completion cancels and joins the watchdog. Reconcile a timeout under
the operational allocation before any repeat.

Before any update, exact original metadata text is atomically written to a new
0600 file outside the repository. This file **contains private data**: protect
it as a secret-bearing operational artifact, never attach it to a PR or report.
It is created without overwriting an existing destination and fsynced before
commit. Its presence means only that beforeimages were captured, not that apply
committed. Keep it for the allocated retention period. Exact postcommit rollback
requires a separately reviewed operation using these original representations;
neither a hash nor JSON regenerated from the repaired object can recreate the
original encoded string. This tool does not perform that rollback automatically.

After commit the CLI closes its apply pool, opens another verified normal-role
pool, and checks every selected row in a read-only transaction: native PostgreSQL
JSONB equality with privately held intended text, all nonmetadata fingerprints,
and the actual `metadata->>'session_id'` expression. Output reports success only
after that independent readback. A readback failure after commit is unresolved;
it does **not** mean the committed update rolled back. Reconcile actual state
under the operational allocation before another action. A repeated exact plan
reports `already_applied` only for the identical intended object and unchanged
other columns. Any different object is drift. A new beforeimage path is required
for each attempt, including an idempotent repeat.

## Evidence and follow-through

Software controls cover the strict protocol, content-blind errors, explicit
connection/role admission, compare-and-swap, rollback/cancellation, idempotence
and independent-lease orchestration. The existing migrated native owner adds
raw/production-codec companions for actual casts, precision, unchanged columns,
session lookup, malformed/stale refusal and real transaction rollback. Local
collection does not execute those bodies. Exact-head hosted/protected native
evidence remains required; software models cannot substitute for it.

After a separately allocated real dry-run, retain dated current identity,
selection, classification counts and plan hash without private row contents.
After authorized apply, retain independent committed content-blind equality and
shape evidence. Refused rows remain incomplete. Source merge and a repaired-row
count alone do not close `bu-rokpwe`.

Only then perform the already governed Chronicler recovery **dry-run** under
`bu-26xyyx` and its approved environment/timezone/range. See
[Chronicler recovery](chronicler-day-close-recovery.md). Its 31-day maximum stays
intact: a 36-day request needs explicit finite batches, not a raised cap. Other
narration/session/deferred-envelope/provenance refusals remain binding. Metadata
repair does not authorize cache apply, notification, model calls or recovery
completion.
