# OwnTracks Location Retention

## ADDED Requirements

### Requirement: Declared Raw Policy and Protected Owner Control

The system SHALL implement the policy, clock and protected owner boundary in P1. Its typed canonical policy SHALL default to30days, accept owner-shorter1..30-day horizons and version changes, and SHALL not infer authorization from generic state, actor fields or the legacy audit-row environment setting.

ID: REQ-location-retention-001
Source: bu-s11n0s.7 original Outcome, Behavior matrix and S3; about/heart-and-soul/security.md Sensitive Data Categories; proposed source-protocol P1
Scope: v1-mandatory


#### Scenario: Default and actual owner setter

- **WHEN** actual fresh startup and authenticated owner PUT use no raw-policy override
- **THEN** the canonical policy is30days by default, a strict shorter integer is committed under server-derived principal and CAS version, and separate readback returns that real policy


#### Scenario: Untrusted and malformed policy refused

- **WHEN** header/cookie/control is missing, caller actor is asserted, value is bool/null/string/fraction/zero/>30 or stale expected_version
- **THEN** mutation is refused at the genuine boundary, no purge grant is created, and a genuine current-owner strict integer positive remains available


#### Scenario: Original clock preserved on replay

- **WHEN** the same logical raw source is retried or device ts is implausible
- **THEN** its original effective retention_at uses the captured existing skew rule without a fresh birth or expiry, equality with the cutoff survives, and a genuinely new accepted point retains its independent birth


#### Scenario: Shorter and wider windows

- **WHEN** the owner changes the window while a retention plan is in flight
- **THEN** unprepared work uses the committed new version, exact already-prepared decisions remain irreversibly frozen and visibly pending, and widening restores neither purged points nor committed precision reduction

### Requirement: Genuine Per-Row Projection Coverage

The system SHALL earn each applicable adapter coverage only through native committed output/lineage/carryover/checkpoint work under P2. An episode, timestamp cursor or client-supplied covered bit SHALL NOT certify a raw fix.

ID: REQ-location-retention-002
Source: bu-s11n0s.7 original Outcome and S1; RFC 0014 D2/D3; openspec/specs/chronicler-source-compatibility/spec.md Privacy and Retention Declaration; proposed source-protocol P2
Scope: v1-mandatory


#### Scenario: Required outputs are causal

- **WHEN** a genuine point contributes to both a movement and a place output
- **THEN** the exact raw revision records both actual committed contributions plus applicable SSID disposition; an unrelated leg/place UUID never authorizes deletion


#### Scenario: Terminal no-output differs from pending

- **WHEN** minimum-dwell or missing/unlabelled SSID evaluation produces no episode
- **THEN** only an actual closed terminal evaluation is coverage; an open carryover, invalid row or absent adapter remains blocked and preserves raw evidence


#### Scenario: Ties and late commits are found

- **WHEN** more rows share a timestamp than one batch, or a raw transaction/arrival lands behind a prior tuple/frontier
- **THEN** the arrival tuple plus exact pending anti-join reaches every row; the UUID cursor never uses BIGINT watermark_id and no late row is lost permanently


#### Scenario: Partial projection rolls back

- **WHEN** a native output/link/carryover/coverage/checkpoint write is poisoned
- **THEN** the same transaction rolls back every corresponding success claim; a separately recorded failure does not imply a new successful watermark

### Requirement: Owning Ready Grant and Raw Commit

The system SHALL implement P3 with separate truthful owning commits. Chronicler SHALL never gain raw DELETE or foreign write authority. The connector SHALL delete only an immutable ready frozen covered set under its actual existing role and SHALL commit tombstones and exact source receipts together.

ID: REQ-location-retention-003
Source: bu-s11n0s.7 original Outcome and S1/S2; RFC 0006 Staffer Schema Permissions and Database Connection Scoping; RFC 0014 D2; proposed source-protocol P3
Scope: v1-mandatory


#### Scenario: Native producer to owning deletion positive

- **WHEN** actual accepted OwnTracks ingestion, native projection and all required holder transitions have committed
- **THEN** the configured connector obtains the real registered ready grant, rechecks locked exact raw revisions and commits only eligible DELETE/tombstone/receipt, while newer and uncovered sentinels remain


#### Scenario: Wrong rows roles or ready state refused

- **WHEN** the caller supplies row arrays/cutoffs, an unrelated role invokes the writer, a revision changes or holder coverage is incomplete
- **THEN** no ready deletion is acknowledged, no peer SQL shortcut is used, and the current genuine owning ready batch remains a positive


#### Scenario: No lock skips or network transaction

- **WHEN** a candidate is locked or source MCP becomes unavailable
- **THEN** work waits within its bounded budget or records busy/unknown rather than using SKIP LOCKED/partial success; MCP I/O stays outside business transactions and lock order remains stable


#### Scenario: Lost commit acknowledgement

- **WHEN** raw COMMIT or final Chronicler ACK is lost
- **THEN** recovery reads the same source batch from a separate acquisition and finalizes that immutable decision only when its genuine committed receipt exists; absence is UNKNOWN and does not remint or double-count

### Requirement: Preserved Summaries and Reduced Spatial Precision

Before raw forgetting the system SHALL apply P4. It SHALL preserve legitimate legs/visits and their metrics/privacy while reducing source-derived geometry and maintaining temporal precision separately.

ID: REQ-location-retention-004
Source: bu-s11n0s.7 original AC path_m/duration and S2; RFC 0014 D1/D4; openspec/specs/butler-chronicler/spec.md Storage Shape; proposed source-protocol P4
Scope: v1-mandatory


#### Scenario: Real cumulative path and duration preserved

- **WHEN** a multi-batch genuine movement with a bridge edge is projected then forgotten
- **THEN** path_m is computed once from its actual ordered fixes/cumulative state, duration/episode identity/meaningful counts survive, and no missing legacy metric is replaced by invented0 or endpoint straight-line distance


#### Scenario: Approximately150m geometry without raw leakage

- **WHEN** unbound centroids/endpoints and their location titles/carryover/cache copies are eligible
- **THEN** meter-based spatial quantization and stripped raw fields remove planted exact values from actual stored and returned paths, including polar/antimeridian controls; source timestamps/privacy stay honest


#### Scenario: Bound reference is independent owner input

- **WHEN** an owner-labelled reference matches a visit
- **THEN** only that explicit independent static reference may remain exact; the observed centroid does not become an exact reference merely because a row or label exists


#### Scenario: Evidence tombstones preserve interfaces

- **WHEN** expired location point events and their links are removed
- **THEN** minimal permanent tombstones and typed evidence-chain expired descriptors remain without raw titles/coordinates, evidence_refs remains list[str], and retained summaries no longer claim fresh raw corroboration

### Requirement: Raw Copy Cohort and No Resurrection

The system SHALL enforce P5 on every actual system-owned source-derived copy and monotone floor. It SHALL preserve unrelated audit/provider/user-authored scope and SHALL not claim completeness from ambiguous lineage.

The cohort SHALL include source-owned server exports and stored/processing API, session, input-bundle, cache and prose copies. Native ASGI final-body send followed by delegate completion may settle only the actual source-owned response lifetime under its fixed producer and policy-first writer fences. A client ACK, principal string, remote browser claim or arbitrary user download SHALL NOT be terminal authority. Interrupted responses, missing source binding, unknown committed receipts and active source-owned holders SHALL remain held; source response completion SHALL NOT certify remote-recipient erasure.


Configured Memory producers SHALL capture the actual accepted native episode and exact consolidation input bundle under the owning writer, preserve immutable parent generations, and bind derived facts/rules and shared-catalog generations to their actual persisted bodies. Existing catalog sensitivity and relevance selection SHALL remain; registry addresses, citations, nullable source_episode_id, row existence or caller headers SHALL NOT establish lineage or closure. Historic catalog generations and receiver loans SHALL remain in the native holder census even after a current head changes.

Catalog body delivery and owning prepare/status SHALL use the actual registered Switchboard inter-butler route. The constructor-owned outer admission SHALL bind the exact target/tool/loan before instrumentation, verify the actual fixed source and online committed receiver generation/body/incarnation, strip the private ephemeral capability, and clear the private context in finally. Public loan/decision/receipt UUIDs SHALL be locators only, and generic requests SHALL preserve existing streaming behavior. Bounded duplicate-key, oversized, excessive-frame and interrupted controls SHALL refuse native admission. No new credential, principal, signer, grant, peer-private SQL or caller-asserted receiving authority SHALL be introduced.

Native artifact/catalog reduction and disposal SHALL require complete exclusive captured input, exact current body digests, every historic receiver/source response disposition and separate owning committed readbacks. Unknown legacy catalog bodies, mixed bundles, active native session/context descendants and missing producer reservations SHALL remain visibly incomplete and preserve raw source and point evidence. A source server response disposition SHALL settle only that source-owned transient copy. Episode reference/lease/consolidation counters may advance without changing the frozen content digest only through the explicit lifecycle profile; content, metadata, vectors and authority fields SHALL remain bound, while active leases and every actual derived bundle/link endpoint SHALL independently block until their native committed disposition is proven.

Native MCP processing SHALL reserve the constructor-owned receiving session and full tool-input fingerprint before invoking the handler, commit and independently read back that reservation, bind each actual borrowed loan to that exact processing generation, and freeze the actual result before emission. The owning complete session disposition SHALL require one-to-one immutable executed input/result witnesses, every selected parent, ended runtime and unchanged whole session body; a handler return or server send SHALL NOT close a runtime/model copy. Interrupted/error/unknown mutation, mixed selected results, unselected late inputs and changed recorded bodies SHALL preserve holders. New derived artifacts SHALL freeze a separate complete input snapshot including actual later tool loans and full native parent generations, leaving earlier bundles immutable. Existing owner roles and configured Memory schema boundaries SHALL remain unchanged.

Native owning Memory episode reads SHALL compare the actual selected canonical content with the producer's immutable body digest under the policy-first writer, capture every applicable source generation and the privately admitted receiving session, commit and independently read back births before returning bytes. Operational reference counters SHALL NOT erase content lineage. Nonempty independent, changed, mixed, unknown fact/rule or uncaptured selections SHALL remain nonexclusive. Every applicable same-name read witness SHALL independently be successful and exclusive; one good witness SHALL NOT authorize another mixed call, and full one-to-one input/result matching SHALL retain multiplicity. A failed handler's original error SHALL be captured before failure-receipt cleanup; ordinary secondary cleanup failures SHALL preserve that error and unresolved holders, while a newly requested cancellation during cleanup SHALL still cancel. A successful handler SHALL NOT return its result when its committed result witness is unknown.

The actual owning fact/rule artifact writer SHALL retain its original full-body digest and MAY additionally freeze the nullable `native_memory_artifact_content.v1` witness in that SAME canonical writer transaction. This profile SHALL exclude only `reference_count` and `last_referenced_at`; content, embeddings, authority, metadata, links and every other field SHALL remain bound. Existing NULL witnesses SHALL NOT be backfilled or reinterpreted from a current row. Actual local reads SHALL lock and reread the canonical body, require the returned fields to match that body with only native numeric search rank/similarity added, and obtain their complete parent generations from the actual immutable exclusive source bundle. Changed, missing, mixed or unknown source bodies SHALL remain nonexclusive. Generations already selected by a prepared retention plan SHALL be fenced before new sensitive local Memory emission. Original full-body history and receipts SHALL remain immutable; content witnesses SHALL NOT grant a principal, caller, source or disposal authority.

ID: REQ-location-retention-005
Source: bu-s11n0s.7 original nonresurrection non-goal and S2; about/heart-and-soul/security.md Sensitive Data Categories; openspec/specs/connector-filtered-events/spec.md Full Payload Shape and Replay lineage and event payload age independently; proposed source-protocol P5
Scope: v1-mandatory


Native consolidation SHALL acquire the qualified owning policy and adapter locks before its actual episode-claim lease locks. It SHALL capture the full selected episode, deduplication-fact and rule bundle and every producer-recorded parent generation in the SAME writer transaction, commit, and independently read back the immutable claim before rendering or dispatching copied prompts. Native processing completion SHALL close only that Python processing lifetime; its persisted, catalog and runtime descendants SHALL retain separate required dispositions. A crashed, interrupted or unknown committed processing claim SHALL remain in the holder census.

The actual constructor SHALL capture both configured domain and Memory schema/role and their same-database witness. Only Chronicler's OWN configured Memory writer may read its qualified Chronicler policy; other receivers SHALL use their own registered routed admission. The actual Spawner SHALL reserve its receiving session before Memory context reads, bind its full composed input and stored session before runtime processing, and capture native stored episodes on their actual writer. The native runtime finalizer and native server-response finalizer SHALL attest only their own ended lifetimes. Runtime-context disposal SHALL require the unchanged full bundle, all selected source parents, every actual loan and ended lifetime, and separate committed owning readback. Independent instructions/provenance SHALL survive exact source-derived context reduction. Mixed, changed, active, unknown or unbound contexts and tool/derived descendants without completed native dispositions SHALL survive and block erasure. Late native session, process-log and Memory writes SHALL remain fenced.

Native consolidation SHALL reread every full canonical episode/fact/rule on the actual policy-first owning writer before prompt admission and capture all producer-owned immutable body/parent witnesses. A complete exclusively native fact/rule input SHALL NOT be classified as independent merely by table type. Every independent, changed, absent or legacy-unknown selected input SHALL keep the entire composed bundle mixed. Every actual native parent generation SHALL remain captured, and already prepared or disposed parent generations SHALL refuse new sensitive prompt admission. Model citations, returned IDs and context labels SHALL NOT install that lineage.

Actual configured owning Memory mutation transactions SHALL take the policy-first lock before canonical target locks and refuse current prepared source generations through their real immutable parent relation. Schema selection SHALL match constructor-owned configuration; a caller identifier or a generation from another namespace SHALL NOT substitute for the parent binding. Unconfigured ordinary behavior SHALL remain within its existing contract. These fences SHALL NOT attest exclusive mutation lineage or terminal erasure, and changed, mixed, routed or unknown descendants SHALL retain their required source/copy witnesses.

Native fact/rule INSERTs SHALL capture their complete final canonical body on the SAME configured Memory writer before COMMIT from the private registered runtime invocation or constructor-owned context. Caller provenance, actor, source strings or returned UUIDs SHALL NOT mint that ancestry. An exclusive Chronicler context SHALL reread its complete immutable composed bundle and every native parent before publishing a catalog source generation through its existing registered owning-MCP plane. Live catalog writes and backfill SHALL bind every actually emitted ID on that same writer, preserving the ordinary unconfigured maintenance contract. Mixed, borrowed, unknown or other-source context outputs SHALL NOT borrow Chronicle's source authority. Own canonical body, other-context, graph, catalog and loan descendants SHALL be checked before removal; exact owning disposals SHALL be independently read back. Child disposal SHALL precede parent terminal disposal after all exact parents are selected and fenced; it SHALL require actual ended-runtime and finished-processing witnesses rather than a cyclic requirement that the still-held parent be terminal first. Unknown operations or unbound/routed descendants SHALL remain held and SHALL NOT imply complete erasure.


Artifact, catalog and receiver census SHALL resolve dispatch input generations through the immutable dispatch-parent map, never equate an input-generation UUID with a copy-generation UUID. ALL captured parents must match their exact digests and committed selected dispositions; one disposed parent SHALL NOT close a mixed bundle. Ordinary unconfigured readers SHALL preserve only persisted ordinary rows that have no canonical projection/native-copy ancestry and no OwnTracks source label. They SHALL supply no native lineage or authority and SHALL refuse stored native ancestry even when the display label changes. Closed failure diagnostics SHALL expose only fixed stage/category/class and validated SQLSTATE, preserving the original failure and excluding exception text, arguments and source values.

#### Scenario: Actual accepting and receiving holders

- **WHEN** a full or metadata OwnTracks envelope is genuinely accepted and routed
- **THEN** server-owned request_id/raw revision linkage discovers the actual stored copy holders; each own-role transition commits and is independently read back before the ready grant is returned


#### Scenario: Missing lineage or active holder blocks

- **WHEN** a legacy copy is ambiguous or its processing lease still needs input
- **THEN** raw source forgetting is incomplete/blocked with a visible count-only reason; no substring/episode existence manufactures coverage or silently drops active input


#### Scenario: Permanent replay audit and errored copies

- **WHEN** an exact accepted OwnTracks source is forgotten while replay/audit rows remain
- **THEN** its raw-copy replay is unavailable with a source-purge tombstone, public.audit_log entries persist, other connector contracts remain unchanged, and an unaccepted unprojected raw error stays visibly blocked rather than projected for deletion


#### Scenario: Replay correction reset and new source

- **WHEN** a tombstoned source is replayed, an upsert/override/mapping changes or retention widens
- **THEN** the native source floor prevents raw point/exact geometry/carryover/cache refill; a genuinely new unrelated admitted fix and valid independent correction remain positive

### Requirement: Durable Receipts and Honest Health

The system SHALL implement P6, committing attempt and actual outcome evidence, preserving incomplete/unknown distinctions and deriving source/owner health from real current receipts.

ID: REQ-location-retention-006
Source: bu-s11n0s.7 original Behavior matrix and S2; openspec/specs/connector-owntracks/spec.md Retention Purge Degradation Visibility; proposed source-protocol P6
Scope: v1-mandatory


#### Scenario: Committed counts dedupe

- **WHEN** an eligible source batch commits then the same run repeats
- **THEN** separate committed readback supplies per-table/holder deleted/prepared/blocked counts and the same receipt; the forgotten total is not incremented twice


#### Scenario: Projection lag condition preserves raw

- **WHEN** an overdue raw fix lacks genuine required coverage
- **THEN** it survives, the actual deterministic job opens chronicler:location-retention-projection-lag using genuine counts, and an unrelated active owner condition is not resolved


#### Scenario: Failure cancellation and stale success

- **WHEN** a run fails after old success, is cancelled or loses its completion receipt
- **THEN** latest attempt/failure streak and expiry show degraded/unknown; prior-success timestamps cannot hide it, no raw exception/coordinates reach diagnostics and missing failure recording remains unknown


#### Scenario: Complete empty versus unavailable

- **WHEN** all relevant sources/holders are actually examined and empty, or an optional source/holder is missing
- **THEN** only the first is no_work with complete observation; the latter is unavailable/incomplete and never resolves prior lag by omission; existing error has precedence

### Requirement: Authoritative API Map and Owner Controls

The system SHALL implement P7 using true server policy and source retention state. It SHALL not use a frontend TTL filter as evidence that upstream raw deletion occurred.

Managed dashboard query/cache and MapLibre geometry SHALL independently fence stale or late responses and invalidate current and archived location views after genuine committed retention changes. Native server-response completion SHALL NOT substitute for these managed UI controls. Planted expired geometry and a retained fresh-point positive SHALL be exercised through the real browser/runtime path; a cosmetic plaque, client purge field, empty fixture or policy clock alone SHALL NOT prove this requirement.

ID: REQ-location-retention-007
Source: bu-s11n0s.7 original map outcome and S3; openspec/specs/dashboard-chronicles/spec.md Map Render Privacy Contract and Where-You-Went Map Trail; proposed source-protocol P7
Scope: v1-mandatory


#### Scenario: Expired raw trail gone with legs remaining

- **WHEN** a genuine complete retention run forgets an expired range
- **THEN** actual event/episode/evidence API responses reach the map; trail/heatmap/playhead contain no expired raw points, legitimate legs/path/duration remain and new raw points still plot


#### Scenario: Plaque and versioned setter

- **WHEN** the authenticated owner reads and shortens or widens retention
- **THEN** the query-backed plaque shows the actualN, conditional projection rule and nonresurrection/prepared-batch warning, the setter uses current server version and racing saves cannot revert newer data


#### Scenario: Lag failed missing and archive status

- **WHEN** retention is pending, failed, stale or unavailable on a current or archived day
- **THEN** source strip/map/EpisodeDrawer expose the real state without fabricated30-day success, raw privacy defaults persist and no cosmetic filter conceals API leakage


#### Scenario: Postcommit invalidation removes stale geometry

- **WHEN** actual committed precision/deletion emits count-only freshness
- **THEN** real query caches and MapLibre markers/trail/heatmap/playhead update from the reduced upstream view; late old responses cannot restore deleted geometry

### Requirement: Native Compatibility and Documentation

The system SHALL carry P8 into its own active native change and complete source/docs. Existing scenarios and foreign active scope SHALL be preserved, and migration numbers SHALL remain unallocated until actual source allocation.

ID: REQ-location-retention-008
Source: bu-s11n0s.7 original Documentation impact, Completeness gate and S1; RFC 0014 D2; proposed source-protocol P8
Scope: v1-mandatory


#### Scenario: Full governing before after parity

- **WHEN** the source change modifies retention/provenance/map contracts
- **THEN** all whole old requirement/scenario bodies are retained alongside narrowly scoped OwnTracks amendments; unrelated Health/calendar/other-source/foreign tests and native tasks are unchanged


#### Scenario: Truthful source and security declaration

- **WHEN** privacy/retention docs and source registry are reviewed
- **THEN** OwnTracks declares raw/projected retention, postpurge precision/tombstones and metadata normalized-text copies honestly, and neither raw archival comments nor a source presence claim implies deployed enforcement


#### Scenario: Real migration role installation

- **WHEN** the future allocated core/Chronicler migrations and fresh bootstrap run
- **THEN** core-only/optional schemas remain safe, actual connector and Chronicler roles retain proper DML/SELECT boundaries including only the declared receipt read grant, and downgrading cannot promise restoration of forgotten data


#### Scenario: No unrelated adoption

- **WHEN** source implementation is integrated against actual protected main
- **THEN** no held h3/capture/custody/relationship migration or unpublished helper is imported, revisions are allocated from the actual chain and all source-holder/owner-condition hunks are serialized

### Requirement: Whole Original Scope and Meaningful Proof

The system SHALL fulfill all original S1/S2/S3 and original acceptance outcomes, using the V1–V6 actual controls in P9. Planning, source parsing and synthetic context SHALL NOT be runtime proof.

ID: REQ-location-retention-009
Source: bu-s11n0s.7 complete original S1/S2/S3 and acceptance criteria; about/craft-and-care/testing-and-verification.md; proposed source-protocol P9
Scope: v1-mandatory


#### Scenario: Original gate remains mandatory

- **WHEN** PRIMARY or partial source is handed off
- **THEN** every original outcome remains UNMET until its real proof, no slice is silently deferred and no source/SQL/live purge/native adoption is implied


#### Scenario: Positioned coverage red and positive

- **WHEN** the actual eligibility predicate is neutralized with uncovered and unrelated-episode sentinels planted
- **THEN** the native DELETE is reached and the old guard failure is causally observed, then restored coverage protects them and genuinely covered rows are actually deleted


#### Scenario: Positioned privacy replay and ACK reds

- **WHEN** precision/replay floor or durable receipt validation is neutralized
- **THEN** actual stored/read source leakage, replay refill or false completed receipt is reached; missing helper/error-before-boundary is rejected as proof and restored genuine positives pass


#### Scenario: Exact supported deployment and budget

- **WHEN** the implementation claims completion
- **THEN** actual owning roles/registered same-process and independent supported processes, API/UI/readers, separate COMMIT readbacks and one exact hosted broad gate prove the claim; actual source planner/collection/net delta and justified budget preserve all invariants
