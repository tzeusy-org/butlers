# butlers Helm chart

Runs Butlers on k3s in namespace `butlers-dev` (`values.dev.yaml`) or `butlers`
(`values.prod.yaml`). Operator guide: [docs/operations/kubernetes-deployment.md](../../../docs/operations/kubernetes-deployment.md).

```bash
scripts/k8s/deploy-dev.sh   # from the repo root: build + push HEAD, bootstrap, helm upgrade, status
```

`make deploy-dev` first runs the read-only dev topology preflight, then reruns the deployed tag's
`scripts/init-db.sql` (`make bootstrap-dev`), then rechecks the same preflight immediately before
`helm upgrade`. The deploy host needs the checkout's real virtualenv (`uv sync --dev`); the guard
uses `uv run --no-sync`. Unknown runtime state or an active topology transition stops before
bootstrap. A split/cohost change requires both runtime roles observed at zero with no old or
terminating pods. The guard never scales or deletes anything, and competing automation must
remain held because these reads are not an atomic ownership lock. Online migrations only check
that privileged bootstrap; they never create it. Production deployment behavior is unchanged.

Dev Helm and kubectl must use the same inherited context/`KUBECONFIG`. All supported namespace
forms, including `-nVALUE` and `-n=VALUE`, must select `butlers-dev`. Helm-only `--kube-*` /
`--kubeconfig` flags and any `HELM_KUBE*` environment key are refused before render/query;
their values are never reported. Use the shared inherited context rather than Helm-only overrides.

`localSecrets.source` (chart default `local`, dev `bws`) chooses whether `make secrets-*` or
External Secrets owns `butlers-runtime-probe-control` and `butlers-local-env`; see the operator
guide.

Dev temporarily selects `butlersControl.enabled=false` by owner decision on
2026-11-04 so RFC 0023 recovery can use the accepted same-PID authority. One
`butlers-up` includes Switchboard and shares all five session slots; the control
workload, session listener and ingress policy are absent. This source selection
does not attest deployment or enable approval delivery. Follow the
[exclusive, data-preserving rollout](../../../docs/operations/approval-delivery-dev-rollout.md)
before replacing either topology; preserve live DB/PVCs and all durable evidence.

Explicit `butlersControl.enabled=true` retains the key-absent split stage:
`butlers-control` runs today's
Switchboard service on 41100 plus the refusal-only session port 41199, while
`butlers-up` excludes Switchboard. Probes use service health. Both Deployments
have exactly one replica with `Recreate`; their session capacities are 1 and 4
from the owner fleet budget 5. Invalid/nonpositive staged partitions or budgets
above five refuse rendering. The production default remains disabled/unsplit at
five. No GOAL private key is provisioned or mounted in this stage.

When the split stage is selected, `butlers-control-ingress` permits service callers within the namespace on 41100.
Port 41199 permits only pods with this release's instance label and component
`control-session-health` (operator/test caller) or `session-host`. These labels are
trusted deployment identities, not caller input.

`sessionHost.enabled` (default false, requires `butlersControl.enabled`) renders the
dark RFC 0038 D3 session-host skeleton: one idle `session-host` Deployment with no
port, Service, secret, volume or credential, and excluded from the namespace ingress
allow so it has no inbound path. Egress is not restricted yet (`trackerEgress` allows
all destinations and policies union); that is the dev reachability work.
`tests/scripts/test_session_host_env_guard.py` pins its environment composition.
NetworkPolicy enforcement and the owner Telegram smoke still require actual dev
verification; a successful render proves neither. See [stage operation and rollback](../../../docs/architecture/key-absent-control.md).

`connectorMetrics` (default enabled) makes connector `prometheus_client` metrics scrapeable
in-cluster. Connector health servers bind `127.0.0.1` and also serve `POST /reload`, so every
connector with a `healthPort` gets a `metrics-proxy` sidecar
(`scripts/connector_metrics_proxy.py`) that republishes only `GET /metrics` on the named pod
port `metrics` (9464). Pods carry `k8s.grafana.com/*` and `prometheus.io/*` scrape annotations;
`connectorMetrics.podMonitor.enabled` adds a Prometheus Operator `PodMonitor` whose `job` and
`connector` labels match the Compose scrape jobs. Default-deny ingress selects every pod, and the
metrics port admits only pods in `connectorMetrics.sourceNamespace` (default `lgtm`, matched on
`kubernetes.io/metadata.name`). The chart refuses an empty value and the public MCP namespaces.
Which discovery the cluster LGTM stack honours, and which namespace its scraper runs in, are site
configuration; confirm the targets appear after deploy.
