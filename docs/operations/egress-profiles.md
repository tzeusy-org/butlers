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

- **Identity is the port.** `<release>-egress-gateway-ingress` admits each workload's pods
  to its own `gateway_port` only, and the gateway is excluded from
  `<release>-namespace-ingress` so no wider rule unions onto it. No header can claim a
  workload: the gateway ignores `Proxy-Authorization` and every other identity header.
- **Per tunnel:** only `CONNECT host:port` for an exact declared pair, with any `Host`
  header agreeing; IP literals refused; the gateway resolves the name itself and refuses
  it if *any* answer is not globally routable unicast (private, loopback, link-local and
  metadata, CGNAT/tailnet, multicast, reserved), then dials only an address it checked,
  so a rebinding answer cannot redirect it; the client's ClientHello must carry SNI equal
  to the CONNECT host (no SNI, a split ClientHello or non-TLS bytes are refused). Plain HTTP
  proxying does not exist. Redirects are the client's next CONNECT, checked the same way.
- **Its own egress** (`<release>-egress-egress-gateway`): DNS, and `0.0.0.0/0` except
  `egressPolicy.privateCidrs`, `egressProfiles.gateway.deniedCidrs` and the tracker, on the
  external ports profiles declare (today 443).
- **Audit:** one JSON line per tunnel on logger `butlers.egress.audit`: workload, host,
  port, verdict, closed reason, byte counts, duration. Never payload, headers or tokens.
- **Outage:** an unreachable resolver or upstream answers `502` with
  `X-Egress-Refusal: dns_failed` or `upstream_unreachable`; a refused request answers `403`
  (or `405` for non-CONNECT) with its reason; a workload over 128 concurrent tunnels gets
  `503 busy`. With no gateway pod, connections are refused. In every case the caller sees
  an ordinary connection error and its own bounded retry and cursors apply; the gateway
  never falls back to a direct or internal route.

Workloads whose profile declares external hosts get `HTTPS_PROXY`/`https_proxy` set to
`http://egress-gateway:<gateway_port>` and `NO_PROXY` for cluster names; `HTTP_PROXY` is
never set. Workloads without external hosts get no proxy variables.

## Current limits

- **Not every client honours the proxy variables.** httpx does by default; the Home
  Assistant connector's aiohttp session does not (it needs no external host today), and
  Telethon does not (its profile is held). LLM CLIs' proxy support is unverified.
- **Connector outage behaviour is not yet proven** against a down gateway (cursor
  retention and bounded backoff, acceptance 3), and no CI check yet compares profiles
  with static base URLs in source (acceptance 4).
- **The external host lists are a seed inventory.** Static base URLs were collected from
  source; the LLM CLIs' provider endpoints (claude, codex, gemini, opencode) are
  unverified and need conformance before activation.
- **Internal peers are reachability only.** RFC 0038 S7 also requires both endpoints to
  authenticate; that is the existing application authentication, not this policy.
- `scripts/egress-firewall.sh` (compose) stays until every supported caller is replaced
  and its containment is shown preserved.

## Rollout

Not authorized by this source change. Each step needs its own authorization:

1. Prove connector outage behaviour and land the static base-URL coverage check.
2. Run per-workload conformance in the disposable `butlers-rehearsal` namespace: each
   workload reaches a declared service, and is refused an undeclared public host,
   private/link-local/metadata destinations, another workload's gateway port and an
   SNI/authority mismatch. Delete the throwaway pods and policies afterwards.
3. Owner-authorized dev enable: supply `egressProfiles.sites` for dev, disable or resolve
   held workloads, set `egressPolicy.enabled=false`, `egressProfiles.enabled=true`.

## Rollback

Set `egressProfiles.enabled=false` and `helm upgrade`. The per-workload and default-deny
egress policies are pruned and the tracker-egress policy renders again exactly as before,
so the tracker deny (`trackerEgress.enabled`) is never dropped. Rollback restores the
previous posture; it does not widen egress beyond it.

Under profiles a held workload cannot be enabled (the render fails) and a disabled one
renders no pods; any pod no profile names keeps the default deny. None is silently
admitted.
