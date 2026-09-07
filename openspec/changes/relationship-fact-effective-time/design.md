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

The final schema uses an active occurrence uniqueness index over `(subject, predicate, object,
COALESCE(effective_period_id, '00000000-0000-0000-0000-000000000000'::uuid))`. This representation
allows more than one active occurrence for one SPO. It does not decide whether a predicate is
single-valued or whether two periods overlap; `bu-4ss0u` owns that later exclusion policy.

The existing `UNIQUE (subject, predicate, object) WHERE validity = 'active'` index cannot be removed
in the expand migration. The deployed writer names that exact inference target in `ON CONFLICT`; if
the index disappears while that writer is live, PostgreSQL rejects the statement before it can
write. The rollout therefore carries both indexes through a transition stage. While both exist, the
legacy index intentionally prevents repeated active occurrences even though the final expression
index is present.

### 4. Make corrections explicit compare-and-swap operations

The writer gains optional nullable `effective_period_id`, the four bound fields, and
`corrects_fact_id`. It does not track JSON key presence. Omission and explicit JSON `null` have the
same canonical meaning at every optional position:

| Input | Assert/replay mode (`corrects_fact_id` omitted or null) | Correction mode (`corrects_fact_id` non-null) |
|---|---|---|
| `effective_period_id` omitted/null | Default NULL occurrence | Inherit the target row's occurrence id |
| `effective_period_id` non-null | Use that stable non-zero occurrence id | Must equal the target row's occurrence id |
| Bound value omitted/null and precision omitted/null | Unknown bound | Desired replacement bound is unknown |
| Bound value omitted/null and precision `unbounded` | Explicitly open bound | Desired replacement bound is explicitly open |
| Bound value non-null and concrete precision | Normalize and store the concrete bound | Normalize as the desired replacement bound |
| Bound value non-null with omitted/null/`unbounded` precision | Invalid | Invalid |
| Bound value omitted/null with concrete precision | Invalid | Invalid |

`corrects_fact_id` omitted or explicit null selects assert/replay mode. A non-null value selects
compare-and-swap correction mode even when every other temporal key is omitted or null. Therefore
`corrects_fact_id` alone means "replace this exact active version with wholly unknown effective
bounds while retaining its occurrence id." A correction packet is the complete desired result, not
a partial patch: a caller changing only one bound must resend the other desired bound. The string
`unknown`, empty strings, and the all-zero UUID are invalid rather than aliases. `corrects_fact_id`
is an operation argument and is not stored as a new column.

For a new or repeated occurrence, `corrects_fact_id` is absent. If the active occurrence slot is
empty, the writer inserts it. If that slot already holds an identical normalized temporal packet and
identical assertion fields, replay is unchanged and returns the existing fact id; cited evidence is
still appended under the existing evidence deduplication contract. If the occupied packet differs,
the call fails and instructs the caller to name the exact row it intends to correct. This prevents a
repeated-period request or a stale retry from silently becoming a correction.

For correction, `corrects_fact_id` must identify the active row with the same SPO. An omitted/null
period id inherits that row's occurrence id; a supplied id must match it. Under the existing
transaction and per-occurrence lock, the writer supersedes exactly that row, inserts one active
replacement with the complete desired packet, preserves the old packet on the old row, and carries
evidence forward. The compare-and-swap precondition makes two concurrent different corrections
deterministic: the transaction that successfully locks the named row while it is active and commits
first succeeds; the other finds that exact id no longer active, fails as stale, and performs no
fact, evidence, coverage, approval-context, or projection write. The caller may inspect the new
active version and retry a deliberate correction against its id.

Calls with all temporal values omitted or null and no correction target retain the current
default-occurrence behavior for non-temporal provenance changes, so existing callers remain
compatible. There is no separate "explicit all-null packet" branch.

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

The normalized caller-controlled temporal packet, resolved `effective_period_id`, and
`corrects_fact_id` are part of `pending_actions.tool_args` because they are part of the exact
assertion the owner reviews. The parked JSON uses one canonical shape: all six temporal keys are
present; unknown/default values are JSON null; concrete bounds are normalized UTC strings; and UUIDs
are canonical strings. Thus omitted and explicit-null requests deduplicate and replay identically.
These values are not server provenance. `src` and `observed_at` remain only in server-written
`relationship.fact_approval_context` as specified by `fact-evidence-and-coverage`.

Approval deduplication and verification compare the canonical stored temporal packet, correction
target, and SPO identity; dispatch may not alter them. Approved replay uses the parked normalized
packet, source, observation time, evidence, session, and action id. Pre-temporal pending actions that
carry none of the six keys normalize to the same all-null/default packet on replay. A temporal
correction carries prior evidence to the replacement and appends the approved evidence under
existing ledger rules. It never rewrites evidence or the old effective packet.

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

Implementation under `bu-h3b7t` chooses then-free Relationship revisions for two separate schema
acts. A single migration that swaps the indexes is forbidden because it cannot serve both the
deployed old writer and repeated-period writes.

1. **Expand schema while the old writer remains valid.** The first migration adds the five nullable
   columns with no temporal backfill, adds the named shape, canonical-alignment, range, and
   non-zero-period checks, and creates the occurrence-scoped unique index. It MUST retain
   `uq_ef_spo_active` unchanged. The exact deployed insert with
   `ON CONFLICT (subject, predicate, object) WHERE validity='active' DO NOTHING` MUST continue to
   prepare and execute against this two-index schema. Legacy rows remain the null occurrence with
   unknown bounds; no value is synthesized from `created_at`, `observed_at`, or `last_seen`.
2. **Deploy a transition writer while both indexes remain.** The new writer uses targetless
   `ON CONFLICT DO NOTHING` plus its locked re-read/CAS path, so its insert SQL is valid with the
   legacy index present, with both indexes present, and with only the final occurrence index. Before
   approval parking or any write, it checks whether `relationship.uq_ef_spo_active` exists. While it
   exists, any temporal intent (a non-null bound or precision, a non-null period id, or a non-null
   correction target) MUST fail with a stable `temporal_cutover_pending` error. Omitted/all-null
   calls continue the old single-slot behavior. This prevents an old writer from later replacing a
   temporal row with an all-null version and prevents the legacy index from turning a repeated
   occurrence into an unexplained conflict.
3. **Prove the writer transition.** Every Relationship writer instance must report the transition
   writer's exact image/code identity, old instances must be absent, and real-PostgreSQL transition
   tests must pass before the second migration is authorized. Process health or elapsed rollout time
   alone is insufficient proof. No temporal approval may be parked during this stage because the
   cutover check precedes parking.
4. **Contract the legacy index, then enable temporal writes.** A later migration drops only
   `uq_ef_spo_active`; it leaves the occurrence index and temporal columns/checks in place. The same
   transition writer detects that absence and enables normalized temporal assertions, explicit
   repeated period ids, and CAS corrections. This index absence is the schema capability signal;
   there is no time-based or process-local feature flag. Old-writer SQL is expected to fail after
   this point, which is why step 3 is a hard prerequisite.

Rollback is bounded by the same compatibility facts:

- Before step 4, code rollback is safe: disable/drain the transition writer, restore the old writer,
  then optionally reverse the expand migration. Temporal intent has been rejected, the old conflict
  target still exists, and no temporal payload needs preservation.
- After step 4 but before any temporal write is admitted, an operator MAY quiesce all fact writers,
  prove there is at most one active row per SPO, recreate `uq_ef_spo_active`, and then roll code back.
  The proof and index creation must occur in one controlled no-write window.
- After any temporal assertion, correction, or repeated occurrence is admitted, automatic downgrade
  or old-writer rollback is prohibited. Multiple active occurrences may make the legacy index
  impossible to recreate, and even a single temporal occurrence could be silently replaced by old
  code with an all-null packet. Recovery must roll forward or use a separately reviewed,
  data-preserving procedure; it MUST NOT delete, choose, supersede, flatten, or discard temporal rows
  merely to make the legacy index build.

## Risks and Mitigations

- A caller confuses unknown with unbounded: distinct canonical encodings and CHECK constraints keep
  the difference visible.
- Coarse intervals drift by timezone: coarse values carry no timezone and normalize in UTC by rule.
- A correction overwrites another correction: exact-row compare-and-swap makes the loser stale with
  no partial writes.
- An old writer loses its inferred conflict target: the expand migration retains that index until
  exact old-writer absence is proven, and the transition writer uses targetless conflict handling.
- Temporal behavior starts while mixed writer versions are live: the transition writer rejects
  temporal intent while the legacy index exists and checks before approval parking or persistence.
- Repeated periods overlap before cardinality enforcement: the representation permits this by
  design, and `bu-4ss0u` is the explicit enforcement owner.
- Current reads appear to mean "effective now": scenarios pin their continued assertion-current
  behavior until `bu-1ypjo`.

## Open Questions

None within the proposal. Every decision above remains subject to exact owner acceptance before
implementation.
