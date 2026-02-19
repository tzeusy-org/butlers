# WhatsApp Module Research Draft

Status: **Draft** (Research Only — no implementation)
Last updated: 2026-02-19
Author: Research pass, butlers-962.1
Depends on: `docs/connectors/interface.md`, `src/butlers/modules/base.py`

---

## 1. Purpose

This document captures research into WhatsApp as a butler data ingestion channel.
WhatsApp is the user's primary messaging platform alongside Telegram. Ingesting
messages and media would give butlers rich conversational context and enable
the same pattern already established for Telegram (connector + module + pipeline).

This is a **research-only** deliverable. No implementation code accompanies this doc.
The goal is to identify the best API/library approach, map data models to existing
butler conventions, and surface risk factors for a future implementation ticket.

---

## 2. API and Library Landscape

Five approaches exist, spanning official Meta APIs to unofficial reverse-engineered
protocol libraries and Matrix bridging infrastructure.

### 2.1 WhatsApp Business Cloud API (Meta Official)

**What it is:** Meta's cloud-hosted REST + webhook API for WhatsApp Business numbers.
Messages are received via HTTPS webhook (POST from Meta's servers) and sent via
Graph API (`POST /v19.0/{phone-number-id}/messages`).

**Auth model:** Meta app access token + phone number registration. Business Manager
account required. Phone number must be verified and not already on personal
WhatsApp. Uses long-lived System User tokens (not OAuth PKCE flows).

**Data model:**
- Inbound via webhook: text, image, video, document, audio, sticker, location,
  contacts, interactive replies, reaction, referral context.
- Outbound: template messages (pre-approved by Meta) or free-form replies within
  the 24-hour customer service window.
- Webhook payload includes `from`, `timestamp`, `id` (message ID), `type`, and
  type-specific content objects.

**Free tier / pricing (as of July 2025):**
- Service conversations (user initiates, you reply within 24 h) are **free and
  unlimited** as of November 2024 (the previous 1,000/month cap was removed).
- Template messages (marketing, utility, authentication) switched to per-message
  pricing on July 1, 2025 (range: $0.0008–$0.1365 per message depending on category
  and recipient country).
- For butler ingestion use case (reading the user's own messages), **no outbound
  template costs apply**; cost is zero if the butler only reads and replies within
  the service window.

**Rate limits:**
- New unverified accounts: 250 unique conversations per 24 h.
- Tier 1 (verified): 1,000/day initially, scaling to 10K, 100K, unlimited based on
  quality rating and engagement. Meta is removing the 2K/10K tiers in Q1–Q2 2026;
  verified accounts jump directly to 100K.
- Throughput: up to 500 messages/second on Cloud API.

**On-Premise API note:** Deprecated. New signups closed May 15, 2024; fully
end-of-life October 23, 2025. Not a viable option.

**Tailnet fit:**
- Webhook endpoint must be publicly reachable via HTTPS (no self-signed certificates).
- The Meta webhook push originates from Meta's IP ranges, not from within a tailnet.
- A tailnet-resident service must expose a public HTTPS endpoint (or reverse-proxy via
  Cloudflare Tunnel / ngrok equivalent) to receive webhook callbacks.
- Alternatively, the butler could poll the Conversations API instead of using webhooks,
  but this is not the standard pattern and has higher latency.
- **Constraint:** This option requires punching through the tailnet boundary for webhook
  ingress, unless a DMZ proxy is deployed specifically for this purpose.

**ToS and ban risk:** None for the Cloud API. It is the official Meta-sanctioned path.
The phone number must be a dedicated business number (cannot be a personal number
already registered on WhatsApp personal app).

**Verdict:** Compliant, zero-cost for read/reply use case, but requires a dedicated
business phone number (not the user's personal WhatsApp) and a public webhook endpoint.
Unsuitable as a personal inbox ingestion channel without a dedicated number.

---

### 2.2 whatsmeow (Go library, unofficial)

**What it is:** A Go library implementing the WhatsApp Web multidevice protocol via
WebSocket. Used internally by `mautrix-whatsapp` and `lharries/whatsapp-mcp`. Connects
to a personal WhatsApp account (or WhatsApp Business app account) via QR code pairing.
No Meta account, no Business Manager required.

**Repository:** `go.mau.fi/whatsmeow` (tulir/whatsmeow on GitHub, MIT license)

**Auth model:** QR code pairing (scan with WhatsApp mobile app). After pairing, the
library holds a session key (stored in SQLite or PostgreSQL). Multidevice protocol: the
phone does not need to remain online after initial pairing. Session re-pairs
automatically with the mobile app.

**Data model:**
- Full access to incoming messages across all chats (DM, group, broadcast).
- Message types: text, image, video, audio, document, sticker, location, contact card,
  poll, reaction, view-once messages, disappearing messages, status updates.
- Read receipts, typing indicators, presence (online/offline).
- Group metadata (participants, admin roles, group name/description).
- Message replies and quoted context.

**Tailnet fit:**
- Runs fully inside the tailnet. No inbound public port required.
- The Go binary connects outbound to WhatsApp's WebSocket endpoint over the internet
  (standard TLS). WhatsApp's servers initiate no inbound connections to the host.
- Ideal for tailnet-resident operation: deploy as a Docker container on any tailnet
  node; all traffic is outbound.

**Rate limits / behavior:**
- No published official rate limits (unofficial protocol).
- High-volume sending triggers quality checks and bans.
- Low-volume personal inbox read/reply is generally safe with established accounts.

**Stability:**
- Actively maintained (the same library powers mautrix-whatsapp, which is widely deployed).
- Protocol breaks occur when Meta updates the WhatsApp Web binary; typically fixed within
  days to weeks by the maintainers.
- Session loss requires manual QR re-pair.

**ToS and ban risk:** **Medium.** Using a third-party client on a personal WhatsApp
account violates Meta's Terms of Service. Ban risk for low-volume personal inbox
reading is historically low with established accounts, but is increasing:
- GitHub issue #1869 (WhiskeySockets/Baileys) and whatsmeow issue #810 document
  account bans and "your account may be at risk" warnings being sent to accounts
  running unofficial clients as of late 2024/2025.
- Risk factors that increase ban probability: newly created accounts, VoIP numbers,
  high message volume, running alongside Android emulators, initiating DMs to
  non-contacts.
- Risk factors that decrease ban probability: established account (3+ years old),
  real SIM card, low message volume, only replying to contacts.
- The official mautrix-bridges documentation explicitly states: "Just using the bridge
  shouldn't cause any bans, but getting banned is more likely when combining the bridge
  with other suspicious activity."

**Verdict:** Best technical fit for personal inbox ingestion with tailnet constraints.
Zero cost. Full message type support. Moderate ban risk on a mature, low-volume account.

---

### 2.3 whatsapp-web.js / Baileys (Node.js, unofficial)

**What it is:** Browser-based (whatsapp-web.js uses Puppeteer/Chromium) or WebSocket-
based (Baileys) Node.js libraries implementing the WhatsApp Web protocol.

- **whatsapp-web.js:** Puppeteer drives a headless Chromium running the WhatsApp Web
  frontend. Higher resource consumption (~200–500 MB RAM). More faithful to the web
  client but heavier.
- **Baileys (WhiskeySockets/Baileys):** TypeScript WebSocket implementation, no browser
  required. Functionally similar to whatsmeow but in Node.js. MIT license.

**Auth model:** QR code or pairing code. Same multidevice protocol as whatsmeow.

**Data model:** Comparable to whatsmeow — full personal inbox access, all message types.

**Tailnet fit:** Same as whatsmeow — outbound-only connections, fully tailnet-resident.

**ToS and ban risk:** **Medium-High.** Same ToS violation as whatsmeow, but the ban
rate appears higher for Baileys in 2025. Issue #1869 on the Baileys repository shows
increased bans affecting even long-running bots. Security concern: a malicious NPM
package `lotusbail` (cloning the Baileys API with embedded malware) was distributed for
6+ months with 56,000+ downloads, highlighting supply-chain risk specific to the
Node.js ecosystem.

**Additional concern (supply chain):** The NPM ecosystem for WhatsApp libraries has
documented malicious packages. Using whatsmeow (Go module, no NPM) is safer from
this vector.

**Verdict:** Functional but less preferred than whatsmeow for this stack. The butler
codebase is Python/Go-friendly; introducing Node.js as a dependency for a Go-equivalent
library adds operational complexity. Ban risk is comparable but supply-chain risk is
higher. Not recommended.

---

### 2.4 mautrix-whatsapp (Matrix Bridge)

**What it is:** A Go application that acts as a Matrix application service (appservice),
bridging a Matrix homeserver to WhatsApp via whatsmeow. Conversations appear as Matrix
rooms; messages are relayed bidirectionally.

**Repository:** `github.com/mautrix/whatsapp` (mautrix organization, AGPLv3)
Docker image: `dock.mau.dev/mautrix/whatsapp:latest`

**Architecture:**
```
Personal WhatsApp account
       |
  whatsmeow (Go, WebSocket)
       |
mautrix-whatsapp (appservice)
       |
Matrix Homeserver (Synapse / Dendrite)
       |
  Matrix clients / integrations
```

**Auth model:** QR code pairing with personal WhatsApp account via the Matrix bridge bot.
After pairing, the bridge maintains the session. The user interacts via their Matrix
account (e.g., Element, Beeper).

**Data model:** Messages are relayed as Matrix events. The bridge supports:
- Text, images, video, audio, documents, stickers (converted to Matrix `m.image` etc.)
- Reactions (mapped to Matrix `m.reaction`)
- Replies (Matrix `m.relates_to` reply)
- Read receipts
- Group chats (bridged as Matrix rooms)
- Typing indicators
- Disappearing messages (partial support)
- Voice messages (bridged as audio files)

**Tailnet fit:**
- mautrix-whatsapp runs fully inside the tailnet. Outbound WebSocket to WhatsApp's
  servers; inbound connections only from the Matrix homeserver (also tailnet-resident).
- The Matrix homeserver itself needs to be accessible for federation if desired, but
  federation can be disabled for a private deployment.
- A minimal deployment: Matrix Synapse + mautrix-whatsapp, both in Docker containers
  on the same tailnet node. No public HTTPS endpoint required.

**Infrastructure overhead:**
- Requires a running Matrix homeserver (Synapse is the reference; ~500 MB RAM minimum).
- Adds significant operational complexity vs. direct whatsmeow usage.
- Synapse requires its own PostgreSQL database (separate from butler DBs).
- For butler use (data ingestion only), this is overbuilt.

**Rate limits:** Same underlying whatsmeow limits; the Matrix layer adds no WhatsApp-level
rate limits.

**ToS and ban risk:** **Medium.** Same as whatsmeow (it uses whatsmeow underneath).
The mautrix-bridges documentation is transparent: "Just using the bridge shouldn't cause
any bans, but getting banned is more likely when combining the bridge with other
suspicious activity."

**Encryption note:** Matrix E2EE (Olm/Megolm) protects the Matrix side. WhatsApp E2EE
(Signal protocol) protects the WhatsApp side. The bridge decrypts WhatsApp messages
and re-encrypts for Matrix (and vice versa). The bridge host can therefore read all
messages in plaintext. This is expected for a self-hosted bridge but means message
plaintext is stored on the Synapse server's database.

**Verdict:** Powerful but overbuilt for butler ingestion. The Matrix layer adds
~500 MB RAM, an additional database, and significant deployment complexity without
meaningful benefit over direct whatsmeow use. Best suited if the user already runs a
Matrix homeserver or wants multi-client access to their WhatsApp messages.

---

### 2.5 WAHA (WhatsApp HTTP API, unofficial)

**What it is:** An open-source self-hosted REST API wrapper around the WhatsApp Web
protocol. Exposes HTTP endpoints for WhatsApp operations. Three engines:
- `WEBJS`: Puppeteer/Chromium-based (whatsapp-web.js under the hood)
- `NOWEB`: Node.js WebSocket (Baileys-based)
- `GOWS`: Go WebSocket (whatsmeow-based, available in WAHA Plus)

**Repository:** `github.com/devlikeapro/waha` (6,000+ GitHub stars)
Docker image available; WAHA Core (free) vs WAHA Plus ($19/month).

**Auth model:** QR code or pairing code. Session stored by the WAHA server.

**Data model:** HTTP webhook or polling. Full message type support depending on engine.

**Tailnet fit:** Fully tailnet-resident (outbound-only). HTTP server accessible within
the tailnet; butler calls WAHA API instead of implementing whatsmeow directly.

**ToS and ban risk:** **Medium-High.** Same as whatsmeow/Baileys depending on engine.
The WAHA GOWS engine (whatsmeow) carries the same moderate ban risk as direct
whatsmeow use.

**Verdict:** Reasonable for teams wanting a HTTP-first interface. Adds an unnecessary
service hop for this use case since the butler framework already handles async flows
natively. Direct whatsmeow integration (as done in whatsapp-mcp) is more efficient.
Not recommended as primary option but acceptable as a lower-implementation-effort
alternative if Python bindings to whatsmeow prove difficult.

---

## 3. Tradeoff Matrix

| Criterion | Cloud API (Meta) | whatsmeow (Go) | Baileys (Node.js) | mautrix-whatsapp | WAHA |
|---|---|---|---|---|---|
| Personal inbox access | No (business only) | Yes | Yes | Yes | Yes |
| Official / ToS compliant | Yes | No | No | No | No |
| Cost | Free for service conv. | Free | Free | Free | Free (core) / $19/mo |
| Tailnet-native | Partial (needs public webhook) | Yes | Yes | Yes | Yes |
| Infrastructure overhead | Low (managed by Meta) | Low | Low | High (Matrix homeserver) | Medium (extra HTTP service) |
| Ban risk | None | Medium | Medium-High | Medium | Medium-High |
| Language fit (Python codebase) | Python SDK available | Go binary / subprocess | Node.js subprocess | Go binary / API calls | HTTP API |
| Data richness (message types) | Partial (template limits) | Full | Full | Full | Full |
| Media support | Yes | Yes | Yes | Yes | Yes |
| Group chat support | Yes | Yes | Yes | Yes | Yes |
| Supply-chain risk | Low | Low | Medium (NPM ecosystem) | Low | Medium |
| Operational maturity | High | High | Medium | High | Medium |
| Protocol stability | High (official) | Medium (unofficial, actively maintained) | Medium | Medium | Medium |

---

## 4. Recommendation

### Primary Recommendation: whatsmeow via a sidecar Go bridge process

For a personal butler inbox ingestion use case under the tailnet constraint, **whatsmeow**
is the best-fit option. The approach mirrors the `lharries/whatsapp-mcp` architecture:

1. A small Go binary (`whatsapp-bridge`) wraps whatsmeow, connects to WhatsApp via QR
   pairing, persists session state to the butler's PostgreSQL database (or a separate
   SQLite file), and exposes a local HTTP or gRPC interface for the Python butler module.
2. The Python `WhatsAppModule` implements the `Module` ABC, starts the Go sidecar on
   `on_startup()`, and polls or receives webhooks from the sidecar for inbound events.
3. Inbound events are normalized into `ingest.v1` envelopes and submitted to Switchboard
   via the canonical connector pattern (`docs/connectors/interface.md`).

**Why not Cloud API:** Cannot access the user's personal WhatsApp inbox. Requires a
dedicated business number. Webhook endpoint breaks the tailnet isolation requirement.

**Why not Baileys:** Same ban risk as whatsmeow but higher supply-chain risk (NPM),
language mismatch (Node.js), and the whatsmeow-based option (via the Go ecosystem) is
actively maintained by the same author (tulir) who maintains mautrix-whatsapp.

**Why not mautrix-whatsapp:** Overbuilt. Requires running a Matrix homeserver. The butler
framework already provides the routing, storage, and session infrastructure. The Matrix
layer adds 500 MB+ RAM and a separate database with no benefit for this use case.

**Why not WAHA:** Adds an unnecessary HTTP service hop. If WAHA's GOWS engine is used,
it wraps whatsmeow anyway — better to use whatsmeow directly and eliminate the
intermediary. WAHA Plus costs $19/month.

### Fallback: WAHA GOWS engine

If implementing the Go sidecar is out of scope for the implementation sprint, WAHA with
the GOWS engine is an acceptable fallback. It provides the same ban-risk profile as
whatsmeow with a simpler integration surface (HTTP REST) and is fully tailnet-resident.
The $19/month cost may be acceptable for a self-hosted deployment.

---

## 5. Ban / ToS Risk Assessment per Option

| Option | ToS Status | Ban Risk | Risk Profile |
|---|---|---|---|
| Cloud API (Meta) | Compliant | None | No risk if using a dedicated business number; cannot access personal inbox |
| whatsmeow | Violates WA ToS | Medium | Risk increases with: new accounts, VoIP numbers, high volume, suspicious activity. Established personal accounts (3+ years, real SIM) with low-volume ingestion-only use have historically had low ban rates, but 2024–2025 saw increased enforcement |
| Baileys | Violates WA ToS | Medium-High | Higher 2025 ban rate than whatsmeow; NPM supply-chain risk adds security concern |
| mautrix-whatsapp | Violates WA ToS | Medium | Same underlying protocol as whatsmeow; mautrix docs explicitly warn ban risk exists |
| WAHA (GOWS) | Violates WA ToS | Medium | Same as whatsmeow; WAHA Core is free but NOWEB/WEBJS engines may carry higher risk |

**Risk mitigation for whatsmeow:**
- Use an established personal number (not newly created).
- Avoid VoIP numbers.
- Keep outbound message volume low (ingestion-first, send rarely).
- Do not run alongside Android emulators or other unofficial clients on the same account.
- Implement exponential backoff and connection rate limiting.
- Store session credentials securely (PostgreSQL or encrypted file).
- Monitor for "your account may be at risk" signals and implement graceful degradation.

---

## 6. Data Model Mapping to Butler Connector Pattern

### 6.1 ingest.v1 Mapping

Following `docs/connectors/interface.md`, a WhatsApp connector would map events as:

```json
{
  "schema_version": "ingest.v1",
  "source": {
    "channel": "whatsapp",
    "provider": "whatsapp",
    "endpoint_identity": "whatsapp:<phone_number>"
  },
  "event": {
    "external_event_id": "<whatsapp_message_id>",
    "external_thread_id": "<chat_jid>",
    "observed_at": "<RFC3339 timestamp>"
  },
  "sender": {
    "identity": "<sender_phone_jid>"
  },
  "payload": {
    "raw": { "<full whatsapp message object>" },
    "normalized_text": "<extracted text or media caption>"
  },
  "control": {
    "idempotency_key": "whatsapp:<endpoint_identity>:<message_id>",
    "policy_tier": "default"
  }
}
```

**Field mapping details:**

| WhatsApp Field | ingest.v1 Field | Notes |
|---|---|---|
| `Info.ID` (message ID) | `event.external_event_id` | Stable per-message identifier |
| `Info.Chat` (JID) | `event.external_thread_id` | Group JID or peer JID for DMs |
| `Info.Timestamp` | `event.observed_at` | Unix timestamp → RFC3339 |
| `Info.Sender` (JID) | `sender.identity` | Sender's WhatsApp JID |
| Full message object | `payload.raw` | Serialized protobuf/JSON |
| Extracted text/caption | `payload.normalized_text` | From `Conversation`, `ExtendedTextMessage`, media caption |
| Configured phone | `source.endpoint_identity` | `whatsapp:<e164_number>` |

### 6.2 Message Types and Normalization

whatsmeow exposes the following message types (mapped from WhatsApp protobuf):

| WhatsApp Type | Normalized Text Strategy |
|---|---|
| `Conversation` | Use verbatim |
| `ExtendedTextMessage` | Use `Text` field; include URL context if present |
| `ImageMessage` | Use `Caption` field; annotate `[image]` if empty |
| `VideoMessage` | Use `Caption`; annotate `[video]` |
| `AudioMessage` / `PTTMessage` | Annotate `[voice message]` or `[audio]`; consider transcription hook |
| `DocumentMessage` | Use `FileName` and `Caption` |
| `StickerMessage` | Annotate `[sticker]` |
| `LocationMessage` | Format as `[location: lat, lon, name]` |
| `ContactMessage` | Format as `[contact: DisplayName]` |
| `ReactionMessage` | Annotate `[reaction: emoji to message_id]` |
| `PollCreationMessage` | Format as `[poll: question — option1, option2, ...]` |
| `ProtocolMessage` (revoke) | Annotate `[message deleted]` |
| `GroupInviteMessage` | Format as `[group invite: group_name]` |

### 6.3 Module Integration Points

Following the `Module` ABC (`src/butlers/modules/base.py`):

```python
class WhatsAppModule(Module):
    name = "whatsapp"
    dependencies = ["pipeline"]  # depends on the MessagePipeline module

    async def register_tools(self, mcp, config, db) -> None:
        # MCP tools for reading/sending WhatsApp messages
        # - user_whatsapp_send_message (approval_default="always")
        # - user_whatsapp_reply_to_message (approval_default="always")
        # - bot_whatsapp_send_message
        # - bot_whatsapp_get_messages (read inbox)
        # - bot_whatsapp_get_contacts
        # - bot_whatsapp_get_groups

    async def on_startup(self, config, db) -> None:
        # Start Go bridge sidecar (whatsmeow-based)
        # Register inbound event handler → submit to pipeline

    async def on_shutdown(self) -> None:
        # Gracefully terminate sidecar
```

**Tool naming follows identity-prefix convention** (`docs/roles/base_butler.md`):
- `user_whatsapp_*` — user-identity tools (require approval for send/reply)
- `bot_whatsapp_*` — bot-identity tools (configurable approval)

**Connector pattern:**
The WhatsApp module doubles as both a connector (ingestion) and a module (MCP tools).
The inbound message handler uses `MessagePipeline.process()` with:
- `source="whatsapp"`
- `source_channel="whatsapp"`
- `source_identity="whatsapp:<phone>"`
- `source_tool="bot_whatsapp_get_updates"` (analogous to Telegram pattern)

### 6.4 Database Schema

A WhatsApp module would need:

```sql
-- Inbound message inbox (mirrors telegram pattern)
CREATE TABLE whatsapp_message_inbox (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id       TEXT NOT NULL,        -- WhatsApp message ID
    chat_jid         TEXT NOT NULL,        -- Chat JID
    sender_jid       TEXT NOT NULL,        -- Sender JID
    message_type     TEXT NOT NULL,        -- text, image, audio, etc.
    content          JSONB NOT NULL,       -- full message payload
    normalized_text  TEXT,
    observed_at      TIMESTAMPTZ NOT NULL,
    ingested_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    dedupe_key       TEXT NOT NULL UNIQUE, -- whatsapp:<endpoint>:msg:<message_id>
    pipeline_request_id UUID
);

-- Session persistence for QR pairing
CREATE TABLE whatsapp_sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone_number    TEXT NOT NULL UNIQUE,
    device_id       TEXT NOT NULL,
    session_data    JSONB NOT NULL,  -- whatsmeow session keys
    paired_at       TIMESTAMPTZ NOT NULL,
    last_seen_at    TIMESTAMPTZ,
    active          BOOLEAN NOT NULL DEFAULT true
);
```

Alembic migration: `wa_001_create_whatsapp_tables.py` under
`alembic/versions/whatsapp/`.

---

## 7. Privacy and End-to-End Encryption Considerations

### 7.1 WhatsApp E2EE

WhatsApp uses the Signal Protocol for end-to-end encryption. All messages are encrypted
in transit between WhatsApp clients and WhatsApp's servers. However:

- The **whatsmeow library is a legitimate WhatsApp client**. It decrypts messages on
  receipt (like any other WhatsApp client). The decrypted plaintext is then stored
  in the butler's PostgreSQL database.
- This is functionally identical to WhatsApp Web reading your messages.
- The security model is: **messages are protected in transit but visible in plaintext
  on the butler host**. The butler host must be trusted.

### 7.2 Data Storage

- Message plaintext and metadata are stored in PostgreSQL.
- Media files (images, video, audio) are downloaded and may be stored locally or in
  object storage.
- WhatsApp's media CDN URLs are time-limited; files must be downloaded promptly.
- The butler's PostgreSQL database is not accessible outside the tailnet (architectural
  constraint).

### 7.3 Meta Data Access Policies

- Meta can see: message metadata (sender, recipient, timestamp, message size), but not
  message content (E2EE).
- Meta's ToS prohibits third-party client access but cannot technically prevent it
  (they can only detect and ban).
- GDPR and similar regulations: messages stored in the butler DB are the user's own
  messages accessed by the user. No third-party data processing for commercial purposes.
  The user is both the controller and processor of their own message data in this model.

### 7.4 Media Handling

WhatsApp media is encrypted at rest on WhatsApp's CDN. whatsmeow downloads and decrypts
media on demand. For the butler:
- Inline media should be downloaded immediately (CDN URLs expire after hours to days).
- Large media files (video) should be stored with a configurable size cap.
- Audio voice messages are candidates for transcription (external STT or local Whisper).

---

## 8. Tailnet Deployment Architecture

The recommended deployment for a tailnet-constrained environment:

```
Tailnet (WireGuard mesh)
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│  ┌─────────────────────────────────────────────────────┐  │
│  │  Butler Node (Docker host)                          │  │
│  │                                                     │  │
│  │  ┌─────────────────┐    ┌──────────────────────┐   │  │
│  │  │  Butler Daemon  │    │  WhatsApp Go Bridge  │   │  │
│  │  │  (Python/FastMCP│◄──►│  (whatsmeow sidecar) │   │  │
│  │  │   WhatsAppModule│    │  QR-paired session   │   │  │
│  │  └────────┬────────┘    └──────────┬───────────┘   │  │
│  │           │                        │               │  │
│  │  ┌────────▼────────┐               │ outbound TLS  │  │
│  │  │  PostgreSQL     │               │               │  │
│  │  │  (butler DB)    │               │               │  │
│  │  └─────────────────┘               │               │  │
│  └─────────────────────────────────────┘              │  │
│                                                        │  │
│  ┌─────────────────────────────────────────────────┐  │  │
│  │  Switchboard Butler (on tailnet)                │  │  │
│  └─────────────────────────────────────────────────┘  │  │
│                                                         │  │
└─────────────────────────────────────────────────────────┘  │
                                                              │
                    Internet (outbound only)                  │
                    WhatsApp WebSocket (TLS)                  │
                    Meta CDN (media download, TLS)            │
```

**No inbound connections from the public internet are required.** All connectivity is
outbound-initiated from the tailnet.

The Go bridge sidecar:
- Connects outbound to WhatsApp's WebSocket (`wss://web.whatsapp.com`)
- Downloads media from WhatsApp's CDN (outbound HTTPS)
- Listens on `127.0.0.1:<port>` or a Unix socket for the butler module to read events
- The butler module calls the sidecar to send messages (which the sidecar relays outbound)

---

## 9. Open Questions for Implementation

The following questions should be resolved in the implementation ticket:

1. **Go sidecar deployment:** Should the Go bridge be a standalone binary packaged with
   the butler Docker image, or a separate container? A subprocess approach (similar to
   the existing `LLMCLISpawner`) is simpler. A separate container gives process isolation
   and easier binary updates.

2. **Session recovery:** What is the recovery path when the whatsmeow session is
   invalidated (manual phone logout, account ban, protocol break)? Should the butler
   expose an MCP tool `whatsapp_pair_device` that outputs a QR code for the user to
   scan via the Telegram notification channel?

3. **Media storage policy:** Should media be stored inline in PostgreSQL (BYTEA) or
   offloaded to an object store (S3-compatible)? Large video files suggest object storage.

4. **Audio transcription:** Voice messages are common on WhatsApp. Should the module
   call a local Whisper instance or a cloud STT API for transcription on ingest?

5. **Historical backfill:** WhatsApp's multidevice protocol delivers recent message
   history on re-pair (limited to recent messages). Is a bounded backfill on startup
   desired?

6. **Group message ingestion policy:** Should all group chats be ingested or only
   selected groups? Unfiltered group ingestion could be noisy.

7. **Phone number registration:** The user must decide whether to dedicate a secondary
   number to the butler's whatsmeow session or use their primary number (with attendant
   risk of primary account impact on ban).

8. **Connector vs. module distinction:** The existing Telegram pattern uses the module
   as both a connector and a module. This should be documented explicitly for WhatsApp
   to avoid architectural drift.

---

## 10. Implementation Checklist (for future ticket)

When the implementation ticket is created, the following steps are required:

1. Create `roster/{butler-name}/` with MANIFESTO, CLAUDE, AGENTS, butler.toml.
2. Implement Go bridge binary (`whatsapp-bridge/`) wrapping whatsmeow.
3. Implement `WhatsAppModule` in `src/butlers/modules/whatsapp.py`.
4. Write Alembic migration `wa_001_create_whatsapp_tables.py`.
5. Register `whatsapp` channel in Switchboard ingress dedupe contract.
6. Add connector profile at `docs/connectors/whatsapp.md`.
7. Write unit tests for module tools, migration, and ingestion normalization.
8. Update `docs/connectors/interface.md` to reference `whatsapp.md`.

---

## 11. References

- [WhatsApp Business Platform Pricing](https://business.whatsapp.com/products/platform-pricing)
- [WhatsApp Business API Pricing Update July 2025 (ControlHippo)](https://controlhippo.com/blog/whatsapp/whatsapp-business-api-pricing-update/)
- [WhatsApp Cloud API vs On-Premises (Wuseller)](https://www.wuseller.com/blog/the-real-difference-between-whatsapp-cloud-api-and-on-prem-api-2025-guide/)
- [mautrix-whatsapp GitHub](https://github.com/mautrix/whatsapp)
- [mautrix-whatsapp Docker Setup](https://docs.mau.fi/bridges/general/docker-setup.html?bridge=whatsapp)
- [mautrix-whatsapp Authentication Docs](https://docs.mau.fi/bridges/go/whatsapp/authentication.html)
- [whatsmeow GitHub (tulir)](https://github.com/tulir/whatsmeow)
- [lharries/whatsapp-mcp (reference architecture)](https://github.com/lharries/whatsapp-mcp)
- [Baileys ban risk issue #1869](https://github.com/WhiskeySockets/Baileys/issues/1869)
- [whatsmeow ban risk issue #810](https://github.com/tulir/whatsmeow/issues/810)
- [WAHA GitHub](https://github.com/devlikeapro/waha)
- [WhatsApp API Rate Limits (WATI)](https://www.wati.io/en/blog/whatsapp-business-api/whatsapp-api-rate-limits/)
- [WhatsApp Messaging Limits 2026 (Chatarmin)](https://chatarmin.com/en/blog/whats-app-messaging-limits)
- [WhatsApp MCP Server Guide (Toolworthy)](https://www.toolworthy.ai/blog/whatsapp-mcp-ultimate-guide)
