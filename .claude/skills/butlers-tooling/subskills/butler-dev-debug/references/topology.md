# Butler Dev Debug Topology

Use this file when you need repo-grounded container names, ports, or upstream docs.

## Source of Truth

- `about/lay-and-land/deployment.md` for deployment topology and host ports
- `docs/getting_started/dev-environment.md` for local dev assumptions
- `docs/api_and_protocols/dashboard-api.md` for dashboard API behavior
- `deploy/helm/butlers` (values `connectors:` map) for the live deployment names used by this skill
- `docs/operations/kubernetes-deployment.md` for the operator guide

## Kubernetes Deployments (namespace `butlers-dev`)

Service names match the old compose service names, so in-cluster URLs are unchanged.

| Deployment | Port | Purpose |
|------------|------|---------|
| `butlers-up` | `41100` (Service) | Main butler daemon aggregate process |
| `dashboard-api` | `41200` (Service), NodePort `32200` | Dashboard API; initContainer `migrations` |
| `frontend` | NodePort `32173` | nginx serving the static frontend build |
| `connector-telegram-bot` | - | Telegram bot connector |
| `connector-telegram-user` | - | Telegram user connector |
| `connector-gmail` | - | Gmail connector |
| `connector-google-calendar` | - | Google Calendar connector |
| `connector-google-drive` | - | Google Drive connector |
| `connector-spotify` | - | Spotify connector |
| `connector-whatsapp-user` | - | WhatsApp connector |
| `connector-owntracks` | NodePort `32086` | OwnTracks connector |
| `connector-home-assistant` | - | Home Assistant connector |

`connector-live-listener` is not ported; it still runs only under compose (needs `/dev/snd`).

## Notes

- Dashboard API health: `curl -sf http://<ClusterIP>:41200/health`, with the ClusterIP from
  `kubectl -n butlers-dev get svc dashboard-api -o jsonpath='{.spec.clusterIP}'`. The
  `dashboard-api.butlers-dev.svc.cluster.local` name does not resolve off-cluster.
- Switchboard health is `http://localhost:41100/health` after
  `kubectl -n butlers-dev port-forward svc/butlers-up 41100:41100`.
- Container names inside each pod: `butlers-up`, `dashboard-api` (plus `migrations`), `nginx`
  (frontend), `connector` (every connector).
