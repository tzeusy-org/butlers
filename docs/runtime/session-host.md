# Session host (dark implementation)

RFC 0038 D3 separates untrusted runtime processes from the control and domain
processes. This source increment is **not an enabled runtime or an eligible
runtime/model tuple**. Ordinary startup and the dedicated image default OFF;
OFF reads no config, credentials or evidence, and creates no socket or worker.
The existing spawner and key-absent control profile are unchanged.

## Process and credential boundary

`python -m butlers.session_host host --enable --config <file>` runs only the
host listener. Configuration selects a fixed runtime executable, broker session
endpoint, service TLS files and independently installed adapter evidence.
`Dockerfile.session-host` derives from an explicit reviewed application image
digest; it installs no dependencies or keys. Its entrypoint clears inherited
environment, leaving only PATH and disposable HOME. The Compose and Helm
commands repeat that clearing explicitly, including the proof init container.
Neither host nor gateway mounts a database credential, service-account token,
domain runtime home, connector credential, signing key or secret-store resolver.

The private length-prefixed JSON protocol authenticates actual negotiated TLS
peer certificates before reading application data. The runner certificate has
exact URI `spiffe://butlers/control/session-runner` and client-auth EKU; the host
has `spiffe://butlers/session-host`, server-auth EKU and the configured DNS SAN.
Same-CA dashboard/domain certificates are insufficient. No operational CA,
certificate or model credential is supplied by this change.

A closed request carries invocation UUID, runtime/model/provider, bounded prompt
and system text, timeout, one model credential and broker bearer token. It cannot
choose argv, environment, evidence, endpoints or staging paths. Frames are
bounded to 262144 bytes before JSON parsing, with duplicate/nonstandard JSON
refused. The full serialized frame bound also applies when individual fields
fit their own limits. Replies carry UUID-bound sequential typed text chunks and
one completion; no partial reply is accepted after refusal/disconnect.

The goal-session Codex/OpenAI API-key profile uses
fixed deny-tool flags, read-only native sandbox settings and broker-only MCP
configuration. Every tuple still refuses unless the exact executable/profile
matches installed, independently reviewed conformance evidence. A schema-valid
fixture or local parser test is **not adapter, model or provider-hosted-tool
proof**. Other adapters/auth modes remain ineligible; OAuth staging is not
silently treated as API-key authority. A separate host-side OpenCode/OpenAI
model-function profile stages a closed deny-all tool/MCP configuration and
accepts text output only. Its fixed one-step configuration is not native proof
of one provider turn or absence of provider-hosted tools. The current shared
network composition also does not establish its required provider-only egress;
that profile remains ineligible until its exact image and network pass.

The existing root Bubblewrap supervisor leases a separate outer UID, constructs
a fresh filesystem/PID namespace and stages exactly three files. Only the
registered CLI input closure, fixed PATH, staged authority and broker config
enter the child. Result text is accepted only after namespace-PID1/descendant
termination is proven. Cancellation, timeout and client loss cancel the owned
launch once and join its original containment cleanup. Incomplete cleanup
retains/quarantines the invocation; it never earns output or identity reuse.

## Network composition

The host has one network peer: `session-egress`. The gateway accepts only:

- Native TLS with an exact configured provider SNI; an initial ClientHello is
  bounded to 16 KiB across TCP chunks **and TLS records** before forwarding.
  Upstream DNS results must all be global addresses. TLS remains end-to-end;
  the gateway has no TLS key or model authority.
- Fixed broker TCP forwarding to control port 41199, never service port 41100.
- Opaque runner-to-host mTLS relay from control port 41300 to host port 41299.
- Nonrecursive DNS: configured provider names and `broker.session.internal`
  resolve only to the gateway; unknown names/subdomains get NXDOMAIN. There is
  no DNS forwarding, arbitrary CONNECT proxy or caller-selected destination.

Model/broker relays have a 340-second aggregate lifetime; the control relay has
a 942-second bound covering the existing 900-second login, 32-second containment
settlement and ten seconds of transport reserve. All retain the 32 MiB traffic bound,
64-connection fail-fast capacity and bounded socket cleanup. Model ClientHello
and upstream connect phases have separate 3/5-second bounds within that lifetime.

Compose's optional `docker-compose.session-host.yml` overlay requires Docker
28+ isolated gateway mode on its internal network. **Internal alone is not
sufficient:** an ordinary internal bridge exposes host services at its bridge
address. The host joins only the isolated network; only the gateway joins the
provider/control networks. The control relay binds the gateway's separate
control-facing address. No host port is published. See Docker's
[isolated gateway mode](https://docs.docker.com/engine/network/port-publishing/#gateway-modes).

Helm defaults `sessionHost.enabled` false. Enabled rendering requires an immutable
image digest, existing service-identity Secret, reviewed proof ConfigMap and
explicitly allocated gateway Service IP. The host's ONLY DNS is that Service IP;
its egress policy admits only gateway 443/41199/UDP53. The gateway admits control
41300, host 443/41199/UDP53, fixed control/host destinations, DNS to kube-system,
and global TLS. The existing broad tracker-egress policy excludes **both** new
components: NetworkPolicies add permissions, so omitting this exclusion would
reopen domain/database access. Dashboard-to-host/relay ingress is absent.

The proof init container copies bounded root-owned read-only ConfigMap
projections into real single-link files in a dedicated memory volume; the host
mounts that snapshot read-only. Ordinary runtime loading refuses symlinks.
Neither init nor host creates conformance evidence or widens a tuple's scope.

## Implementation notes and proof limits

The host factory shares one platform and one identity allocator across goal,
model-function and CLI-login profiles. A live or quarantined UID cannot be
leased independently by another profile. Its fixed staging root is
`/tmp/session-host-stage`: the supplied Compose tmpfs and Helm memory-backed
volume bound that storage to 256 MiB. The server requires root-owned mode 0711
on that root, with private mode 0700 invocation directories; the container root
filesystem remains read-only.

Goal and model-function invocations bind their whole staged HOME read-only,
protecting configuration and its ancestors against write, chmod and path
replacement. Only their disposable `.cache` subtree is overlaid read-write
from the same bounded stage; `/tmp` retains the existing isolated scratch
mount. Ordinary CLI login keeps its separate writable-HOME artifact contract.
Both model profile digests include this filesystem layout, invalidating prior
profile evidence. Actual native configuration attacks, scratch writes, runtime
compatibility and provider-hosted tool absence remain unexecuted conformance
obligations, not outcomes inferred from a launch-plan test.

Credential-volume executor futures remain owned after a request cancellation
or timeout. Shutdown stops admission and joins the actual workers under the
same absolute 32-second host/auth shutdown deadline. Repeated cancellation or
close does not renew that deadline or cancel a worker wrapper. A filesystem
worker that does not finish remains retained and the volume stays closed to
new work; shutdown returns explicit incomplete cleanup. Python cannot forcibly
terminate such a thread, and a missing reply never proves a successful CAS.

Host CLI login has a typed TLS protocol with separate dashboard/control peer
roles, bounded streaming and containment-before-finalization. Software controls
exercise two real TLS hops through the existing `CLIAuthSession` consumer;
they do not run a provider login or native sandbox. The host credential-volume
adapter accepts only the fixed Codex OAuth document, validates file ownership,
mode and links, and uses a bounded lock plus digest compare-and-swap and atomic
replacement. The control-side adapter preserves the database authority CAS, so
an old rotation cannot overwrite a newer dashboard login or recreate deleted
authority. A lost reply requires independent readback.

The existing control service now has an optional private WebSocket route at
`/internal/session-host/cli-auth`. It carries encrypted TLS records; Python's
`SSLObject` performs the real inner mTLS handshake. Neither WebSocket headers nor
JSON fields establish identity. This keeps the existing plaintext MCP/service
callers unchanged without adding a third control listener. The dashboard's
existing sandbox factory selects this route only with the immutable deployment
selection `BUTLERS_SESSION_HOST_CLI_AUTH=1`. OFF opens no configuration or TLS
material. An unavailable enabled configuration refuses CLI auth without a local
launch fallback; it does not remove ordinary deterministic service routes.

The dashboard reads `/etc/session-host/dashboard-auth.json`, containing exactly
`hostname`, `port` and `tls` (`ca`, `certificate`, `private_key` absolute file
paths). The control process reads `/etc/session-host/control-auth.json`, containing
exactly its server `tls` and a `host` object with the same endpoint fields. These
configuration files must be single-link regular files owned by their process,
mode 0444, at most 8 KiB. Dashboard configuration is frozen on first use; control
configuration is frozen during startup. Changes require process restart.
The dashboard endpoint is the existing control service on port 41100; the host
endpoint is the fixed egress relay on port 41300. Certificates must cover those
configured DNS names as well as the exact purpose URI and EKU: dashboard client
`spiffe://butlers/dashboard/cli-auth`, control server
`spiffe://butlers/control/cli-auth`, and the existing runner/host identities for
the second hop. Operational files, identities and activation are not provisioned
by this source change. No broker signing key is reused.

Both hops retain the closed login protocol. Inner TLS chunks are bounded before
feeding OpenSSL; aggregate received/sent records and decrypted input are each
bounded to 8 MiB, with at most four admitted connections and five-second
authentication. The client disables proxy/environment credentials, cookies and
redirects. A normal final response drains before TLS close-notify and WebSocket
closure. The daemon's existing ASGI lifespan owns relay shutdown; repeated close
joins one retained settlement with a 34-second absolute bound. Incomplete cleanup
is refused, never a credential or containment receipt.

The existing staged device-auth persistence consumer selects remote projection
when the dashboard transport is enabled. After its existing credential-store CAS
commits, it requests the closed `codex_sync` operation without sending credential
bytes or paths. Control reads its selected current system-global authority and
uses its one shared sync owner to reconcile the host's fixed Codex volume. A
missing authority or unacknowledged projection returns failure without creating
a dashboard-local credential file; the prior database commit remains factual.
The authenticated dashboard cannot substitute a credential payload for that
server-held authority. OpenCode has a separate fixed `opencode-auth.json` and
lock. Its OAuth and API entries are rebuilt together from one selected-pool
query; local-first fallback and an old volume entry are not authority. Confirmed
absence removes its entry, while a failed query or malformed sibling refuses
without erasing the prior volume. Codex absence preserves its prior fixed-file
projection but refuses preparation; it cannot bootstrap a deleted database
value from that file or confer launch authority.

The optional Helm `sessionHost.cliAuthEnabled` selection defaults false. When
selected, the host gets a dedicated 1 MiB memory-backed credential volume and
the fixed `/run/session-credentials` mapping. An environment-cleared init command
prepares only an empty owned 0755/0777 directory as 0700, or accepts an already 0700
directory. Links, wrong ownership and an existing permissive tree refuse; the
running volume adapter continues to require exact 0700/0600 metadata. No
credential is generated or imported by the initializer. The volume is disposable;
the selected control credential store remains the authority.

The explicit `sessionHost.cliAuthTransportEnabled` switch additionally wires
control/dashboard consumers; it defaults false and requires both host switches
and the control workload. Two purpose-specific provisioned Secrets are required:
`controlAuthSecretName` and `dashboardAuthSecretName`. Each contains its fixed
`<role>-auth.json`, `ca.crt`, `tls.crt`, and `tls.key`; control additionally has
`host-ca.crt`, `host.crt`, and `host.key` for its distinct outgoing runner identity.
The JSON endpoint schemas above still apply, with TLS paths fixed below
`/etc/session-host/`. Control's outgoing TLS paths use the `host-` filenames.
The Secret source keys are 0400 and visible only to an init snapshot. That init
copies bounded regular source files to a private memory volume, config/certs
0444 and private keys 0600; the serving mount is read-only. A repeated init may
confirm the exact same snapshot but refuses changed material; changing material
requires a fresh volume and process restart. It generates no identity or keys.
The optional `docker-compose.session-host-auth.yml` overlay supplies the same
two init snapshots and selected host credential tmpfs. It must follow the normal
session-host overlay with the `session-host-auth` profile and preinstalled
purpose-specific input directories. The ordinary launch remains OFF.

Configured startup, device-auth persistence, provider listing, API-key
save/delete and probes select transport before local binaries or token paths.
A store commit followed by unavailable projection is reported as that partial
outcome, never silently restored into a dashboard-local file. Codex health is
the existing fixed parent HTTP probe using selected DB authority. OpenCode OAuth
status resolves the registered non-model `auth list` command in the host with
the immutable sandbox profile. Its retained worker must settle before any
output can be accepted, including cancellation. Probe log/cache/audit writes
share the selected authority's value-fenced transaction. OpenCode Go status only
means a key is configured; its model-producing test is refused until a genuine
adopted D18 caller/profile is bound, not relabelled as a read-only command.

With transport ON, the legacy runtime reconciliation consumer projects the
selected Codex authority to the fixed host volume before touching any local
file. It deliberately returns no local-spawn authority; its local finalizer
cannot turn a dashboard file into a settled host rotation. Actual eligible
OAuth goal staging, per-operation post-settlement rotation, and the real
runner/retained D18 caller retarget remain unfinished. They are required source
integrations, not requirements to activate or provision this dark source.

The real service-route software control uses ephemeral certificates, a controlled authority and sandbox backend;
it is not a provider login, deployed network, native sandbox or credential-volume
activation proof. An empty trusted proof index grants no model-runtime eligibility.
The configured control factory refuses missing authority before opening transport
material. Each login then rechecks its selected owning store before forwarding;
unavailable or malformed authority refuses. An absent credential permits initial
enrollment. This production boundary is separate from the low-level relay ABI,
which transports authenticated sessions without selecting a credential store.

`python scripts/check_session_host_boundary.py` checks the checked-in Compose
authority composition without reading any mounted content. Add
`--rendered-kubernetes <render.yaml>` for actual Helm-rendered pod composition.
It rejects ambient env/env_file, broad credential mounts, direct host network
routes and entrypoints that omit environment clearing. This is source admission,
not a claim about a deployed environment. An eventual dev check must list only
environment variable NAMES and record actual endpoint denials/allowed paths.

Exact-image Bubblewrap checks remain opt-in. The Helm source requests a
node-installed sandbox seccomp profile and root supervisor capabilities; it
never requests privileged/host-PID mode. The current documented k3s node's
masked-proc/user-namespace limitations may prevent this profile from running.
Source rendering does not prove node compatibility. No tuple may be installed
until the actual node/image proves namespace setup, sibling filesystem/process
isolation, resistant-descendant death and the **whole effective CNI egress union**
(including node-local exceptions), not merely the rendered policy. Deployment
and any node provisioning remain separate, unauthorized operations.

The full original eight acceptance criteria remain open. In particular, this
increment does not yet complete dashboard CLI login/Codex sync against a
deployed host credential volume. The private service relay, dashboard sandbox
selection, host credential CAS and model-function profile source are implemented,
but actual OAuth login,
rotation, native adapter execution and production transport integration remain
unproved. The separate
`bu-m1u7zy.2` owns D18 caller/relay retargeting, not these host-side obligations.
Actual provider-hosted-tool/catalog conformance and dev isolation/network
controls have not run. No ingress, schedule, goal authority, model call or live
runtime was activated. The full source needs a different-author review before
publication and terminal hosted/protected delivery afterward.
