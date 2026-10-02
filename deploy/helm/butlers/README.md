# butlers Helm chart

Runs Butlers on k3s in namespace `butlers-dev` (`values.dev.yaml`) or `butlers`
(`values.prod.yaml`). Operator guide: [docs/operations/kubernetes-deployment.md](../../../docs/operations/kubernetes-deployment.md).

```bash
bws run --project-id "$BWS_PROJECT_ID" -- make secrets-dev image-dev
make deploy-dev
```
