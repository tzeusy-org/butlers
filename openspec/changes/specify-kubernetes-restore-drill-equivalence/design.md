# Proposed Kubernetes restore-drill protocol

**PROPOSED / NOT ADOPTED.** This is the complete source proposal for `bu-viat6h.4`.
P0 remains the operating disposition. P1 and W1 are reviewable alternatives, not
accepted equivalence or permission to implement. Source publication, strict validation,
a draft PR or a green check cannot activate them.

## D1. Governing boundary and non-goals

Security doctrine, RFC0006 least-privilege connection scoping, RFC0005 content-blind
failure evidence, RFC0007 honest API states and RFC0008 network boundaries govern.
The active `restore-drill-recovery-truthfulness` change carries the specific
REQ-deployment-hardening-007 / REQ-database-security-006 executor boundary.
Its protected `restore_drill_executor.restore_drill_results` is sole due/read authority;
`public.audit_log` is projection telemetry. A host/ordinary-role public audit spoof
cannot manufacture pass or affect due state. The separate fixed-search-path NOLOGIN
audit writer cannot reach private results; rejected audit projection rolls back the
whole authoritative result transaction. Bootstrap origin, installer/finalizer ownership,
shared precreation refusal and absence/retry behavior remain mandatory.

Keep the purpose-bound LOGIN CREATEDB NOINHERIT NOSUPERUSER NOCREATEROLE
NOREPLICATION executor with no ordinary-role membership. Dashboard, migration,
butler and connector credentials remain NOCREATEDB. The already-adopted isolated
credential choice is not asked again. Neither ephemeral PostgreSQL nor a Job's exit
code replaces protected restore authority or proves the backup's coverage.

No source in another change is amended. No migration number, signer, Secret object,
BWS project, issuer service account, cluster role, host helper, CNI or policy is installed
or selected as an adopted authority. No production inspection, credential read, host
command, Docker/Kubernetes operation, restore or external message is authorized.

## D2. Concrete dispositions and exact owner gate

**P0: retain Compose (current disposition).** Keep chart exclusion and the current
fixed root-owned prepare/create/fence/start sequence. Drafting satisfies the original
proposal deliverable; a future port still needs exact acceptance and executing proof.

**P1: node-fenced generation protocol (candidate, UNIMPLEMENTED).** A separately
trusted launch controller and node enforcement helper observe actual CRI/container,
network namespace/interface, kernel boot and peer topology, hold both created protected
containers stopped through a trusted runtime/CNI barrier, fence both before either
workload starts, and release one generation-bound capability.
A late secret-materialization component would be required because normal Kubernetes
Secret volumes can be present when a pod starts. This introduces new privileged
node enforcement, proof/custody and secret-release authorities. They are explicit
security-contract deltas requiring exact owner acceptance; their existence, API access
and enforcement are not assumed. P1 is an equivalence target, not proof it is attainable
on the installed CNI/kernel or that its added trust is acceptable.

**W1: ordinary Job plus NetworkPolicy (candidate weakening).** Use separate executor
and uncredentialed relay pods, restartPolicy Never/backoffLimit 0, exact endpoint
policies, private file credential and authenticated API metadata. Preserve the current
credential/result role boundary and scratch truth. This still loses the guarantees below.
It cannot run without an exact reviewed owner choice enumerating each accepted loss.
A password-free executor or uncredentialed relay can still execute and reach the
network; delaying a password is not equivalent to holding both services stopped.

| Weakening | Exact lost guarantee / risk | Required choice |
| --- | --- | --- |
| W-host | Generic NetworkPolicy admits own-node traffic and has implementation-dependent hostNetwork behavior; current executor/relay host/gateway fence is lost. A compromised credentialed process may reach a node service or host route. | Accept this named host-path loss with scope and compensations, or retain P0 / require demonstrated P1 enforcement. |
| W-start | API policy creation/readback and a normal Job do not hold both executor and relay stopped until the actual fence is applied. Even a password-free process or uncredentialed relay can run before fencing; a mounted secret adds credential exposure. | Accept this named before-both-workload-start loss, or require the actual trusted barrier for both created containers plus applied-fence proof and the separate pre-secret boundary. |
| W-boot | Node.status.nodeInfo.bootID is node-reported and asynchronous; Pod UID survives container restart. API observations alone are not fresh kernel/container-generation proof. | Accept the specifically bounded freshness/restart residual, or require node-observed generation/boot proof. |
| W-policy | Other policies allow by union, service DNAT and connection revocation vary by CNI. A policy object/hash alone cannot prove exact endpoint-only routes or old-flow revocation. | Accept only explicitly demonstrated residual paths, or keep P0 until full path enforcement proof. |

W1 does not weaken credential/result isolation, permit shared CREATEDB, make public
or D1 audit authority canonical, remove backup coverage, waive cleanup failure or admit
concurrent scratch writers. If those must change, a separate exact contract delta is
required. `bu-dss7he.14`'s ephemeral-Postgres replacement remains held by `bu-i4khaz`;
this proposal neither adopts that alternative nor re-scopes .4 or deletes its mechanisms.

The eventual owner request is finite: after independent resulting-source review, name
one exact Git SHA/tree, choose P0 or the fully specified P1 authority/implementation
prerequisites or individually named W1 losses, and record continuing exclusions. Partial
or silence is not acceptance. Existing isolated-credential/adopted-old-artifact choices
are not repeated. Adoption is not deployment or live verification authority.

## D3. P1 authenticated pre-start and credential-release protocol

The following is a concrete candidate contract, not an existing service or an assertion
that the installed CRI/CNI has it. **Both protected workload processes remain stopped
until both fences are applied.** Late credential release is an additional boundary.

1. Management first obtains D7's exclusive scratch ownership, allocates a random
   256-bit nonce and immutable attempt UUID, and records the fixed image, endpoint,
   backup/scratch scope and one selected node UID. Old owned processes, flows and SQL must be quiesced and
   absent before fresh creation; stop/cleanup uncertainty is terminal for admission.
   Both distinct Pods must actually
   land on that same node; this matches the current single-host fencing domain and
   avoids pretending two asynchronous node attestations form an atomic start barrier.
   A placement mismatch refuses P1; affinity labels alone are not placement proof.
2. Before either Pod is submitted, management prepares a closed launch reservation
   with a fixed operator-registered runtime-handler version and trusted node gate.
   A proposed CRI facade delegates sandbox/network creation and CreateContainer to
   the selected runtime, but intercepts every StartContainer for either protected Pod.
   The gate permits only the two exact authorized image/entrypoint definitions;
   extra init, sidecar or ephemeral workloads are refused. Neither executor nor
   relay workload entrypoint executes at creation. A normal Job, init container,
   sleep entrypoint, readiness check or Secret absence does not supply this barrier.
3. The trusted runtime creates each Pod sandbox, its netns/interface and each stopped
   workload container. The ordinary executor/relay run no code and receive no recovery
   credential. The runtime's pinned sandbox holder and CNI/node helper are explicitly
   trusted infrastructure outside those workloads: no recovery credential, protected
   service listener or ordinary application work. Their extra runtime/CNI authority
   is a new unadopted prerequisite, not hidden equivalence to an ordinary pause Pod.
4. Under one node-local launch-generation critical section, the gate derives actual
   CRI Created/not-started states for both containers, namespace/Job/Pod UIDs, service
   account, image/entrypoint digests, actual node/kernel boot, netns/veth, addresses,
   routes and exact relay/remote endpoint. It never trusts labels, supplied 'verified',
   pod names or a copied marker. The helper applies both executor peer-only and relay
   endpoint-only fences at the actual pod interface and host/forward paths, including
   own-node routes and established-flow handling. Concrete CNI/runtime integration,
   including hooks that close the gate before network/topology mutation, is required.
5. The helper issues a closed signed `both-created-and-fenced` record containing the
   attempt/nonce, both exact runtime container IDs and Created-state generations,
   namespace/controller/Job/Pod IDs, node UID/kernel boot, netns/interface/address/route
   and image/configuration/endpoint/fence digests, and issuance/expiry. Candidate
   authentication is management-only mTLS, an operator registry binding the node
   certificate to that node UID and permitted scope, and an Ed25519 envelope binding
   issuer and controller audience. Issuer keys stay in the trusted helper; verifier
   keys are read-only. No credential, content, personal path or free-form diagnostic
   is evidence. New signer/CA/registry custody remains unadopted. The five-minute
   release expiry is checked against trusted time and actual monotonic boot/generation;
   uncertainty refuses. Expiry alone does not terminate an already-running attempt.
6. After fresh authenticated verification of both stopped generations and both actual
   fences, management atomically consumes the nonce and authorizes the trusted late
   materializer to expose the private executor-only file while the start gate is
   still closed. Materialization failure, missing peer or uncertain state leaves both
   starts denied and revokes/removes owned credential exposure. No relay/dashboard/
   ordinary runtime receives the recovery credential. Materializer custody is a new
   explicit bootstrap/secret authority, separate from the adopted runtime-only mount.
7. The same trusted gate rechecks both Created identities, current boot/placement and
   both installed fence generations under the critical section before admitting either
   exact StartContainer. It records at most one start admission for each bound container;
   a copied or consumed token cannot admit a replacement or a second start. The joint
   barrier must have no fail-open timeout, recovery path or alternative runtime bypass.
   Proposed wait is at most 30s, then refusal. If only one start succeeds, its actual
   applied fences remain mandatory; close admission, revoke credential exposure and
   terminate only the owned partial generation before any fresh attempt is admitted.
8. After start, the socketless executor independently verifies only observable local
   identity, exact name resolution/relay route, nonce and generation claims before
   credential read. Host/CRI/CNI facts remain authenticated issuer claims. Malformed,
   extra, stale, wrong issuer/audience/nonce/peer/endpoint/boot or generation evidence
   refuses within the proof bound without secret read or scratch work.
9. Before any CNI/container/peer/policy/address mutation or restart, the trusted gate
   invalidates start admissions and closes the relevant network paths. Node boot,
   gate/helper restart, kubelet retry, reschedule or lost control leaves the default
   start state DENY; no persisted marker/token reinstates it. Fence/terminate old
   processes and flows, prove absence plus D7 SQL/scratch recovery, then repeat the
   prepare -> create-stopped -> fence-both -> credential-materialize -> start sequence.
   Same Pod UID is never sufficient for container restart. A guard connection loss
   alone does not prove another SQL client or relay has stopped.

### D3a. Preconditions, refusal and proof limits

P1 requires an actually implemented, reviewed runtime/CNI gate that controls the complete
start surface and topology-mutation surface on the selected node. Normal Kubernetes
RuntimeClass selection merely chooses a handler; it supplies no such security guarantee.
An ordinary runtime, missing integration, unobservably stopped container, controller
partition, unavailable kernel evidence, stale admission, failed fence or unsupported
same-node placement makes P1 unavailable. Both starts remain denied, owned creation
is cleaned up within D6's finite bounds, and P0 remains. If absence cannot be proved,
report incomplete/unknown and keep successor admission closed.

A proposal to run either credential-free workload before the fence is specifically
**W-start**, with risk of pre-fence executor traffic or relay listener/upstream activity.
It requires the separate exact reviewed owner weakening choice; it is not an implicit
fallback inside P1. This correction asserts neither that an installed handler exists nor
that a YAML render proves the stopped-state, applied-fence or restart guarantees.
Future executing proof must observe no executor/relay entrypoint, listener or upstream
connection before joint admission, test positive authorized starts afterward, and exercise
missing peer, failed fence, partial start, nonce replay, helper/kubelet/node restart,
control loss and rollback with independent old-process/flow absence witnesses.

Pod namespace/name/UID/nodeName are useful Downward API inputs, not authentication
or boot proof. Downward API has no node bootID field. Node API bootID is node-reported;
any Node read scope, freshness and trusted helper is explicitly gated. For W1, proposed
per-generation get-only Pod/Job identity and a separately scoped node reader deliver
read-only authenticated metadata from outside the executor/relay. The management
creator must provision exact resourceName scopes; API TLS authenticates metadata,
while W-boot still names its asynchronous kernel/restart loss. No executor/relay API
token, management listener, management-network route, cluster-wide read role,
TokenReview, signer or hostPath is quietly granted. These W1 read scopes are unadopted.

## D4. Exact current-to-candidate attestation mapping

| Current source / protected guarantee | P1 candidate | W1 / unresolved proof |
| --- | --- | --- |
| Docker cgroup ID; exact created generation | CRI container identity, restart generation, Pod/Job/namespace UID, actual netns/interface bound to nonce | Pod UID/name alone insufficient; W-boot. |
| /proc kernel host boot ID | Helper-observed kernel boot plus node UID and authenticated current placement | Node API is metadata; Downward API cannot supply boot; W-boot. |
| Fixed root wrapper version, root-private preparation/capability, random nonce | Versioned authenticated scope-limited management proof with exclusive custody and no caller minting | A ConfigMap, label or YAML checksum is not a root-applied capability. |
| Stop/down failure terminal; create stopped; fence before either start | D3 fixed trusted runtime/CNI gate holds both actual Created containers stopped; one same-node joint barrier applies and attests both fences before either exact StartContainer, then permits credential materialization/start | Handler and CNI hooks are unimplemented new trust; absent proof refuses P1. Running either password-free workload early is explicit W-start, not P1 equivalence. |
| Exact executor IP/gateway/relay-alias IPv4 and independently observable marker dimensions | Actual interface/routes/peer observed by issuer; executor verifies only observable bound claims | Service addresses/DNAT need real path proof; W-policy. |
| Internal executor bridge, no host/gateway/non-relay routes | Separate pod/netns, no hostNetwork/privilege/NET_ADMIN, enforced node and peer paths | Shared sidecar netns is rejected; ordinary policy has W-host. |
| Relay only remote PostgreSQL, both DOCKER-USER/FORWARD and host INPUT | Exact relay endpoint route and node/interface fence, including old flows | Generic public/cluster allowance is not an equivalent; W-host/W-policy. |
| Secret read after capability; private regular file | Late executor-only private file, exact format/ownership checks and pre-read verification | Materializer/Secret custody is new explicit trust; normal mount has W-start. |
| No auto-restart fence bypass; unchanged already-fenced direct stop/start is not new authority | Kubernetes container restart is conservatively treated as a new generation, no capability reuse | This is a proposed stricter lifecycle; no existing Compose behavior changes. |
| Root discovers networks/containers; socketless executor checks only observable facts | Trusted node issuer observes host facts; socketless executor never invents visibility | Extra infrastructure and custody remain UNIMPLEMENTED and owner-gated. |

## D5. Relay, endpoint, DNS and TLS

Keep two pods/network namespaces. Executor has no listener, Docker/runtime management
socket or ordinary backend/frontend/egress access; backup mount is read-only. Relay is uncredentialed,
and only it reaches PostgreSQL. The executor's untrimmed DNS hostname is TLS/SNI
identity and resolves only to the relay alias; it never gets the direct remote IPv4 route.
A candidate trusted launcher resolves and validates the remote endpoint before
creating the fenced generations. It prepares a read-only exact hostname-to-relay
mapping for the executor, preserving hostname TLS/SNI without general DNS egress;
a mutable cluster DNS service or arbitrary caller hosts file cannot supply this binding.
The issuer binds that mapping to the created relay generation and digest; the executor
checks its exact content and actual observable resolution before secret use. Changing
the mapping or relay requires revocation and fresh proof. The relay uses only the
separately validated endpoint literal and never needs broad runtime DNS access.
This local name-resolution mechanism is a candidate implementation prerequisite,
not a claim that existing Helm or mounted files supply it. Database-client TCP to the
sole relay remains allowed; the socket exclusion concerns management sockets.
Separate canonical dotted-decimal remote-unicast IPv4 and canonical ASCII decimal
port 1..65535 are the relay/fence route. Reject loopback, unspecified, link-local,
multicast, documentation/policy-reserved and noncanonical decimal/octal/hex/abbreviated
spellings before reinterpretation. Current allowed RFC1918/CGNAT/public unicast
endpoint classes remain. No arbitrary runtime URL/host/port input is accepted.

Reproduce exact endpoint-literal grammar without sourcing arbitrary shell as proof;
raw RHS whitespace remains invalid. Runtime configuration is validated without
trimming. DNS replacement/rebinding, relay replacement, family and service-DNAT
changes revoke proof; hostname string alone is not identity of the destination.
Initially accept only actually evidenced IPv4-only executor/relay routes. IPv6,
NAT64, mapped routes or dual-stack requires a concrete complete boundary before
activation; an untested alternate path is refusal, not silent equivalence.

For verify-ca/full preserve the dedicated executor-only read-only CA root, same
libpq/asyncpg validation, hostname SNI and fail-closed missing/invalid roots; require
keeps its existing behavior. Password file retains private regular-file/no-symlink
rules and exactly the existing one-terminal-LF rule. Secret values never enter argv,
shared POSTGRES_*/DATABASE_URL, chart values, logs, proof, result or API.

Relay preserves admission cap/backlog, over-cap close before upstream dial,
connect=10s, idle=7200s, total-session=21600s and one-second graceful close per side
before abort as current source defaults. An attempt's shorter watchdog must still
terminate only its owned relay/processes and prove old-flow absence; it does not rely
on the six-hour session ceiling as proof of exclusive scratch ownership.

## D6. Scratch lifecycle and actual source limitations

Fixed scratch `butlers_restore_drill` stays distinct from live application data.
Maintenance connection only creates/drops scratch; restore/verify explicitly target
scratch. Precleanup -> create -> restore -> verify -> postcleanup remains obligatory;
postcleanup failure is failed, never pass. Backup omission/coverage, FK/ACL/owner and
credential-recovery contracts remain with their existing owners. No dump/restore runs.

Current backup_health source explicitly bounds psql restore at 1800s but its cleanup,
create and integrity subprocess calls lack per-call timeout. Therefore this proposal
preserves the bounded-lifecycle target without claiming current all-stage execution
already proves it. It does not fix this source gap or take bu-h40h2b.3's implementation.

Candidate future bounds: 30s identity/proof acquisition; 60s each precleanup/create/
verify/postcleanup; existing 1800s restore; 30s result transaction/readback; 60s owned
termination. Application watchdog starts early enough to reserve final cleanup and
stops at 2200s; outer Job/controller watchdog is 2400s. These are proposed caps,
not calibrated SLOs. They require real PG/client failure and cancellation tests and
exact completion/cleanup receipts before acceptance of a future implementation.
Hard kill/node loss without cleanup leaves an incomplete attempt; it cannot pass.
Subprocess groups, resistant descendants, connection loss and unreadable/changed
backup artifacts must have bounded owned cleanup and a categorical failure/unknown.

## D7. Exclusive ownership, replay and recovery

Before enabling Kubernetes there must be one exclusive lifecycle owner for this fixed
scratch across scheduled/manual Jobs, retries, releases and any surviving Compose
executor. Job Forbid/parallelism 1 does not cover manual Jobs, another release or
Compose. A lease expiry is not proof an old process/SQL/relay stopped.

P1 candidate: launch management holds a durable attempt generation and a session
exclusive scratch guard through postcleanup and protected finalization. Every admitted
path uses it; unmanaged/legacy writers must be proven absent before enablement.
The old Compose path is not modified here. On lock/lease/control-plane loss, deny
new admission; node fence/terminate and database connection-absence evidence are
required before another owner may acquire/recover. A disconnected guard session
alone cannot fence still-running psql on another connection. If that absence cannot
be proved, keep scratch unavailable and report unknown rather than expire-and-run.
A then-free guard/result representation amendment, if necessary, belongs to the
future owning contract after exact adoption; no current function or migration is added.

Duplicate trigger reuses the same authenticated attempt identity and must not re-run
scratch after a committed terminal result. Possible-commit result timeout uses
separate protected readback/reconciliation before retry. Current record_result has no
new Kubernetes attempt/generation key; do not claim current exactly-once attempt
storage. Preserve current due/result API until a compatible reviewed extension exists.
No public-audit search, Job status, private DB-state witness or caller nonce proves
protected attempt lineage. Orphan recovery first proves old process/flow absence,
then cleans scratch under exclusive ownership; no guessed new generation clears it.

## D8. Caller / failure / replay matrix

| Trigger / caller | Authority, finite transition and future proof seam |
| --- | --- |
| Scheduled/manual launch, second release, surviving Compose | Management admission + actual old-writer absence, exclusive scratch owner; reject concurrent trigger before cleanup/create. Real PG dual-client/lifecycle proof, not Forbid rendering. |
| Missing handler/peer/stopped-state proof, failed fence or 30s barrier expiry | Trusted runtime gate admits neither executor nor relay start, materialization or scratch work; bounded owned creation cleanup and truthful unknown if absence cannot be proved. Actual CRI/CNI entrypoint/listener/flow negative witness plus authorized positive companion. |
| Partial start, helper/control loss, kubelet retry or replayed StartContainer | Close joint admission and revoke exposure; retain actual fence, terminate only owned partial generation and prove old process/flow/SQL absence before a fresh created pair. No timeout/restart can fail open. Actual runtime/node and cross-client recovery seam. |
| Missing/tampered/extra/stale proof; wrong issuer/audience/nonce | Pre-secret executor validation refuses within 30s, no credential read or DB command. Extend current capability-before-password tests with authenticated envelope cases. |
| Same Pod UID/new container; reused name/new UID; node boot/reschedule | Revoke generation, fence/stop owned flow <=60s, retain incomplete truth until absence/cleanup; actual CRI/node proof and cancellation fixture. |
| Policy changed/removed/unapplied; controller/CNI disabled; old connection | No credential release/new admission; revoke and prove flow absence. Actual host/peer/endpoint fresh and established-flow probes under future bounded dev authority. |
| Forged labels/env/ConfigMap or caller 'verified' | Never issuer authority; API write/admission and proof-custody negatives. No arbitrary runtime caller can create a management capability. |
| Relay replacement, endpoint/DNS change, DNAT/IPv6 path | Revoke exact peer/route generation; invalid path refuses before secret. Existing endpoint/TLS parser seams plus real path proof. |
| Credential or CA missing/invalid | Private file/CA validation rejects without shared fallback or privilege repair; extend current executor config tests. |
| No backup / corrupt or changed backup | No backup is no-result, never fabricated fail/pass; corrupt/change fails fixed scratch attempt and preserves artifact. Existing backup runner/owner evidence seam. |
| Any scratch stage timeout/failure; final cleanup failure | Stage-bounded failure with reserved cleanup; final cleanup failure stays failure. Actual pg_dump/psql/dropdb execution and separate scratch-absence query. |
| Cancellation, SIGKILL, node loss, orphan Job/relay | Incomplete, revoke/fence, no next admission without actual old absence and exclusive cleanup. Owned process-group/PG recovery evidence. |
| Result writer rejection, audit trigger rejection, possible commit | Protected transaction remains authority; rejected projection rolls back result, timeout reconciles by separate protected readback. Never infer success from audit or retry with guessed identity. Real owner/role/trigger PG proof. |
| Retry/due/attention/API recovery | Keep current truth and successor ownership; no-result doesn't advance cadence, failure doesn't impersonate pass or hide age. No duplicate notification or new sender; existing due/attention/API owner tests. |
| Rollback during active attempt | Stop admission, revoke capability, prove termination and scratch cleanup; keep protected history/backups and current Compose usability. No live restore or grant widening. |

All source proof is distinct from actual installed topology. A render pass or an
unreachable test endpoint without a reachable permitted companion is not denial proof.

## D9. Lifecycle, ownership and rollback

1. Author and strictly validate this complete native proposal; open a draft PR.
2. Independent security/spec review of exact resulting SHA/tree and full source matrix.
3. Owner adoption of that exact candidate/disposition and named authority/weakening
   deltas only. Any changed head requires renewed review and appropriate exact adoption.
4. Root allocates cohesive future source implementation and compatibility work,
   preserving existing owners; representation -> producer/replay -> enforcement.
5. Actual real PG/bootstrap/ACL/client/cleanup proof and separately authorized bounded
   development generation/CNI/host-path/secret-placement evidence. Provisioning,
   deployment and production remain independent exact operations.
6. Only after those gates can the chart exclusion change. No sync/archive/merge,
   task completion or source publication here activates a port.

Rollback does not drop backup artifacts, private result authority or failure history,
restore dumps into live data, regress ordinary credentials to CREATEDB, re-enable
unfenced restart or make audit canonical. Disable only a future owned path after
actual quiescence, retaining current Compose recovery. Incompatible constraint/result
rollback needs an exact data-preserving amendment; no destructive downgrade is hidden.

bu-h40h2b.3 keeps recovery/API/attention and real end-to-end outcomes; its older exact
1ff636d44b590c0baddf7eb143ee23559d6d170e adoption does not cover moved PR4170
b98c89ee470616e3cc414de0865f67908dd0ec20 or future operations. Filtered-event sibling
is unapproved. bu-e1410/bu-lw18o production backup facts and bu-kqnum.8.8 BYPASSRLS
remain independent. Fleet offline authority, capture/ingestion/OwnTracks restore
survivors and current CLI/public-probe owners are not acquired by this proposal.

bu-viat6h.5's actual release row/literal-no-schema contradiction and .6's policy union,
tracker boundary and original dev smoke remain incomplete. No boot row satisfies
release evidence. bu-u03nw8 keeps all four canonical-writer outcomes; production
UNKNOWN. bu-r6wzr Option C forbids runbook preparation/prod host proof; no duplicate
question. bu-fqiym9 secrets, bu-hkjj9y accepted A/bu-fi32bc compensations, bu-ckkpz.3
tracker bridge and bu-wyram0 external hardening retain their authority. Held .14 keeps
all deletion desires and ledger/restore/egress/voice/observability survivors; no closure,
retarget, retirement or ephemeral/public-audit replacement follows from this proposal.

## D10. Primary API semantics

Upstream semantics are reference evidence, not installed-version proof:
[Kubernetes NetworkPolicy](https://kubernetes.io/docs/concepts/services-networking/network-policies/)
uses additive allow sets, needs an enforcing controller, permits own-node traffic and
has implementation-dependent rewriting/connection behavior.
[Downward API](https://kubernetes.io/docs/concepts/workloads/pods/downward-api/) does
not expose node boot ID; [Node API](https://kubernetes.io/docs/reference/kubernetes-api/core/node-v1/)
reports it through nodeInfo.bootID. [K3s networking](https://docs.k3s.io/networking/networking-services)
has a disableable policy controller; actual enforcement must be evidenced. These facts
explain W1 gaps; they do not certify P1's unimplemented issuer or fence.

## D11. Clause dispositions, inherited contradictions and source owners

The obligations ledger binds each original literal/hash to its exact source requirement,
scenario and clause ordinal, a specific intent and an actual ownership disposition.
A candidate relation names exact native scenarios where applicable; unchanged foreign
API/attention/backup clauses keep their own requirement/scenario. No keyword-selected
relay/proposal ID is evidence for source availability, healthy attention or cadence.
Carrier integrity, address completeness, author assessment and independent semantic
review are different receipts. The first two cannot certify behavioral equivalence.

Current ownership observations are source-only: `bu-h40h2b.3` is the blocked unassigned
backup/restore reconciliation owner at draft PR4170 head b98c89ee470616e3cc414de0865f67908dd0ec20;
its older exact adoption does not adopt that moved head or operations. Closed
bu-kqnum.8.4/.5/.6/.7 are historical scheduler/attention, API/UI and real-PG slices,
not current completion evidence. Existing attention-reader source originated in closed
bu-tdd4k.4/PR3090; no active reader-file claimant was found in the fresh census. Its
ordinary summary/list clauses stay foreign, while restore additions/conflicts remain
with h40 reconciliation. bu-ckkpz.3's active human-owned bridge is not transferred.
No blank assignee is invented as an active worker. Future source allocation remains ROOT's.

| Conflict / gap | Exact current source and governing disposition | Existing owner and remaining gate |
| --- | --- | --- |
| C-audit-reader | Legacy 005 `Restore-drill ledger read failure degrades only that field` names public.audit_log. Specific 006 isolated executor requires the private ledger/fixed reader as sole API/due authority; specific 005 `Restore-drill authority read failure degrades only drill facts` names it correctly. A public-audit read failure alone cannot govern the protected result. All four inherited conditional clauses retain literals/hashes, but are not declared simultaneously mandatory equivalents. | h40 reconciles the inherited reader scenario without weakening protected authority; this candidate edits no foreign source and creates no public reader. |
| C-public-pass | Legacy 006 `Drill succeeds` makes the public-audit pass readable through the API. Specific database-security-006 process-boundary clauses and 006 `Drill succeeds and closes a failure epoch` make public projection telemetry only. Preserve actual scratch/pass/API outcomes through the protected reader; retain the legacy literal as a conflict awaiting its owner's amendment. | h40 owns exact foreign reconciliation; no old source body or protected result is deleted here. |
| C-weekly-failure | Legacy 006 `Drill fails at any stage` promises the next weekly retry. Its specific requirement prose and `Failed result retries on recovery cadence` require 24h after fail, seven days after pass and immediate no-result. The port must retain that result-aware target; weekly failed retries are not an adopted weakening. | h40 resolves the conflicting inherited clause; no cadence implementation or schema change here. |
| C-cleanup-absence | The legacy failure scenario says scratch is unconditionally dropped. Current deployment-hardening-007 explicitly permits cleanup to fail but requires a failed drill; actual cleanup failure cannot prove absence. Preserve bounded cleanup attempt/non-pass and honest residual scratch, not an unconditional success claim. | h40 owns foreign wording and actual cleanup/recovery proof; no live cleanup or weakened success classification. |
| G-current-result-contract | Current init-db.sql is_due(integer) compares max(recorded_at) with one caller interval regardless of result. Its private ledger/latest_result exposes checked_at/result/detail; API backup_health and system readers use it. This source does not establish the specific 24h-failure, structured-stage/code or failing_since target. Current writer deliberately discards supplied diagnostic compatibility inputs. | h40's protected, compatible reconciliation/implementation gate remains open. Do not loosen the writer, invent lineage, use public audit or call closed historical slices actual proof. |
| G-attention-proof | Current attention reader distinguishes unavailable pool (source_available=false) from absent table (true); generic non-missing query errors are not converted by that path. Source inspection is not complete unreadable-pool/reader or attention-producer proof. | Existing reader owner retains generic endpoint behavior; h40 retains restore additions. Every foreign scenario and future executing seam remains required, not passed by this proposal. |

The repository precedence order binds doctrine/RFC contracts above specifications and
specifications above code. Code is supporting evidence, never authority to lower a required
outcome. The specific already-governing private-authority boundary excludes a legacy
public authority interpretation; the explicit result-aware target identifies the weekly-fail
contradiction. This does not claim a universal same-layer precedence rule or silently
amend a foreign scenario: h40 must reconcile its actual conflict at the proper source and
exact adoption process. Candidate adoption cannot close those foreign gaps, certify current
execution, adopt an unapproved sibling or authorize any operation.

[Kubernetes RuntimeClass](https://kubernetes.io/docs/concepts/containers/runtime-class/)
selects a configured runtime handler; the proposed interception/barrier semantics
are additional unimplemented requirements. The [CRI API](https://github.com/kubernetes/cri-api/blob/master/pkg/apis/runtime/v1/api.proto)
separates CreateContainer and StartContainer; that API shape alone is not evidence
of the complete trusted gate, CNI hooks or both-workload refusal contract.
