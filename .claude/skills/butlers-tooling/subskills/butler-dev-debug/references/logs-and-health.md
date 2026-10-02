# Butler Dev Debug Logs and Health

Use this file when the next step is log inspection or service-health triage.

## Primary Rule

Use pod stdout/stderr via `kubectl -n butlers-dev logs`. Do not start from the repo-local `logs/`
directory. File logs are on the shared PVC `butlers-logs`, mounted at `/app/logs` in each pod.

## Log Commands

```bash
kubectl -n butlers-dev logs deploy/butlers-up --since=10m
kubectl -n butlers-dev logs deploy/butlers-up --since=10m --tail=200
kubectl -n butlers-dev logs -f --since=5m deploy/butlers-up

kubectl -n butlers-dev logs deploy/connector-gmail --since=10m
kubectl -n butlers-dev logs deploy/connector-telegram-bot --since=10m
kubectl -n butlers-dev logs -f deploy/connector-whatsapp-user

kubectl -n butlers-dev logs deploy/dashboard-api -c migrations   # migration initContainer
kubectl -n butlers-dev logs deploy/butlers-up --previous         # last crashed container
```

Search by session ID:

```bash
kubectl -n butlers-dev logs deploy/butlers-up --since=10m | grep "<session-id>"
kubectl -n butlers-dev logs deploy/connector-gmail --since=10m | grep "<session-id>"
```

Search all deployments for recent errors:

```bash
for d in $(kubectl -n butlers-dev get deploy -o name); do
  echo "=== $d ==="
  kubectl -n butlers-dev logs "$d" --all-containers --since=10m 2>&1 | grep -iE 'error|traceback|failed|exception'
done
```

Read file logs on the shared PVC:

```bash
kubectl -n butlers-dev exec deploy/butlers-up -c butlers-up -- ls /app/logs
```

## Health and Pod Status

```bash
kubectl -n butlers-dev get pods -o wide        # STATUS and RESTARTS columns
kubectl -n butlers-dev describe pod <pod>       # events, probe failures, OOMKilled
kubectl -n butlers-dev get events --sort-by=.lastTimestamp | tail -20

curl -sf http://localhost:32200/health | python3 -m json.tool   # dashboard-api NodePort, from the k3s host
kubectl -n butlers-dev port-forward svc/butlers-up 41100:41100 &  # butlers-up has no NodePort
curl -sf http://localhost:41100/health | python3 -m json.tool

kubectl -n butlers-dev rollout restart deploy/butlers-up
kubectl -n butlers-dev rollout restart deploy/connector-gmail
```

Restarting reruns the same committed image. Code changes reach the live stack only through
`make ship-dev` (see `docs/operations/kubernetes-deployment.md`).
