## Context

The canonical `relationship-facts` specification already separates the Relationship-owned
structural RDF store from the per-butler narrative memory store. It also separates assertion time
(`created_at`), observation and staleness inputs (`observed_at`, `last_seen`), confidence (`conf`),
and assertion lifecycle (`validity`). The missing axis is effective time: when the relationship
described by a structural triple held in the world.

The active `fact-evidence-and-coverage` change adds immutable evidence, coverage receipts, and
server-held approval provenance. It explicitly defers effective time and does not modify either
baseline requirement changed here. The active `relational-edges-single-home` change modifies
`Predicate catalog` and adds store-boundary requirements; it does not modify either requirement
changed here. There is therefore no same-requirement active-delta conflict at this base. Both
changes' independent evidence and structural-versus-narrative clauses remain binding.

## Goals and Non-Goals

Goals:

- Represent exact, coarse, partial, unknown, and explicitly unbounded effective intervals without
  inferring them from another time axis.
- Give the same structural triple a stable occurrence identity so repeated non-contiguous periods
  and corrections have distinct meanings.
- Specify replay, correction, approval replay, evidence carry-forward, and concurrency behavior
  precisely enough for real PostgreSQL tests.
- Preserve current assertion-current readers until the separately owned as-of work lands.

Non-goals:

- Predicate-cardinality or effective-period overlap enforcement (`bu-4ss0u`).
- `as_of` MCP or REST selection and any new owner endpoint (`bu-1ypjo`).
- Temporal fields on narrative memory `facts`, cross-store inference, or cross-schema reads.
- A migration number, a backfill from existing timestamps, implementation, or runtime/data effects.

## Decisions

### 1. Store a half-open interval with precision on each bound

The table gains these columns:

| Column | SQL type | Null meaning |
|---|---|---|
| `effective_period_id` | `UUID NULL` | The backward-compatible default occurrence for this SPO |
| `effective_from` | `TIMESTAMPTZ NULL` | Interpreted with `effective_from_precision` |
| `effective_from_precision` | `TEXT NULL` | Unknown lower bound when both lower fields are null |
| `effective_to` | `TIMESTAMPTZ NULL` | Interpreted with `effective_to_precision` |
| `effective_to_precision` | `TEXT NULL` | Unknown upper bound when both upper fields are null |

Each bound needs its own precision because a claim such as "worked there from March 2020 until an
exact resignation instant" has unequal knowledge at its two ends. A single interval-level precision
would discard that distinction.

Concrete stored precision tokens are `instant`, `day`, `month`, and `year`. `unbounded` is the only
non-concrete stored token. SQL `NULL` is the canonical unknown-precision representation; an
additional string token named `unknown` is rejected so equivalent unknown packets do not acquire
two encodings.

For each bound, a concrete timestamp is present if and only if its precision is one of the four
concrete tokens. A null timestamp is valid only with null precision (unknown) or `unbounded`
precision (explicitly no boundary). The migration must enforce those shape rules and the vocabulary
with `ck_ef_effective_from_shape` and `ck_ef_effective_to_shape`. It must enforce UTC unit-start
alignment for stored `day`, `month`, and `year` bounds with
`ck_ef_effective_from_canonical` and `ck_ef_effective_to_canonical`. When both timestamps are
concrete, `ck_ef_effective_range` must enforce `effective_from < effective_to`. Equality and
reversed bounds are invalid. `ck_ef_effective_period_nonzero` reserves the all-zero UUID sentinel.

The represented interval is half-open: the lower instant is included and the upper instant is
excluded. Unknown is epistemic, not infinite. A missing lower or upper bound with null precision
means the writer does not know that boundary. `unbounded` means the caller explicitly asserts there
is no boundary in that direction. Two unknown bounds mean unspecified temporal extent. Two
`unbounded` bounds explicitly assert all-time extent.

### 2. Normalize exact and coarse values before persistence

The writer accepts ISO 8601 temporal input paired with each concrete precision. `instant` requires
an explicit UTC designator or numeric offset, rejects a naive timestamp, converts the instant to
UTC, and preserves sub-second precision supported by PostgreSQL. Coarse inputs are civil values:
`YYYY-MM-DD` for `day`, `YYYY-MM` for `month`, and `YYYY` for `year`; offsets and time-of-day
components on a coarse value are rejected.

A coarse lower bound is normalized to 00:00:00 UTC at the first day of its named unit. A coarse
upper bound is normalized to 00:00:00 UTC at the first day after its named unit, preserving the
half-open contract. Thus an upper `2024-03`/`month` stores `2024-04-01T00:00:00Z`; it includes all
of March without storing an inclusive terminal instant. Calendar parsing uses the proleptic
Gregorian calendar. Impossible dates, malformed values, overflow while advancing an upper unit,
and precision/value mismatches fail validation.

Normalization occurs before comparison, approval parking, deduplication, or any fact/evidence/
coverage write. Persisted rows therefore have one canonical representation for an equivalent input.

### 3. Give each effective occurrence a stable id

`effective_period_id` identifies one occurrence of an SPO relationship. Its null value is a reserved
backward-compatible default occurrence: legacy rows and calls that omit all temporal arguments keep
their current single-slot behavior without a data backfill. An explicit all-zero UUID is rejected
and reserved as the SQL expression-index sentinel for the null occurrence.

A caller that needs a genuinely repeated occurrence supplies a stable, non-zero UUID distinct from
the earlier occurrence and reuses it on retries. A correction retains the occurrence id. This makes
"the same relationship held again" distinct from "the dates on this occurrence were wrong" before
overlap policy exists.

The existing `UNIQUE (subject, predicate, object) WHERE validity = 'active'` index is replaced by an
active occurrence uniqueness index over `(subject, predicate, object,
COALESCE(effective_period_id, '00000000-0000-0000-0000-000000000000'::uuid))`. This representation
allows more than one active occurrence for one SPO. It does not decide whether a predicate is
single-valued or whether two periods overlap; `bu-4ss0u` owns that later exclusion policy.

### 4. Make corrections explicit compare-and-swap operations

The writer gains optional `effective_period_id`, the four bound fields, and `corrects_fact_id`.
Temporal arguments are a complete packet: omitting all of them selects the legacy default
occurrence with unknown bounds. For each side, a concrete timestamp requires a concrete precision;
a concrete precision requires a timestamp; and `unbounded` requires the timestamp to be omitted.
The unknown form omits both fields for that side. `corrects_fact_id` is an operation argument and is
not stored as a new column.

For a new or repeated occurrence, `corrects_fact_id` is absent. If the active occurrence slot is
empty, the writer inserts it. If that slot already holds an identical normalized temporal packet and
identical assertion fields, replay is unchanged and returns the existing fact id; cited evidence is
still appended under the existing evidence deduplication contract. If the occupied packet differs,
the call fails and instructs the caller to name the exact row it intends to correct. This prevents a
repeated-period request or a stale retry from silently becoming a correction.

For correction, `corrects_fact_id` must identify the active row with the same SPO and effective
period id. Under the existing transaction and per-occurrence lock, the writer supersedes exactly
that row, inserts one active replacement with the corrected packet, preserves the old packet on the
old row, and carries evidence forward. The compare-and-swap precondition makes two concurrent
different corrections deterministic: the transaction that locks and commits the named row first
succeeds; the other finds that exact id no longer active, fails as stale, and performs no fact,
evidence, coverage, or projection write. The caller may inspect the new active version and retry a
deliberate correction against its id.

This rule applies only when a temporal packet is present. Calls that omit the whole temporal packet
retain the current default-occurrence behavior for non-temporal provenance changes, so existing
callers remain compatible.

### 5. Keep effective validity independent of every existing axis

`created_at` remains when that assertion version was inserted. `observed_at` remains when its source
observed the evidence. `last_seen` remains a staleness/recency input. `conf` remains assertion
certainty. `validity` remains whether that assertion version is current, retracted, or superseded.
None determines or defaults an effective bound.

An active assertion may describe an interval whose upper bound is already in the past. Time passing
beyond `effective_to` does not change `validity`. Retracting or superseding a row does not alter its
effective fields. A provenance, confidence, verification, observation, or staleness replacement
copies the occurrence id and normalized effective packet unchanged unless the call is an explicit
temporal correction.

### 6. Preserve temporal intent through approval and evidence paths

The normalized caller-controlled temporal packet, `effective_period_id`, and `corrects_fact_id` are
part of `pending_actions.tool_args` because they are part of the exact assertion the owner reviews.
They are not server provenance. `src` and `observed_at` remain only in server-written
`relationship.fact_approval_context` as specified by `fact-evidence-and-coverage`.

Approval verification compares the stored temporal packet as well as the SPO identity; dispatch may
not alter it. Approved replay uses the parked normalized packet, source, observation time, evidence,
session, and action id. A temporal correction carries prior evidence to the replacement and appends
the approved evidence under existing ledger rules. It never rewrites evidence or the old effective
packet.

### 7. Leave readers assertion-current

Existing readers continue selecting `validity = 'active'` without comparing `now()` or an implicit
as-of value to the effective bounds. Adding storage does not make current readers effective-now
readers. An active row with a closed effective interval remains visible to those readers.

`bu-1ypjo` will define opt-in as-of selection, including the treatment of unknown bounds and the
selection of the latest assertion version within an occurrence. No endpoint or parameter is added
here.

## Alternatives Considered

### One `effective_precision` for the entire interval

Rejected because the lower and upper bounds often come from different evidence and have different
precision. It also cannot distinguish an unknown bound from an explicitly unbounded one without a
second encoding.

### Null means both unknown and unbounded

Rejected because it turns absence of knowledge into an all-time claim. `NULL` means unknown;
`unbounded` is explicit.

### Every changed interval automatically supersedes the active SPO

Rejected because it cannot tell a date correction from a repeated period and would erase one of the
meanings before `bu-4ss0u` can enforce predicate cardinality.

### Last writer wins for concurrent corrections

Rejected because scheduling would silently choose the surviving history. Exact-row compare-and-swap
admits one correction and returns a visible stale-write failure to the other.

## Migration and Compatibility Plan

Implementation under `bu-h3b7t` chooses the then-free Relationship migration revision. The migration
adds the five nullable columns with no temporal backfill, adds the named shape, canonical-alignment,
range, and non-zero-period checks, and replaces only the active-SPO unique index with the
occurrence-scoped equivalent.
Legacy rows remain the null occurrence with unknown bounds; no value is synthesized from
`created_at`, `observed_at`, or `last_seen`.

Downgrade removes only the temporal columns, constraints, and occurrence index, then restores the
old active-SPO index. Because multiple active occurrences may exist by then, downgrade must first
fail closed with a diagnostic if collapsing the occurrence dimension would violate old uniqueness;
it must not delete, supersede, or choose rows automatically.

## Risks and Mitigations

- A caller confuses unknown with unbounded: distinct canonical encodings and CHECK constraints keep
  the difference visible.
- Coarse intervals drift by timezone: coarse values carry no timezone and normalize in UTC by rule.
- A correction overwrites another correction: exact-row compare-and-swap makes the loser stale with
  no partial writes.
- Repeated periods overlap before cardinality enforcement: the representation permits this by
  design, and `bu-4ss0u` is the explicit enforcement owner.
- Current reads appear to mean "effective now": scenarios pin their continued assertion-current
  behavior until `bu-1ypjo`.

## Open Questions

None within the proposal. Every decision above remains subject to exact owner acceptance before
implementation.
