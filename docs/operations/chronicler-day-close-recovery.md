# Chronicler day-close cache recovery

This runbook describes source capabilities. It does not authorize a live database
connection, backfill, notification, model invocation, schedule change or deployment.
The original recent-days recovery remains required after source delivery and a
separate concrete operational authorization. Merging the helper does not complete
that work or prove that any day was recovered or any message delivered.

## Narration and containment

The scheduled writer prefers the one executed owner-default `notify` narration
from its own runtime session and exact date/timezone bundle. Parser-only records,
failed execution, another session/recipient/tuple, suppressed output and multiple
executions cannot establish authority. A deferred notification supplies intended
text; it does not prove transport delivery. A direct or silent manual refresh may
still supply genuine final retrospective prose without calling `notify`.

The shared deterministic predicate rejects execution/status scaffolding and MCP
tool summaries, including old stored rows. The reader returns the existing
invalid-without-prose response and does not delete those rows or invoke a model.
Ordinary narrative about a delivery remains valid. Exact local-day windows,
timezone identity, quiet-day coverage, refresh rate limits and memory writeback
retain their existing contracts.

## Dry-run and private connection interface

After separate authorization, select explicit settled local dates (at most 31
consecutive days) and one exact IANA timezone. Supply the owning Chronicler
runtime-role connection string through stdin from the approved secret injector;
never put it in command arguments, shell history, a plan, repository file or log.
The wrapper requires `current_user = butler_chronicler_rw`; it does not discover
credentials or fall back to an administrator. No live invocation occurred as part
of source implementation.

The invocation shape, with an approved injector supplying stdin, is:

```text
uv run --no-sync python scripts/recover_chronicler_day_close_cache.py \
  --start YYYY-MM-DD --end YYYY-MM-DD --timezone IANA/Zone
```

Default mode only plans. Preserve its JSON outside the repository with private
access and bind its hash in the operational record. It contains only timezone,
date, closed reason and source fingerprint; never prose, recipient, session/tool
payload or credentials. A refused day remains visible as incomplete recovery.
An exit of 2 is a refusal, not successful backfill. The operator is bounded to
120 seconds overall, including the bounded stdin line, and 30 seconds per
database command. An incomplete input pipe expires within the same deadline;
cancellation and other failures produce fixed error categories without raw exceptions.

## Authority and application

Each eligible day requires exactly one successful completed scheduled session
with its exact executed canonical bundle and owner-directed deferred notify.
The notification UUID must join the Chronicler-owned deferred row and the
existing `public.attention_ledger` notify/session reference. Origin, channel,
intent, envelope, message and live deferred status must agree. A timestamp or
nonempty message alone never supplies the day, recipient or runtime provenance.
Missing, malformed, ambiguous or mismatched sources refuse; immediate messages
without a durable deferred reference are not guessed into this recovery route.

Only after reviewing the private plan and obtaining separate application
authorization, pass that exact plan through `--apply-plan /private/plan.json`
with the same injected stdin connection. JSON keys, population, fingerprints and
file size are bounded and validated. Plan input must be an actual regular file:
the descriptor is opened without waiting and checked before its bounded read;
symlinks, FIFOs and devices refuse. Application takes existing exact tuple
locks in date order inside one serializable transaction, read-locks and
re-fingerprints the sources, then revalidates the destination. Any source drift,
serialization conflict, cancellation or database error rolls back all
replacements in that invocation. It does not retry implicitly.

An already-admissible destination is preserved, including a competing normal
refresh. A repeat of the same recovery reports `already_applied` without changing
its build timestamp. Recovery source citations come only from the validated
executed bundle for that exact date/timezone; parser-only or unrelated list
results cannot add provenance. New rows retain those citations plus a recovery fingerprint
in provenance. Replaced invalid destinations also contribute a digest of their
prior selected cache fields to that audit trail; raw prior prose is not exported.
No transport/deferred state or coverage witness is written.

## Required operational evidence

Keep the exact source head, approved range/timezone, private plan hash, per-day
eligible/refused/unavailable decisions, application result and independent
committed readback. Explicitly account for every requested day; do not omit
unrecoverable days or call a partial refusal complete. Verify expected destination
windows, valid prose, provenance, preserved newer rows and unchanged transport
state without copying private content into public reports.

Local unit controls prove only their named assertions. The migrated PostgreSQL
atomicity/concurrency bodies require a supported executor; collection is not
execution, and infrastructure setup refusal is not a product assertion result.
Actual backfill, delivery and deployment remain separate from both local tests
and hosted source verification. Health timing/privacy (`.3`) and gap-interview
credential transport (`.5`) are separate work.


## Sanitized capture and requested-input binding

Core notify capture deliberately omits raw message/recipient/context input. The
writer reads the actual registered notify signature through an inert local
registrar, expands its declared defaults, and recomputes the existing complete
input fingerprint for same-session parser values. It requires one unambiguous
match to the trusted successful execution. A merged record can inherit parser
input: adjacency to an outcome, or a parser-supplied fingerprint, is never that
binding. Missing/changed signatures, missing carriers/hashes, mismatches,
duplicate candidates and malformed arguments refuse; historical defaults are
not guessed. This adds no raw capture fields and invokes no notify handler.

The match proves requested input only. Owner-default request, exact executed
bundle date/timezone and actual return semantics remain separate checks.
Deferred identity/channel come from the actual return and must agree with any
explicit requested channel plus the exact durable row/envelope and session
ledger link. No Telegram/current-owner-config inference replaces that evidence.
Immediate nested `sent` may have no best-effort notification-log ID; this is not
proof of no send or permission to read a different row. Recovery consumes only
proven deferred sources, never an immediate ID rebound into deferred storage.
Missing stored parser values can make historical recovery impossible; preserve
that refusal instead of reconstructing prose or selecting by time.
