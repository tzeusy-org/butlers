# Butler Dev Debug Logs and Health

Use this file when the next step is log inspection or service-health triage.

## Primary Rule

Use container stdout/stderr via `docker logs`. Do not start from the repo-local `logs/` directory for compose debugging.

## Log Commands

```bash
docker logs butlers-dev-butlers-up-1 --since 10m
docker logs butlers-dev-butlers-up-1 --since 10m --tail 200
docker logs -f --since 5m butlers-dev-butlers-up-1

docker logs butlers-dev-connector-gmail-1 --since 10m
docker logs butlers-dev-connector-telegram-bot-1 --since 10m
docker logs -f butlers-dev-connector-whatsapp-user-1
```

Search by session ID:

```bash
docker logs butlers-dev-butlers-up-1 --since 10m 2>&1 | grep "<session-id>"
docker logs butlers-dev-connector-gmail-1 --since 10m 2>&1 | grep "<session-id>"
```

Search all dev containers for recent errors:

```bash
for c in $(docker ps --format '{{.Names}}' | grep '^butlers-dev-'); do
  echo "=== $c ==="
  docker logs "$c" --since 10m 2>&1 | grep -iE 'error|traceback|failed|exception'
done
```

## Health and Container Status

```bash
docker ps --filter name=butlers-dev --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
docker ps --filter name=butlers-dev --filter status=restarting --format '{{.Names}}\t{{.Status}}'

curl -sf http://localhost:41200/health | python3 -m json.tool
curl -sf http://localhost:41100/health | python3 -m json.tool

docker restart butlers-dev-butlers-up-1
docker restart butlers-dev-connector-gmail-1
```
