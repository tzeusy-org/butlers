# Butler Dev Debug Topology

Use this file when you need repo-grounded container names, ports, or upstream docs.

## Source of Truth

- `about/lay-and-land/deployment.md` for deployment topology and host ports
- `docs/getting_started/dev-environment.md` for local dev assumptions
- `docs/api_and_protocols/dashboard-api.md` for dashboard API behavior
- `docker-compose.yml` for the live compose service names used by this skill

## Compose Service Names

| Container | Host port | Purpose |
|-----------|-----------|---------|
| `butlers-dev-butlers-up-1` | `41100` | Main butler daemon aggregate process |
| `butlers-dev-dashboard-api-1` | `41200` | Dashboard API |
| `butlers-dev-connector-telegram-bot-1` | — | Telegram bot connector |
| `butlers-dev-connector-telegram-user-1` | — | Telegram user connector |
| `butlers-dev-connector-gmail-1` | — | Gmail connector |
| `butlers-dev-connector-google-calendar-1` | — | Google Calendar connector |
| `butlers-dev-connector-google-drive-1` | — | Google Drive connector |
| `butlers-dev-connector-spotify-1` | — | Spotify connector |
| `butlers-dev-connector-whatsapp-user-1` | — | WhatsApp connector |
| `butlers-dev-connector-owntracks-1` | — | OwnTracks connector |
| `butlers-dev-connector-home-assistant-1` | — | Home Assistant connector |
| `butlers-dev-connector-live-listener-1` | — | Live listener connector |

## Notes

- Dashboard API health is `http://localhost:41200/health`.
- Switchboard health is `http://localhost:41100/health`.
- This skill assumes containerized compose debugging, even though the repo also supports tmux-based local workflows.
