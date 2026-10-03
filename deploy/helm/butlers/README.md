# butlers Helm chart

Runs Butlers on k3s in namespace `butlers-dev` (`values.dev.yaml`) or `butlers`
(`values.prod.yaml`). Operator guide: [docs/operations/kubernetes-deployment.md](../../../docs/operations/kubernetes-deployment.md).

```bash
scripts/k8s/deploy-dev.sh   # from the repo root: build + push HEAD, helm upgrade, status
```

`localSecrets.source` (chart default `local`, dev `bws`) chooses whether `make secrets-*` or
External Secrets owns `butlers-runtime-probe-control` and `butlers-local-env`; see the operator
guide.
