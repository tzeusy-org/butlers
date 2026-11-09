# Workload Egress Profiles

Each chart workload's network egress is declared once, in
[`deploy/egress-profiles.toml`](../../deploy/egress-profiles.toml), under RFC 0038 S7
(`about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md`) and owner
decision bu-l8y3fn = B: egress widens only by a reviewed git change to a workload's
profile. There is no runtime widening tool, API or model-supplied header. Tracked as
`bu-dhloi1.15`.

The chart option `egressProfiles.enabled` is **off by default and off in every
environment**. Turning it on anywhere is an operational act this document does not
authorize (see [Rollout](#rollout)).

## The profile file

One `[workloads.<component>]` table per `app.kubernetes.io/component` the chart renders.
The file's header documents each key. In short:

| Key | Meaning |
| --- | --- |
| `internal` | Exact chart peers: destination component, port or `port`..`end_port`, protocol. |
| `sites` | Peers outside the chart (Postgres, the OTLP collector, Home Assistant, the off-node backup device). The profile owns the port; the deployment supplies only the location. |
| `external` | Exact public `hostname:port` entries, reachable only through the shared gateway. |
| `gateway_port` | This workload's port on the gateway. Unique per workload: it is the workload's identity there. |
| `dynamic` | Configured destinations deliberately **not** admitted, with the reason. |
| `held` | A reason the workload cannot be enforced yet. A held workload admits nothing. |

The parser (`src/butlers/egress/profiles.py`) refuses IP literals, wildcards, uppercase
or trailing-dot names, single-label names, cluster/LAN/tailnet suffixes (`.svc`,
`.cluster.local`, `.local`, `.lan`, `.internal`, `.home.arpa`, `.ts.net`), unknown keys,
a gateway port shared by two workloads, a held workload that also declares routes, and
an internal peer that is not a declared workload or is the gateway itself (the gateway is
reachable only through `gateway_port`). The parser checks spelling, not resolution: a
public-looking name that resolves to a private address (for example a wildcard-DNS
service) passes it. Refusing those at connect time, including DNS rebinding, is the
gateway's job.

After editing the TOML, run `make egress-profiles` and commit both files. The chart
reads the rendering, `deploy/helm/butlers/files/egress-profiles.json`;
`tests/config/test_egress_profiles.py` fails while it is stale.

The still-held tool-less analysis host (bu-l8y3fn q1) has no profile: it is absent, not
implicitly admitted.

## What the chart renders

| `egressProfiles.enabled` | Egress policies |
| --- | --- |
| `false` (default) | As before (the only render change is a component label on the log-cleanup pod): the one `<release>-tracker-egress` policy when `trackerEgress.enabled` or `egressPolicy.enabled` (see [Kubernetes deployment](kubernetes-deployment.md#egress-policy-opt-in)). |
| `true` | `<release>-default-deny-egress` selecting every pod with no rule, plus one `<release>-egress-<component>` per rendered workload. `<release>-tracker-egress` is not rendered. |

Egress allow rules union, so the profiles replace the tracker-egress policy rather than
sitting beside it: its `namespaceSelector: {}` and `0.0.0.0/0` rules would otherwise stay
open. For the same reason the chart refuses `egressProfiles.enabled` together with
`egressPolicy.enabled`. Ingress policies are unchanged.

Each workload policy admits exactly:

- cluster DNS (`kube-system` pods labelled `k8s-app: kube-dns`, port 53 UDP/TCP);
- each `internal` peer, as a same-release pod selector on its declared ports;
- each located `sites` peer on its declared port;
- the gateway pods (`app.kubernetes.io/component: egress-gateway`) on `gateway_port`,
  when the workload declares external hosts.

Nothing else is reachable. In particular the tracker stays unreachable: no profile can
name it, enabling profiles requires `trackerEgress.deniedCidrs` (in every environment,
not only where `trackerEgress.enabled`), and a site `/32` equal to one of its entries is
refused. Site CIDRs must be written with canonical octets, because Kubernetes reads
`01` as `1`.

Rendering fails, naming the workload or site, when:

- a rendered workload has no profile (the workload list is `butlers.egressWorkloads` in
  `_helpers.tpl`; the chart test proves it matches the rendered pods);
- a rendered workload's profile is `held` (today: `connector-telegram-user`, because
  Telethon dials Telegram data centres by address and ignores proxy environment);
- a required site peer has no location, or a location is malformed: not exactly one of
  `namespace` or `cidr`, the release's own namespace, a CIDR other than one canonical
  IPv4 `/32`, or a tracker, metadata, loopback, unspecified, multicast or reserved
  address;
- `trackerEgress.deniedCidrs` is empty, or `egressProfiles.enabled` is not a boolean.

`tests/scripts/test_chart_egress_profiles.py` evaluates the rendered union for every pod.
That is rendered intent, not enforcement evidence.

## Site locations

Profiles name sites; deployments locate them under `egressProfiles.sites`. Never commit
real LAN or tailnet addresses. Example with synthetic values:

```yaml
egressProfiles:
  sites:
    database: {namespace: postgres-dev}
    otlp: {cidr: 198.51.100.9/32}
    home-assistant: {cidr: 198.51.100.10/32}
    offsite-backup: {cidr: 198.51.100.11/32}
```

A namespace location may add `podLabels` to narrow it. A required site with no entry
fails the render; an `optional` one is simply not admitted.

## The shared gateway

`src/butlers/egress/gateway.py`, rendered by `templates/egress-gateway.yaml` only with
`egressProfiles.enabled`: a Deployment `egress-gateway` (`egressProfiles.gateway.replicas`,
default 2, no service-account token), a Service with one port per profiled workload, and
the profile rendering mounted from a ConfigMap (a checksum annotation rolls the pods on
change).

- **Identity is the port.** Only the workload's pods may reach its dedicated listener.
  No header can claim a different workload. CONNECT and opaque upgraded TCP are refused.
- **Request execution:** an explicit internal HTTP/1.1 hop targets
  `/_butlers/egress/v1/https/<host>/<port>/<path>?<query>`, with hop Host
  `egress-gateway:<listener-port>`. The exact profile-approved origin supplies both
  outbound Host and certificate-verified TLS server name. The gateway resolves one
  absolute public IPv4 answer set, rejects the entire set if any address is forbidden,
  then dials a validated numeric address without resolving again. Duplicate authority,
  ambiguous framing, forwarding/identity headers and unsupported upgrades refuse.
- **WebSockets:** the distinct `/_butlers/egress/v1/wss/<host>/<port>/<path>?<query>`
  route accepts only an HTTP/1.1 GET with a valid version13 WebSocket handshake,
  no HTTP body, and the same exact hop Host and destination binding. It forwards
  the original key and offered subprotocols through gateway-owned verified TLS.
  Before exposing101 it validates origin Upgrade, Connection, Accept and selected
  subprotocol. Every300..399 refuses exactly as on the HTTPS route. Compression
  extensions are deliberately not negotiated: compliant clients use ordinary
  frames. Both directions validate masking, RSV/opcodes, canonical lengths,
  fragmentation, incremental text UTF-8 and close controls. Valid frame bytes,
  including masks, ping/pong and close reasons, pass unchanged. This is a framed
  WebSocket execution route, with no generic upgraded byte-stream route.
  A valid non101, nonredirect provider response retains its status, application
  headers (including retry metadata) and bounded streaming body through the same
  HTTP response path. An authentication error or rate limit therefore remains
  distinguishable from a gateway refusal and never becomes an upgrade.
- **Response boundary:** h11 parses complete bounded origin headers before exposure.
  Every300..399 (including304 and same-origin redirects) returns a fixed empty403
  `X-Egress-Refusal: redirect_refused`, with no Location or redirect-target dial.
  Upload and response progress run concurrently, so an early final response stops
  and joins the remaining upload. Nonredirect errors and SSE retain streaming.
  Origin-supplied reserved refusal metadata is discarded.
- **Resources:** headers8KiB/10s; streamed16KiB chunks;1GiB per direction;
 300s inactivity and3600s total exchange. The process admits32 exchanges globally,
  with an additional128 per-workload cap held through bounded task/writer teardown.
  The response-header budget starts on its first byte or completed upload, whichever
  comes first; a still-progressing upload does not start an unconditional response
  timer. Streaming avoids whole-upload buffering
  against the256MiB pod limit. Known Gmail25MiB/base64, voice20MiB and notification
  document50MiB limits fit; larger configurable/model/dependency transfers require
  separate compatibility closure. Busy gives503; DNS/dial failures502. No fallback.
  WSS shares those admission, total-time, byte and cleanup limits, with a64MiB
  message bound and16KiB payload reads rather than whole-frame buffering. Activity
  in either direction keeps an otherwise quiet reader alive. A close is relayed
  with at most10s for the opposite close; EOF, malformed frames, cancellation and
  idle/total expiry stop and join both directions before releasing admission.
  After101, failures close the connection and never inject an HTTP error into it.
- **Privacy:** the gateway transiently receives approved request headers/query/body.
  It is a credential-bearing execution boundary when activated. Audit contains only
  validated origin/workload, closed verdict/reason, body byte counts and duration;
  never paths, payloads, headers or exception text. Real credential transit/activation
  remains gated separately.

Profiled workloads receive paired nonsecret `BUTLERS_EGRESS_MODE=request-v1` and
`BUTLERS_EGRESS_GATEWAY=http://egress-gateway:<port>`. Shared external HTTPX/SDK
construction selects the request transport; incomplete configuration refuses.
Internal/site-local clients remain separate. Off-mode preserves existing caller
proxy behavior. No ambient proxy variable forwards into selected mode.

The spawner forwards only this validated deployment pair into native CLI children,
including QA override and healing paths. Declared credential variables or child
override dictionaries cannot replace it. This does not forward other daemon
environment values. Patched Codex, Gemini and OpenCode factories consume the pair
at their terminal transport seams; their complete artifact/native coverage remains
separately required.

For unmodified native Claude, the adapter maps the supported
`ANTHROPIC_BASE_URL` to the request prefix using the original HTTPS host, port and
base path. It preserves API-key lookup, custom auth headers, model/resume arguments,
strict MCP configuration and the native tool surface. A custom Host must match
the logical API authority; the internal request uses the gateway Host and a
controlled `Connection: close`. Selected children receive no conventional proxy
environment override. Off-mode retains the existing native environment.
This supported API-base setting is a partial transport interface. Native API-key
inference and the fixture's actual bare-mode Bash/Edit/Read operations have owned
synthetic evidence; the separate native `/api/hello` check is refused for its
internal header shape. Whole native auth/MCP/other configured operation coverage
and actual network policy enforcement remain unearned. No tool is removed or
replaced to obtain the inference result.

## Structural source coverage

`make check-egress-profile-coverage` is a required local/default and hosted guard.
It reads `scripts/egress-profile-coverage.json`, the workload profiles and production
source without importing application modules or using DNS, credentials or a cluster.
Its positive result is `SOURCE_ACCOUNTING_ONLY`, never runtime conformance.

Discovery inventories non-docstring Python URL literals, URL/URI/HOST/ENDPOINT
configuration names and `base_url` factory expressions under `src/butlers` and
`roster`; URL-valued roster TOML; and URL-bearing noncomment deployment scalar
lines, including Helm templates and Compose. Tests/testing helpers are outside this
production boundary. Each discovered declaration needs an explicit disposition,
real workload reachability and owning explanation. New, removed, duplicated or
changed declarations refuse. Internal peers, site locations, browser callbacks,
OAuth scope identifiers, help references and build inputs are distinguished from
terminal external bases. Conservative candidates do not grant destinations.

Dynamic Python declarations additionally name their actual owning function/class
and bind its AST plus the exact configuration identity to `profile.dynamic.source`.
The reviewed upstream inventory names 21 finite resolver families across Codex,
Gemini, GoogleAuth, GenAI and OpenCode: provider/model configuration, separate
auth/ADC/STS/AWS/impersonation endpoints, CodeAssist, upload-returned URLs, HTTP/WS,
model-catalogue refresh and plugin/MCP configuration. Each names an actual source
file/symbol, configuration keys, workload and resolution policy. CLI `dynamic`
metadata grants no origin. Three additional local Claude records bind the supported
`ANTHROPIC_BASE_URL` mapping at `src/butlers/egress/claude.py:child_environment`,
`ClaudeCodeAdapter.invoke` auth/invocation and `build_config_file` MCP configuration.
Their explicit unresolved native-auth/tools status grants no destination; opaque
native tool/network behavior remains unproven rather than inferred from inference-base
coverage.

Vendor locator bytes were reviewed in the exact pinned public archives/replayed
patches. CI binds archive/commit/tree identities and terminal patched-file hashes;
it does not fetch or parse the full upstream source archive. Every tracked vendor,
shared executor, toolchain and model/plugin build input is digest-bound so a change
requires a fresh named resolver review. The complete OpenCode catalogue identity
includes all 235 providers and 8939 models; their configured API values are dynamic,
not a list of granted hosts. Build catalogue/logo downloads are build inputs, not
runtime route declarations. Workload/module topology changes also invalidate the
reviewed reachability map. Keep the canonical parser's rendered JSON synchronized.
The Dashboard readiness canary is an actual internal outbound resolver, bound to
`FleetReadiness._route_canary` and its roster-selected Switchboard target. Its
metadata records the Dashboard workload and existing control peer; it grants no
additional route.

Inventory schema `butlers.egress-profile-coverage/v2` stores bundle and topology
identities as explicit `{"sha256": "<digest>"}` records. These readable public
content hashes remain exact source checks, distinct from credential assignments;
unknown fields, wrong algorithms, types or digest bytes fail the same comparison.
Secret scanning and its real-shaped credential detection remain unchanged.

There is deliberately no automatic inventory refresh or classification command.

The guard reports compatibility gaps separately from structural completeness:
OneMap search/token and Nominatim defaults currently have no route, as do the named
site-configured transcription/S3/Prometheus/SimpleFIN/deadman declarations without
an adequate peer/destination. Several upstream defaults (for example CodeAssist,
Vertex, STS/impersonation and catalogue refresh) are also absent from some workload
profiles. Their required functionality remains incomplete and cannot be enabled
merely by accepting this inventory. Arbitrary provider/model/plugin/credential-file
results, held workloads and the absent analysis host remain denied. Review any
necessary route change independently; do not widen profiles to make this guard pass.

For a source/config change, review the discovered declaration and actual caller
before editing its inventory row. For an upstream revision/patch, inspect the
named resolver and any newly introduced destination families before updating its
archive/locator/bundle pins. Preserve the documented denied/held behavior and run
the owning checker/profile controls. A new resolver family requires an explicit
family entry; a file hash alone is not an explanation. These finite discovery
boundaries are not a universal parser for computed endpoints, dynamically loaded
code or opaque binaries; actual supported callers and configured/native tool
behavior still require their separate runtime/network evidence.

## Current limits

- **Vendor/runtime compatibility is incomplete.** The Python request transport does
  not prove the four pinned CLI/auth adapters, native WS parity, image dependency/plugin/model
  closure, or actual per-workload CNI/principal/outage/cursor behavior. Those are required
  subsequent stages; no opaque TLS success or permanently held required caller replaces
  them. No real rollout/credential activation is authorized by source controls.
- **The external host lists retain known compatibility gaps.** Structural source
  accounting above does not establish caller compatibility or connector outage behavior;
  those still require their named runtime evidence.
- **IPv4 only.** The gateway resolves and dials IPv4; a declared host with no IPv4
  address is unreachable.
- **Internal peers are reachability only.** RFC 0038 S7 also requires both endpoints to
  authenticate; that is the existing application authentication, not this policy.
- `scripts/egress-firewall.sh` (compose) stays until every supported caller is replaced
  and its containment is shown preserved.

## Rollout

Not authorized by this source change. Each step needs its own authorization:

1. Prove connector outage behaviour and preserve the structural base-URL coverage guard.
2. Run per-workload conformance in the disposable `butlers-rehearsal` namespace: each
   workload reaches a declared service, and is refused an undeclared public host,
   private/link-local/metadata destinations, another workload's gateway port and an
   SNI/authority mismatch. Delete the throwaway pods and policies afterwards.
3. Owner-authorized dev enable: supply `egressProfiles.sites` for dev, disable or resolve
   held workloads, set `egressPolicy.enabled=false`, `egressProfiles.enabled=true`.

## Rollback

Rollback is a separately authorized operation, not permission to restore the broad
predecessor policy. Keep `egressProfiles.enabled=true` and
`egressPolicy.enabled=false` throughout the hold. Flipping profiles off while workloads
remain runnable removes default-deny and restores broad legacy egress (or no egress
policy when both legacy switches are off). That is not a security-preserving rollback.

1. Stop admission and upstream producers for the affected release, including queued
   invocations and external controllers that could create or restart pods. Preserve
   content-free failure/audit receipts and connector cursor state; do not replay effects.
2. Render and inspect the same release with the existing site/profile values and this
   held overlay, then apply it only under the operation's explicit authorization:

   ```yaml
   suspend: true
   egressProfiles:
     enabled: true
   egressPolicy:
     enabled: false
   ```

   The chart's `suspend` switch sets every Deployment to zero replicas and suspends
   CronJobs. It does not terminate already running Jobs/Pods or stop an external
   autoscaler/controller. Drain or terminate those affected workloads under the same
   authorization; positively verify zero affected running/pending pods, no active Jobs,
   all CronJobs suspended and no recreator/admission path. Do not infer drainage from a
   successful Helm command or from desired replicas alone.
3. Retain the rendered default-deny and per-workload policies, profile/configuration
   receipts and existing internal constraints while drainage is in progress and for the
   entire hold. A remaining or unexpectedly recreated pod still needs containment.
4. Keep the release held until a reviewed, separately authorized safe replacement has
   passed policy-union and real network conformance. Do not remove restrictive policies
   or re-enable workloads as an automatic rollback step. If containment itself cannot be
   retained, keep admission disabled and workloads stopped; resolve that boundary before
   any replacement or resumption.

`trackerEgress` alone preserves the older tracker exclusion, not per-workload default-deny.
Its accepted opt-in predecessor semantics and default values are unchanged by this source
correction. The chart tests cover the held render and its policy union, not CNI behavior,
actual drainage, stopped admission or an executed rollout/rollback.

Under profiles a held workload cannot be enabled (the render fails); disabled optional
workloads render no pods, and `suspend` scales rendered workloads down. Any pod no profile
names keeps default-deny. None is silently admitted.

The source inventory also pins the compiler, backend and runtime artifact manifests.
These build inputs account for immutable public dependencies; they grant no runtime
destination. OpenCode's installed default-plugin seed reads the complete local graph
only for a fresh, selected request for the exact default plugin, and settles that
copy before releasing the install lock. Custom plugins and off-mode retain their
existing resolution paths and profile restrictions. Updating the bundle identity
requires checking the named upstream resolver files as well as the changed build
inputs. Source accounting does not establish native operation, image or CNI parity.
