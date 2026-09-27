# RFC 0037: General capture and private source boundaries

**Status:** Adopted target; implementation and operational verification remain outstanding.

Owner adopted the exact six-file composition manifest `7b97e54600398001330aa5180f9d1ed269db4ce73e104a3de46260fab672c5c7` and bounded repository delivery. The original bytes are retained as provenance in `openspec/changes/compose-general-capture-with-private-custody/adopted-artifact.json`; this RFC is the single durable design authority. Private custody and checklist feature implementation are not released by this change.


## Authority and source precedence

Baseline: current main8607220fdd283b155486363684d2b1e80a23acb8,
openspec/specs/butler-general/spec.md and General manifesto. Preserved candidate
PR4121 head8b0c7a253d0b3630c5bf4bdd150043ca327e3e4d is not adopted.
Private custody PR4239 head6de6c73e5c3b3d00b50f4c4e83c68240896fec6f,
manifest6065db92afa01f2294392ab972e9465663e6b2fbed7e49f0f6e8e210fcf1ac5a,
is the governing adopted target and is preserved without edits. No rule here
weakens its whole-parent/member secrecy, ordinary-only name index, private
capture/confirmation, or absence of model-facing custody tools.

## Durable ordinary capture and routing

A verified owner conversation/dashboard intake submits ordinary text or explicitly
selected ordinary attachment references to a deterministic capture admission.
The server assigns owner/source provenance; model actor fields cannot assert it.
Commit a held ledger row and return its opaque capture_id before launching any
classification or target write. This means a killed caller after admission leaves
recoverable held work, not a promise of a response surviving a killed connection.
Caller idempotency keys bind verified principal, exact payload digest and source;
identical replay returns the original receipt, changed input refuses with no write.
Different source occurrences are distinct captures even if their content matches.

The ledger is not a shared plaintext scratchpad: owning service writes through
its fixed admission/resolution seam; runtime and dashboard reads are bounded by
server-held owner/source authority, real runtime-role RLS and content-blind errors.
No generic tool chooses target schema/table, claims routing success, or browses
another source's held content. Define exact grants and forced-RLS/bootstrap/backup
behavior in the implementation packet before schema work; public placement does
not grant the fleet arbitrary reads or writes. Preserve held data on rollback.

Routing operates only through Switchboard MCP to the owning domain. General-local
ordinary target writes can use one transaction with receipt linkage; specialist
writes return a source-owned durable receipt/version through MCP, never General
SQL SELECT on the peer's table. A routed status requires that exact receipt and
cannot be minted from non-null locator fields. Ambiguous delivery remains held
with typed reconciliation-needed status; no automatic duplicate effect. Failed
classification or unavailable destination retains held content and a fixed error
category, never an exception suffix containing owner text. Once refusal establishes
specialist ownership, no later generic retry silently absorbs it into General.

## Ownership and user path

Finance, Relationship, Home, Lifestyle and other registered specialist boundaries
come from their manifests/contracts. Missing citable tool means unavailable owning
destination, not permission to store a shadow General record. Explain the domain
and available owner next step without inventing a tool. Ambiguous ordinary intent
requests clarification. The supported chat/dashboard intake and keyboard held lane
must actually be wired; direct MCP-only capture is not full delivery. Held is not
Saved, routed links to source-owned receipt, refused explains domain boundary,
unknown source service degrades rather than declaring the ledger empty.

## Collection and alias compatibility

Ordinary item_create/collection_create retain current creation semantics after
filtering to custody_private=false; a private-only matching name behaves exactly
like an absent ordinary name and creates an independent ordinary UUID. No aliases,
trigram candidates, normalized-name uniqueness errors, counts or index documents
may reveal private names/members. Private owner screen continues UUID selection.

collection_declare creates explicit ordinary vocabulary metadata with nonblank
shape and a normalized-key uniqueness constraint; concurrent case/punctuation
variants converge or return the same bounded ordinary conflict, without private
lookup. Resolve only declared ordinary exact aliases automatically. Near matches
are suggestions, never a guessed identity. New capture routing defaults to stable
ordinary notes/facts/preferences declarations and asks before changing destination;
it does not turn a typo into an implicit capture classification. Existing generic
item_create remains compatible; the stricter unknown-name refusal proposed in
PR4121 is not governing. Vocabulary applies to ordinary namespace only. Historical
live normalization/merging requires separately prepared exact owner selection;
no migration derives a live mapping from an old count or renames rows automatically.

## Retrieval, catalog and private transition

Bound search by validated limit and deterministic keyset cursor over ordinary
rows; filters precede counting/pagination. Use existing General JSONB/text indexing
and a generated indexed text projection where justified. Missing optional index
may use bounded containment with explicit degraded reason, not unbounded fallback.
Exact item identity and source version accompany permitted results; no private
placeholder, derived count or existence hint. Catalog admission uses the existing
owner-held sensitivity ceiling and owning-source fetch/retirement protocol. No
caller sensitivity override, direct cross-schema read or unsupported capture type
is assumed. A new catalog source kind must be explicitly wired end-to-end with
synthetic positive/exclusion tests before any capture is reported discoverable.

Every generic mutation/source read/catalog job respects adopted private collection
fences. Enrollment locks parent before item, retires old catalog/search eligibility
atomically and invalidates outstanding ordinary read fences. In-flight generic
read either returns pre-enrollment ordinary data or refuses, never new private
fields. No model/context/held ledger can read a private record after enrollment.
Prior ordinary text voluntarily supplied is not retrospectively sanitized; any
future projection retaining a source link must recheck current source eligibility.

## General-local checklist source fence

A separately adopted checklist may use an existing ordinary item as evidence only
after explicit owner selection. It obtains an immutable content version/digest
from a server-held projection, not just item_id/updated_at. Lock parent, source item
and packet in a consistent order; validate source privacy, owner/read authority,
exact revision and accepted match before receipt commit. Ordinary mutation,
delete, enrollment and catalog retirement serialize with this fence. A source
becoming private/unavailable invalidates coverage and prevents a new current-complete
receipt. No checklist grant lifts custody privacy. Historical immutable receipts
remain stored but their current model-visible source projection must withhold newly
protected data; a private source cannot leak through an old snapshot/receipt cache.
This does not share custody confirmation authority or add a private checklist mode.

## Rollout, ownership and verification

Do not edit/reopen PR4121 or its foreign worktree. bu-2jtfw.9 remains the primary
General outcome; reconcile this exact proposal with that owner before allocating
implementation. Custody code remains separately released and shares item/collection
paths only through explicit serialized ownership. Source adoption does not transfer
that reservation. Checklist can reference this exact source-read contract as a
prerequisite, without pretending it is implemented.

Independent review covers doctrine/refusal, primary source authority, privacy,
cohesion, API shape and all old .9 acceptance outcomes. Synthetic real-Postgres
checks exercise capture commit-before-routing, idempotency changed-payload refusal,
source-owned success, owner/RLS/bootstrap, private-name create equivalence,
normalized ordinary declare races, bounded search/counts, catalog admission and
retirement, and source-fence/enrollment/receipt races. Production-component tests
cover real intake/held-lane transitions and keyboard handling. Existing source tests
from PR4121 are evidence to salvage, not proof of this corrected contract. No
provider/live test or implementation result is claimed in this draft.

## Concrete authority and persistence boundaries

### Passive classification is not custody enrollment

General's shared ordinary-source guard owns the passive
`general.collections.custody_private BOOLEAN NOT NULL DEFAULT false`
representation and ordinary-only name uniqueness. A General-chain expand migration
preserves an existing compatible column/index and every existing true value. It
never creates a `possession_profile`, enrolls an item, manufactures a custody event,
or implements custody capture/confirmation/UI/history/export. Those remain separate
private-custody work. The General expand migration adds the ordinary-only partial
unique index while retaining any existing global name constraint, so deployed
legacy `ON CONFLICT (name)` writers remain compatible. Updated writers use targetless
`ON CONFLICT DO NOTHING` followed by a locked ordinary-only reread, valid before
and after custody's eventual index cutover. General does not drop the global
constraint or enable enrollment. A separately released custody rollout owns that
cutover and old-writer absence proof. Preserve an already compatible post-cutover
schema rather than reintroducing global uniqueness.

Every name-based admission path checks the schema-wide combination of ANY private
parent and the legacy global name constraint before resolving a supplied name.
That incompatible combination returns the same fixed unavailable outcome for ALL
names; never expose a target-specific collision or distinguish unique errors.
Preserve flags/data and retain owner inputs as held where appropriate. Disposable
PostgreSQL tests cover legacy writer compatibility, both index stages, uniform
incompatible-state refusal, and post-cutover private-only name equivalence. The
synthetic post-cutover fixture proves General readiness, not custody implementation.

Do not infer ordinary eligibility from missing or malformed fencing. Before exposure,
verify the schema contract and parent classification. A reserved `possession_profile`
on a false/unclassified parent is inconsistent: refuse the parent's generic reads,
search, catalog, writes and source fences with the same fixed unavailable category,
without inspecting or returning profile content. A migration encountering reserved
profiles without an authoritative classification must refuse transactionally rather
than reset flags, guess ownership or auto-enroll legacy data. Ordinary capture may
retain a valid owner input as held while target eligibility is unavailable; it may
not file or expose an ineligible item. Missing-schema fallback is not read authority.

Every generic create/update/delete/move and every bulk/search/export/catalog path
checks the parent-first/item lock order and reserved namespaces. Generic input cannot
set/remove classification or profile metadata. Private enrollment is disabled until
its separately released implementation proves this complete guard set. Before any
true classification exists the passive migration can be reverted normally; after
private classification or duplicate names exist, downgrade refuses rather than
reopen data, merge rows or flatten names. This representation can be delivered by
General without claiming that private custody works.

### Capture service, roles and owner provenance

The deterministic General capture service is the only ledger writer. The new
`public.captures` and `public.capture_operations` tables are owned by the migration
owner but use ENABLE and FORCE RLS permitting the effective `butler_general_rw`
role only. Replayed public grants must not widen this. Explicit no-TRUNCATE and
terminal-receipt immutability guards also fence the table owner. Other daemon roles
and role-less audit/API pools have no direct ledger authority. No peer table grants
or caller-selected SQL/schema are introduced.

Owner-authenticated dashboard routes validate owner control and unsafe-request
Origin/CSRF before reading bodies, then use a narrowly scoped General-role pool
transaction with cleanup on success/error/cancellation. Model/core-tool capture
cannot assert an owner, source, target or role: the service validates the active
server-held session/source binding through the source-owning Switchboard MCP
boundary. The source must be an admitted owner-origin request tied to the current
session and exact input digest. Missing or unverifiable binding refuses admission;
a guessed ID, quoted text or caller actor string supplies no authority. This is
not a new permission to read arbitrary Switchboard inbox rows.

`capture()` is admitted only through existing configured registration gates. Its
core registration uses the existing `state` group and explicit presentable metadata;
no group is enabled or widened automatically. The General service's deterministic
admit/resolve/source-fence operations are infrastructure-only metadata and require
verified server request authority independently of visibility. Dashboard owner
status/read returns an allowlisted projection, never unrestricted table contents.

The ledger holds opaque capture/operation IDs, verified owner/source binding,
canonical input digest, bounded original input/reference, current disposition,
target owner and source-owned target receipt/version, timestamps and fixed failure
categories. Original intake is not a canonical specialist record: after routing or
refusal, General does not turn a Finance/Relationship/Home payload into a General
item or catalog entry. Access to retained source content remains owner/source-gated;
telemetry contains only opaque operation IDs and bounded categories, never payload,
raw exception, source message or private identity. Input limits refuse atomically,
never truncate into a falsely complete receipt.

### Commit-before-routing and target-owned outcome

Admission reserves `(owner, source, mutation_id)` and exact payload digest in one
transaction. Commit held state before scheduling any classification or target call;
return the receipt independently from asynchronous processing. Retrying identical
input returns that same receipt; changed input with the same key refuses. Different
source occurrences are distinct. Every worker claims a durable next stage before
its effect. Crash after a possible target call becomes `held/target_outcome_unknown`,
not a retry that might create another target record.

For a General target, target write, source-version creation and routed receipt
commit in one General transaction. For a specialist target, Switchboard brokers an
exact operation-bound request to that owner. Only an owning-service immutable
receipt proving the target operation/version may finalize routed state. General
never SELECTs a peer table and never trusts a caller's non-null target locator.
Timeouts or unknown replies retain the original binding; exact read-only reconciliation
with the target's operation receipt may resolve it. Missing target idempotency or
receipt support means held/unavailable, not simulated cross-domain completion.
Cooldown, restart, repeated classification and owner refresh cannot create a new
target operation while that lineage remains in doubt. An explicit refusal remains
refused unless a later separately authorized owner action resolves the source policy.

### Durable backup and rollback

Capture inputs, operations, source versions and fences are durable, not disposable
runtime-probe nonces. Extend the existing shared-snapshot backup/restore machinery
for a General-role projection and fixed validated restore path; schema, forced RLS,
immutability, ownership and no-TRUNCATE fences must survive. Role-less pg_dump cannot
silently exclude these records. Restore starts routing and fence grants disabled;
rotate a host-held service epoch outside restored rows before new authority is issued,
mark restored in-flight effects/fences in doubt and reconcile exact outcomes before
release. Never replay an operation merely because an older snapshot lacks its receipt.
Source implementation of this fence does not authorize live restore/deployment.

## General source-fence readiness without checklist implementation

General owns immutable source snapshots and an eligibility generation bound to the
parent/item and current owner/read/privacy state. Ordinary mutation, deletion,
classification change, catalog retirement and fence operations serialize on parent
then item. A source fence binds source ID/version/generation, verified owner/consumer,
opaque future packet/revision/operation digest and service epoch. Caller locators or
asserted selection do not mint it. No protected value is returned before eligibility.

A future General-local checklist consumer can verify and commit its own receipt
only in the SAME General transaction that holds the source and packet locks. A
Switchboard check followed by a separate commit is not equivalent. No checklist
packet tables, finalization endpoint or UI is implemented by this General release.

For a future remote consumer, the source-owned protocol is `prepare`, `commit_start`,
and `resolve`. Prepare creates a bounded durable reservation of the exact source
version and authority; all participating source mutators honor it. Before returning
permission to commit, commit_start transitions the reservation to durable in-doubt
commit authority. Source changes/privacy enrollment are blocked while it is in doubt.
A timeout/lease expiry may retire an unused prepared reservation, but MUST NOT release
a commit-started reservation. Only an authenticated, exact consumer operation outcome
read through MCP can resolve it as committed or aborted; missing proof remains held.
Replay returns the same reservation/outcome, never another permission. An outcome
acknowledgment is validated against the exact consumer/packet/revision/digest and
source generation, not a model-authored `committed=true` field. Source release and
outcome recording are one transaction. Process or database recovery preserves holds.

These source endpoints remain unavailable to an unimplemented consumer. General
may deliver and test interface readiness with a deterministic synthetic consumer;
that is not proof of checklist receipt atomicity or a specialist's compatible fence.
If a future checklist/Finance/Travel implementation cannot satisfy this protocol,
it stays unavailable; no peer-schema transaction or optimistic remote version check
is a substitute. Each future owner-side integration remains separately adopted and
released. Historical generic receipt projections must still recheck current privacy.

### First supported target and unsupported specialist receipts

The initial supported capture target is an ordinary General collection item.
`roster/general/tools/capture_service.py` coordinates the existing
`roster/general/tools/items.py::item_create` seam and the new source-version writer
on one acquired General connection/transaction. Only fixed ordinary kinds
`note`, `fact` and `preference` map to declared `notes`, `facts` and `preferences`
collections; the owner/refusal classifier must not treat specialist facts as an
ordinary kind merely to get a receipt. The service creates the target and immutable
version, then records `owner=general`, capture operation ID, item UUID and exact
source-version/digest in its routed receipt within that transaction. A verification
read is served by the same General capture service over the exact operation ID,
checking the stored receipt/version and current owner/privacy boundary, not a
caller-supplied schema/table. Existing item_create returns an item UUID; that alone
is not this new receipt and must not be relabelled as one.

No current Finance, Travel, Relationship or Home writer is assumed to provide this
capture operation/verify contract. The initial capability registry admits only the
implemented General receipt seam. A specialist target lacking an explicitly
reviewed source-owned idempotency/receipt/verification contract is refused or held
as destination-unavailable BEFORE a target mutation; it is not called optimistically
and later verified by peer SQL. Existing domain tools may be named as owner next
steps only when actually present, without claiming capture receipt compatibility.
Enabling a specialist adapter requires its owner's separate contract and source
release. Mock specialist receipts prove refusal/parser behavior only, not implemented
or deployed support. This General release does not grant new Finance/Travel or other
source-owner authority.

### Capture audit and validation do not retain source bodies

The capture API and its explicit chat-intake bridge use a server-owned no-body
audit policy chosen from their fixed routes before DashboardAuditMiddleware reads
or buffers request JSON. A caller header, body flag or model argument cannot select
that policy. Keep the normal audit event with server actor, fixed operation/category,
outcome and opaque operation ID; do not disable audit wholesale. No captured text,
attachment locator, proposed private key/value, source label or raw exception enters
audit, trace, logs or diagnostic metadata on success or failure.

Use a capture-scoped APIRoute/exception projection before generic ValueError and
FastAPI validation handlers. Malformed JSON, 422 validation, domain ValueError,
timeout and cancelled requests have fixed content-blind outcomes and retain no
framework `input` payload or `str(exc)`. Cancellation propagates after cleanup and
content-blind audit; it is not a successful admission. Other API error/audit policies
remain unchanged.

The explicit dashboard/chat capture action must reach this protected admission
route, not ordinary conversation submission followed by a late audit-policy change.
The chat bridge uses a distinct server-known capture endpoint and creates a source
binding plus held receipt before routing. A capture already originating from an
ordinary user conversation retains that original source's existing message history
and provider semantics; General makes no claim to erase prior ordinary disclosure.
It adds no raw capture copy to audit or process logs. The separate private custody
surface remains the only private capture path for custody data.

### Deliberate consolidation source delivery

The repository outcome includes deterministic preview, exact owner-confirmed
mapping application, and inverse compare-and-swap; none is optional. Implement
these in `roster/general/tools/vocabulary.py`, capture owner API endpoints, and a
General-owned proposal-operation store in the General migration chain. Persist
proposal digest, input parent/member revisions, exact mapping, alias history and
post-apply revision atomically. Lock parents then members and operation state;
resolve proposal names and aliases only in the ordinary namespace. A private-only
matching name behaves absent and never causes a target-specific collision refusal.
Refuse stale revisions, private/unavailable source or destination UUIDs, unknown
authority and reserved namespaces uniformly before mutation. The incompatible
legacy/private schema state still refuses ALL names as defined above. Preserve
collection/item identities,
immutable source versions and source-fence constraints; consolidation changes
explicit ordinary aliases/mappings without deleting history. Inverse application
requires the exact recorded post-apply revision to still match, otherwise refuses
without guessed repair. Idempotent replay returns the same operation outcome.

Synthetic real-Postgres tests exercise preview/apply/inverse, stale confirmations,
concurrent writes/protection, changed-payload replay, rollback, inverse-after-edit
refusal and identical proposal-name behavior with absent versus private-only
matching names, paired with an ordinary positive control. Owner-authenticated API and UI tests exercise exact confirmation and fixed
content-blind errors. No actual live/private mapping selection or application is
authorized by repository delivery; operational selection remains a separate act.
