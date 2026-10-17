# Key-absent control stage

Recommendation B (owner adoption 2026-10-15, bu-jnhxc5) permits a dev topology
stage before private signer custody and the complete RFC 0038 cutover. This
source change prepares that stage; it does not record deployment or acceptance.

`KEY_ABSENT_STAGE` runs today's Switchboard daemon in `butlers-control` on service
port 41100. Classification, durable ingestion, routing, notify and deliver retain
their existing handlers. Domains run in `butlers-up --exclude switchboard`.
`SWITCHBOARD_MCP_URL` selects control for canonical registered localhost:41100;
`BUTLERS_HOST` selects domains. Remote registered endpoints remain unchanged.
Dashboard process facts use each resolved endpoint's host.
Configuration loading also resolves an explicit canonical
`[butler.switchboard].url = "http://localhost:41100/mcp"` before notify,
heartbeat reconnect, fact-authority admission/recovery and runtime consumers
receive it. Registered path/query and explicit remote or custom endpoints stay
intact; an unsplit launch without cross-container settings keeps localhost.

The separate internal session port 41199 has no MCP server, goal admission,
service routes, signer or credential resolver. Only GET /health returns fixed
`{"status":"unavailable"}` with 503; every other HTTP route returns refused
404 and WebSockets close. Probes use service /health, never session /health.
A listener failure stops the control process and shutdown closes both listeners.

Trusted startup accepts only the explicit stage profile. Environment names
beginning GOAL_BROKER_SIGNING_KEY, named files under /run/secrets, or matching
credential key names in switchboard/public.butler_secrets refuse startup even
when empty or unusable. It enumerates names and queries existence only, never
reads private values, and refuses metadata errors. Deployments contain no GOAL
private key reference or fixture; no signer provisioning occurs. This assumes
trusted source/deployment operators and does not grant a malicious host a
security boundary. Existing unrelated runtime-probe public verification and
Dashboard signing remain separate contracts.

Exactly one control process receives one session slot and one domain process
receives four. This preserves owner bu-6y6f6m's existing aggregate maximum five
with unchanged process-local semaphores. Idle reservations are not borrowed.
Helm uses one replica and Recreate for each role; the canonical Compose launcher
selects either baked or hotreload instances, scaling the other role instances
to zero. Manual duplicate processes/replica changes violate this deployment
contract. Source/render assertions do not prove a live fleet's aggregate cap.

Dev Compose selects docker-compose.control-stage.yml and the appropriate
control-stage profile. Its session socket binds only the control-session
internal network alias, not the backend interface. Only control and the opt-in
control-session-health operator/test service join that network; no session host
is deployed yet. The health client performs only a bounded GET /health and
expects 503. Helm's separate ingress policy permits 41199 only from release
component control-session-health or future session-host identities. Ordinary
backend callers reach only the service interface. Actual CNI/network reachability
requires independent dev verification.

Production Compose omits the overlay and production Helm leaves
butlersControl.enabled=false: every daemon remains in the original process at
five. Activation must first stop the old unsplit process before starting either staged
reservation: Recreate is per Deployment and cannot enforce ordering across two
Deployments. Rollback of dev must likewise be an exclusive replacement: stop the staged control
and domain pair before starting the unsplit release at five, restore the
Switchboard MCP destination to butlers-up, and verify no staged listener/process
remains. Do not overlap split and unsplit processes against one database.
No rollout or rollback is executed by this source assignment.
The Compose launcher checks both dev Kubernetes runtime Deployments,
`butlers-up` and `butlers-control`, including observed replicas remaining after
desired replicas reach zero. Either active role refuses Compose startup before
any lifecycle action. Production retains its unsplit `butlers-up` ownership
check. A configured context must yield a successful ownership query for every
selected role: only confirmed absence or valid zero replica counts allow
startup. Timeouts, access denial, API/transport errors and invalid replica
metadata refuse before lifecycle actions. Hosts without kubectl or a selected
local Kubernetes context retain standalone Compose support; invalid local
configuration also refuses. This preflight is not an atomic cross-platform ownership lock: a
coordinated transition must stop both staged roles and confirm their pods have
terminated before Compose or an unsplit release starts. The reverse transition
must stop the complete Compose project before either Kubernetes role starts.
The existing override is solely for a non-live database.

`ISOLATED_CANDIDATE` remains a separate, closed trusted harness with no live
ingress, schedules or dashboard wiring and no reachable local legacy launcher.
It requires the full launcher and D18/adapter/helper/descendant custody proof
before any private-key mount. Its session endpoint serves only goals and refuses
health. `SWITCHED_RELEASE` requires all one-release owner gates, actual ingress
and obligation wiring, legacy deletion and domain verification. This stage
neither implements nor enables those profiles.

Required remaining evidence includes actual dev Compose and Kubernetes service
compatibility, session reachability/refusal, key-absence/startup refusal, the
owner Telegram smoke and aggregate concurrency <=5. It requires authorized
operational verification after independent source review. Stage evidence cannot
close the original seven-criterion task 2.0. Changes to the governing topology
oracle also require independently protected baseline authority and the supported
contract-evolution admission; a green test count cannot replace that proof.
