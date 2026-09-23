## Baseline and placement

[Observed] General owns `general.collection_items`, collection organization,
and `collection_export` (`roster/general/tools/{items,collections}.py`), but
these paths do not encode a checklist revision or evidence coverage. [Observed]
Finance keeps transactions and email receipts in its own domain; Travel owns
trip documents (`roster/finance/MANIFESTO.md`, `roster/travel/tools/documents.py`).
[Observed] `src/butlers/core/commitments.py` requires closure evidence; a
preparation receipt does not itself prove submission. The new capability is a
General-local opt-in administrative packet, not a replacement for any of
those stores.

This package proposes a General-only first slice from owner-entered checklist
text and explicitly selected General-local sources. Finance and Travel doors
may be shown as selected but cannot become `covered` until their owning teams
ship and review the commit-valid source version/read-fence contract below.
There is no direct `finance.*`, `travel.*`, or other peer-schema SQL from
General. Cross-butler reads and any future source-fence operations travel
through Switchboard MCP. `bu-2jtfw.9` remains the owner of collection capture,
vocabulary, search and generic item mutation semantics; no part of that work
is credited to this proposal.

## State and source vocabulary

The owner supplies one checklist with a server-generated packet ID and an
immutable ordered revision. Each revision stores exact owner-entered clause
text, stable clause IDs within that revision, source time, and a canonical
digest over both its active clauses and its explicit exclusion manifest. An
edit, reorder, exclusion, or rewording creates a successor revision; the
previous revision is not rewritten. Active clauses alone take one of the four
coverage states. An excluded clause is not a fifth coverage state: the
successor retains its prior clause ID/text, the exclusion decision, the
verified owner's confirmation ID, and server time in a separately visible
exclusion manifest. Each successor carries that exclusion forward until the
owner explicitly reintroduces the clause; older revision records stay
immutable. An owner-only revision and receipt export distinguishes
the active set from that manifest; it cannot silently present a shortened list
as the original supplied list. A model may segment pasted text and propose
matches, but segmentation and proposals are not owner-approved clauses or
proof. A declined clause is removed only through an owner-confirmed new
checklist revision, never a model waiver.

The future General-local schema is allocated, not created here: `packets`
owns the mutable current-revision pointer; `packet_revisions` owns immutable
checklist versions/digests; `packet_clauses` owns ordered owner-confirmed text;
`packet_clause_exclusions` owns immutable, owner-confirmed removed-clause
provenance for each successor revision; `packet_evidence_links` owns
accepted/proposed match state and minimal source doors;
`packet_preparation_receipts` owns immutable operation/receipt/index
identities. Use unique `(packet_id, revision)` and `(packet_id, operation_id)`
constraints and a packet-row lock/CAS; keep full specialist documents out of
these tables. The exact migration is a separately reviewed implementation
artifact after adoption.

The first slice caps a revision at 200 active-plus-excluded clauses and 500
selected evidence links, with at most 10 accepted links per clause. Exceeding
a cap refuses creation or
revision with a typed limit result; it does not silently truncate the owner's
list. Owner-only status and index reads are bounded/paginated. The cap is an
operational admission limit, not an official-document-size claim.

Each clause has exactly one derived state:

| State | Meaning and owner next step |
| --- | --- |
| `missing` | No owner-accepted source match exists; provide/select evidence or revise the list. |
| `needs_review` | A proposed/ambiguous/changed match needs explicit owner acceptance; no model guess counts as proof. |
| `unavailable` | An accepted source cannot be read, authorized, pinned to an immutable version, or fenced through commit. The reason is typed, including `version_unverifiable`; retry or choose a different source. |
| `covered` | At the last status evaluation, every owner-accepted match needed for this clause is readable under current authority, pinned to an immutable version, and served by a source that can provide a commit-valid fence. A final receipt still requires fresh fences at commit. Coverage is only against the supplied clause wording. |

One accepted match may be sufficient only if the owner explicitly records that
choice; all accepted matches named by a clause's reviewed decision are
required for its `covered` state. A source that is merely a candidate never
counts. Ambiguous competing evidence is `needs_review`, not whichever source
the model scored highest. A selected source with no authoritative version is
`unavailable`, not `missing` or `covered`.

Before a specialist fact is read, the owner may select a candidate opaque
source for inspection against one clause without accepting it as coverage.
Any candidate door shown before the pre-read grant is owner-supplied or comes
from a separately authorized owner inventory surface; the checklist resolver
cannot leak a specialist-derived label or existence signal to create it.

## Evidence authority and commit fence

A source door has `source_owner`, opaque `source_id`, a safe owner-visible
label only after the source authorizes its disclosure, and the version
descriptor returned by the owning source. The descriptor must identify one
immutable version of the exact evidence the owner
reviewed: an append-only version ID, versioned reference, or owner-authorized
content digest. A row ID, filename, URL/blob locator, `updated_at`, or
`fetched_at` alone is not such a descriptor. The source owner decides what may
be read and returns only a bounded clause-relevant fact, its exact version,
current read-authority state, and a source door. General stores the minimal
fact/door/version needed for the reviewed match, not a Finance receipt image,
Travel document body, financial transaction copy, or provider payload.

For a General-local source, the future General transaction locks the source
row and packet revision together, derives or verifies its immutable snapshot
version, rechecks the accepted match, and commits the receipt while those
locks still hold. A stale whole-document write must not replace the source
snapshot under the packet lock. If `bu-2jtfw.9` has not supplied a stable
versioned read/locking seam, the source is `unavailable` for completion even
though its current content can still be opened as an unverified door.

For a specialist source, an opaque source ID or caller's claim of
`owner-selected` is not read authority. Before even a bounded label, existence
signal, clause fact, or version leaves Finance/Travel, the verified owner must
confirm the exact packet ID, current revision/digest, clause/match ID, source
owner/opaque source ID, and read purpose through a trusted owner ingress. A
future Switchboard-owned, server-held **pre-read selection grant** records
that tuple, owner identity/provenance, issue time, short expiry, and a
request-bound nonce. At the verified owner ingress, Switchboard reads the
current packet/revision and proposed candidate tuple through a trusted
General MCP lookup, compares the owner's exact selection, then stores the
grant. A proposal or caller assertion alone cannot enter that owner-confirmed
selection manifest. Selection permits bounded inspection, not acceptance as
clause evidence; the owner reviews the returned fact before accepting the
match. Neither General's MCP caller nor a free-form `source_ref` can mint or
edit the grant.

For each specialist read, Switchboard issues a short-lived, audience- and
request-bound opaque assertion ID from the live grant. The source-owning
endpoint calls a narrow Switchboard server-to-server MCP validator with its
authenticated service identity and the assertion ID before any source lookup
or response containing specialist data. The validator checks the server-held
grant, verified owner, exact packet/revision/digest/clause/match/source tuple,
source-service audience, request/nonce, expiry/revocation, and current General
selection manifest. It consumes the nonce once, allowing only an idempotent
repeat of that same bound request. The source owner trusts the validator's
authenticated answer, never the caller-supplied ID or assertion alone. Generic
MCP callers cannot mint grants, call the privileged validator, or replay its
answer. Absent, mismatched, revoked, expired, unreadable, or fabricated proof
returns only a content-blind typed refusal and leaves packet revision/history
unchanged. Owner selection revocation also invalidates the grant. This
pre-read proof is distinct from the later complete-packet confirmation and
commit fence.

The owning butler must separately define and review a **version-fence MCP
contract** before General can mark its link `covered`:

1. A read-only resolution accepts only the exact selected source authorized
   by the validated pre-read grant and returns the least clause-relevant fact,
   an immutable version descriptor, current read authority, and a bounded safe
   label. It never treats General's copied locator or caller assertion as
   proof of authority.
2. A source-owned prepare operation atomically checks that exact version and
   read authority under the source's own lock, then issues an opaque,
   request-bound, short-lived fence. All source edits, revocations, or deletion
   paths must serialize with and invalidate/refuse that fence. The fence binds
   packet ID, checklist revision, clause/match ID, source ID/version, owner
   confirmation, operation ID, and expiry. General cannot mint it.
3. The owning source must keep the version/read authority stable until General
   acknowledges a bounded receipt commit or the fence expires. General may
   enter its final transaction only with enough source-declared remaining
   lease time to finish under a stricter local timeout; it checks the current
   packet revision, all source fences, and owner confirmation before commit.
   A missing, expired, revoked, changed, or unacknowledged fence refuses
   `current_complete`. No optimistic read-then-write or post-hoc callback may
   manufacture a complete receipt after a race.
4. General releases each fence after commit or abort. A crash leaves a
   bounded source lease that expires without minting a receipt. An immutable
   receipt records the versions that were valid at commit; future source
   changes make its *current* projection superseded/unavailable, not rewrite
   the historical receipt.

The pre-read grant/verification and exact lease/ack protocols are future
Switchboard- and specialist-owned design and real-DB/contract verification
prerequisites, not assertions that current Finance or Travel tools provide
them. If either cannot be implemented without a new trust exception, the
source remains `unavailable`; this proposal does not authorize
cross-schema locks, shared SQL views, credentials, or an alternate transport.
No current-complete receipt may commit when the checklist remains r1 but an
accepted source changes or loses read authority before the commit fence.

## Owner review and receipt semantics

A General session may propose clause matches and an index, but cannot confirm
the owner or mint a preparation receipt by setting an `actor`, `confirmed`, or
`source_ref` argument. Switchboard owns the future server-held confirmation
record, as the custodian of verified ingress provenance. It may accept an
ingress event independently resolved to the owner or a Dashboard action that
has crossed the authenticated owner boundary, under a separately reviewed
server-to-server assertion contract; a generic MCP caller cannot impersonate
either path. The confirmation presents the exact checklist revision and
ordered clauses, accepted match set, source versions, and exclusions. Its
immutable confirmation record binds owner identity, canonical digest,
operation ID, issue time, short expiry, and confirmation source. General
validates that record through the owning trusted boundary before finalizing.
The proposed custody PR #4211 sketches a similar Switchboard owner-statement
receipt; if both are adopted, the owning worker may share a reviewed primitive
but must keep possession and checklist payloads, authority, and receipt
meanings distinct. Neither proposal grants authority to the other today.

The final receipt/index contains packet ID, exact checklist revision/digest,
ordered active clause IDs and states, accepted source owners/opaque
doors/immutable versions, and the separately named excluded-clause manifest
with prior clause IDs/text and owner-confirmation provenance. It also carries
the final owner confirmation ID, prepared-at server time, and a stable receipt
ID. It is immutable and owner-only. Its statement is **complete against the
active clauses of this owner-confirmed supplied-list revision and pinned
evidence versions at preparation time; the listed clauses were explicitly
excluded by the owner**. It does not certify an official list, legal
sufficiency, eligibility, submission, acceptance, or provider processing. A
request that cannot prove all active clauses covered returns the typed clause
states and no current-complete receipt. The owner can export the structured
index through General conventions; a rendered PDF or external send is not
implied.

## Revision, replay, and lifecycle

The future General evaluator uses a row lock or CAS on the packet's current
revision and owner-confirmed match manifest. It checks operation identity
under the same lock before revision comparison: an identical committed
retry returns its original immutable receipt/index even after later revisions;
reusing that operation ID with different canonical input fails without a
second receipt, index, or commitment. Two contenders for one revision cannot
both mark current completion. A checklist edit or accepted source version
change during preparation invalidates the in-flight current attempt; it
returns a recoverable changed-revision/evidence result with no false receipt.

Archived or reopened packets keep old checklist revisions, match decisions,
and receipts. Current status derives from the current revision and source
versions/read authority; a historical receipt may be shown as superseded or
unavailable with its original meaning intact. Rollback disables new packet
write tools but retains a compatible owner-only read/export path and immutable
receipts. If a proposed older binary lacks that reader, the implementation
must stage a read-compatible rollback target before any write is activated;
it cannot rely on a destructive downgrade or call retained-but-unreadable data
recovery. Rollback does not undo a third-party submission or claim one occurred.

A preparation-specific commitment may close against this receipt only when
that commitment's wording and owner intent are about preparing this exact
index. An external submission deadline, a promise to send documents, an
application outcome, and any other commitment remain open until their own
separately authorized evidence closes them through the existing commitment
contract. A retried prepare never creates another downstream task.

## Future allocation and evidence

After exact owner adoption and reconciliation with `bu-2jtfw.9`, allocate:

- General: owner-only checklist revision store, bounded clause status/read,
  General-local source versioning, deterministic evaluator, receipt/index
  persistence/export, and idempotent CAS. Do not reuse the collection item
  table as a second receipt authority.
- Switchboard: the pre-read owner selection grant, source-owner-verifiable
  assertion, and final owner confirmation/validation seam, with an
  authenticated Dashboard forwarding contract for dashboard-origin reports.
  Reconcile its implementation with the custody
  proposal without equating their receipts.
- Finance and Travel, separately: minimal source resolution and source-owned
  version-fence operations if those owners elect to support packet evidence.
  Until then their links remain `unavailable` for complete coverage.
- Existing deadline/commitment owners: optional explicit linkage only, no
  new scheduler or automatic closure policy.

Future verification is behavior-executing and real-PostgreSQL where state
races matter: extend `tests/tools/test_general_items.py`,
`tests/core/test_deadlines_db.py`, `tests/core/test_commitments.py`,
`tests/integration/test_commitments_roundtrip.py`, and
`tests/modules/test_document_renderer.py` only at seams actually changed.
Add one General transaction matrix for checklist r1 racing r2 and for source
version/revocation changing while r1 stays current; neither may commit a
current-complete receipt. Verify one immutable receipt for identical retries,
changed-payload refusal, no duplicate commitment, and rollback retention.
MCP contract tests under Finance/Travel must prove source-owner version/read
fences and deny direct General peer-schema access. They must also prove a
forged, unselected, mismatched, revoked, or unreadable selection grant returns
no specialist label/fact/version and does not mutate the packet. An exclusion
of B from r1=A,B,C must leave r2=A,C with B and its owner-confirmation
provenance visible in the r2 receipt/export. A read failing or returning
only a mutable locator must be `unavailable`; a declined/ambiguous match must
stay `needs_review` or `missing` as appropriate. Owner-confirmation tests
must reject forged caller actor/receipt data. Test the owner-only index wording
and ensure external submission commitments remain open. Target roughly eight
focused collected cases by parametrizing failure axes and reusing fixtures;
run the dirty-worktree test planner, real-Postgres lane, collection, Ruff,
strict OpenSpec/overwrite/guards, and terminal hosted CI at the exact
implementation head.
