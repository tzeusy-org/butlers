# WhatsApp Setup Guide

> **Audience:** Operators deploying the Butlers WhatsApp connector.
> **Prerequisites:** Butlers stack running via `docker compose up`, access to the dashboard at
> `http://localhost:41200`.

---

## Overview

Butlers connects to your personal WhatsApp account via a Go bridge that implements the
WhatsApp Web multi-device protocol (whatsmeow). The connector is **readonly-first**: it
ingests messages from your account for butler context but does not send anything until you
explicitly enable outbound messaging in the Messenger butler configuration.

The Go bridge authenticates via a QR code pairing ceremony — the same mechanism WhatsApp
uses when you link a new device. After pairing, the session is stored in PostgreSQL and
survives restarts without re-pairing.

### Bridge ownership (one authenticated session)

WhatsApp permits only one active link per device slot. Two `whatsapp-bridge` processes
authenticated against the same account race each other: the second login triggers a
`StreamReplaced` and silently bumps the first offline (see §2.1.1). To guarantee a single
session, ownership is explicit:

- **The `connector-whatsapp-user` service is the sole owner** of the authenticated
  `whatsapp-bridge` sidecar and the whatsmeow device session. It is the only process that
  spawns a bridge subprocess. Its socket lives on the shared `wa_bridge_socket` Docker
  volume at `/tmp/wa-bridge/bridge.sock`.
- **The Messenger butler's `whatsapp` module is a client, not an owner.** Its
  `whatsapp_send_message` / `whatsapp_reply_to_message` tools POST to the connector-owned
  socket (resolved from `WHATSAPP_BRIDGE_SOCKET`, default `/tmp/wa-bridge/bridge.sock`),
  exactly as the dashboard's pair/status endpoints do. The module never spawns its own
  bridge, so flipping `send_enabled` to `true` cannot authenticate a second client.

For this to work, every container that talks to the bridge (`connector-whatsapp-user`,
`butlers-up`, `dashboard-api`) mounts the shared `wa_bridge_socket` volume and points
`WHATSAPP_BRIDGE_SOCKET` at `/tmp/wa-bridge/bridge.sock`. If a send tool reports the bridge
is *not reachable*, the connector service is down or the volume mount is missing; if it
reports *not connected*, re-pair via the dashboard (§2.2).

The standalone connector and `whatsapp-bridge` CLI deliberately default to the flat
`/tmp/wa-bridge.sock` path, which needs no pre-created parent directory. That default is for
single-host, non-compose operation only. Any separately running client must select the same
socket explicitly; compose instead supplies the shared-volume path above to every process.

---

## 1. QR Pairing Workflow

### 1.1 Dashboard UX (Primary)

The dashboard provides a guided pairing flow at **Settings → WhatsApp**.

**Steps:**

1. Open the dashboard at `http://localhost:41200` and navigate to **Settings**.
2. Locate the **WhatsApp** section. If no session exists, the status badge shows
   `pair_required` or `not_configured`.
3. Click **Link WhatsApp Account**.
4. A modal opens displaying a QR code. The QR code expires in approximately 60 seconds.
5. On your phone, open WhatsApp → **Settings** → **Linked Devices** → **Link a Device**.
6. Scan the QR code shown in the dashboard modal.
7. WhatsApp confirms pairing. The dashboard detects this automatically (polling every 3 seconds)
   and closes the modal, showing a connected status badge with your masked phone number.
8. The Go bridge begins streaming messages to the connector immediately after pairing.

**QR refresh:** If the QR code expires before you scan it, click **Refresh QR** in the modal.
Each refresh generates a fresh code through the dashboard API at
`POST /api/connectors/whatsapp/pair/start`, which proxies to the bridge's `/pair/start` endpoint.

**Session persistence:** Whatsmeow stores the resumable protocol session in its own
`public.whatsmeow_*` tables. The bridge separately records pair history and active-session
bookkeeping in `messenger.whatsapp_sessions`; the protocol store is what lets subsequent
restarts resume without re-pairing.

### 1.2 CLI Fallback (Headless)

For headless environments where you cannot access the dashboard, use the bridge CLI directly:

```bash
# Enter the running bridge container or host and trigger QR generation:
docker compose exec -T connector-whatsapp-user sh -c \
  'curl -s -X POST --unix-socket /tmp/wa-bridge/bridge.sock http://bridge/pair/start | python3 -m json.tool'
```

This returns a JSON payload with `qr_data_uri` (a base64-encoded PNG). Decode and display it:

```bash
# Extract and decode the QR image to a file:
docker compose exec -T connector-whatsapp-user \
  curl -s -X POST --unix-socket /tmp/wa-bridge/bridge.sock http://bridge/pair/start \
  | python3 -c "
import json, sys, base64
data = json.load(sys.stdin)
uri = data['qr_data_uri']
raw = base64.b64decode(uri.split(',', 1)[1])
with open('/tmp/wa-qr.png', 'wb') as f:
    f.write(raw)
print('QR saved to /tmp/wa-qr.png')
"
```

Transfer `/tmp/wa-qr.png` to a local machine and display it, or use a terminal QR renderer:

```bash
# Render as UTF-8 QR in terminal if qrencode is available (headless-safe):
qrencode -t UTF8 -r /tmp/wa-qr.png 2>/dev/null || \
  echo "QR saved to /tmp/wa-qr.png — copy to a machine with a display to scan"
```

Alternatively, poll pairing status:

```bash
# Poll until paired:
for i in $(seq 1 20); do
  STATUS=$(docker compose exec -T connector-whatsapp-user \
    curl -s --unix-socket /tmp/wa-bridge/bridge.sock http://bridge/pair/poll \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])")
  echo "$(date +%H:%M:%S) — status: $STATUS"
  [ "$STATUS" = "paired" ] && break
  sleep 3
done
```

---

## 2. Session Recovery

### 2.1 Detecting an Expired Session

WhatsApp invalidates sessions when the account is active on too many devices, when the user
manually unlinks a device, or after prolonged inactivity. You can detect an expired session via:

**Dashboard:** A new, never-paired device shows `pair_required`. A temporarily
link-dead device may still show `disconnected`, but a previously paired device
whose live link remains down is surfaced as `pair_required` after the
invalidated-session threshold (five minutes by default), even when its stale
event-driven state still says `connected`, so it has an actionable recovery path.

**Logs:** The Go bridge logs a session-invalid exit (exit code 2):

```
bridge[stderr]: session invalidated — re-pair required
BridgeSubprocessManager: Bridge session invalidated (rc=2) — no restart; re-pair required
```

**API:**

```bash
docker compose exec -T connector-whatsapp-user \
  curl -s --unix-socket /tmp/wa-bridge/bridge.sock http://bridge/status \
  | python3 -m json.tool
# A never-paired device shows: "state": "pair_required".
# A dead stored device can remain "disconnected" or "connecting".
```

The `/status` payload also reports the live link state, probed directly from the
whatsmeow client at request time:

- `connected` — websocket is up
- `logged_in` — the WhatsApp session is still valid

These are authoritative for liveness. The event-driven `state` field can lag
reality if a connection event is missed (see 2.1.1), so prefer `connected` /
`logged_in` when scripting health checks.

#### 2.1.1 Persistently invalidated sessions

An invalidated stored device does not always exit with code 2 or report
`pair_required` itself. It can cycle through `disconnected`/`connecting`, or
report `connected` while the live `connected` or `logged_in` probe is false. The
connector treats this as a recoverable outage at first; if it persists for the
configured `WHATSAPP_INVALIDATED_SESSION_THRESHOLD_S` (300 seconds by default),
it classifies the outage as terminal and requires a new QR pair.

For any persistent link-dead bridge, the dashboard status surfaces
`pair_required`; the connector sends a best-effort Telegram alert once per
invalidation episode with the recovery action. This is distinct from first-time
setup: a new device can wait in `pair_required` without an invalidated-session
alert.

#### 2.1.2 Session taken over by another device (StreamReplaced)

WhatsApp permits only one active link per device slot. If the **same** session is
linked again elsewhere (another stack reusing the credentials, or WhatsApp Web
opened with this number), WhatsApp sends a `StreamReplaced` and silently bumps
this connector off. whatsmeow deliberately does **not** auto-reconnect after a
replaced stream — reconnecting would just get replaced again — so without
handling this the bridge would keep reporting `connected` while ingesting
nothing.

The bridge now marks the link `disconnected` on `StreamReplaced`:

```
bridge[stdout]: stream replaced by another device — session taken over, link is down
```

The connector then reports `degraded` on its heartbeat, and a **stale-link
watchdog** restarts it once the link has been down continuously past
`WHATSAPP_STALE_RESTART_THRESHOLD_S` (default `3600`, i.e. ~1h). The restart
exits the container; Docker's `restart: unless-stopped` policy respawns it, and a
fresh `Connect()` **re-claims** the WhatsApp session. The long default is
intentional: it avoids a reconnect war with a genuinely-competing session while
still self-healing a transient takeover within an hour. Set the env var to `0` to
disable the watchdog, or lower it to re-claim faster (at higher war risk).

The watchdog only fires on **recoverable** outages (a restart can re-claim the
link). Terminal states that need a human QR re-pair — `pair_required`, an
explicit session-invalid exit (rc=2), pairing timeout (rc=1), or the persistent
invalidated-session classification above — are exempt; restarting could not
recover them, so they follow the re-pair flow in section 2.2 instead.

If this fires repeatedly, two stacks are sharing one WhatsApp session — stop the
duplicate rather than lowering the threshold.

**Health endpoint:**

```bash
curl -s http://localhost:40082/health
```

A healthy connector returns `{"status": "ok", ...}`. An unhealthy one returns a non-200
status or a degraded state payload.

### 2.2 Re-Pairing After Session Expiry

For an expired or invalidated session, use the dashboard recovery path:

1. Open **Settings → WhatsApp** and click **Pair device**. This calls
   `POST /api/connectors/whatsapp/pair/start`.
2. If the bridge is already in QR mode, the dashboard shows a QR code immediately.
   If it reports that an invalidated session is being cleared and restarted, wait
   up to one minute, then click **Pair device** again to request the QR code.
3. For an invalidated stored session, the first request records the dashboard-requested
   reset. The connector clears the stale protocol device, restarts the bridge into QR-pairing
   mode, and leaves it there for the new scan. This destructive reset is deliberately not
   automatic when the invalidation is detected.
4. Do **not** manually delete `public.whatsmeow_device`, edit
   `messenger.whatsapp_sessions`, or stop/restart the connector for this recovery.
   The dashboard route is the supported owner-triggered path. In a headless
   deployment, invoke that same dashboard API route rather than the bridge's
   Unix-socket `/pair/start` endpoint, which cannot request the reset.
5. After successful re-pairing, the bridge exits pairing mode and enters `connected` state.
   The connector resumes event streaming from the last checkpoint.

### 2.3 Verifying Recovery

```bash
# Check bridge status after re-pair:
docker compose exec -T connector-whatsapp-user \
  curl -s --unix-socket /tmp/wa-bridge/bridge.sock http://bridge/status \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['state'], d.get('phone',''))"
# Expected: connected +1...

# Check connector health:
curl -s http://localhost:40082/health
```

---

## 3. Ban-Risk Mitigation

WhatsApp aggressively bans unofficial client implementations. The following practices reduce
ban risk significantly.

### 3.1 Account Requirements

**Use an established account, not a fresh one.**
- Accounts that have been active for 6+ months with a real SIM carry substantially lower risk.
- Do not create a new phone number specifically for this integration.
- The account should have a real contact list and existing conversation history.

**Use a real SIM, not a VoIP number.**
- WhatsApp assigns trust scores to phone numbers. VoIP numbers (e.g., Google Voice, Twilio)
  are flagged and banned more aggressively.
- Use a mobile carrier SIM with a number that has been linked to WhatsApp for a meaningful
  period.

### 3.2 Usage Patterns

**Keep message volume low.**
- The connector is designed for passive ingestion, not bulk processing.
- Default configuration: buffer flush every 10 minutes, 50 messages max per batch.
- Avoid configuring very short flush intervals or very high throughput unless necessary.

**Do not send until you assess your risk tolerance.**
- The Messenger butler defaults to `send_enabled = false`. Sending via unofficial clients
  is the highest-risk activity.
- If you enable sending, keep volume under 10 messages per minute and avoid automated
  message blasts.
- Only enable sending to verified contacts (owner auto-approve is safe; external contacts
  go through the approval gate by default).

**Avoid media-heavy usage.**
- Sending images, videos, and documents at scale is more detectable than text messages.
- The connector only ingests; media is described as `[image]`, `[video]`, etc. in normalized
  text, with no raw media forwarded.

### 3.3 Session and Device Management

**Do not exceed 4 linked devices.**
- WhatsApp allows up to 4 linked devices per account. If you are already at the limit,
  adding another raises flags.
- Unlink unused devices at **Settings → Linked Devices** before pairing a new one.

**Do not run multiple bridge instances against the same account.**
- Running two whatsapp-bridge processes for the same account simultaneously will cause
  session conflicts and likely trigger a ban.
- Use a single connector per WhatsApp account.

**Do not rapidly pair and unpair.**
- Frequent QR pairing cycles (multiple per day) are a signal of automated abuse.
- Only re-pair when genuinely needed (session expiry, device reset).

### 3.4 Monitoring

**Watch bridge logs for warning signals:**

```bash
docker compose logs -f connector-whatsapp-user 2>&1 | grep -i "ban\|logout\|invalid\|revoke"
```

**Prometheus metrics** (if configured) expose `connector_messages_processed_total{connector_type="whatsapp_user_client"}` for volume monitoring.

**Alerts to configure:**

| Signal | Meaning | Action |
|--------|---------|--------|
| Bridge exit code 2 | Session invalidated | Re-pair (could indicate a soft ban) |
| Bridge exit code 1 | Pairing timeout | Try QR flow again |
| Repeated disconnect/reconnect cycles | Network instability or rate-limit | Investigate logs |
| Account phone number changes | Account takeover or ban recovery | Stop bridge immediately |

### 3.5 What Happens If Banned

A soft ban typically manifests as the account being locked out of WhatsApp Web for 24–72
hours. A hard ban results in permanent account suspension.

**Soft ban recovery:**
1. Stop the bridge (`docker compose stop connector-whatsapp-user`).
2. Wait the full ban duration (typically 24–72 hours) without any bridge activity.
3. Re-enable on the native WhatsApp mobile app first to verify the account is accessible.
4. Restart the bridge and re-pair after the ban lifts.

**After a soft ban, reduce activity:**
- Disable sending (`send_enabled = false` in butler.toml).
- Increase `WA_FLUSH_INTERVAL_S` to 1800 (30 minutes) for lower ingestion frequency.

---

## 4. Configuration Reference

### Environment Variables (connector service)

| Variable | Default | Description |
|----------|---------|-------------|
| `SWITCHBOARD_MCP_URL` | — (required) | MCP URL for the Switchboard butler |
| `WA_BRIDGE_SOCKET` | `/tmp/wa-bridge.sock` standalone; `/tmp/wa-bridge/bridge.sock` in compose | Unix socket path for bridge communication |
| `WA_FLUSH_INTERVAL_S` | `600` | Seconds between per-chat buffer flushes |
| `WA_BUFFER_MAX_MESSAGES` | `50` | Max buffered messages before force-flush |
| `CONNECTOR_HEALTH_PORT` | `40082` | Port for the connector health endpoint |
| `CONNECTOR_BACKFILL_WINDOW_H` | — | Optional 1-168 hour replay window for already-received bridge history on startup |

### butler.toml (Messenger butler)

```toml
[modules.whatsapp]
send_tools = true     # Register send tools in MCP schema
send_enabled = false  # Runtime gate: disable actual sending (default safe)

[modules.approvals.gated_tools.whatsapp_send_message]
risk_tier = "medium"

[modules.approvals.gated_tools.whatsapp_reply_to_message]
risk_tier = "medium"
```

To enable sending (after assessing ban risk):

```toml
[modules.whatsapp]
send_tools = true
send_enabled = true   # CAUTION: carries ban risk — review section 3 above
```

---

## 5. Troubleshooting

**Bridge not starting / binary not found:**

```
RuntimeError: whatsapp-bridge binary not found. Build with EXTRAS=whatsapp or install manually.
```

Rebuild the image with the WhatsApp extra:

```bash
docker compose build --build-arg EXTRAS=whatsapp connector-whatsapp-user
```

**QR code modal shows an error instead of a QR:**

- The bridge is not running. Check `docker compose logs connector-whatsapp-user`.
- The bridge may be starting up; wait 10–15 seconds and try again.

**Dashboard shows `pair_required`, or Pair device reports an invalidated session:**

- Follow the supported recovery in section 2.2: click **Pair device**, wait up to a minute if
  the reset is in progress, then click it again to scan the QR code.
- Do not manually delete `public.whatsmeow_device` or edit
  `messenger.whatsapp_sessions`; the connector performs that protocol-store reset only after
  the dashboard's owner-triggered recovery request.

**Messages not appearing in butler context:**

- Check flush interval: messages accumulate for up to `WA_FLUSH_INTERVAL_S` seconds (default 10 min).
- Check the connector logs for discretion `IGNORE` verdicts — the LLM may be filtering low-weight messages.
- Ensure the Switchboard is healthy: `curl http://localhost:41100/health`.
