# General Butler

The catch-all second brain. It stores anything that has no specialist home as schema-free
items in named collections, and it is Switchboard's safe fallback target when classification is
uncertain, so no message is dropped.

- **Identity and scope:** [`roster/general/MANIFESTO.md`](../../roster/general/MANIFESTO.md)
- **Required behavior:** [`butler-general` spec](../../openspec/specs/butler-general/spec.md)
- **Schedules, modules, and port:** [`roster/general/butler.toml`](../../roster/general/butler.toml)

## Ordinary-Only Collections

General's generic tools and dashboard API see only **ordinary** collections
([RFC 0037](../../about/legends-and-lore/rfcs/0037-general-capture-and-private-source-boundaries.md),
migration `gen_003`). The boundary is passive: General never classifies a collection private and
does not implement private custody.

- **Classification:** `collections.custody_private` defaults to `false`. A collection that is
  `true`, or that is flagged ordinary but holds an item with the reserved `possession_profile`
  key, is excluded from every generic list, search, export, count and statistic. An exact read or
  id-based write of such a collection behaves exactly like one for a missing id. Generic input
  can never set the flag or write `possession_profile`, and a classification can never be
  cleared.
- **One predicate:** `roster/general/tools/source_authority.py` owns the ordinary-parent
  predicate and the lock order: parent collection first, then item. The MCP tools and
  `roster/general/api/router.py` share both.
- **Name namespace:**
  - Uniqueness among ordinary names comes from the partial index
    `collections_ordinary_name_key`. It sits beside the legacy global `UNIQUE (name)` constraint,
    which keeps deployed `ON CONFLICT (name)` writers working; General never drops that
    constraint.
  - While the legacy constraint still holds, any private collection makes every name-based call
    refuse with the same fixed "unavailable" error. This covers create, declare, resolve, export
    and search by name, and it stops a name-specific conflict from revealing a private
    collection.
  - After a separately released custody cutover removes global uniqueness, a name used only by
    a private collection behaves exactly like an unused name.
  - A schema without this contract answers "unavailable" (HTTP 503 on the dashboard) instead of
    serving unfiltered reads.

## Vocabulary

`collection_declare(name, shape_description, aliases)` records an explicit ordinary collection
with a required, non-blank shape. Spellings that differ only by case, spacing, `_`/`-` or
punctuation share one normalized key. The database enforces its uniqueness among ordinary
collections, so concurrent variant declarations converge on one entry.

`collection_resolve(name)` resolves only:

- an exact ordinary collection name, or
- an exact declared spelling.

Normalized and fuzzy near matches come back only as `suggestions`, never as a guessed collection.

`item_create` and `collection_create` keep their absent-name creation; declaring is optional. No
vocabulary is seeded, renamed or consolidated automatically.

## Source Versions

Every generic item mutation appends an immutable row to `source_versions` in the same
transaction. The row records the item, parent collection, version number, sha256 digest of the
item projection, and the parent's `eligibility_generation`. Deletes append a content-free
tombstone. The table rejects `UPDATE`, `DELETE` and `TRUNCATE`.

`read_source_version` returns a stored version only while:

- the item still exists under an ordinary parent, and
- that parent's eligibility generation still matches the version.

Classifying a parent advances its generation, so stored history never outlives privacy.
`item_create_versioned` accepts an acquired connection, so a caller such as the future capture
service can commit the item, its version and its own receipt in one transaction.

## Rollback

Downgrading `gen_003` refuses while any private collection or globally duplicated collection name
exists. It never resets flags or renames, merges or deletes rows to fit. Otherwise it restores the
global name constraint and drops the passive representation. The `source_versions` and vocabulary
tables are dropped only when empty; if they hold rows, they stay in place unused, and a later
re-upgrade picks them up again.

## Owner Projects

General holds owner projects: owner-authored, time-bounded charters that group goals,
obligations and links; a project grants no authority (`gen_004`, bu-jv1moa.12;
`openspec/specs/butler-general/spec.md` "Owner Projects").

- `projects` mirrors the current charter's title and horizon (at most 180 days ahead) and holds
  `state` `draft`, `closed` or `expired`. A closed or expired row is frozen by trigger. While a
  project is a draft, its normalized title plus horizon is unique, so a repeated create returns
  the existing draft. The create idempotency key is bound to the immutable SHA-256 digest of the
  create request: a replay returns the project, the same key with a different request is 409.
- `project_charter_versions` is insert-only (UPDATE, DELETE and TRUNCATE refused). Each version
  stores title, horizon, objective, done condition, typed constraints (an optional descriptive
  budget and `must_keep` entries) and the authenticated owner actor. An edit inserts
  `expected_version + 1` under a row lock; a stale changed edit is 409 `VERSION_CONFLICT`.
- `project_links` stores a link kind (`deadline`, `owner_condition`, `trip`, `drive_file`,
  `calendar_event`) and an opaque record id only. Removing a link or closing a project never
  touches the referenced record.
- A trigger on both child tables refuses a new charter version and any link insert or delete
  while the project is closed, expired or past its horizon, so the freeze holds below the API.

Expiry needs no job: reads report a draft past its horizon as `expired` (with `closed_at` at the
horizon), and the next write to it persists that state and refuses. Creates and charter edits
persist every due expiry before their draft title/horizon uniqueness check.

A create that matches an existing draft returns that draft with `meta.outcome = "deduplicated"`
and stores nothing from the request; the Projects tab says so, keeps the form text, and offers to
open the existing draft.

The routes under `/api/general/projects` require owner control before any body read or pool
access, and every write also requires the owner's verified dashboard session: an owner API key,
connector callback or runtime session can read but not write. Request models forbid unknown
fields, so draft-lineage, promotion, actor or state fields are refused. No MCP tool reaches these
tables. The dashboard's General **Projects** tab resolves each link through the record's own
owner-authorized reader (Travel trip summary, calendar workspace entry, owner condition ledger);
deadlines and Drive files have no per-record reader yet and say so. Nothing here schedules,
arms, wakes, researches or parks anything; arming, check-ins, cards and close-out are
bu-jv1moa.30.

Roll back by removing the routes or the tab. Downgrading `gen_004` drops the project tables only
while all three are empty; otherwise the charter history stays in place unused for a re-upgrade.

## Explicit Context and Prompt Values

The context tools follow the adopted [context-security contract](../concepts/context-security.md).
`check_context` remains a data read; model-mediated `set_context` and `clear_context`
require the server-verified invocation bound to the actual owner or authenticated
owner device. A session locator, tool argument or claimed source label is not
that binding. Canonical DND mutation identity, replay and receipt rules still apply.

General's deterministic Calendar job emits only `meeting/busy` or `focused/focused`.
It never publishes an event title in the context value, metadata or result. An
exact returned row witnessed by that producer in the same process/pool can render
the typed value in the prompt preamble. Legacy rows, foreign pools, restarts,
expiry, changes and finite cache eviction fall back to type/confidence only;
matching stored labels do not confer provenance. No new signal or authority is
introduced.

## Related Pages

- [Switchboard Butler](switchboard.md) -- routes messages here as the default fallback
- [Relationship Butler](relationship.md) -- owns contact data beyond General's freeform model

## Internal Capture Ledger

`core_287` installs `public.captures`, `public.capture_operations`, and the fixed
`public.capture_service_control` singleton. FORCE RLS admits only effective
`current_user = butler_general_rw`; inherited membership and replayed public grants
cannot admit another runtime or a role-less API/migration pool. All three reject
TRUNCATE and deletion, and routed/refused capture receipts cannot be rewritten.

`roster/general/tools/capture_service.py` is an internal service. Admission commits
held state before returning its opaque receipt. Identical principal/source/mutation
keys reuse it; changed payload refuses. The ingress owner must supply a server-verified
source binding, exact digest, original occurrence time and current external epoch;
constructing `VerifiedAuthority` does not authenticate a caller. Text is bounded to
32 KiB UTF-8, normalized owning-source UUID reference pairs to eight, canonical
admission to 64 KiB, and an optional nonblank mutation key to 128 UTF-8 bytes.

Normalized reference intake can remain held; processing does not fetch attachments
or route a referenced intake without a separately implemented source-eligibility
adapter. Retained handles cannot authorize a newly private source.

Only General is supported. Fixed note/fact/preference kinds require already declared
ordinary notes/facts/preferences collections. No vocabulary is seeded. A single
General transaction writes the item, immutable source version and exact routed
operation receipt. The item's UUID is the server-generated capture operation UUID;
the public item writer accepts no caller-selected capture identity. Deferred commit
checks require both ledger rows and the matching immutable first create version,
original input and digest before terminal finalization. Normal finalization also
checks current ordinary eligibility. Verification locks the current ordinary parent/item and recomputes
the live digest; stored history does not authorize private, stale, deleted or legacy
unversioned contents. Unsupported specialist ownership refuses before any writer;
unknown lineages retain their operation and cannot be retried into a fresh target.

Admission and dispatch default **false**, independently. No capture MCP registration,
classifier or processing loop is enabled. These remain later units; the owner HTTP
intake below only admits held captures.
The nonsecret epoch manifest lives outside the repository and database; missing,
malformed or mismatched epoch/control refuses admission and dispatch. Restore rotates
its generation and original-occurrence cutoff while keeping both flags disabled.
Exact read-only outcome verification is available during recovery, but does not
rebase old operations or re-enable delivery. Rollback removes an empty boundary only;
stored inputs/operations/control authority require forward remediation.

## Owner Capture API

`src/butlers/api/routers/captures.py` exposes the ledger to the authenticated owner
(RFC0037, REQ-general-capture-001/-006). All routes are owner-only; the outer owner
middleware checks authentication, Origin and CSRF before any body octet is read.

| Route | Purpose |
| --- | --- |
| `GET /api/general/capture-intake-context?door=dashboard\|chat[&conversation_id=]` | Issue an intake context |
| `POST /api/general/captures` | Dashboard admission |
| `POST /api/general/capture-chat` | Explicit chat capture bridge |
| `GET /api/general/captures?limit=&cursor=` | Owner's captures, newest first (`Page`) |
| `GET /api/general/captures/{capture_id}` | One capture's status |

**Intake context.** Both POST routes require `X-Capture-Intake`, a server-issued
context checked before the body is read. It is an expiring (10 minute) owner action
grant bound to its door, the verified session (or API-key authority), an optional
owner-verified conversation and the current host epoch. It carries no source payload
and no model, session or target-operation authority. It is HMAC-signed with a
process-held key, so an API restart retires outstanding contexts. A context minted
before an epoch rotation (restore) is refused before any target effect. Never log it.

**Source binding and retries.** The ledger principal is derived from the authenticated
owner; requests cannot assert an actor, owner, source, target, schema, table or role
(`extra="forbid"`). With a `mutation_id`, the source occurrence is bound to the
context's digest, so an identical retry under the same context returns the original
receipt and changed content refuses with `409 CAPTURE_CONFLICT`. Without one, each
request is a fresh occurrence. A fresh context is a new explicit owner action and a
new occurrence: it never retries or relabels an old, possibly in-doubt lineage. The
raw context is never stored; the ledger mutation key records only the door and a
digest of the `mutation_id`.

**Admission.** Each request runs one General-role transaction (`SET LOCAL ROLE
butler_general_rw`, `search_path` public) on the dashboard's General pool and calls
the internal service, whose own transaction is a savepoint inside it. `202` with
`disposition: held` is returned only after the outer commit. Nothing classifies or
routes yet, so new intake truthfully stays `held/pending` until processing is
enabled. The login role must be able to `SET ROLE butler_general_rw`; otherwise every
route answers `503 CAPTURE_UNAVAILABLE`. The epoch manifest path comes from
`BUTLERS_CAPTURE_EPOCH_PATH`; missing or mismatched epoch/control is also `503`.

**Projection.** Responses carry the opaque capture id, disposition, fixed category,
source door (`dashboard`, `chat` or `internal`), the typed General target receipt
(owner, operation id, item id, version) once routed, and timestamps. Original intake
text and reference handles are never returned. Reads use the General role too, so an
unreadable ledger is `503`, never a false empty list.

**Content-blind audit and errors.** `DashboardAuditMiddleware` selects a server-owned
no-body policy for the two exact POST routes before it would read a body; no caller
header or field can select or bypass it. It still writes the normal audit row with
the owner actor, a fixed operation (`general_capture_admit` / `general_capture_chat`),
the outcome category and the opaque capture id, on success, error, timeout and
cancellation. The capture route class maps malformed JSON, validation, service and
unexpected errors and timeouts to fixed codes before generic handlers can log
`str(exc)` or validation input. A cancelled request propagates after the transaction
rolls back and the audit row is written; it never reports success. OTel header
capture sanitizes `X-Capture-Intake`.

The chat bridge is a distinct route: it never submits an ordinary conversation turn
or changes a conversation's privacy policy. The held-capture UI and consolidation
endpoints (bu-2jtfw.9.6) consume this contract unchanged.
