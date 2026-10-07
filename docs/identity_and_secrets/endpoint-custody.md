# Endpoint custody

The governing contract is the complete `endpoint-custody-holds` OpenSpec change.
This document records the source implementation boundary and integration traps.
The draft is not installed or deployed; no live fleet or recovery outcome is
claimed. All original acceptance groups and slices still require their real
owning controls and protected delivery.

The adopted trust premise is the existing trusted host, daemon, configured
administrative bootstrap and enforced runtime database roles. A model argument,
session ID, channel locator, UUID or digest does not establish authority. This
implementation introduces no LOGIN, credential, signer, OS broker or privileged
container. It does not isolate a hostile child that compromises its trusted
same-UID host.

## Owning transactions

`CustodyRuntime` supplies daemon/connector startup through the existing trusted
connecting identity and a separate live anchor. Its role/configuration profile
is constructor-fixed. It rejects the dashboard profile: the restricted API
channel is implemented separately by `custody_api_parent` / `custody_api`,
rather than obtaining an administrative connection through this factory. The
actual dashboard CLI starts a fixed child with one inherited local socket; the
parent validates the exact dashboard profile and performs one SQL enrollment.
The child constructs its anchor with the existing restricted owner-auth LOGIN,
checks that identity with the existing Tier0 verifier and binds only its actual
auth pool. The parent freezes the expected profile and configured restricted login
before spawning. Existing host enrollment returns its actual nonce-bound
anchor login/database/PID/backend-start metadata in the bounded receipt; the
parent compares that login OID with its own fixed launch record before releasing
the receipt. The engine also refuses dashboard anchors reachable to privileged
or unrelated roles, host EXEC or direct private auth/custody table access. These
are server-observed identities/lifecycle witnesses, not self-reported authority.
The socket carries no credentials and exposes no HTTP/MCP host endpoint. CLI, Compose and Helm already use this same dashboard command. Raw
uvicorn factory invocation has no startup socket and the custody door remains
unavailable. The trusted-host premise still applies: neither inherited FDs nor
process separation claim hostile same-UID or environment isolation. Actual
migrated child/startup/termination/topology controls remain unrun.

`CustodyAdmission.writer()` acquires the actual owning pool connection.
`bound_writer(connection)` instead accepts an already acquired connection from
that exact pool. It validates asyncpg's actual proxy/holder/in-use identity and
refuses foreign or released checkouts, an already running transaction, and a
nested acquisition. Call it before domain locks. It owns the outer transaction
but leaves acquisition/release with its caller. Existing conn-aware domain
writers receive `writer._connection`; their nested transactions are savepoints.
Reuse the yielded writer rather than bind it again. The admission object checks
its exact live yielded writer identity, not only a connection ID or a caller's
wrapper. A private receiving-guard ContextVar can return only that admission's
still-active writer and is reset in finally; being bound alone does not install
a guard verdict. SQL must still recheck current authority at each final write.

The server nonce is begun and bound by the live anchor before the transaction.
`custody_connection_finish` executes inside the business transaction, before the
domain handler, so its auth-singleton/control locks last through that COMMIT.
Unbind follows transaction exit. A lost COMMIT acknowledgement is UNKNOWN;
failed or ambiguous unbind terminates/discards the physical backend before pool
return. Cancellation does not turn rollback into a successful acknowledgement.
Finite leases provide bounded liveness, not instant process-death observation.

The protected command checks the current owner session, CSRF, origin, RP and
epochs at the same COMMIT boundary. A verdict obtained from another pool
acquisition is insufficient. Auth/control precede inventory/origin metadata,
the complete sorted target union, immutable command/call/question, and finally
case/evidence. Network I/O never occurs inside this transaction.

## Registered MCP admission

Trusted startup registers `CustodyMcpService` once on its actual FastMCP
registry. `CustodyToolAdmission` is the first tool middleware, before the
existing FunctionTool logging/capture/span wrappers. Verification runs in the
actual tool execution task, including the legacy SSE task path; an ASGI-only
ContextVar does not establish this boundary. The guard clears all private
contexts in finally and the service consumes its exact live writer rather than
opening another transaction. Protected tools are registered with `task=False`;
background requests and error ToolResults are refused. Ordinary calls retain
their existing owning behavior, while reserved custody claims in arguments or
the real SDK request metadata are refused before instrumentation.

`custody.apply` takes one `wire` argument containing exact UTF-8 JSON **text**.
The inner `custody-wire.v1` object and the 25 governing protocol/control interface
names and signatures are retained. Receipt recovery's current-authorization
selection is described below; private implementation helpers are separate. A dictionary DTO is refused: it would have discarded duplicate
keys before the registered parser could inspect them. Bound the inner wire at
8192 UTF-8 bytes, rather than its larger escaped enclosing MCP argument. Raw
JSON-RPC envelope validation still belongs to the transport; the unit controls
prove duplicate rejection inside this exact custody wire. New source callers
send canonical JSON text and no dictionary compatibility alias is registered.
No deployed consumer of the previous unpublished draft is claimed.

The guard verifies the bound method and argument digest before instrumentation,
then repeats `custody_verify` on that same writer after the handler, before
COMMIT, to catch advancing lease/challenge expiry and compare the exact immutable
source and operation. Nested argument mutation is refused. Domain handlers still
perform their final source/auth/target checks; the final guard check cannot
replace those checks. The client receives success only after COMMIT and safe
unbind acknowledgement; UNKNOWN never becomes success. FastMCP's raw-argument
DEBUG logger is pinned to WARNING, and private handler exceptions are reduced
to content-free custody errors before the existing logging wrappers.

The SQL draft stores the exact online-verified call on its acquired connection
metadata, clears it on every begin/unbind, and refuses changing call identity
inside one transaction. `custody_admit_write` admits a receiving process only
through that same call/source/operation/target binding, never another writer's
verdict or an unrelated source association. An owning producer can still admit
its own actual registered source. This is private admission metadata, not a
cross-butler delivery bus. Actual role, rollback and same-connection controls
for this SQL remain unrun.

`CustodyCommandReceiver` supplies only the fixed Switchboard command/result
handlers; IDs locate immutable commands already bound by the source and SQL.
`CustodyRuntime.attach_mcp` installs the constructor-fixed admission middleware
after trusted startup, before serving. The canonical core dispatcher registers
the two fixed Switchboard definitions against that retained service. The actual
run/up daemon lifecycle now calls startup and shutdown; migrated startup and
shutdown execution remain unproved. Restricted API startup now has separate
native CLI/lifespan wiring. Real producer/native canonical writer integration,
both HTTP transports and independent supported deployment remain required proof.

## Canonical channel currentness

`CustodyChannelBindings` is a constructor-fixed Relationship producer. Its
`publish_current(writer, channel_type, channel_value)` reads the actual active
canonical facts and live entities using that same guarded connection, locks
entities and re-reads after waiting. Exactly one identified live owner produces
an owner projection; missing, ambiguous or non-owner mappings produce explicit
unbound state. No resolver DTO, claimed actor or model UUID provides the owner,
source version, digest or expiry.

The query preserves the adopted owner resolver's bounded phone suffix matching
as well as exact email/handle matching. A competing live identity must remain
in the ambiguity set even when its phone spelling differs.

Typed origin hashing unifies Telegram transport aliases, `@`/`telegram:`
spellings, normalized email and WhatsApp device suffixes. Other platforms stay
distinct. Hashes are linkable selectors, not anonymous or authenticating. Actual
fact IDs, hashed values, entity lifecycle and roles determine the snapshot;
raw channel values remain in the owning business data. The ordinary state API
is not trusted: every projection field is reconstructed from canonical reads,
and state values supply only version/dedup hints.

The owning callback registers its projection through the existing
`custody_source_register` domain-evidence family, `identity_binding` intent.
Private origin pointers and frozen per-source associations subdivide the
existing source model; there is no new public selector, peer SQL or role grant.
The source engine captures the actual owner birth witness from its locked
public entity row. It compares owner, birth, current role and binding revision
before targets and after waits. Birth time is a bounded lifecycle witness,
not a cryptographically unique incarnation; deliberate identical UUID/timestamp
recreation by a trusted administrator is outside that claim.

A genuine fresh canonical observation can issue a new finite five-minute source
revision after expiry. An existing accepted report keeps its exact original
captured association across replay/restart; a missing original association is
unavailable and cannot be refilled from today's pointer. A renewed observation
of identical canonical content can supply current liveness; it cannot change
the report's frozen binding content, owner/birth or original expiry. Changed
canonical fact/version/owner content advances a private monotonic binding
generation and refuses the old report, including a later return to the same
content. That generation is derived by the engine from its previous immutable
observation, never supplied by a model or resolver. Every actual owning
mutation must publish in the same transaction for that transition to exist.
Restored-history
admission remains subject to its independently owned, undelivered history fence.

Actual integration must wrap all canonical channel-changing paths and publish
both old and new affected selectors before the outer COMMIT: assertion,
supersession, correction, retraction/deletion, merge, provider all-version
cleanup, owner-role/lifecycle changes, and phone-to-WhatsApp fallback. A
postcommit notification cannot substitute. The .2 request-admission context
and minimized `identity_resolve_channels` MCP result provide no custody
current-at-COMMIT privilege. First shared writer/transport hunks remain with
their original author until actual adapter integration is serialized.

`mutation(connection, entity_ids)` supplies that private wrapper for the fixed
native writer's complete subject/merge batch. It captures actual old facts
before destructive writes and actual new facts afterward, then publishes the
complete affected typed selector universe. A phone can match a sender with up
to two extra or missing leading digits, so those variants are included even
when a changed phone belongs to a competing foreign identity. No model-selected
subset or mutable index supplies completeness. An unrepresentable batch rolls
back the mutation rather than acknowledge a partial currentness update.
`bound_mutation(writer, entity_ids)` reuses that exact writer when an outer
guard already owns the transaction; it does not open a second checkout or
rebind after domain locks. Publication failure propagates through the outer
transaction. The baseline native caller hooks are installed as detailed below. Real
rollback/rebind controls are authored and remain unexecuted PostgreSQL proof.

`observe_channels(channel_type, channel_values)` supplies the owning resolver
callback for the existing registered `identity_resolve_channels` tool. It
validates every requested typed selector, opens its actual owning writer,
publishes canonical current projections and resolves on that same connection,
then acknowledges COMMIT before returning only the existing minimized DTO. It
exposes no birth/version/grant field. This callback is installed at the baseline owning registered tool; its
real-role, registered-network and serialized peer integration remain unproved. Switchboard must call it through MCP outside its own locked
business transaction, then reread and register its canonical accepted row;
network I/O while holding control locks cannot establish this protocol.

## Holds, recovery and effects

A genuinely owner-resolved surviving source can request cheap LOCK, including
unauthenticated email under the adopted literal policy. This grants authority
removal only: no release, approval, steering, session revocation or owner-class
content. A source-owning parser/selector must read the actual accepted row;
a model's LOCK flag or proposed targets are insufficient.

`CustodyAcceptedIngress` now reads the actual owning inbox partition rows on
the bound writer, refuses missing/duplicate/history/batch ambiguity, and freezes
the original row/content digest. `assert_unchanged` re-reads and compares that
same record; changing lifecycle output alone does not rewrite the original
report content. Its private report cannot be serialized. The bounded parser
recognizes explicit target LOCK and loss/theft entry text. A target UUID remains
a locator; natural device labels require genuine unambiguous selection or a
same-source selection confirmation. The reader and parser authenticate neither
the sender nor the owner. Actual source registration, canonical owning MCP
resolution and the protected command pipeline are still required integration.

Authenticated provider NO with unresolved affected account selects the complete
current proven owner-associated cohort of that provider. Incomplete inventory
must commit the actual answer/case/recovery evidence with containment UNKNOWN,
without acknowledging a partial set as all held. A changed complete version
requires reconciliation and a new answer bound to that selection. Complete
empty inventory means no known target, not protection of an unknown account.
Constructor-owned roster/contributor producers and live inventory reconciliation
are still required implementation; companion account entities alone do not
establish the instance owner or completeness.

Private case scopes distinguish lost-device recovery from independent account
security episodes. Case membership binds actual hold generations and has a
version. Recovery locks the whole member-target union before the case, then
checks that version. Only the selected lost-device case can close after all its
assigned generations are disposed. Partial recovery and unrelated cases stay
active. One held episode can contribute to independent cases; its first public
case association does not replace explicit generation membership. Repeated LOCK
correlation uses actual held episodes rather than changing pre-hold expectations.

`start_effect` returns a possible-start grant only after its stable owning effect
marker COMMIT is acknowledged and the writer exits safely. Provider I/O follows
that return. UNKNOWN requires same-ID readback; an already possible start is
never resent. Real queued/deferred/scheduled producers, provider invocation,
automatic eligible no-start notice recovery, protected question tokens and API
doors still need their actual integration and controls.

## Implementation Notes

- Private migration core260 now points to the actually adopted core261 (main795d). The current chain has one core head260; adopted261 remains its parent. Capture259
  is neither imported nor a prerequisite for independent fresh sources.
- New definers use exactly `search_path=pg_catalog,pg_temp` with qualified
  relations and non-catalog functions. Private table DML and hold lifecycle are
  unavailable to ordinary runtime roles; replay bootstrap/finalizers and prove
  forced RLS under genuine roles. Startup now compares all 55 fixed function
  body/ABI/configuration records against the checked-in source manifest; its
  privileged prover checks owner, bounded/missing function grants, private raw
  access, runtime-role hierarchy and the sole forced-RLS hold policy. These SQL
  checks and the trusted first-install schema-identity comparison are unexecuted;
  migrated replay/drift/default-role controls remain required. A helper count
  alone is insufficient.
- After changing owned function source, run
  `scripts/generate_custody_interface_manifest.py` and its `--check` mode. The
  generator reads only the fixed feature source; it does not inspect a database
  or stand in for migrated installer proof. Validator unit positives/refusals
  are software evidence only.
- Positioned wire/lifecycle unit controls prove encoding, checkout, transaction
  ordering and cleanup only. They provide no SQL, enrollment, real source,
  current owner, actual registered guard or deployment proof.
- Local Docker access is known denied before SQL. Do not retry it, widen
  privileges or claim SQL from mocks/collection. Use genuine disposable migrated
  owning controls and exact hosted gates before source approval.
- Required real controls include same-connection source/business rollback,
  rebind/delete and stale-source negatives with fresh current positives,
  revocation and expiry after waits, exact same-process/independent supported
  deployment, whole generation closure and unrelated-case preservation,
  inventory completeness/UNKNOWN, effect acknowledgement and truthful recovery.
- Do not invent the .20 authentication-artifact producer or restored-history
  fence. Both remain required and undelivered; neither silently defers a slice
  or blocks all unrelated genuine fresh-source engineering.


## Installation identity and acknowledgement witnesses

The fixed privileged installer records its schema identity only when the
feature tables do not already exist. It refuses unrecorded existing feature
tables instead of adopting their structure. The recorded identity is held in
the bootstrap-owned private configuration; ordinary runtime roles cannot read
or replace it. Replays and startup compare exact columns, types, defaults,
constraints, FK actions, indexes, RLS policies, user triggers and rules. Owner,
ACL and runtime membership checks remain separate. The body manifest now has
55 fixed internal and wrapper functions, including the private accepted-row compiler.
This adds no public authority selector. This is source allocation, with real
first-install, replay and tamper controls still awaiting migrated PostgreSQL.

Custody core tool capture is staged by the actual outer middleware. Handler
return alone produces no captured success; successful COMMIT and cleanup must
complete first. A lost acknowledgement records UNKNOWN, and failed final
verification records error. The staged record retains only the tool name,
module and argument fingerprint, never the challenge nonce, wire, raw error or
private result. These software ordering controls have a registered MCP positive
and lost-ack/refusal controls; they are not SQL authorization evidence.

Trusted daemon startup retains the actual Relationship channel producer or
Switchboard accepted-row reader after enrollment. They have no model-facing
allocation endpoint. Baseline native writer and inbox callbacks are wired as detailed below;
real same-transaction/current-source and complete writer-universe proof remain
required. Failed later daemon startup stops
its retained custody runtime; shutdown clears producers before closing anchors.


## Current-authority receipt recovery

The trusted host source uses a distinct `host-switchboard` process identity
under the existing configured Switchboard owning role (normally
`butler_switchboard_rw`). It can produce only fixed host
control operations addressed to Switchboard; it is never mistaken for the
registered MCP receiver. Mint refuses zero or multiple live receiving
incarnations rather than selecting the newest one.

An expired original command keeps its durable command ID, selection,
generations and receipt. Current browser/host preparation may create a fresh
private `eligibility` ticket whose closed selection contains only the original
result command ID, an empty target set and version 1. The source registration,
mint and registered receiver all check that current ticket and exact original
ID/body. Only `custody.result` is permitted; it cannot commit or recreate the
original mutation. Reading the same receipt after a lost acknowledgement does
not resend any effect. Old source authorization, wrong result IDs and current
proof expiry refuse; absence of a durable receipt remains UNKNOWN. The
private command-source helper and accepted-row compiler are included in the
55-function source manifest. The reviewed 25 governing names/signatures and
`custody-source.v1` remain intact; the bounded result-carrier refinements below
are explicit source transport changes.

The constructor-fixed `CustodyControlTransport` performs actual MCP challenge,
source response and guarded apply after source registration COMMIT, outside
business transactions. It has no caller-selected verifier URL. Host/browser startup and CLI wiring are implemented on this baseline.
Independent-process network controls and live current-owner revocation tests
remain required; presence of this helper is not that evidence.
The owning migrated SQL group now includes real registered service/role
command/receipt/expiry/ambiguous-incarnation controls, authored but not locally
executed under the known Docker restriction.


The actual `butlers custody control --config roster/switchboard --operation
hold|release|replaced --selection PATH` host command loads the configured owning
database and fixed Switchboard endpoint. The selection is bounded JSON with
duplicate-key rejection; caller actor, role, credential and verifier URL options
do not exist. `butlers custody result --config roster/switchboard --command UUID`
prepares a fresh current read ticket and reads that same durable command.
Neither command installs or repairs schemas, nor applies historical cleanup.
Failures print only fixed verdicts; source/wire/target/evidence values are not
CLI diagnostics. Genuine standalone network/deployment proof remains required.

The host CLI emits the safe original command UUID after source preparation
commits and before remote I/O. It retains that locator on UNKNOWN, so recovery
reads the same command even if the apply acknowledgement is lost. It never
prints the fresh private read-authorization ticket, raw result payload, target
bindings or provider diagnostics. CLI tests prove this rendering and selector
surface only; they do not substitute for real host authority or SQL proof.


The SQL command commit, sensitive receipt read and provider-start marker require
the exact online-verified call on the current acquired writer. An armed call
UUID copied into another bound pool transaction is insufficient. Ingress and
security-answer commands additionally retain their original source identity;
a different accepted source cannot borrow an otherwise matching prepared
command. These checks add a private fixed writer helper, leaving the public
25 ABI unchanged. Genuine direct-call-negative and registered-call-positive
controls belong to the migrated group, not the transport doubles.

## Accepted-source compilation and source-side integrity

The governing P1c `source_register` result was an immutable source reference
and digest. Its concrete JSON result is now the closed object
`{source_ref, source_digest, projection}`. `projection` is the exact immutable
server-compiled projection whose canonical digest is `source_digest`, returned
only to the enrolled source-owning process. It is absent from resolver DTOs,
model tool arguments, public API results and receiving handler authority.
This changes the bounded result carrier of the existing
`public.custody_source_register(text,jsonb)` interface; it adds no selector or
public interface name. All 25 names/signatures and both v1 wire versions remain
the governing contract.

For exact-ID LOCK, the private actual-row producer submits only the inbox
locator, revision 1, actual stored-row digest, derived origin digest, explicit
IDs from that row, bounded expiry, intent LOCK and empty owner/issuer/target
placeholders. The installed compiler independently reads the unique physical
accepted row on the source's owning Switchboard connection. It rejects history,
batch identities, non-LOCK text, changed digest and mismatching origin/selection.
Owner, issuer generation and sorted target snapshots come from protected current
metadata published by the owning Relationship writer. An old report reuses its
exact frozen projection and expiry; a changed association cannot relink it.
The exact-ID LOCK deadline is five minutes after the server's actual accepted
row timestamp, never five minutes after a replay. Revision stays 1. An expired
historic row cannot be revived by a new revision or freshly selected expiry.

`CustodyAcceptedIngress.capture_lock` independently checks the returned digest,
original locator/content/origin/selection and typed compiled target values. It
returns a private source handle only after acknowledged registration COMMIT.
The fixed owning Relationship MCP call must precede this capture outside every
business transaction. Its minimized resolver reply never authenticates the
report. Native same-transaction publisher hooks and accepted-row write fences
remain required integration; this compiler does not prove that a synthetic
projection or a bare resolver response is current canonical evidence. Bare
LOCK and natural-language loss/theft retain the mandatory same-source selection
flow; exact-ID capture cannot substitute for those entry paths.

The existing `public.custody_mint(uuid,jsonb,text)` result is refined from
`{call_ref, operation_digest, expires_at}` to include the closed `binding`
descriptor used to compute that digest. It binds source reference/digest,
issuing and destination process incarnations, destination actor, exact operation
and body/target generations, control/restore epochs and expiry. The source
checks it against its own committed source digest, retained enrollment receipt,
fixed audience and exact intended operation, then recomputes the SHA-256 before
sending a challenge. Rehashing a wrong body/source/issuer/epoch does not satisfy
those independent comparisons. Digest agreement is integrity, not secret
identity; actual online challenge and final current same-writer SQL verification
remain mandatory. Tests of these value checks are software evidence only.


Protected wire, selector file and JSON-RPC envelopes require exact UTF-8 JSON.
The raw guard classifies every JSON encoding the SDK would accept before
checking this restriction; UTF-16/32 and BOM custody requests therefore cannot
bypass outer duplicate-key checks. Ordinary MCP encoding behavior is retained.
Positioned pre-fix controls reached the parser and actual raw guard with valid
encoded custody bodies and failed; current positives cover normal UTF-8 custody
and ordinary UTF-16 delegation. These remain transport/software evidence.


Daemon profiles bind the actual constructor-selected existing owning role,
which may differ from the logical actor and permits the configured SQL schema
identifier grammar. Enrollment and interface proof validate that existing
role's real hierarchy and backend; the actor/role spelling does not provide
permission. Default parsed configuration still derives the butler schema.
Explicit legacy no-schema daemon startup now selects its existing
`butler_<name>_rw` role and requires SET ROLE enforcement rather than retaining
the connecting login for domain writes. No role, membership or fallback grant
is created. The actual legacy/default migration, table-access and supported
runtime positive remain required; schema/profile source presence is not proof.

The migrated group now authors both actual default-config profile construction
and a legacy no-schema owning pool/source registration under the real existing
role. This is a profile/pool/source control, not whole daemon or independent
legacy deployment proof. No SQL control has run locally.


The fixed prover checks effective runtime schema/table/column privileges and
runtime database ownership in addition to explicit ACLs and dangerous role
attributes/parents. This catches inherited/predefined and column-only access
which ACL-list comparison alone can miss. The migrated control distinguishes
initial SELECT refusal from RLS proof: after a genuine committed hold, a
disposable test-only SELECT grant yields zero rows under the real role, and
policy neutralization finds that same planted row before full restoration and
finalization. Those source controls remain unexecuted PostgreSQL obligations.


## Browser command preparation and recovery

`OwnerAuthMiddleware` treats only `/api/endpoint-custody/commands` and its
subpaths as the dedicated browser command door. It requires the actual owner
cookie, trusted HTTPS, exact configured Origin and CSRF before body handling,
even when a header key is also present. Generic routes retain their existing
header precedence; configured-key owners obtain the cookie through the existing
owner session ceremony. The middleware captures only digests in a private,
unpickleable request ContextVar, resets it in finally, and never exposes auth
epochs or a proof field in the public command body.

`CustodyBrowserProducer` checks its constructor's actual pool identity and uses
that pool's bound writer to call the existing five-field
`dashboard_auth.custody_prepare` interface. SQL locks the auth singleton and
selects credential/session epochs itself. Prepared-source registration on a
fresh bound writer rechecks the immutable command/current auth and derives the
single canonical owner inside the engine. Neither the restricted auth role nor
the Python producer needs a public entity SELECT grant. The source result
carrier now compiles the owner-command projection as well as accepted ingress;
source reference/digest/projection remain closed, and the producer checks every
other field against its own immutable selection. No public ABI is added.

The native API returns the original durable command ID from a prepare request
before any remote effect. An explicit empty-body commit request uses only its
live private prepared source and cannot resend it after an attempted delivery.
The commit door compares the current actual captured cookie/CSRF/config proof
with the frozen preparation proof, so a different session cannot lend its HTTP
verdict to the old command. SQL repeats that same original proof at COMMIT. A
fresh session can still obtain a read-only result ticket. The bounded private
cache contains at most 128 entries for at most 30 seconds;
SQL deadlines may expire earlier and cannot be extended by the cache. Missing
cache, restart, deadline or lost ACK returns UNKNOWN for that ID. The result
request creates a fresh current browser read ticket for the SAME original ID;
it never recreates a mutation. All three door paths require current cookie/CSRF
before request-body handling, and the engine repeats command proof at the
receiving domain COMMIT. Network I/O occurs after source COMMIT/unbind, outside
business transactions. Cache/state/ID possession is not command authority.

The software control neutralizing the dedicated cookie door actually returned
200 to a header-only request, against the required 401; restored software is
positive. That control does not prove SQL revocation, actual parent enrollment,
registered-network COMMIT or supported deployment. Those remain separately
required migrated owning controls.


Actual accepted-source birth is frozen by the existing-bootstrap-owned AFTER
INSERT trigger on the canonical partitioned Switchboard inbox. The native
`ingest_v1` acquisition enters its constructor-allocated SAME-pool writer before
its existing transaction/advisory locks. The trigger records only the physical
record/time, content digest, original process, and control/restore epochs in the
SAME transaction as the inbox/event inserts. Recent locators, caller fields,
`xmin`, resolver DTOs and ordinary unbound inserts cannot create this record.
Legacy ingestion remains accepted without custody provenance. Duplicate returns
reuse the original record; they cannot add a birth to an old unbound report.

An AFTER UPDATE/DELETE companion retires the frozen birth one-way for original
content/lifetime changes or deletion. Ordinary unchanged lifecycle processing
preserves it. Delete/recreate or changing content back cannot refill it. Final
custody COMMIT locks and checks that protected birth metadata, without reading
peer inbox rows. Retirement and admission serialize before their respective
commits. Trusted maintenance that bypasses triggers (TRUNCATE/partition DDL or
restore) must fence admission through the existing host/control protocol; this
is not hostile-host isolation or delivery of the foreign restored-history
admission producer. Exact-identical privileged restore is not inferred from a
UUID/timestamp/digest. A new enrolled incarnation may rehydrate an unchanged
original birth within the same epoch and expiry, preserving the original report.

The fixed installer runs after owning migrations and before daemon proof and
enrollment. Core-only databases skip the absent inbox. Its prover checks both
fixed triggers on every present partition; ordinary roles receive no private
trigger EXECUTE, table access, ownership or new membership. Two private helpers
are part of the exact 55-definition source manifest; the reviewed 25 governing names,
signatures and protocol versions remain unchanged. These SQL/role/native birth
controls are authored but not locally executed; software doubles and collection
supply no database or independent-process proof.

The baseline Relationship native write block, contact-info retraction,
preferred-channel writes and entity merge now resolve their publisher by the
actual owning pool and enter `native_channel_mutation` before their native
entity/fact locks. Caller-owned genuine bound connections are reused; an
already open unbound transaction cannot establish first-lock order through a
late savepoint. New assertion parking/notification stays outside a newly
opened custody transaction. The registered contacts-group
`identity_resolve_channels` calls the owning publisher before returning its
existing minimized DTO. Empty reads are no binding observation. Legacy
unallocated pools retain their existing behavior and provide no custody proof.

This is baseline implementation, not proof of the complete writer universe.
The separately owned REQUEST-attribution writer now has an earlier complete
`locked_report` entity batch. Its actual protected integration must install the
released outer custody callback before that batch, preserve request attribution
and deferred notification behavior, and use the same business transaction.
The baseline now includes configured API entity lifecycle/role mutations,
Relationship promotion/contact-edit/forget outer transactions and legacy
contact merge. The baseline destructive Google/Steam companion paths are now wrapped as
detailed below. Remaining identity-adoption paths, generic Memory/non-Relationship
public metadata writes, every other canonical channel mutation, and the
serialized current REQUEST-attribution writer must be included and proved
before complete COMMIT-currentness can be claimed. No peer source is
adopted here, no resolver DTO authenticates a report, and no missing constructor
allocation or presence-only callback is credited as a current binding.


## API canonical-writer allocation

Configured schema-scoped Relationship API startup now proves the installed
interface and existing `butler_relationship_rw` role, then enrolls a private
canonical-writer anchor against its actual `DatabaseManager` pool. It uses the
existing configured API connecting identity; this is already trusted bootstrap
code, not the restricted owner-auth pool or a new credential boundary. Raw API
requests, actor fields and resolver DTOs cannot allocate it. Failure closes the
configured pools before publishing the DB singleton or serving handlers. Legacy
configurations without the owning Relationship schema remain unconfigured and
receive no custody-currentness claim.

This constructor profile has exactly `domain_evidence` source family and empty
operation/audience scopes. SQL restricts it to `identity_binding` publication;
it cannot register accepted ingress, prepare/mint a custody command, receive a
custody call or install an MCP guard. Destination selection excludes this empty
scope, while overlapping genuine receiving incarnations still fail closed.
No public interface name, argument signature or wire version changes. The
source manifest still contains 55 internal and wrapper SQL definitions. The
reviewed 25 interfaces remain unchanged, with exact definer search paths
preserved.

Native single-statement entity updates acquire the fixed canonical pool and
enter control-first `bound_writer` and sorted entity locks. They publish the
actual old/new channel mappings before that SAME outer COMMIT returns. Native
promotion, contact edit and forget enter this boundary before their existing
transactions and complete entity/fact batch locks. A newly created entity batch
may be empty before insertion; actual subsequent fact writers publish the
server-generated subject within the same already bound transaction. Parking and
notification behavior still needs the owning serialized writer's no-network-in-
transaction controls; source presence supplies no such proof.

Ordinary API readers retain their existing connecting-role policy. After an
acknowledged mutation COMMIT/rollback and exact UNBIND, the writer explicitly
executes `RESET ROLE`. An unknown reset acknowledgement discards the backend;
`RESET ALL` or generic pool release is not credited as role restoration. A
stopped constructor keeps its refusal-only allocation until the actual pool has
closed, so a late native request cannot fall back to an unconfigured path.
Pool-close cleanup releases only that closed actual pool's allocation.

Software controls neutralize the actual same-writer API callback, reach the
native write without its first locks, and fail the positioned lock assertion;
restored positive and rollback companions remain explicit. Migrated controls
add actual separate API pool/startup, native role update and committed origin
readback, frozen original-report refusal versus fresh accepted-source positive,
a publication failure after the real entity UPDATE with separate rollback
readback, role restoration and shutdown refusal. These migrated controls have
not run locally. They do not prove independent deployment, the complete native
writer universe, foreign REQUEST integration or any whole acceptance outcome.


## Accepted-after-COMMIT dispatch and recovery

The native bound Switchboard inbox INSERT freezes its accepted birth and a
private custody work row in the SAME acceptance/event transaction. Failure of
the event write rolls back both. Duplicate acceptance creates neither a second
birth nor second work row. This work registry carries only the original record
ID, claim/process lifecycle, immutable source reference and dispatch state; it
is custody admission metadata, not a delivery bus or a copied message body.

The constructor-owned worker starts after the actual MCP listener. The existing
trusted startup connection calls the fixed private `accepted_work(text,jsonb)`
helper. No runtime role receives EXEC or table access. Control locks precede
work-row locks; claims have a 30-second lease and preserve the original accepted
five-minute expiry. Resolver unavailability leaves the finite original claim
recoverable. Expired/retired birth and definitive refusal cannot extend expiry
or acquire a fresh source. Prepared restart carries the SAME source and command
ID, and refuses changed source/digest/generation capture.

All canonical reads/source registration commit before resolver or registered
MCP I/O. The work attempt marker commits and is acknowledged before dispatch;
unknown attempt ACK causes no dispatch. This marker is dispatch-attempt evidence
and supplies no provider-start grant. A completed owning command receipt decides
`committed`; a lost ACK can still recover that actual receipt. Once an attempted
claim expires or its process dies, absence of that receipt is `unknown`, never
a resend permit. The scanner excludes terminal attempts rather than reminting
a report or command. Unexpected worker failure refuses local admission and
leaves protected work unresolved; shutdown cancels the worker and still revokes
and closes its anchors even if worker cleanup fails.

Exact-ID LOCK currently uses the real owning resolver and registered control
transport. Bare/natural-language LOCK is stored as `awaiting_selection`; that
state is explicitly not a delivered selection question, mandatory case/evidence
or containment ACK. The original selection/security-question/provider producer,
full native mutation universe, native provider-start wiring and independent
supported run/up/API/connector/Compose/Kubernetes proof remain mandatory. This
exact-ID worker does not fulfill those outcomes.

The generated manifest now has **55 total SQL definitions**: 37 in private
`custody_admission`, 15 in `public`, and 3 in `dashboard_auth`. Those counts are
distinct from the reviewed **25 governing interfaces**, whose names, signatures
and source/wire versions remain unchanged. The new work helper uses the exact
RFC0006 definer search path and fully qualified bodies. No new role, LOGIN,
grant, credential, public trust selector or host privilege is introduced.

Software transport controls reach dispatch and go red when the durable attempt
ACK is bypassed, then pass after exact restoration. They cover prepared recovery,
transient resolver failure, changed recovered source, lost prepared/attempt ACK,
remote ACK loss and actual receipt-verdict branching. Their source/host doubles
are not SQL authority proof. The one migrated owning test is extended with real
birth/work atomic rollback, role refusals, stale-claim refusal, SAME-source
prepared recovery, separate committed attempt readback before registered MCP,
committed receipt readback and no-receipt UNKNOWN recovery. These SQL controls
remain **UNRUN** locally; collection is topology evidence only. All five original
criteria and fifteen slices remain UNMET until their actual complete evidence.


## Shared companion lifecycle and remaining delivery boundary

Fixed API startup enrolls its actual credential shared pool in addition to the
Relationship canonical pool, using the same configured database identity and
existing `butler_relationship_rw` role with zero operations/audiences. Database
mismatch fails closed. Native hard deletion of Google/Steam companion entities
enters control before its current account/entity lookup and domain transaction,
retains the owning all-version cascade and publishes actual old/new channel
selectors before SAME COMMIT. The existing Google creation UPSERT can replace
an existing companion's roles; its native outer boundary now snapshots that
actual active unique entity before mutation and publishes afterward. These
are fixed constructor hooks, not caller callbacks, public trust selectors or
requests to a peer after COMMIT. No new role/grant/login/credential is installed.
Ordinary credential reads and soft disconnect remain unchanged, and Google
provider revocation remains after acknowledged local COMMIT.

Positioned software controls reach the actual registry DELETE and fail when
the native shared-pool callback is bypassed, then pass after exact restoration.
Migrated controls use the actual API shared pool/role, all-version canonical
facts, publication failure after real cascade, separate rollback readback,
restored deletion and fresh-source positive versus unrelinked old report.
Those controls are authored and **UNRUN**; software mocks supply no SQL proof.

Generic Memory metadata mutations on non-Relationship pools and the complete
serialized canonical writer universe remain uncovered. Native B routing/egress
and provider-start enforcement is not yet installed: internal held metadata
and command receipts must not be credited as completed containment. Remaining
B/C/D, selection/security questions, real Finance/device/evidence/foreign auth
producer, independent deployments and whole criteria remain required. This
SOURCE-A candidate is for exact-head review/CI and carries no delivery approval.

## Request resource policy and legacy-role compatibility

The constructor-fixed CustodyJsonRpcGuard HTTP boundary uses a16MiB generic
MCP request cap and a10-second total body-read deadline. This server resource
policy is distinct from the8192-byte inner custody-wire.v1 limit. An already
recognized custody envelope is refused before retaining overflow beyond51200
bytes (sixfold JSON escaping plus2048 framing bytes); incomplete/late-named
requests retain the larger finite server cap until strict classification.
The final duplicate-preserving custody check still precedes SDK instrumentation.
Generic document arguments larger than the custody bound remain supported;
no8KiB generic limit or admission verdict is introduced. Disconnect/cancellation
never forwards a partial request. The server's delivered ASGI chunk is outside
this wrapper's allocation; the wrapper checks length before retaining/copying
its overflow and does not accumulate empty-message objects.

Existing bootstrap-managed runtime roles intentionally retain LOGIN/INHERIT.
LOGIN is not a custody principal: trusted host enrollment, anchor/writer binding,
current source and online challenge/COMMIT checks remain mandatory. The catalog
prover checks ancestry/private schema/table/column/function privileges and RLS,
without inventing a NOLOGIN prerequisite. The restricted dashboard channel
retains its separately adopted role contract. No role, LOGIN, credential or
privilege is newly provisioned by this compatibility correction.

The audited standalone PEP723 merge command uses the dependency-light native
mutation seam. It sees the same actual-pool publisher registry installed only
by constructor-validated enrollment. It neither imports daemon infrastructure
merely for --help nor supplies a new source/caller trust selector. Legacy
unallocated writer behavior remains explicit without custody-currentness credit;
complete native writer coverage and genuine migrated controls remain required.

### Empty migration replay and ordinary backup compatibility

The fixed installer may reopen only the recorded empty `unavailable` rollback
state. Repeated empty shared-schema rollback is idempotent. Enrolled, populated
or revoked boundaries refuse rollback/re-enablement. A legitimate deep
core_217 rollback drops `public.fleet_cases` and its incoming fixed
`case_scopes_case_id_fkey`; only that missing constraint, in the empty recorded
rollback state after the actual fleet table returns, may be restored. The full
stored schema identity stays unchanged and must match again. Other column,
constraint, policy or owner drift refuses; no general schema refresh is allowed.

The ordinary backup excludes the exact fenced `custody_admission` schema and
`public.custody_holds`, preserving its existing role/ACL boundary. Its custom
archive TOC selection also excludes exactly the fifteen public custody wrappers
and their associated function metadata, with a same-snapshot source catalog and
bidirectional exact-signature checks. Unknown/missing/changed/duplicate entries
refuse publication. Ordinary functions and the universal restored-definer owner
assertion are retained. This excludes unrecovered authority interfaces rather
than recreating them under the restoring login. It does not
recover custody command/source/hold history. Restored-history admission and
reconciliation remain undelivered. Ordinary backup availability is not custody
recovery proof; all original recovery/containment acceptance remains mandatory
and unmet. The same canonical writer, source, owner-auth, acquired-connection
and final COMMIT controls remain required.

The protected core downgrade preflight selects the unique known core head from
all current Alembic heads. Known Memory, Relationship and Switchboard version
rows remain intact; unknown/duplicate/ambiguous current heads and invalid targets
refuse. Crossing core_198 still invokes its exact installed rollback proof before
any newer migration work. Software graph/TOC controls are bounded evidence; the
real multichain lifecycle and actual backup/restore species require hosted SQL.
