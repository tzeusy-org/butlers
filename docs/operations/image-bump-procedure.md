# Service Image Bump Procedure

> **Purpose:** How to safely update pinned third-party Docker image tags in `docker-compose.yml`.
> **Audience:** Operators maintaining the Butlers stack.
> **Related:** [Docker Deployment](docker-deployment.md)

## Pinned Service Images

All third-party images in `docker-compose.yml` are pinned to specific release tags
(never `:latest`) to ensure reproducible builds. Current pins:

| Service | Image | Pinned Tag |
|---------|-------|------------|
| `minio` | `minio/minio` | `RELEASE.2025-09-07T16-13-09Z` |
| `minio-setup` | `minio/mc` | `RELEASE.2025-08-13T08-35-41Z` |
| `frontend-dev` | `node` | `22.16.0-slim` |
| `log-init`, `log-cleanup` | `alpine` | `3.19` |
| `backup-cron` | `postgres` | `17-alpine` |

The `butlers-app` and `butlers-app-audio` images are always built locally by
`scripts/compose.sh`. Their tag defaults to `latest` for local development but
can be overridden via `BUTLERS_APP_TAG` (see below).

**Observability stack** (`docker-compose.observability.yml`): already pinned to
specific versions (otel-collector `0.105.0`, Grafana `11.1.0`, Prometheus `v2.53.0`,
Tempo `2.5.0`) — no changes required.

---

## Bumping a Third-Party Image Tag

Follow these steps when a third-party image needs an update (security patch,
new feature, breaking change handled upstream).

### 1. Identify the new tag

Check the upstream registry for the new release tag. Do **not** use `:latest` —
find the specific version tag.

**MinIO (minio/minio and minio/mc):**

```bash
# List recent tags on Docker Hub
curl -s "https://registry.hub.docker.com/v2/repositories/minio/minio/tags?page_size=5&ordering=last_updated" \
  | python3 -c "import sys,json; [print(t['name']) for t in json.load(sys.stdin)['results'] if 'cpuv1' not in t['name']]"
```

MinIO tags follow the pattern `RELEASE.YYYY-MM-DDTHH-MM-SSZ`. Always update
`minio/minio` and `minio/mc` together — use the matching release pair from the
[MinIO GitHub releases page](https://github.com/minio/minio/releases).

**Node.js:**

```bash
# Latest Node.js 22 LTS version
curl -s https://nodejs.org/dist/index.json \
  | python3 -c "
import sys, json
releases = json.load(sys.stdin)
lts = [r for r in releases if r['version'].startswith('v22.') and r.get('lts')]
print('Latest v22 LTS:', lts[0]['version'])
"
```

Verify the slim variant exists:
```bash
docker manifest inspect node:<VERSION>-slim
```

**PostgreSQL / Alpine:** Follow their official release notes.
Pin to the most specific patch tag that's been published (e.g. `17.5-alpine` not `17-alpine`).

### 2. Verify the tag resolves

Always confirm the tag exists in the registry before editing the compose file:

```bash
docker manifest inspect <IMAGE>:<NEW-TAG>
```

If the command returns JSON with a `schemaVersion`, the tag is valid.

### 3. Update `docker-compose.yml`

Edit the `image:` line for the relevant service(s):

```yaml
# Before:
image: minio/minio:RELEASE.2025-09-07T16-13-09Z

# After:
image: minio/minio:RELEASE.YYYY-MM-DDTHH-MM-SSZ
```

**Also update** the pinned-images table at the top of this file.

### 4. Validate the compose file

```bash
POSTGRES_HOST=localhost POSTGRES_PASSWORD=test docker compose -f docker-compose.yml config -q
```

A zero exit code means the file is valid YAML with no interpolation errors.

### 5. Test locally

Bring up only the changed service to verify it starts correctly:

```bash
docker compose --profile minio up -d minio minio-setup
docker compose logs minio --tail=20
```

### 6. Commit

```bash
git add docker-compose.yml docs/operations/image-bump-procedure.md
git commit -m "chore: bump <image> to <new-tag>"
```

---

## Overriding the `butlers-app` Image Tag

`butlers-app` and `butlers-app-audio` are built locally by `scripts/compose.sh`.
By default they are tagged `:latest`. For environments where reproducible image
references matter (CI, staging, production hand-off), set `BUTLERS_APP_TAG`:

```bash
# Pin to the current git commit (recommended for CI/production)
export BUTLERS_APP_TAG=$(git rev-parse --short HEAD)
./scripts/compose.sh --prod
```

The compose script builds the image to `butlers-app:${BUTLERS_APP_TAG}` and
exports the variable so `docker compose up` references the same tag.

To revert to the default local tag:

```bash
unset BUTLERS_APP_TAG
./scripts/compose.sh
```

---

## Checklist

- [ ] New tag verified via `docker manifest inspect`
- [ ] `docker-compose.yml` updated
- [ ] Pinned-images table in this file updated
- [ ] `docker compose config -q` passes
- [ ] Service started and logs look healthy
- [ ] Commit pushed and PR opened
