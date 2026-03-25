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
Each refresh generates a fresh code by calling `POST /api/connectors/whatsapp/pair/start` on
the bridge.

**Session persistence:** The bridge writes session keys to the `whatsapp_sessions` table in
PostgreSQL. Subsequent restarts resume the session without re-pairing.

### 1.2 CLI Fallback (Headless)

For headless environments where you cannot access the dashboard, use the bridge CLI directly:

```bash
# Enter the running bridge container or host and trigger QR generation:
docker compose exec connector-whatsapp-user sh -c \
  'curl -s -X POST --unix-socket /tmp/wa-bridge.sock http://bridge/pair/start | python3 -m json.tool'
```

This returns a JSON payload with `qr_data_uri` (a base64-encoded PNG). Decode and display it:

```bash
# Extract and decode the QR image to a file:
curl -s -X POST --unix-socket /tmp/wa-bridge.sock http://bridge/pair/start \
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
  STATUS=$(curl -s --unix-socket /tmp/wa-bridge.sock http://bridge/pair/poll | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])")
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

**Dashboard:** The WhatsApp section status badge shows `pair_required` or `disconnected`.

**Logs:** The Go bridge logs a session-invalid exit (exit code 2):

```
bridge[stderr]: session invalidated — re-pair required
BridgeSubprocessManager: Bridge session invalidated (rc=2) — no restart; re-pair required
```

**API:**

```bash
curl -s --unix-socket /tmp/wa-bridge.sock http://bridge/status | python3 -m json.tool
# Expired session shows: "state": "pair_required"
```

**Health endpoint:**

```bash
curl -s http://localhost:40082/health
```

A healthy connector returns `{"status": "ok", ...}`. An unhealthy one returns a non-200
status or a degraded state payload.

### 2.2 Re-Pairing After Session Expiry

If the session is expired or invalidated, re-pair using the same QR workflow:

1. **Dashboard:** Navigate to **Settings → WhatsApp**, click **Disconnect** (if shown), then
   **Link WhatsApp Account** to start a new QR pairing.

2. **CLI:** The bridge exits and the connector enters `degraded` mode automatically on session
   invalidation. To re-pair:

   ```bash
   # Stop the connector service (bridge will also stop):
   docker compose stop connector-whatsapp-user

   # Remove the old session from the database (use schema-qualified name):
   docker compose exec postgres psql -U butlers -c \
     "UPDATE messenger.whatsapp_sessions SET active = false WHERE active = true;"

   # Restart the connector:
   docker compose up -d connector-whatsapp-user

   # Then follow CLI QR steps above (section 1.2)
   ```

3. After successful re-pairing, the bridge exits pairing mode and enters `connected` state.
   The connector resumes event streaming from the last checkpoint.

### 2.3 Verifying Recovery

```bash
# Check bridge status after re-pair:
curl -s --unix-socket /tmp/wa-bridge.sock http://bridge/status \
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
| `WA_BRIDGE_SOCKET` | `/tmp/wa-bridge.sock` | Unix socket path for bridge communication |
| `WA_FLUSH_INTERVAL_S` | `600` | Seconds between per-chat buffer flushes |
| `WA_BUFFER_MAX_MESSAGES` | `50` | Max buffered messages before force-flush |
| `CONNECTOR_HEALTH_PORT` | `40082` | Port for the connector health endpoint |
| `CONNECTOR_BACKFILL_WINDOW_H` | — | Backfill window in hours on startup |

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

**Connector says "pair_required" after restart:**

- The session in PostgreSQL may have been cleared or is stale. Re-pair via the dashboard.
- Check `whatsapp_sessions` table: `SELECT phone_number, active, paired_at FROM whatsapp_sessions;`

**Messages not appearing in butler context:**

- Check flush interval: messages accumulate for up to `WA_FLUSH_INTERVAL_S` seconds (default 10 min).
- Check the connector logs for discretion `IGNORE` verdicts — the LLM may be filtering low-weight messages.
- Ensure the Switchboard is healthy: `curl http://localhost:41100/health`.
