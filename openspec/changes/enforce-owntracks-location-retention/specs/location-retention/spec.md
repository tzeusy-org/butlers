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

The owning raw writer SHALL freeze its private processing input generation at the original point INSERT. Preparation and raw deletion SHALL lock the same source boundary and require every original same-source webhook/replay input bundle, complete declared kind/count/digest/incarnation set and committed end, plus every applicable original server header end. Legacy NULL, missing/partial/mismatched history, an unclassified live server or unknown readback SHALL preserve the raw point. Completed matching native input is a necessary own-source disposition, not proof of projection, accepted ingestion, foreign receiving/runtime closure or an all-holder grant. No immutable point SHALL be backfilled with a replacement input generation.

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

Native semantic generation hashing SHALL retain typed BYTEA logical-source bindings when an actual point body is replaced by its minimal tombstone, preserve the original revision, and record the diminished disposal revision without arbitrary string fallback or dropped fields. Stable exact tombstone replay SHALL retain its digest; changed bytes or semantic fields SHALL change it.

The native connector's separate committed observation SHALL bind the complete
immutable header and every selected raw ID/revision/logical digest/disposition,
including exact multiplicity and deleted/already-forgotten counts. A matching
header with missing, duplicated, foreign or unclassified members SHALL remain
unresolved. Replaying an existing complete receipt SHALL select the same frozen
batch; an expired old lease may read that already committed result but SHALL
not authorize a fresh DELETE. Chronicler reconciliation independently observes
the same exact ledger under its existing owning role. Grouped real-role SQL
engine controls with a planted remote frontier SHALL be labelled as engine
evidence and SHALL NOT substitute for actual registered producer/holder
admission or full all-holder closure.

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

Catalog body delivery and owning prepare/status SHALL use the actual registered Switchboard inter-butler route. The constructor-owned outer admission SHALL bind the exact target/tool/loan before instrumentation, verify the actual fixed source and online committed receiver generation/body/incarnation, strip the private ephemeral capability, and clear the private context in finally. Public loan/decision/receipt UUIDs SHALL be locators only, and generic requests SHALL preserve existing streaming behavior. Bounded duplicate-key, oversized, excessive-frame and interrupted controls SHALL refuse native admission. The private admission buffer SHALL check each received chunk against remaining byte capacity before copying it; a 503 after allocating the entire oversized body SHALL NOT constitute resource-bound proof. No new credential, principal, signer, grant, peer-private SQL or caller-asserted receiving authority SHALL be introduced.

Native artifact/catalog reduction and disposal SHALL require complete exclusive captured input, exact current body digests, every historic receiver/source response disposition and separate owning committed readbacks. Unknown legacy catalog bodies, mixed bundles, active native session/context descendants and missing producer reservations SHALL remain visibly incomplete and preserve raw source and point evidence. A source server response disposition SHALL settle only that source-owned transient copy. Episode reference/lease/consolidation counters may advance without changing the frozen content digest only through the explicit lifecycle profile; content, metadata, vectors and authority fields SHALL remain bound, while active leases and every actual derived bundle/link endpoint SHALL independently block until their native committed disposition is proven.

Native MCP processing SHALL reserve the constructor-owned receiving session and full tool-input fingerprint before invoking the handler, commit and independently read back that reservation, bind each actual borrowed loan to that exact processing generation, and freeze the actual result before emission. The owning complete session disposition SHALL require one-to-one immutable executed input/result witnesses, every selected parent, ended runtime and unchanged whole session body; a handler return or server send SHALL NOT close a runtime/model copy. Interrupted/error/unknown mutation, mixed selected results, unselected late inputs and changed recorded bodies SHALL preserve holders. New derived artifacts SHALL freeze a separate complete input snapshot including actual later tool loans and full native parent generations, leaving earlier bundles immutable. Existing owner roles and configured Memory schema boundaries SHALL remain unchanged.

Native owning Memory episode reads SHALL compare the actual selected canonical content with the producer's immutable body digest under the policy-first writer, capture every applicable source generation and the privately admitted receiving session, commit and independently read back births before returning bytes. Operational reference counters SHALL NOT erase content lineage. Nonempty independent, changed, mixed, unknown fact/rule or uncaptured selections SHALL remain nonexclusive. Every applicable same-name read witness SHALL independently be successful and exclusive; one good witness SHALL NOT authorize another mixed call, and full one-to-one input/result matching SHALL retain multiplicity. A failed handler's original error SHALL be captured before failure-receipt cleanup; ordinary secondary cleanup failures SHALL preserve that error and unresolved holders, while a newly requested cancellation during cleanup SHALL still cancel. A successful handler SHALL NOT return its result when its committed result witness is unknown.

The actual owning fact/rule artifact writer SHALL retain its original full-body digest and MAY additionally freeze the nullable `native_memory_artifact_content.v1` witness in that SAME canonical writer transaction. This profile SHALL exclude only `reference_count` and `last_referenced_at`; content, embeddings, authority, metadata, links and every other field SHALL remain bound. Existing NULL witnesses SHALL NOT be backfilled or reinterpreted from a current row. Actual local reads SHALL lock and reread the canonical body, require the returned fields to match that body with only native numeric search rank/similarity added, and obtain their complete parent generations from the actual immutable exclusive source bundle. Changed, missing, mixed or unknown source bodies SHALL remain nonexclusive. Generations already selected by a prepared retention plan SHALL be fenced before new sensitive local Memory emission. Original full-body history and receipts SHALL remain immutable; content witnesses SHALL NOT grant a principal, caller, source or disposal authority.

Native delegated-question producers SHALL freeze their exact question frame and every actual owning native/catalog parent under the policy-first ledger writer, commit that full birth before routing and separately read back the canonical ledger plus complete immutable parent set. Caller ledger/session/actor strings SHALL NOT enroll a producer or authorize a receiver. Question/asking substitutions SHALL refuse before scheduling. Ordinary unconfigured writers SHALL retain their explicit existing Pool/Connection contract. Disposed, prepared, mismatched, mixed and unknown inputs SHALL NOT acquire exclusive lineage or terminal authority. Question descendants SHALL remain in the all-holder census after source-session completion, and neither public terminal status nor emptied body SHALL prove receiving-schedule/runtime disposal. Borrowed loan closure SHALL require those exact question descendants' own committed dispositions; actual registered receiver and pre-prompt schedule admission and reconciliation SHALL remain separate mandatory controls.

Native routed question input SHALL be admitted by the actual configured handler,
current canonical source and fixed registered source/receiver challenge. Genuine
infrastructure traffic SHALL bind the live constructor-owned server request,
without inventing a local CLI session; actual CLI traffic SHALL retain its exact
registered tool/session binding. Locators and caller actor/ACK fields SHALL NOT
create that admission. Native ASGI completion SHALL attest only its own server
copy. Normal and manual scheduled prompt dispatch SHALL commit and read back its
complete current question-derived processing input before model processing, then
bind its immutable pre-context claim and full composed input on the actual session
writer. Every original dispatch and received-question dependency SHALL remain in
forwarded ancestry. Missing, changed or partial dependency rows SHALL refuse;
independent additions SHALL remain nonexclusive. Unknown successful result/lifetime
witnesses SHALL prevent completion; failed secondary witnesses SHALL preserve the
primary failure or cancellation and unresolved copy.

The native daemon MUST enroll an owning question/session writer from its actual domain pool independently of optional Memory configuration, recheck its captured namespace/role inside every owning transaction, and terminate its private pending and admitted lifetimes at shutdown. This core-only enrollment MUST NOT fabricate a configured Memory pool, borrow a peer namespace, infer receiving authority from a caller session or invent a CLI context for an infrastructure route. It MUST retain the same fixed Switchboard-discovered metadata challenge and routed question-body transport, and missing constructor/registry/readback proof MUST remain unavailable rather than minting ancestry or terminal completion.

Configured ordinary deterministic question ingress SHALL require the actual fixed birthday-job producer's private immutable source generation, server-rendered date/body and public canonical ledger to commit together under its owning policy-first/date-dedup transaction. The source SHALL separately read back that birth before routing through Switchboard, and the configured receiver SHALL accept ordinary classification only through its constructor-owned current pending challenge and exact canonical body/lifetime recheck. Absent native ancestry, caller metadata, unknown historical rows, mixed native contexts and arbitrary question strings SHALL NOT select this classification. A duplicate date run SHALL preserve its original ledger; interrupted birth and unknown readback SHALL NOT route a newly unproven body. Explicit ordinary unconfigured Pool/Connection compatibility SHALL remain separate from configured positive source classification.

The actual first-answer writer SHALL freeze its complete private answering-tool context and original native/catalog/received-question parents on the same connection as the existing guarded atomic answer/wake update. Duplicate and unaccepted answers SHALL NOT replace the immutable original answer generation or parent bundle. Interrupted business writes SHALL roll back the answer birth together; unknown separate committed readback SHALL refuse a successful capture. Each actual answer generation SHALL participate in owning raw-source and borrowed catalog-loan frontiers until its exact committed disposition; capturing an answer birth SHALL NOT itself settle routed, wake, processing or receiving descendants.

The actual receiving constructor SHALL freeze an immutable attempt generation, full canonical ledger/body binding and real Tool or server lifetime before its source challenge, commit and separately read it back. The native server finalizer SHALL bind completion to that attempt even when later admission fails. A source loan without the receiver's committed attempt and ended lifetime SHALL NOT certify a missing receiver. Permanent owning question floors SHALL bind the exact decision/manifest, ledger, source question, loan, body and current receiving incarnation under the same lock as input, task and processing admission. They SHALL refuse late admission and task creation before any copied input or business write. Old-incarnation, active, absent-attempt, changed, mixed or unknown copies SHALL remain held.

The delegation group SHALL additionally register location_retention_prepare_questions and location_retention_question_status as non-presentable infrastructure endpoints. Their arguments SHALL select only stored plans/receipts; the actual constructor SHALL obtain the source plan through the registered Switchboard owning-MCP route, never caller endpoint, private header, source verdict or peer SQL. Receiver preparation SHALL dispose only exact unchanged owning scheduled input after every applicable attempt/server/Tool/processing/context lifetime has its own required disposition. It SHALL preserve unrelated content and refuse a changed full task body. Its immutable receipt SHALL require separate committed readback and replay the same generation/receipt. Source reconciliation SHALL revalidate its own loan/question/body/manifest and separately read back the receiver observation; that observation SHALL NOT itself dispose the source ledger, answering context, return/wake or further descendants.

The owning core-only receiving constructor SHALL dispose a completed question-only processing context only after the exact permanent question floor, admitted source/loan/body generation, original pre-context intent, complete frozen composed bundle and every own claim/server lifetime are independently observed. Additional tool calls, catalog inputs, stored Memory descendants, changed or mixed body and missing committed lifetimes SHALL retain the copy. This profile SHALL NOT fabricate a Memory pool or attest other recipients. It SHALL reduce only the selected source-derived prompt, result and process-log copy while preserving the independent configured system and provenance. Its context receipt and reduction SHALL commit together under the actual owning policy lock and require separate full body, receipt and process-log readback, including replay; a corrupt retained log SHALL make that readback unavailable. Receiver preparation SHALL subsequently recheck its distinct full receiver disposition. Every actual private Tool witness SHALL require a matching executed call/result record; NULL or empty stored calls SHALL NOT erase a planted private witness or close its input.

Configured Memory receiver preparation SHALL select its actual constructor-enrolled Memory pool and own domain, recheck the exact committed receiving floor/source loan/body/incarnation, and reconstruct the original pre-context question claim and full frozen bundle. The captured claim SHALL equal the original immutable reservation; missing or mismatched reservations SHALL NOT qualify through a smaller surviving join. That own admitted generation MAY establish receiving prompt ancestry; the source owner's private native births SHALL NOT be queried through a receiver role. Every additional local/catalog input, tool and stored descendant SHALL retain its existing complete-body/parent/disposition requirements. Actual question-derived Memory mutations SHALL reserve their native context at the existing same-writer artifact boundary. The immutable terminal context receipt SHALL freeze the reduced system and preserved provenance digests when its selected body reduction commits. Separate owning-role body/process-log readback SHALL verify those original digests on first return and every replay; a legacy NULL witness SHALL remain unavailable and SHALL NOT be backfilled from current rows. These witnesses SHALL NOT mint source, receiving, role or remote-erasure authority.

A configured answer without an admitted private producer SHALL remain unavailable whenever its actual locked canonical row is eligible. Definitively rejected canonical status/assigned-target or the actual immutable owning source-question disposition MAY return the existing unaccepted result under the current owning policy lock, without reading copied question/answer text, invoking the business writer or creating a native answer birth. Caller actor/target strings, missing context or registry removal SHALL NOT select an eligible fallback. The permanent source floor and unchanged canonical sentinel SHALL survive late replay.

The first-answer birth SHALL additionally freeze a nullable complete canonical answer bundle covering the original ledger, question frame, asking/target/answering butlers, exact answer text/digest and immutable wake identity. Legacy NULL bundles SHALL remain unavailable and SHALL NOT be reconstructed from current rows. Before a configured wake handler processes that copied body, its actual Tool or constructor-owned server invocation SHALL commit and independently read back an owning attempt, obtain the original answer owner's current fixed metadata challenge, and commit/read back the exact receiving input and source loan. Callback locators and copied session strings SHALL NOT create receiving authority. Duplicate source preparation SHALL reuse the original immutable loan for the same receiving generation; a lost ACK SHALL NOT mint replacement lineage.

The actual return-task reconciler SHALL take policy-first locks and bind the full immutable canonical answer bundle to the exact complete task prompt on the SAME owning business transaction. Existing deterministic-name reconciliation and savepoint-protected unique collisions SHALL remain; footer-only equality SHALL NOT bless changed prose. Unknown post-COMMIT prompt or binding readback SHALL refuse successful completion while preserving committed history. Configured return dispatch SHALL challenge every original source loan, compare every declared receiving input using a complete left-joined cohort, freeze count, classification, source/incarnation and full body identities, and commit/read back its complete processing claim before copied prompt processing. Missing, extra, changed or duplicate inputs SHALL refuse; each mixed input SHALL keep the whole claim nonexclusive. The actual pre-context reservation and immutable composed runtime context SHALL bind that claim and every returned parent. Ended processing SHALL settle only its actual Python lifetime; runtime, session, further question/answer, catalog, Memory and stored descendants SHALL retain distinct dispositions. Ordinary secondary witness failure SHALL preserve the primary handler error, newly requested cancellation SHALL still cancel, and success SHALL NOT hide a missing completion witness.


The actual receiving answer constructor SHALL attach every committed attempt to the native server finalizer before separate admission readback. Final-body completion and delegate completion SHALL settle only that server generation, including a committed interrupted admission; interruption SHALL NOT create a receipt. Source loans SHALL freeze the source incarnation on their actual source writer. Legacy NULL incarnation SHALL remain unknown without backfill or re-enrollment. Fixed non-presentable location_retention_answer_plan, location_retention_prepare_answer and location_retention_answer_status tools SHALL use the actual constructor and Switchboard owning-MCP route. Loan and decision locators SHALL select only stored attempts and full source cohorts; caller endpoints, private headers, actor/session fields or source verdicts SHALL NOT mint a plan. Every original answer parent and digest SHALL participate, including missing births and zero-parent unknown ancestry; a smaller surviving JOIN SHALL NOT supply completeness.

Answer preparation SHALL commit a permanent full source/ledger/loan/bundle/decision/manifest and both-incarnation floor under the same owning lock as input, schedule and processing admission. It SHALL fence late sensitive admission before canonical copied-body processing. Completeness qualification SHALL remain distinct from this immutable binding and MAY commit later when the same fixed source reconstructs the complete current cohort; a pending sibling SHALL NOT permanently poison an otherwise unchanged floor. Qualification SHALL NOT attest terminal holder closure. The receiving terminal receipt SHALL require its actual ended server and independent Tool/context lifetime, no active private receiver/pending cell, every original processing claim and reserved context disposition, and exact unchanged scheduled prompt. Only the selected owning return task MAY be disabled and reduced, in the same transaction as its receipt. Separate full floor/receipt/task-body readback SHALL gate success and replay SHALL reuse the original receipt; changed or unknown committed state SHALL remain unavailable.

Configured Memory and core-only return context disposal SHALL reconstruct the original pre-context claim, complete original parent count/set/classification/body digest, all same-decision qualified floors and exact frozen composed input. Missing, extra, mixed, changed or unfinished sibling ancestry SHALL preserve the entire context. Additional local/catalog inputs, executed tools and stored descendants SHALL retain their own existing disposition requirements. Core-only reduction SHALL preserve the independent configured system/provenance and SHALL NOT fabricate a Memory pool. Configured Memory reduction SHALL use only its actual same-database owning writer and complete descendant engine. These source-owned receipts SHALL NOT attest the remote recipient or dispose independent context merely because a receiving Tool finished. Fixed metadata controls SHALL enforce their byte/frame bounds before copying a new chunk into the private buffer.


Before reducing a shared return task, the owning receiver SHALL observe every original receiving binding's qualified floor and actual ended server/Tool lifetime and every processing claim of that physical task, including a claim frozen before a later duplicate wake enrolled another binding. An unfinished sibling claim, reserved context or private receiver SHALL preserve the unchanged full prompt. This ordering SHALL retain the original prompt needed to reconstruct every original composed context until all applicable context dispositions commit. Later healthy closure and replay SHALL remain possible through the original same-plan full-body reduction receipt rather than reconstructing the original bundle from the reduced marker.

A successful delegate_wake MAY establish only its own transient Tool processing completion through an exact locator-only profile: its input SHALL match the two stored ledger/wake locators; every recorded same-name call SHALL match all actual private input/result witnesses one-to-one; its selected result SHALL contain only the fixed successful ledger/task/wake metadata and SHALL match that generation's actual stored return-task binding. Extra answer/prose fields, error/conflict/mixed outcomes, missing or duplicate execution records and absent schedules SHALL refuse this profile. Session completion alone SHALL NOT qualify. This profile SHALL NOT erase or attest the caller's independent model context, scheduled return processing, other Tool generations or remote recipient.

The actual recursive question owner SHALL register fixed non-presentable location_retention_question_owner_plan, location_retention_prepare_question_loan and location_retention_close_owned_questions tools. Decision and loan arguments SHALL select only stored owning state. The constructor SHALL obtain the root manifest through Switchboard, reconstruct every original native/catalog/received-question/received-answer parent, preserve declared count and digest identities, and refuse a missing or smaller surviving ancestry. Each receiving loan SHALL obtain its actual registered owner preparation/status, bind source/receiver incarnations and original ledger/generation/body/manifest, commit its own immutable observation and separately read back that COMMIT. A stored source name SHALL remain a binding value, never caller authority or permission for peer-private SQL; exact narrow installed-shape evolution SHALL preserve owner/column/constraint/history checks and SHALL NOT widen roles or grants.

The actual recursive question child SHALL reduce only its full unchanged own canonical profile after every original receiving loan and source server/Tool/context lifetime has closed. Its body reduction and immutable full original-to-reduced receipt SHALL commit together and require separate committed readback. Receiving and catalog preparation SHALL reconcile those owning children before disposing the copied context. An executed delegate Tool SHALL require complete private/recorded input/result matching and every actual same-Tool child’s full immutable reduced profile; a successful sibling or placeholder SHALL NOT proxy closure. Core-only and configured Memory paths SHALL preserve independent content and remain held for unknown/mixed/unclosed descendants. Answered nested questions SHALL preserve their original question/answer/wake identities and use actual owning reciprocal observations rather than reminting a reference from reduced prose.

Reentrant owning reconciliation SHALL return pending without a terminal receipt and SHALL release its actual constructor-owned guard on success, failure or cancellation. A later invocation SHALL recompute the complete original cohort; neither an in-process pending flag nor an unchanged source name SHALL attest another process or receiver. Actual producer fault controls SHALL call the owning reducer and fault its receipt write after its body update, require a separate acquisition to observe the original body and no receipt, and restore the same producer’s genuine positive. Software doubles and planted receiver observations SHALL retain their proof limits and SHALL NOT claim registered online all-holder disposal.

The actual receiving-attempt producer SHALL lock and re-read the canonical ledger at birth, compare its original source, target, status and complete body digest, and freeze its original source selector together with the receiving generation and current constructor incarnation before any copied processing. A receiving-generation locator MAY select only that stored attempt when admission was interrupted. Its nullable legacy source SHALL remain unknown and SHALL NOT be filled from caller fields, reduced references or later ledger lookups. The fixed registered owning plan SHALL independently prove the complete original question generation/body/loan, receiving generation/incarnation and root decision/manifest before any permanent input floor or terminal receipt. An ended own server response MAY close only that exact unaccepted transient server copy after its own durable lifetime receipt; a missing attempt, mismatched source/loan or live Tool/context SHALL remain unresolved. Terminal evidence SHALL commit and be separately read back; replay SHALL preserve the same receipt and lost acknowledgements SHALL remain unknown. No stored selector, optional locator or server receipt SHALL attest a remote recipient or bypass any admitted descendant.

An admitted receiving caller context SHALL NOT depend on its own final receiving receipt to prove its separately scheduled child. The actual owning scheduled-copy producer SHALL first require its permanent full input floor, exact current receiving incarnation and unchanged original task binding, the ended native server or complete private handler result, and every assigned processing claim and composed-context disposition. Only then SHALL its actual task reduction and immutable original/reduced prompt receipt commit together and be separately read back. That scheduled-copy receipt SHALL NOT attest the caller context or final receiving lifetime. The caller context MAY qualify delegate_receive only through every original own receiving attempt, its full matching private and recorded input/result, exact successful scheduled-result profile and the separate full scheduled-copy receipt. Missing, unaccepted, error, mixed, current-body drift and unmatched same-name siblings SHALL remain unresolved. Once the actual caller context closes, the final receiver producer SHALL verify the already-reduced scheduled body against its exact immutable receipt; neither a placeholder nor another task receipt SHALL bypass the final lifetime. Actual producer receipt-fault controls SHALL retain rollback, separate original-task/no-receipt readback and restored same-producer/replay positives.

A fixed pre-admission rejection MAY qualify only its actual native handler processing copy through a distinct producer-owned stage receipt. The actual handler SHALL obtain a private single-use failure capability minted only after that same invocation has committed its canonical attempt birth; a same-message exception, caller field or error result SHALL NOT mint it. The fixed configured writer SHALL require the same active private Tool and constructor, exact original source/ledger/body/session/incarnation, no admitted input or scheduled child, and a committed immutable rejection receipt followed by separate readback before recording an observed input. Unknown admission acknowledgement, stale lifetime or missing readback SHALL remain unresolved. The caller-context reader SHALL independently require the trusted original owning floor and complete original attempt census, exact full private/recorded Tool input and fixed returned-result fingerprints, and every applicable same-Tool stage; one successful or rejected sibling SHALL NOT proxy another. This qualified rejection closes neither the caller context nor final receiving lifetime and SHALL NOT infer a source loan, floor, remote erasure or terminal state from absence. Other unaccepted/error profiles without that complete distinct producer evidence remain unresolved under the preceding rule; interrupted source preparation without a committed source loan still requires actual constructor-owned source/receiver reconciliation before any owning floor. Actual producer receipt-fault controls SHALL call the producer, observe its rolled-back receipt from a separate acquisition, then execute the restored producer and preserve immutable history.

Before declaring an original question's receiving cohort closed, its actual configured target SHALL commit an immutable source-question fence under its own policy-first locks, bound to the complete original source name, ledger, question generation/body and root decision/manifest from the fixed owning registered source plan. The current full canonical ledger profile SHALL match that plan before the fence COMMIT, and a separate acquisition SHALL read back the entire fence. Fresh receiving birth SHALL retain only its rejected native digest/lifetime and refuse source exchange behind that fence; an exchange already in flight SHALL recheck the same source fence before its admission COMMIT or scheduled copy. The owning census SHALL include every original attempt, including unaccepted, NULL-legacy-source, missing-floor, mismatched-body and old-incarnation attempts; it SHALL NOT shrink through an admitted or terminal-only join. Private pending processing and receiving lifetimes SHALL remain blockers. Every applicable attempt SHALL require its exact current-incarnation full owning floor and immutable terminal receipt before the registered result can declare the source census closed. The original source SHALL compare each target's complete fixed-route census, including a target with zero previously known loans, before interpreting its own loan set as complete. This fence and census SHALL NOT manufacture a missing source loan, attest remote erasure or proxy caller context, Tool, scheduled copy or final receiving disposition. Interrupted source preparation still requires its actual constructor-owned disposition-only recovery binding; an absent loan alone SHALL NOT permit raw deletion or source reduction.

A recursive original question owner SHALL request the actual target's complete receiving census even when its own delivery-loan set is empty. The existing owning preparation tool's optional ledger UUID SHALL locate the current stored canonical target/source only; the constructor SHALL independently read that source's registered native owner plan and require one exact original question generation/body and qualified root manifest. The source name SHALL NOT be accepted from the request or treated as authority. Canonical body SHALL be rechecked under the receiving fence transaction after this network read. Every unresolved original attempt SHALL prevent interpreting zero loans as a closed cohort.

An unaccepted attempt with no source delivery loan MAY obtain a distinct disposition-only recovery binding from its current configured receiver only after its exact original-source fence, frozen source/ledger/body/current incarnation and qualified original source generation/root manifest match. A private fixed native rejection-stage producer or actual owning ended-server receipt SHALL qualify only its own processing copy. Admitted input, scheduled child, live private receiving/pending work, legacy unknown selector or differing incarnation SHALL refuse this recovery profile. Recovery storage SHALL contain no delivery loan ID and SHALL NOT mint an admitted input, schedule, source delivery relation or permission to emit the original question. Its immutable binding SHALL COMMIT and be separately read back before caller-context qualification; a rejection still needs its full private result, immutable Tool input, complete same-Tool sibling census and actual own context disposition. Only after every actual own lifetime ends and no admitted/processing descendant remains may its separate immutable recovery-terminal receipt COMMIT and be separately read back. Known delivery loans retain the original exact delivery path; the complete source census SHALL distinguish exactly one delivery-or-recovery binding per original attempt. Lost ACKs SHALL replay the same binding and receipt, never reinterpret admission uncertainty as a rejected attempt. Fault controls SHALL invoke the actual recovery producer, fail its real receipt insertion, observe the surviving binding and absent terminal from another acquisition, then restore the same producer positive without fabricating a delivery loan. This closes only the actual system-owned receiving copy; remote recipients, unknown legacy lifetimes and full source/runtime/mixed/all-holder obligations remain separate.

Configured native catalog body delivery SHALL traverse the actual registered consumer tool, constructor-held Switchboard client and owning source tool, with the outer online challenge preceding normal tool instrumentation. A catalog UUID, shared discovery row, endpoint string or caller header SHALL NOT replace that admission. Actual source and consumer owning writers SHALL separately commit and reread the same loan generation, full body digest and receiving incarnation; native response completion SHALL settle only its source-owned response copies. The configured Chronicle-private Memory pool SHALL retain its adopted own-domain authority, and other runtime roles SHALL retain their private-schema denial. A same-host registered TCP/SQL control with planted original lineage proves only that explicit transport/profile; independent deployment, real original ingest, other holders and whole erasure SHALL remain separately required.

Stateful registered MCP handlers SHALL consume the actual per-message SDK HTTP Request carrier, rather than treating ContextVars copied by the initialization task as a later POST admission. The fixed ASGI constructors SHALL place private live server/online loan/route cells only on that Request scope after outer verification; the instrumentation boundary SHALL verify same target and active exact server-map identity, install those cells for the actual call and reset them on every result/error/cancellation. HTTP calls without these private cells SHALL not inherit an initialization admission, and headers, locators, caller context or synthetic header-only background snapshots SHALL not mint or rehydrate them. This carrier refinement SHALL retain all online challenge/body/generation/current-writer/lifetime gates and generic/stdio compatibility; a separate-task software control SHALL sit beside the actual registered transport/role positive, without borrowing either scope as whole delivery.

The owning registered source prepare/status plan reader SHALL validate the full original dispatch header, all declared parents and all immutable births under its policy-first writer transaction before deriving any consumer cohort or complete_input verdict. Missing, extra or digest-mismatched ancestry SHALL refuse the source response and SHALL NOT become a smaller terminal cohort. Consumer closure SHALL use actual registered owning prepare/status tools through Switchboard, exact original generation/body/receiving-incarnation/decision/manifest and separate committed owning receipt readbacks; source reconciliation SHALL preserve those bindings and idempotent receipts. A planted source plan may exercise this protocol in real SQL/TCP controls but SHALL NOT substitute for actual accepted OwnTracks lineage, READY, raw disposal or complete fleet erasure.

The actual OwnTracks filtered-event writer SHALL freeze every emitted location copy's original provider/endpoint/event/raw and full copied-row digest on the same owning connector transaction, then separately observe the exact committed birth. Its constructor-owned pre-READY preparation SHALL read the full immutable owning plan without creating a grant, validate every current selected raw generation and every applicable stored copy/legacy row, atomically reduce copied bodies and display/error fields with permanent logical-source floors and complete receipt members, and separately read back the actual reduced rows. Missing/different/extra/partial births, current copies, members, original manifest or commit SHALL preserve point and raw evidence. A stored-copy receipt SHALL NOT certify an active webhook, replay, request, server/runtime or browser holder. Those actual source-owned lifetimes and later writers SHALL remain in the complete current cohort, independently fenced/disposed before point deletion. Default ready-tool compatibility SHALL preserve the existing grant shape; the fixed optional copies phase SHALL expose only complete owning preparation metadata and no caller source/actor/ready verdict.

The configured OwnTracks constructor SHALL commit and independently reread its content-free native server header before receiving authenticated webhook body bytes, and SHALL commit and reread the complete exact canonical raw input bundle before issuing processing. The actual outer server Task lifetime and issued processing Task lifetime SHALL be separate; inner handler return, HTTP client ACK and caller fields SHALL NOT close them. The native replay reader SHALL reserve its own server/read lifetime before reading stored payloads, bind the actual locked stored endpoint/body before emission, release SQL transactions before transport and preserve original pending/current-body guards. Source-owned end observers SHALL retry only their same original observed ended allocations after unknown commit/readback; a restart or missing process SHALL NOT create an end. A duplicate reserve SHALL NOT overwrite an unresolved original server bundle. Unauthenticated and explicitly unconfigured ordinary behavior SHALL preserve its existing contract without minting native evidence.

The native OwnTracks processing producer SHALL reserve its private processing hold before publishing cross-loop work and SHALL observe the actual owning Task end, including cancellation unwind. A transport Future completion or cancellation SHALL NOT certify a processing end. Shutdown SHALL close new admission before joining its own server while the owning loop and pool remain available for original Tasks and observed-end readback. Finite waits, requested cancellation, interrupted shutdown and process restart SHALL NOT mint a terminal receipt; every unresolved original binding and durable birth SHALL remain in the complete source cohort until genuine source-owned reconciliation.

The actual configured Switchboard receiving constructor SHALL freeze its original server header before body forwarding and SHALL commit and independently read the canonical OwnTracks input queue generation, full envelope/dedupe digests and original server binding before releasing the final SDK body frame. Constructor-owned SDK middleware SHALL commit and independently read a separate immutable original handler generation/incarnation claim before FunctionTool validation or processing. HTTP 202, POST completion, accepted UUID, caller fields and a connector end SHALL NOT settle the queued or SDK handler holder. Only the actual original handler Task end and corresponding original committed claim/birth/readback may settle that holder. The canonical accepted row SHALL be bound by the owning writer and separately read back before routing. Source reduction SHALL require the complete nonempty original queue/claim/accepted-body/handler-end/server-end census under the same control-first source mutex. Unknown, missing, partial, changed or legacy lineage SHALL preserve source evidence; generic nonlocation input SHALL preserve its existing transport behavior without native proof. Restart, cancellation request or new incarnation SHALL NOT certify an original end. This boundary SHALL NOT attest any remote recipient, routed descendant or all-holder completion.

ID: REQ-location-retention-005
Source: bu-s11n0s.7 original nonresurrection non-goal and S2; about/heart-and-soul/security.md Sensitive Data Categories; openspec/specs/connector-filtered-events/spec.md Full Payload Shape and Replay lineage and event payload age independently; proposed source-protocol P5
Scope: v1-mandatory


Native consolidation SHALL acquire the qualified owning policy and adapter locks before its actual episode-claim lease locks. It SHALL capture the full selected episode, deduplication-fact and rule bundle and every producer-recorded parent generation in the SAME writer transaction, commit, and independently read back the immutable claim before rendering or dispatching copied prompts. Native processing completion SHALL close only that Python processing lifetime; its persisted, catalog and runtime descendants SHALL retain separate required dispositions. A crashed, interrupted or unknown committed processing claim SHALL remain in the holder census.

The owning current holder predicate SHALL repeat its actual unclassified session/cache census when reusing an immutable frontier and before point/READY/raw disposal, rather than crediting the snapshot's earlier absence. A newly stored opaque body without genuine native lineage SHALL remain UNKNOWN and preserve source evidence; a row's timestamp, source label or caller verdict SHALL NOT classify it as independent. The actual constructor-enrolled owning ordinary session producer SHALL take the same policy-first producer fence before inserting its body, preserving its existing unconfigured behavior and FK recovery inside savepoints. This fence SHALL NOT mint native ancestry or a terminal receipt. Disposable test-owner removal of a planted unknown sentinel SHALL be reported only as fixture cleanup, never as lawful product classification or erasure.

The scheduled owning source SHALL reduce an unanswered native question ledger only after its full original parent cohort matches the stored plan, every actual receiving loan has its exact committed owning observation, the source context/server lifetime has ended, and its source session and original core Tool have unchanged frozen input and a successful exclusive result witness. Nonempty independent metadata, answers and wake/return copies SHALL retain their separate required dispositions. Reduced question/status and immutable original-body/decision/manifest receipt SHALL commit together under policy-first locks and require separate committed readback. A ledger receipt SHALL NOT proxy the original Tool records or composed context. The raw-source frontier SHALL also require the exact context disposition; context erasure SHALL admit delegate_ask only through complete one-to-one input/result matching and that actual Tool's committed source dispositions. Unknown committed producer readback SHALL NOT mark the private Tool as having read its input. Pending-only dispatch and routed-only answer guards SHALL preserve the expired row against late revival.

The actual constructor SHALL capture both configured domain and Memory schema/role and their same-database witness. Only Chronicler's OWN configured Memory writer may read its qualified Chronicler policy; other receivers SHALL use their own registered routed admission. The actual Spawner SHALL reserve its receiving session before Memory context reads, bind its full composed input and stored session before runtime processing, and capture native stored episodes on their actual writer. The native runtime finalizer and native server-response finalizer SHALL attest only their own ended lifetimes. Native processing completion SHALL preserve an original runner failure or cancellation when ordinary secondary receipt persistence/readback fails, while leaving the disposition unresolved. A successful runner SHALL NOT hide its failed completion witness, and new completion cancellation SHALL NOT be swallowed. Runtime-context disposal SHALL require the unchanged full bundle, all selected source parents, every actual loan and ended lifetime, and separate committed owning readback. Independent instructions/provenance SHALL survive exact source-derived context reduction. Mixed, changed, active, unknown or unbound contexts and tool/derived descendants without completed native dispositions SHALL survive and block erasure. Late native session, process-log and Memory writes SHALL remain fenced.

Native consolidation SHALL reread every full canonical episode/fact/rule on the actual policy-first owning writer before prompt admission and capture all producer-owned immutable body/parent witnesses. A complete exclusively native fact/rule input SHALL NOT be classified as independent merely by table type. Every independent, changed, absent or legacy-unknown selected input SHALL keep the entire composed bundle mixed. Every actual native parent generation SHALL remain captured, and already prepared or disposed parent generations SHALL refuse new sensitive prompt admission. Model citations, returned IDs and context labels SHALL NOT install that lineage.

Actual configured owning Memory mutation transactions SHALL take the policy-first lock before canonical target locks and refuse current prepared source generations through their real immutable parent relation. Schema selection SHALL match constructor-owned configuration; a caller identifier or a generation from another namespace SHALL NOT substitute for the parent binding. Unconfigured ordinary behavior SHALL remain within its existing contract. These fences SHALL NOT attest exclusive mutation lineage or terminal erasure, and changed, mixed, routed or unknown descendants SHALL retain their required source/copy witnesses.

Actual enrolled owning fact/rule mutations SHALL append a contiguous immutable before/after content-digest chain on the SAME policy-first business transaction, anchored to the original artifact generation and original non-NULL producer content witness. Original bodies, parent bundles and historical catalog loans SHALL remain frozen. Only verified fixed native lifecycle field changes SHALL retain source exclusivity; freeform body, authority, endorsement or independent metadata changes SHALL retain immutable mixed history along that chain and SHALL require a separately proved lawful own-copy reduction/disposition that preserves independent content; lifecycle verification alone SHALL NOT close them. Native selected-row reads, consolidation, registered catalog source and disposal SHALL verify the complete chain and the actual current body; missing, reordered, foreign, corrupt or NULL-original chains SHALL NOT be refreshed from current rows. The business return SHALL require separate post-COMMIT readback of the exact immutable new transition. An unknown ACK SHALL retain that version's unknown disposition without rewriting its birth. DatabaseManager's private constructor enrollment SHALL bind only the actual existing Chronicler API pool, its frozen current role/domain and canonical configured own Memory schema. Its transaction-local view SHALL revert after COMMIT/rollback and SHALL NOT enroll receiving/runtime/source authority or widen roles/grants. Routed/mutating runtime-tool descendants SHALL still require their own complete input/result and disposal bindings; a lifecycle body chain alone SHALL NOT close that runtime holder. Actual registered native Memory mutations SHALL capture their complete locked artifact input into a distinct immutable input generation before business processing, bound to their private tool generation and receiving session on the same transaction. The trusted Chronicle/runtime-core migration phases SHALL install or converge the exact own-schema validated nondeferrable tool-intent FK only when both actual tables exist; differing or extra tool-generation FKs SHALL refuse even alongside the expected FK, and native producers SHALL NOT provision their own dependency. Standalone ordinary Chronicle storage SHALL remain installable without that later runtime parent, while native mutation admission SHALL refuse an absent or differing installed FK. Before any input birth or business write, the producer SHALL compare the original immutable dispatch parent_count with every declared parent and every birth for that generation; missing births, differing digests or extra declared ancestry SHALL refuse rather than shrink the copied cohort. They SHALL read back that input and its complete parent count separately after COMMIT before returning a result. Actual selected-row Memory reads, consolidation prompt capture, inherited catalog-context production and owning episode disposal SHALL likewise compare every original declared parent with its birth and immutable digest before any copied input or terminal receipt. A native header with no surviving parent births SHALL refuse; independent rows SHALL remain distinguishable from incomplete native ancestry. A shrinking join SHALL NOT replace the frozen cohort. Each successful mutating call SHALL match its one-to-one executed input/result witness and its own immutable input generation; a different same-name call, artifact or read loan SHALL NOT substitute. Runtime disposal SHALL require the exact original artifact disposition under the current decision and every captured parent selected, exclusive and unchanged. Later native outputs SHALL inherit those actual completed mutation inputs; missing or mixed inputs SHALL preserve the whole descendant. Native local-copy receipt production MAY finish an already reduced receiving session only after its exact same-plan full runtime-context disposition and reduced stored body are observed; placeholder strings or an empty tool record alone SHALL NOT qualify. No receiving/source privilege, peer namespace or role grant SHALL be inferred from this binding.


Native fact/rule INSERTs SHALL capture their complete final canonical body on the SAME configured Memory writer before COMMIT from the private registered runtime invocation or constructor-owned context. Invocation-backed producer admission SHALL recheck the exact still-registered native cell identity, fixed target and live deadline before selected-body reads or writer locks. Equal copied cells, settled or expired cells and matching target/session fields without registration SHALL NOT create ancestry. Caller provenance, actor, source strings or returned UUIDs SHALL NOT mint that ancestry. An exclusive Chronicler context SHALL reread its complete immutable composed bundle and every native parent before publishing a catalog source generation through its existing registered owning-MCP plane. Live catalog writes and backfill SHALL bind every actually emitted ID on that same writer, preserving the ordinary unconfigured maintenance contract. Mixed, borrowed, unknown or other-source context outputs SHALL NOT borrow Chronicle's source authority. Own canonical body, other-context, graph, catalog and loan descendants SHALL be checked before removal; exact owning disposals SHALL be independently read back. Child disposal SHALL precede parent terminal disposal after all exact parents are selected and fenced; it SHALL require actual ended-runtime and finished-processing witnesses rather than a cyclic requirement that the still-held parent be terminal first. Unknown operations or unbound/routed descendants SHALL remain held and SHALL NOT imply complete erasure.


Artifact, catalog and receiver census SHALL resolve dispatch input generations through the immutable dispatch-parent map, never equate an input-generation UUID with a copy-generation UUID. ALL captured parents must match their exact digests and committed selected dispositions; one disposed parent SHALL NOT close a mixed bundle. Ordinary unconfigured readers SHALL preserve only persisted ordinary rows that have no canonical projection/native-copy ancestry and no OwnTracks source label. They SHALL supply no native lineage or authority and SHALL refuse stored native ancestry even when the display label changes. Closed failure diagnostics SHALL expose only fixed stage/category/class and validated SQLSTATE, preserving the original failure and excluding exception text, arguments and source values.

The actual configured owning catalog producer SHALL reread each artifact's original dispatch header, all declared parents and all births before publishing a source generation, delivering a body or committing its terminal receipt. Missing headers/births, differing immutable digests and extra or missing declared parents SHALL refuse before the copied body or business/receipt write. Full artifact/catalog frontier and holder inventory SHALL retain every native artifact header, including one with no surviving parents, and SHALL refuse unknown ancestry instead of omitting it through selected-output joins. Only the complete healthy bundle may proceed through unchanged current-body, all selected source parents and actual lifetime/descendant conditions. This validation supplies no remote receipt, exclusivity for freeform mutations or authority from a current projection; independent and mixed content remains under its separate lawful disposition contract.

The answer-owning reducer MUST obtain each stored loan's prepare and full status from its constructor-fixed registered receiver through Switchboard, outside any DB transaction. It MUST compare the exact source/receiver incarnations, answer/ledger/loan/generation and full bundle against own immutable history under policy-first locks, COMMIT only the observation and reread it separately. Its new own observation ledger is append-only and supplies no authority from a caller locator, copied status field or peer-private SQL. Missing/unfinished/changed/unknown receipt bindings keep the source answer intact.

Source answer reduction MUST require every actual current loan observed, the original full canonical question/answer/wake bundle, exact selected successful private delegate_answer input/result witness and genuinely ended frozen source context/server lifetime. It MUST reduce only the canonical answer body and COMMIT a receipt retaining original body/bundle/question/wake reference digests plus the reduced profile. The original question, answer identity and wake identity remain unchanged; later question reduction needs its own disposition. Separate committed body and full original ancestry readback gates success and replay. Prototype NULL receipt fields remain unknown; original prose is never backfilled from a reduced row. This child receipt closes neither source Tool records nor its runtime/session/Memory context. The configured owning context engine MAY select a delegate_answer Tool only when every source child of that actual Tool has the same-plan immutable reduced-body receipt and all original private full input/result witnesses still match. No generic native-MCP allowance or another child's receipt is permitted.

The actual scheduled retention entry MUST invoke stored answer owners selected from original native question loans, including its own answer owner, over the registered source close/status tools. A metadata wake is no terminal receipt or full source completion. Recursive answered questions, mixed/borrowed/routed descendants, restart and actual online all-holder/managed-browser proof remain mandatory until their own lawful full closure.

The source question ledger's own terminal receipt MUST freeze the full reduced question/reference digest, including all preserved catalog and metadata fields, in the SAME business transaction. The constructor-owned registered source-question status reader MUST compare that snapshot, original private generation/body/plan and every original selected parent from a separate locked committed acquisition. A reduced string/status alone, changed preserved metadata/catalog fields or another source's receipt is insufficient. Prototype NULL reduced profiles remain unknown with no current-row backfill. This question-child receipt closes neither a receiving generation nor source runtime/answer descendants. Answered-question reconciliation MUST preserve the original question/answer relation through the actual corresponding owning readers and immutable observations before its separate lawful reduction; the unanswered profile cannot be used as an answered or mixed-copy substitute.

An answered source question SHALL require this question owner's immutable observation of the actual corresponding answer owner's committed child receipt over the fixed registered Switchboard path, preserving original question/body, answer generation/body/bundle, immutable wake identity and exact decision/manifest. Only after every original question receiving loan and source lifetime closes and the original private ask Tool's full input/result matches SHALL its own question reduction and immutable full reduced-reference receipt commit together. The answer owner SHALL select its separately frozen pre-reduction question owner from its own answer receipt, read the question's actual complete owning status, and commit/read back its own immutable original-to-reduced reference observation before future answer or context reads accept a changed question. Locators SHALL NOT select peers or supply bodies, actors or completion verdicts. Lost acknowledgements SHALL resume the same two receipts before original-answer reconciliation, never refill original digests from reduced rows or create a mutual-receipt cycle. NULL original-owner/reference history, changed/missing parent sets and unrelated metadata SHALL remain unavailable; child observations SHALL NOT proxy runtime/Tool/session/receiver termination. Current producer atomicity proof SHALL invoke the actual reducer and fault its actual receipt write after its actual business write, with separate committed survivor readback and a restored actual-producer positive; a handwritten rollback is only a diagnostic companion. Full recursive/mixed/borrowed/routed/online/browser/protected obligations remain mandatory beyond this child profile.

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

Scoped ordinary recovery SHALL preserve the complete installed OwnTracks metadata family: filtered-copy births/floors/batches/members and, when installed, input server births/ends and input copy births/ends. Snapshot locks SHALL precede the exported snapshot; all rows, immutable generation/digest/kind/count/contract bindings, original foreign keys and forced policies SHALL round-trip. The fixed constrained-role native import SHALL apply row_security=on within its own transaction without altering roles, grants or error-stop policy. Four-table predecessor artifacts remain distinct from complete eight-table artifacts; partial families, changed or duplicate staged rows and differing current point-to-input digests SHALL refuse certification. A nullable legacy point reference SHALL remain unavailable for current input closure, not be filled on restore. The point's non-NULL reference SHALL be validated by its immutable original INSERT trigger and complete restore/readback checks; ordinary dump order SHALL NOT require installing a reference to history before that history is imported. Stored ended rows in a backup SHALL NOT certify an active restored receiving/server/runtime incarnation or substitute for restore admission.

ID: REQ-location-retention-006
Source: bu-s11n0s.7 original Behavior matrix and S2; openspec/specs/connector-owntracks/spec.md Retention Purge Degradation Visibility; proposed source-protocol P6
Scope: v1-mandatory


The actual configured owning scheduled producer SHALL complete its durable attempt under current policy-first and connector-source fences, and separately read back that committed outcome. Zero unknown holders SHALL require a nonempty full keyset census of every stored original plan, its current nonempty all-holder frontier, exact immutable disposition/grant/source batch header and every original batch member, and actual raw-source absence; a complete-state flag, first page, empty ledger, transport counter or expired lease SHALL NOT substitute. Outstanding plans, overdue raw rows, late opaque own sessions/cache, missing or differing receipts, changed policy, inaccessible source and unknown COMMIT/readback SHALL remain unresolved. Counter observations SHALL retain their point units: overdue and projection-blocked points at the original cutoff, and their difference as holder-pending candidates; plan count SHALL NOT replace point count. A completion receipt describes its committed as-of census, never future writes or external recipient erasure. Replay SHALL preserve an already committed completion or failure, and a later attempt SHALL reobserve the current census rather than refresh a prior green. Grouped migrated-role controls SHALL invoke this actual producer, prove rollback/independent readback and a restored nonempty completion positive, and separately preserve genuine empty/incomplete/late-holder unknowns; planted source admission or remote closure SHALL remain engine-only proof.

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

The actual managed point-query producer SHALL namespace requests by the observed committed privacy transition generation and forward that same generation through its derived trail/playhead and minimap into the map wrapper. The wrapper SHALL retain the fence after reset settles when its parent still holds old props, including cloned old arrays; it SHALL admit a genuinely new generation even when response values match old values. Query cancellation and cache reset SHALL include current and archived variants; late obsolete responses SHALL NOT recreate retained geometry. This managed generation is a local cache/render witness, never a server deletion receipt or remote-browser attestation.

The actual browser control SHALL plant both current and inactive archive point-query variants, exercise the real authenticated-interface shortening interaction against an explicitly synthetic committed-privacy-revision response, and verify removal of the old MapLibre canvas and loss of its old WebGL context before a surviving new-generation response may render. A new allowed generation with identical coordinates SHALL remain renderable, and a subsequent archived view SHALL obtain the new-generation upstream response rather than remount old cached geometry. A missing WebGL runtime SHALL remain missing proof rather than a silently passing substitute. Synthetic browser API/tiles SHALL NOT be represented as real source deletion, registered all-holder disposition, authenticated server-policy authority or remote-recipient erasure; an incomplete receipt SHALL continue to display unconfirmed deletion.

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

New permanent connector birth, floor and receipt history SHALL retain exact installed owner, columns, complete PK/unique/CHECK/FK set and existing-role forced-RLS denials before and after bootstrap replay. Its ordinary backup and actual scoped export/restore admission SHALL preserve this original history under the existing owning identities without a generic RLS bypass, new principal/grant or omission represented as completed recovery. An intermediate source checkpoint's exact fenced exclusion SHALL be explicitly unfulfilled recovery, with ordinary raw/Chronicler evidence preserved and no merge-ready or whole-outcome credit.

The current scoped recovery producer MUST retain all four connector-owned copy birth/floor/batch/member tables: their schema, owners, constraints, FORCE RLS and immutable triggers stay in ordinary dump; only exact data rows use the existing exported snapshot under the positively checked actual owner-read policy. It MUST refuse partial installation, changed owner, missing existing connector-role membership or extra/restrictive/differing policy. Restore MUST use fixed relation names, non-NULL selectors, the existing connector role and one transaction, validate complete typed rows, preserve original generations/digests/timestamps/floors and check both-direction full-cohort equality including multiplicity. Unknown, extra or differing rows MUST abort rather than certify a partial replay. The preceding temporary table exclusion denotes only an intermediate dated SOURCE checkpoint; omission MUST NOT remain the delivered recovery policy. No generic row-security bypass, new role, definer importer, authority re-enrollment or old-incarnation admission is permitted. Actual current producer/holder reconciliation remains necessary after stored history recovery; stored rows alone MUST NOT reopen active requests, Tool contexts, remote loans or READY.

Deliberately unfenced raw psql recovery MAY continue its original ownership-diagnostic path without native replay, but MUST NOT be certified as complete native history recovery. The supported restore certifier MUST preserve the original definer-ownership refusal, independently read the artifact's complete fixed staging cohort and observe all fixed native tables through the existing constrained connector role under a separate committed snapshot. It MUST refuse absent staging for present native schemas, unavailable owner/forced-policy posture and any missing, duplicate, extra or changed full row. The native importer MUST preserve the caller's psql error-stop mode and keep its admitted four-table writes atomic; a skipped or aborted import SHALL NOT pass independent certification. This stored history certificate SHALL NOT authorize any restored active source, server or receiving incarnation.

ID: REQ-location-retention-008
Source: bu-s11n0s.7 original Documentation impact, Completeness gate and S1; RFC 0014 D2; proposed source-protocol P8
Scope: v1-mandatory


Core264 own-ledger installation and retained validation SHALL use the established owner of the actual resolved core foundation state relation. A present local state SHALL take precedence and SHALL refuse malformed kind/identity; only absent local state MAY use fixed public.state in the adopted independent shared-predecessor replay topology. The target namespace SHALL exist, and the chosen state SHALL be a regular table with stored owner identity. Invocation or namespace identity, inferred role names and peer-private tables SHALL NOT substitute. Newly created ledgers MAY receive that existing owner; existing relations SHALL NOT be reassigned, and all original columns/constraints/ACL/role/populated-history controls SHALL remain enforced. Valid actual public-first health/general complete-chain replay SHALL be positioned beside malformed/missing/local-priority controls without changing any foreign migration or native scenario.

An immutable native catalog disposal receipt SHALL NOT proxy the current retained discovery body or head. Before frontier closure the owning writer SHALL independently read each selected disposed catalog's original source/artifact identity, actual current head and full fixed empty/null reduction profile; missing, changed, restored raw or unqualified-head copies SHALL remain unresolved. The original complete parent census, every assigned registered consumer/server receipt and ordinary independent content preservation SHALL remain required. A causal atomicity control SHALL invoke the actual destructive producer, fail its real receipt insertion after its actual body writes on that acquired transaction, and independently observe original surviving bodies and absent receipt before restoring the same producer. Registered cohort positives SHALL NOT be promoted to actual OwnTracks/raw or whole-holder erasure.

The actual catalog reducer SHALL validate the selected original catalog UUID, configured source schema, source table, source UUID, source butler and current catalog/artifact generation before any reduction. Only that locked source-derived shadow whose complete selected exclusive ancestry and required holders have closed MAY be invalidated. Its unconstrained tenant/type/class/sensitivity strings SHALL NOT be presumed content-blind: the reducer SHALL clear tenant_id to the empty non-discoverable value, derive memory_type only from the validated facts/rules source table, and clear retention_class and sensitivity together with the existing text/vector/reference reduction. This is data removal from the invalidated selected shadow, never active tenant or authority reassignment. Original source identity and immutable receipts SHALL remain frozen. Active unselected catalog generations and independent canonical facts SHALL remain byte-for-byte unchanged. The current retained-body classifier SHALL refuse a refilled label, wrong source identity or active profile beside the exact healthy reduction; no registry, principal, type or role boundary SHALL be relaxed.

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
