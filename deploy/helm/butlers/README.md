# butlers Helm chart

Runs Butlers on k3s in namespace `butlers-dev` (`values.dev.yaml`) or `butlers`
(`values.prod.yaml`). Operator guide: [docs/operations/kubernetes-deployment.md](../../../docs/operations/kubernetes-deployment.md).

```bash
scripts/k8s/deploy-dev.sh   # from the repo root: build + push HEAD, helm upgrade, status
```

`localSecrets.source` (chart default `local`, dev `bws`) chooses whether `make secrets-*` or
External Secrets owns `butlers-runtime-probe-control` and `butlers-local-env`; see the operator
guide.

Dev selects `butlersControl.enabled=true`: `butlers-control` runs today's
Switchboard service on 41100 plus the refusal-only session port 41199, while
`butlers-up` excludes Switchboard. Probes use service health. Both Deployments
have exactly one replica with `Recreate`; their session capacities are 1 and 4
from the owner fleet budget 5. Invalid/nonpositive staged partitions or budgets
above five refuse rendering. The production default remains disabled/unsplit at
five. No GOAL private key is provisioned or mounted in this stage.

`butlers-control-ingress` permits service callers within the namespace on 41100.
Port 41199 permits only pods with this release's instance label and component
`control-session-health` (operator/test caller) or `session-host` (reserved future
identity). These labels are trusted deployment identities, not caller input.
NetworkPolicy enforcement and the owner Telegram smoke still require actual dev
verification; a successful render proves neither. See [stage operation and rollback](../../../docs/architecture/key-absent-control.md).
