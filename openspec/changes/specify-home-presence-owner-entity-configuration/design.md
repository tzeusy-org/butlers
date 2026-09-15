## Context

`home:presence:owner_entities` is a single JSON-array-of-strings value in the
Home butler's `state` table (`src/butlers/core/state.py`: `key text primary
key`, `value jsonb`, integer `version`, `updated_at`). The context producer
`run_home_presence_context_producer`
(`src/butlers/jobs/context_producers.py`) loads it through
`_load_owner_presence_entity_ids()` (a thin wrapper over `state_get`), treats
a missing key, a non-list value, or an empty list as "unconfigured", and
otherwise scopes both `resolve_owner_presence()` (`at_home`) and
`resolve_owner_room()` (`in_space`) to that exact set. The existing
integration coverage includes a non-person room-sensor entity id in that set,
so `in_space` deliberately consumes more than `person.*`/`device_tracker.*`
rows.

No dashboard route was ever added to set this key
(`bu-bdlr3`'s history: PR #3997 slice 1, extended by PR #4022 for
`in_space`). The only paths that can populate it today are generic and
unauthenticated by any owner-specific control:

- `PUT /api/butlers/home/state/home:presence:owner_entities` -- proxies to
  the `state_set` MCP tool with **no validation, no reference check, no
  concurrency token**; a bare upsert (`src/butlers/core/state.py:state_set`).
- `GET /api/butlers/home/state` and
  `GET /api/butlers/home/state/home:presence:owner_entities` -- return the
  **raw stored value** to any caller who can reach the dashboard API.
- The `state_get`/`state_set`/`state_list`/`state_delete` MCP tools
  (`src/butlers/core_tools/_state.py`), registered under the `state` core
  group. Home's `roster/home/butler.toml` sets no
  `[butler.runtime_seed].core_groups` restriction, and
  `src/butlers/daemon.py` treats an absent `core_groups` list as "all groups
  enabled" -- so Home's own LLM session can call `state_set` on this exact
  key today.

None of these paths are gated by `require_dashboard_owner_control`
(`src/butlers/api/owner_control.py`) -- only the optional, fail-open
`ApiKeyMiddleware` may apply, and per
`about/heart-and-soul/security.md`/RFC-0008 that is not a sufficient
boundary for private household identity data. `bu-bdlr3`'s shaping pass
(evidence packet `coordinator-evidence/bu-bdlr3-shaping-20260906/packet.md`)
found no approved specification authorizes disclosing or mutating these ids
through a dedicated HTTP surface, and held the implementation bead behind
exactly that spec plus `bu-pb6oy` (browser credential transport -- open,
unresolved, out of scope here).

The nearest implemented precedent, `roster/home/api/router.py`'s
`GET`/`PATCH /api/home/settings/thresholds`, is **not** a safe template for
this key: it has no `require_dashboard_owner_control` dependency, its PATCH
performs an unconditioned `INSERT ... ON CONFLICT DO UPDATE` per key (no
CAS), and its error text/audit body reference the state key by name (harmless
for numeric thresholds, unacceptable for private entity ids). The nearest
*specified* precedent, `bu-0hgi5` / draft PR #4050
(`specify-ha-person-entity-mapping`, exact head
`c0c34f4e67f424a6e1042a6a1f2c0b9c8c3773b3`), has passed independent
exact-head review but remains unapproved and draft, and governs a **distinct**
write-only HA-person-to-`public.entities` mapping contract (idempotency-key
batch insert, no readback, no canonical-person semantics here). Its reusable
pattern is limited to: fail-closed owner control before body/pool access,
`authenticated_principal()` as attribution only, a generic-audit-body
exemption, content-blind errors/observability, and `bu-pb6oy` as an explicit
prerequisite. This draft does not reuse PR #4050's advisory-lock namespace,
idempotency-key scheme, or `public.entities` validation -- this key has no
canonical-identity table, no remap policy, and (unlike PR #4050) this
contract deliberately permits authenticated owner readback of the exact
stored value.

## Goals and non-goals

Goals:

- Define one owner-authenticated GET and one owner-authenticated whole-list
  PUT for the exact stored value at `home:presence:owner_entities`, using the
  `state` row's existing integer `version` for optimistic concurrency instead
  of inventing a parallel idempotency-key scheme.
- Preserve both `at_home` and `in_space` consumer semantics, including the
  non-person room-sensor case `in_space` reads from the same list.
- Make every non-owner-facing surface (audit, logs, metrics, traces, errors,
  generic middleware) content-blind to the actual entity ids.
- State plainly, rather than silently assume, which existing generic paths
  this contract does **not** close.
- Define the tests and independent approvals required before implementation.

Non-goals:

- No HA-person-to-`public.entities` mapping, alias inference, or entity
  creation (that is `bu-0hgi5`/PR #4050's separate, unmerged contract).
- No Home Assistant API, provider, or snapshot call; no state/context/data
  mutation of any kind in this change.
- No new authentication mechanism or bypass of `bu-pb6oy`; no frontend
  settings panel before `bu-pb6oy` lands.
- No change to `core-state`'s or `context-bus`'s existing requirements, no
  migration (the `state` row already carries `value` and `version`), and no
  restriction of the pre-existing generic state API/MCP tools -- see
  "Honest boundary" below for why that is named explicitly rather than
  quietly out of scope.
- No restart, replay, runtime exercise, merge, deployment, or real
  identifier submission.

## Decisions

### D1: Two owner-only routes over the existing state row, not a new table

`GET /api/home/settings/presence/owner-entities` and
`PUT /api/home/settings/presence/owner-entities` are the sole surface. Both
use `ApiResponse<T>`/`ErrorResponse` from RFC 0007. Success `data` is
identically shaped on both routes:

```json
{"configured": true, "owner_entities": ["person.owner", "sensor.owner_room"], "version": 4}
```

- No row exists yet: `{"configured": false, "owner_entities": [], "version": 0}`.
- A row exists holding an empty list (the owner explicitly cleared
  configuration): `{"configured": false, "owner_entities": [], "version": <real row version>}`
  -- **not** `0`. A client that wants to reconfigure after clearing must
  read the real version first; treating a cleared row as version `0` would
  let a stale client silently win a CAS race against a clear that already
  happened.
- Both success responses set `Cache-Control: no-store`. Neither route
  accepts path or query parameters; PUT's body carries exactly
  `owner_entities` (array of strings) and `expected_version` (integer,
  `0` meaning "I expect no row yet"), `extra="forbid"`. There is no
  caller-asserted-actor field to strip -- none is defined.
- The decoded PUT body is bounded to 32 KiB (defense in depth; the 50-item
  ×255-byte bound below is the primary limit).

### D2: Fail-closed owner control precedes body/pool access; attribution is separate

Both routes depend on `require_dashboard_owner_control` (or an
owner-approved successor from `bu-pb6oy` with equivalent fail-closed
guarantees) ahead of any body read, pool acquisition, or protected-state
observation. Missing `DASHBOARD_API_KEY` configuration is `503`; a missing or
wrong `X-API-Key` is `401` (constant-time compare, per the existing
dependency). Neither denial reads or logs the body.

After authentication, `authenticated_principal()` supplies the audit actor.
It is attribution only, not an authentication check, and no request field
may assert or override it.

The dashboard UI, and any description of this workflow as "usable", remain
blocked on `bu-pb6oy`. This draft selects no cookie, JS-held key, build-time
key, or same-origin bypass for the browser.

### D3: Validation preserves the room-sensor case and never touches Home Assistant

`owner_entities` accepts zero through 50 members. Each member is a string of
1-255 UTF-8 bytes matching `\A[a-z0-9_]+\.[a-z0-9_]+\Z` byte-for-byte -- the
general `<domain>.<object_id>` shape, **not** a `person`/`device_tracker`
allowlist, so the sensor entity `resolve_owner_room()` already accepts from
this same list stays representable. IDs are never trimmed, case-folded,
normalized, autocompleted, or resolved from a friendly name -- the owner
supplies exact, already-observed ids.

A duplicate member (identical or not) is one fixed `422 INVALID_REQUEST`
category with no member index or value; so is an unknown field, non-array
`owner_entities`, wrong types, or an out-of-bounds count/length. Every
non-empty member must additionally name a row already present in the local
`ha_entity_snapshot` table (no live HA call). Missing rows collapse into one
aggregate `422 INVALID_REFERENCE` naming only `invalid_reference_count`, not
which member failed -- a stale snapshot row (old `last_updated`) is still a
valid reference; freshness governs signal derivation
(`resolve_owner_presence`'s existing 30-minute window), not storage
eligibility. On success the server stores members in ASCII sort order --
compatible with the consumer's set semantics and giving both routes a stable
response shape.

Empty list (`[]`) is the explicit, reversible way to restore "unconfigured";
it stores an empty JSON array and keeps the row (not a delete) so its
`version` keeps incrementing under the same CAS discipline.

### D4: One route-scoped advisory lock decides CAS, insert-if-absent, and rollback

PUT runs on one Home database connection in one transaction. Before reading
the current row, validating references, or writing, it takes:

```sql
SELECT pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('butlers:home:presence-owner-entities:v1', 0)
);
```

This lock is scoped to this route's write path only. It does not touch, and
is not taken by, the plain `SELECT value FROM state WHERE key = $1` the
producer and the GET route use to read the row -- so a scheduled producer
run's timing and the GET route's read latency are both unaffected by write
contention on this key; a read racing a commit observes the complete old row
or the complete new row (JSONB single-column replace is atomic), never a
partial value.

Under the lock:

1. Read the current `(value, version)` for `home:presence:owner_entities`,
   or absence.
2. Validate every submitted id against the same-transaction
   `ha_entity_snapshot` (D3). Any invalid reference returns
   `422 INVALID_REFERENCE` and performs no write, even if `expected_version`
   also happens to be stale.
3. Compare the validated, sorted submitted list to the current value:
   - **Identical** (including the no-row-and-empty-submission case): return
     `200` with the *current* version and perform no write, regardless of
     whether the caller's `expected_version` matches -- an exact retry is
     always a no-op success, not a version-conflict error.
   - **Different**, and `expected_version` matches the current version (or
     the row is absent and `expected_version == 0`): commit the change --
     `state.core.state_compare_and_set`'s `UPDATE ... WHERE key = $1 AND
     version = $2` for an existing row, or a plain insert (`version = 1`)
     when no row exists yet, since the generic CAS helper does not cover
     "insert if absent". Both paths run inside this same locked
     transaction.
   - **Different**, and `expected_version` does not match: return
     `409 VERSION_CONFLICT` with no current identifiers and no write.
4. Commit the write (or the unchanged-no-write success) together with the
   explicit audit row (D6) atomically. Any validation, lock, database, or
   injected pre-commit failure leaves the prior value/version completely
   unchanged and returns a failure -- never a partial write or a false
   success.

Because every check and write for this key happens under the one fixed
advisory lock, concurrent divergent PUTs from the same observed version
serialize: exactly one commits, and the loser observes the winner's new
version and returns `409` (its own `expected_version` is now stale).
Concurrent *identical* PUTs converge: the first commits, and the rest are
identical-to-current no-ops that all return the same `200` state. A PUT is
only a configuration receipt -- it never invokes the producer or writes
`public.user_context`; the next scheduled `run_home_presence_context_producer`
run applies its existing healthy/unmeasurable/unconfigured/freshness/TTL
handling to whatever it next reads.

### D5: A malformed pre-existing value fails closed instead of being repaired silently

`_load_owner_presence_entity_ids()` treats a non-list stored value as
"unconfigured" -- a deliberate producer-side fail-safe so a corrupted value
never gets treated as populated. This route does **not** inherit that
behavior for GET or PUT: silently presenting a corrupted value as
`configured: false, owner_entities: []` would let a PUT overwrite it blind,
destroying whatever the malformed value actually held (e.g. a legacy shape
from a bypassed generic write) before the owner ever saw it. Both GET and
PUT instead fail with fixed `503 PRESENCE_CONFIG_UNAVAILABLE` when the stored
`value` is not a JSON array of strings, disclosing no raw value, type, or key
name. Repair is a separate, explicitly authorized operator action, not an
implicit side effect of a read or write here. Ordinary pool/database
unavailability after authentication is the same fixed `503`; it must never
present as an empty/unconfigured success.

### D6: Explicit content-blind audit; generic middleware exemption is structural, not a redaction

`DashboardAuditMiddleware.dispatch` (`src/butlers/api/dashboard_audit_middleware.py`)
reads and buffers the full request body for **every** mutating `/api/*`
request before `call_next` -- i.e. before FastAPI resolves any route
dependency, including `require_dashboard_owner_control`. Adding
`owner_entities` to the middleware's `_REDACTED_FIELDS` name-list is
insufficient: a malformed/non-object body is still captured verbatim
(`{"__raw__": ...}`), and the redaction list only catches the literal field
name, not each list member. The exemption must be a structural check in
`dispatch` -- by exact path and method -- that skips `request.body()`
entirely for `PUT /api/home/settings/presence/owner-entities`, evaluated
before the body read, so it applies identically to authenticated and
unauthenticated (401/503) calls and to malformed JSON.

The route instead emits one explicit audit event,
`home_presence_owner_entities_put`, whose allowlist is exactly:
server-derived actor, fixed operation name, `configured` (bool),
`entity_count` (int), `prior_version`/`new_version` (nullable ints), and a
fixed `outcome` (`updated` | `unchanged` | `invalid_request` |
`invalid_reference` | `version_conflict` | `unavailable`). No entity id,
request body, raw URL, header, SQL argument, or exception text is present in
that row, in application/access logs (route template + fixed outcome only),
in metrics/traces (fixed low-cardinality outcome label + counts only), or in
any prompt, session, tool call, MCP resource, connector event, notification,
or Beads record -- this route is never registered as an MCP/runtime tool.

### D7: Honest boundary -- what this contract does not close

This is the load-bearing finding from the `bu-bdlr3` shaping pass and it is
recorded here rather than left implicit:

- The generic `GET /api/butlers/home/state`,
  `GET /api/butlers/home/state/home:presence:owner_entities`, and
  `PUT /api/butlers/home/state/home:presence:owner_entities` dashboard
  routes remain live, unauthenticated by any owner-specific gate, and
  unvalidated. They can read or blind-overwrite this exact key today and
  after this contract ships. This draft does not modify, gate, or deprecate
  them -- doing so is separate, unauthorized-here work with its own blast
  radius (every other butler's state key uses the same generic routes).
- The `state_get`/`state_set`/`state_list`/`state_delete` MCP tools remain
  enabled by default for the Home butler (no `core_groups` restriction in
  `roster/home/butler.toml`). Home's own LLM session can call `state_set` on
  this key with no validation and no CAS.
- Consequently, this contract's CAS/lost-update guarantee (D4) is scoped
  strictly to callers of the two new routes. A write through either generic
  path is invisible to, and not serialized against, this route's advisory
  lock -- it changes `version` outside this contract's transaction and can
  race a PUT decided under D4's lock. Nothing in this specification, its
  response bodies, its audit event, or any future implementation SHALL
  describe or imply that these two routes are the only way this key can
  change, or that the CAS guarantee extends to the generic paths.
- Closing those generic paths (an owner gate on the whole state API, or
  narrowing MCP `state_set`/`state_get` per key) is out of scope and would
  need its own spec and its own review; it is not a prerequisite this draft
  silently assumes will happen.

## Risks and trade-offs

- Real lost-update protection is bounded to this route's two callers (D7).
  The alternative -- silently implying full protection -- is the exact
  overclaim the shaping pass flagged; naming the gap is safer than hiding it
  behind a route-local lock that cannot see the generic write path.
- Failing closed on a malformed pre-existing value (D5) means a corrupted row
  blocks both read and write until separately repaired, rather than
  self-healing like the producer's own unconfigured fallback. This is
  deliberate: the producer's fallback exists to keep `at_home`/`in_space`
  safe (never guess), not to make a corrupted *configuration* row
  invisible to the owner trying to fix it.
- The advisory lock only orders writes through this route; it adds no
  contention with the scheduled producer's plain read (D4), so this change
  does not alter producer cadence or latency.
- No idempotency-key scheme (unlike PR #4050) is needed: `expected_version`
  already gives exact-retry and stale-write semantics off the row's existing
  version column, and a whole-list PUT has no meaningful "batch order"
  concern a digest would need to normalize.

## Delivery gates

1. Land this draft only after independent privacy/security review of its
   exact commit. Any semantic edit invalidates that review.
2. Obtain separate owner approval naming the exact reviewed commit and this
   change. Review or merge is not approval to implement.
3. Resolve `bu-pb6oy` with its own owner-approved browser-auth contract
   before implementing or exposing the UI; `bu-bdlr3` stays blocked until
   then regardless of this draft's status.
4. Implement under `bu-bdlr3` (or an explicitly approved successor bead)
   with the tests in `tasks.md`; obtain a fresh independent exact-head
   review and terminal hosted CI.
5. Treat merge, queue, deployment/environment availability, real HA
   identifier submission, and later natural at_home/in_space transition
   verification as separate acts.

## Open questions

None are silently decided here. Closing the generic state API/MCP disclosure
and lost-update gap (D7) is explicitly left open for a separate, later spec
-- it is not assumed, scheduled, or implied by this change.
