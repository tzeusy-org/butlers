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
a gateway port shared by two workloads, and a held workload that also declares routes.

After editing the TOML, run `make egress-profiles` and commit both files. The chart
reads the rendering, `deploy/helm/butlers/files/egress-profiles.json`;
`tests/config/test_egress_profiles.py` fails while it is stale.

The still-held tool-less analysis host (bu-l8y3fn q1) has no profile: it is absent, not
implicitly admitted.

## What the chart renders

| `egressProfiles.enabled` | Egress policies |
| --- | --- |
| `false` (default) | Unchanged: the one `<release>-tracker-egress` policy when `trackerEgress.enabled` or `egressPolicy.enabled` (see [Kubernetes deployment](kubernetes-deployment.md#egress-policy-opt-in)). |
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
name it, and a site `/32` equal to a `trackerEgress.deniedCidrs` entry is refused.

Rendering fails, naming the workload or site, when:

- a rendered workload has no profile (the workload list is `butlers.egressWorkloads` in
  `_helpers.tpl`; the chart test proves it matches the rendered pods);
- a rendered workload's profile is `held` (today: `connector-telegram-user`, because
  Telethon dials Telegram data centres by address and ignores proxy environment);
- a required site peer has no location, or a location is malformed: not exactly one of
  `namespace` or `cidr`, a CIDR other than one IPv4 `/32`, or a tracker, metadata,
  loopback or unspecified address.

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

## Current limits

- **The shared gateway is not deployed yet.** Workload policies point at
  `egress-gateway` pods that no chart template renders, so enabling profiles today denies
  every external host. The gateway (CONNECT proxy with per-port workload identity,
  SNI/authority agreement, IP-literal, private-address and rebinding refusal, content-free
  audit, two replicas) is the next `bu-dhloi1.15` package.
- **Clients do not use the gateway yet.** Workloads need proxy configuration
  (`HTTPS_PROXY` to their own gateway port, `NO_PROXY` for internal peers). httpx honours
  it by default; the Home Assistant connector's aiohttp session and Telethon do not.
- **The external host lists are a seed inventory.** Static base URLs were collected from
  source; the LLM CLIs' provider endpoints (claude, codex, gemini, opencode) are
  unverified and need conformance before activation.
- **Internal peers are reachability only.** RFC 0038 S7 also requires both endpoints to
  authenticate; that is the existing application authentication, not this policy.
- `scripts/egress-firewall.sh` (compose) stays until every supported caller is replaced
  and its containment is shown preserved.

## Rollout

Not authorized by this source change. Each step needs its own authorization:

1. Land the gateway and per-workload proxy wiring.
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
