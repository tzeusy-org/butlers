# Connector Gap Analysis

> **Purpose:** Comprehensive catalogue of potential connectors for the Butlers ecosystem, prioritized by practical value for a single-user personal assistant.
> **Status:** Planning document. Not a commitment to build everything listed here.
> **Last updated:** 2026-03-25

## How to Read This Document

Each connector entry includes:
- **Butler(s):** Which specialist butler(s) benefit most
- **Data type:** Messages, structured data, events, files, etc.
- **Integration method:** API polling, webhook, file watch, SDK, etc.
- **Priority:** P0 (build next) through P3 (speculative / future)
- **Complexity:** Low / Medium / High
- **Key challenges:** Auth, rate limits, data format, privacy, ToS risk

**Priority rationale:**
- **P0** — High daily-use value, proven data source, clear integration path
- **P1** — Strong value but blocked by complexity, API limitations, or lower frequency of use
- **P2** — Useful but niche, or dependent on P0/P1 connectors being in place first
- **P3** — Speculative, limited API availability, or marginal personal-assistant value

## Current State

| Connector | Status | Channel |
|---|---|---|
| Gmail | Stable | `email` |
| Telegram Bot | Stable | `telegram` |
| Telegram User Client | Stable | `telegram_user_client` |
| Live Listener (audio) | Evolving | `voice` |
| WhatsApp User Client | In progress (openspec) | `whatsapp_user_client` |
| Discord User Client | Draft (archived target-state) | `discord` |

All connectors follow the same pattern: standalone process, ingest.v1 envelope normalization, MCP submission to Switchboard, cursor-based checkpointing, heartbeat liveness.

---

## 1. Messaging

### 1.1 WhatsApp User Client

> **Status: IN PROGRESS** — See `openspec/changes/whatsapp-connector/`

- **Butler(s):** Relationship, General, all (via Switchboard routing)
- **Data type:** Messages (text, media metadata), group messages, reactions
- **Integration method:** Go sidecar (whatsmeow) wrapping WhatsApp Web multidevice protocol, local IPC
- **Priority:** P0
- **Complexity:** High
- **Key challenges:** Go sidecar dependency (first non-Python component), QR pairing ceremony, session persistence, WhatsApp ToS / ban risk for unofficial clients, media CDN URL expiry, no official API for personal accounts

### 1.2 Signal

- **Butler(s):** Relationship, General
- **Data type:** Messages (text, attachments), group messages, reactions, read receipts
- **Integration method:** signal-cli (Java CLI tool) or libsignal via signal-cli REST API daemon. Similar sidecar pattern to WhatsApp bridge.
- **Priority:** P2
- **Complexity:** High
- **Key challenges:** signal-cli requires a dedicated phone number for registration (cannot share with phone app since Signal enforces single-device primary). Alternatively, use Signal's linked-device protocol (like WhatsApp multidevice) but signal-cli support is fragile. Java runtime dependency. No official bot or integration API. Privacy-focused community may resist scraping patterns. End-to-end encryption means the sidecar must hold key material.
- **Notes:** Only valuable if the user actively uses Signal. The linked-device approach mirrors WhatsApp's multidevice pattern but is less mature. Consider only after WhatsApp connector proves the sidecar model.

### 1.3 iMessage

- **Butler(s):** Relationship, General
- **Data type:** Messages, attachments, reactions, tapbacks, read receipts
- **Integration method:** macOS-only. Options: (a) AppleScript/JXA automation on a Mac, (b) `imessage-rest` or `pypush` libraries, (c) parse `chat.db` SQLite directly from `~/Library/Messages/`
- **Priority:** P2
- **Complexity:** High
- **Key challenges:** Requires macOS host (incompatible with Linux Docker deployment). Apple actively blocks third-party iMessage access. Direct `chat.db` polling is read-only and fragile across macOS updates. No official API. Full Disk Access permission required. BlueBubbles/AirMessage servers exist but add another sidecar. Fundamentally platform-locked.
- **Notes:** Practical only if the Butlers host runs on macOS or a dedicated Mac Mini acts as a bridge. The `chat.db` polling approach is the most reliable for read-only ingestion. Outbound sending via AppleScript is possible but brittle.

### 1.4 SMS / RCS

- **Butler(s):** Relationship, General, Finance (OTP/2FA codes)
- **Data type:** SMS text messages, MMS media
- **Integration method:** (a) Android: KDE Connect or Tasker webhook forwarding, (b) Twilio/Vonage API with a dedicated number, (c) Google Messages web interface scraping (fragile), (d) Modem AT commands via USB GSM dongle
- **Priority:** P3
- **Complexity:** Medium (Twilio) to High (phone integration)
- **Key challenges:** No universal API. Phone-based approaches require always-on phone with forwarding app. Twilio requires a separate number and per-message cost. Privacy sensitivity of SMS content (2FA codes, bank alerts). Carrier restrictions on programmatic access.
- **Notes:** SMS is declining in relevance for interpersonal messaging but remains critical for 2FA, delivery notifications, and appointment reminders. A Twilio-based connector with a dedicated number is the cleanest approach but means contacts must text that number specifically. Phone-forwarding via Tasker is more practical for capturing existing SMS flow.

### 1.5 Discord User Client

> **Status: DRAFT** — See `src/butlers/connectors/discord_user.py`

- **Butler(s):** Relationship, Education (study servers), General
- **Data type:** Messages, embeds, reactions, threads, voice channel presence
- **Integration method:** Discord Gateway WebSocket (user token) or Discord Bot API (bot token with guild membership)
- **Priority:** P2
- **Complexity:** Medium
- **Key challenges:** User-token automation violates Discord ToS (account ban risk). Bot-token approach is ToS-compliant but requires server admin to add the bot and only sees channels the bot has access to. Rate limiting (50 requests/second). Large volume of messages in active servers requires aggressive discretion filtering. Archived as target-state, needs privacy/consent review.
- **Notes:** The bot-token approach is safer and sufficient for servers where the user is admin. The user-token approach captures DMs and all visible servers but carries ban risk similar to WhatsApp.

### 1.6 Slack (Personal Workspace / Multi-workspace)

- **Butler(s):** General, Relationship (work relationships)
- **Data type:** Messages, threads, reactions, files, channel events
- **Integration method:** Slack Web API + Events API (webhook) or Socket Mode (WebSocket, no public URL needed)
- **Priority:** P2
- **Complexity:** Medium
- **Key challenges:** OAuth scopes are workspace-scoped; multi-workspace requires separate tokens. Enterprise Grid adds complexity. Socket Mode is ideal for tailnet-first deployment (no public webhook endpoint needed). High message volume in active workspaces. Workspace admin approval may be required for custom apps. Free-tier workspaces have 90-day message history limits.
- **Notes:** Socket Mode fits the Butlers deployment model well (no public ingress). Most valuable for users who use Slack for personal projects or small-team collaboration. Enterprise Slack connectors are out of scope (IT policy barriers).

---

## 2. Financial

### 2.1 Bank Transaction Feed (Plaid / Open Banking)

- **Butler(s):** Finance
- **Data type:** Structured data (transactions, balances, account metadata)
- **Integration method:** Plaid API polling (Link token flow for account connection, `/transactions/sync` for incremental fetch) or Open Banking APIs (PSD2 in EU, CDR in Australia)
- **Priority:** P1
- **Complexity:** Medium
- **Key challenges:** Plaid requires paid plan for production access ($0+ for personal use via Plaid development mode with limited institutions). OAuth/Link flow for initial account connection requires a web UI. Plaid webhooks need public endpoint or can be polled. PII sensitivity (full transaction history). Regional API availability (Plaid is US/UK/EU focused; Open Banking varies by country). Institution coverage gaps. Multi-factor auth re-linking periodically required.
- **Notes:** This is the single highest-value connector for the Finance Butler beyond email. Email-based transaction parsing (already handled by Gmail connector + Finance butler routing) covers many cases, but direct bank feeds provide real-time balances, categorized transactions, and account-level insights that email cannot. Start with Plaid development mode for proof of concept.

### 2.2 Cryptocurrency Exchange APIs

- **Butler(s):** Finance
- **Data type:** Structured data (portfolio balances, trade history, price alerts)
- **Integration method:** REST API polling per exchange (Coinbase, Binance, Kraken). Most use API key + secret auth. CCXT library provides unified interface across 100+ exchanges.
- **Priority:** P3
- **Complexity:** Low (via CCXT)
- **Key challenges:** API key management (read-only keys recommended). Rate limits vary by exchange. Price volatility means polling frequency matters. Multiple exchanges means multiple credentials. Tax reporting implications of trade history ingestion.
- **Notes:** Only relevant if the user holds crypto. CCXT makes the technical integration straightforward. The Finance Butler already handles email-based exchange notifications; direct API access adds portfolio snapshots and trade history.

### 2.3 Payment Processor Notifications (Stripe, PayPal)

- **Butler(s):** Finance
- **Data type:** Events (payment received/sent, subscription changes, disputes)
- **Integration method:** Webhook receivers (Stripe webhooks, PayPal IPN/webhooks) or API polling
- **Priority:** P3
- **Complexity:** Low (Stripe) to Medium (PayPal)
- **Key challenges:** Requires public webhook endpoint or tunnel (conflicts with tailnet-first architecture). Stripe webhook signing verification. PayPal's API is notoriously inconsistent. Only relevant for users who are merchants/freelancers.
- **Notes:** For most personal-assistant users, payment notifications arrive via email and are already captured by the Gmail connector. Direct integration only adds value for high-volume freelancers or small business owners who need real-time payment tracking.

---

## 3. Health & Fitness

### 3.1 Apple Health Export

- **Butler(s):** Health
- **Data type:** Structured data (steps, heart rate, sleep, workouts, body measurements, lab results)
- **Integration method:** File watch on periodic Apple Health XML/ZIP export. Options: (a) Manual export from iPhone, (b) Auto-export via iOS Shortcuts automation to iCloud/Dropbox/SFTP, (c) Health Auto Export app (third-party, supports REST webhook)
- **Priority:** P1
- **Complexity:** Low (file parsing) to Medium (automated export pipeline)
- **Key challenges:** No direct API from Apple. Export is a large XML file (can be 1GB+ for years of data). Incremental sync not natively supported (must diff against previous export). Requires user to set up iOS Shortcut or third-party app for automation. Data schema is CDA (Clinical Document Architecture) XML — verbose but well-documented.
- **Notes:** Apple Health is the single richest personal health data aggregator for iPhone users. Even with manual periodic exports, the Health Butler can build a comprehensive longitudinal health profile. The Health Auto Export app ($3) sends JSON webhooks on schedule — this is the cleanest automated path.

### 3.2 Fitbit / Garmin / Wearable APIs

- **Butler(s):** Health
- **Data type:** Structured data (steps, heart rate, sleep stages, stress, SpO2, workouts, GPS tracks)
- **Integration method:** OAuth2 REST API polling. Fitbit Web API (15-minute intraday data). Garmin Connect API (daily summaries, activity files). Withings API (weight, blood pressure, sleep).
- **Priority:** P2
- **Complexity:** Medium
- **Key challenges:** OAuth2 flows with periodic re-authorization. Fitbit rate limits (150 requests/hour). Garmin has no official public API (must use Garmin Connect unofficial endpoints or register as Garmin developer partner). Withings has a proper developer API. Data normalization across different wearable schemas. Multiple device support.
- **Notes:** If the user has Apple Health with a wearable that syncs to it, the Apple Health export connector (3.1) already captures this data indirectly. Direct wearable APIs add value for real-time intraday data (heart rate alerts, live workout tracking) that Apple Health export batch processing misses.

### 3.3 FHIR Health Records

- **Butler(s):** Health
- **Data type:** Structured data (lab results, medications, conditions, immunizations, encounters, clinical notes)
- **Integration method:** FHIR R4 REST API polling. US health systems increasingly expose patient-facing FHIR endpoints via SMART on FHIR authorization. Apple Health Records also uses FHIR.
- **Priority:** P2
- **Complexity:** High
- **Key challenges:** FHIR endpoint discovery is fragmented (no universal directory). SMART on FHIR OAuth2 flow per health system. Data quality varies wildly between providers. US-centric (21st Century Cures Act mandates patient API access). Highly sensitive PII/PHI requiring encryption at rest and in transit. Terminology mapping (SNOMED, LOINC, ICD-10). Large initial data load.
- **Notes:** Extremely high-value for the Health Butler's clinical record awareness, but the fragmentation of health system APIs makes this a "build for one provider at a time" effort. Start with the user's primary health system. Apple Health Records (available on iPhone) aggregates FHIR data from connected providers and may be a simpler starting point via the Apple Health export path.

### 3.4 Pharmacy / Medication APIs

- **Butler(s):** Health
- **Data type:** Structured data (prescriptions, refill status, medication interactions)
- **Integration method:** Varies. Options: (a) Pharmacy chain APIs (CVS, Walgreens — limited/nonexistent public APIs), (b) SureScripts (requires healthcare entity registration), (c) Parse pharmacy notification emails via Gmail connector, (d) Manual entry via Health Butler MCP tools
- **Priority:** P3
- **Complexity:** High
- **Key challenges:** No universal pharmacy API. SureScripts is gated to licensed healthcare entities. Pharmacy chains protect prescription data behind consumer portals with CAPTCHAs. Most practical approach is parsing pharmacy notification emails (already covered by Gmail connector) and manual entry.
- **Notes:** Email parsing plus manual entry is likely sufficient. Direct pharmacy API integration is not practical for a personal assistant due to API access restrictions.

---

## 4. Productivity & Knowledge

### 4.1 Google Calendar (CalDAV / Google Calendar API)

- **Butler(s):** General, Travel, Relationship, Health
- **Data type:** Events (calendar entries, RSVPs, reminders, recurring events)
- **Integration method:** Google Calendar API (already have Google OAuth infrastructure from Gmail). Polling via `events.list` with `syncToken` for incremental updates. Push notifications via Pub/Sub (same pattern as Gmail).
- **Priority:** P0
- **Complexity:** Low
- **Key challenges:** OAuth scope expansion (add `calendar.readonly` to existing Google OAuth flow). Recurring event expansion. Timezone handling. Multi-calendar support (personal, work, shared). Already have Google OAuth infrastructure — this is mostly "add another scope and build the connector."
- **Notes:** Natural extension of existing Google integration. Calendar awareness is foundational for multiple butlers: Travel (flight times vs calendar conflicts), Relationship (birthday reminders, meeting prep), Health (appointment tracking), General (daily schedule briefing). The Google OAuth credential lifecycle is already built (`core-credentials` spec).

### 4.2 CalDAV (iCloud, Fastmail, Nextcloud, etc.)

- **Butler(s):** General, Travel, Relationship
- **Data type:** Events (iCalendar/ICS format)
- **Integration method:** CalDAV PROPFIND/REPORT polling with ctag/etag-based change detection. Libraries: `caldav` (Python).
- **Priority:** P2
- **Complexity:** Medium
- **Key challenges:** CalDAV is a standard but implementations vary. iCloud CalDAV requires app-specific passwords. Authentication methods differ per provider. Sync token support is inconsistent. Must handle recurring event expansion client-side. Timezone VTIMEZONE parsing.
- **Notes:** Only needed if the user's primary calendar is not Google. If Google Calendar connector (4.1) is built first, CalDAV adds value for iCloud/Fastmail/self-hosted calendar users.

### 4.3 Microsoft Outlook / Microsoft 365

- **Butler(s):** General, Finance (work emails), Relationship
- **Data type:** Messages (email), events (calendar), contacts, files (OneDrive)
- **Integration method:** Microsoft Graph API with OAuth2 PKCE flow. Delta queries for incremental sync. Webhook subscriptions for real-time events.
- **Priority:** P2
- **Complexity:** Medium
- **Key challenges:** Azure AD app registration required. OAuth2 flow with Microsoft identity platform. Graph API rate limits (per-app and per-tenant). Webhook subscriptions require public HTTPS endpoint (or use polling with delta tokens). Separate from personal vs. work Microsoft accounts. Personal Microsoft accounts have limited Graph API scope.
- **Notes:** Essential if the user's primary email/calendar is Outlook. For users on Gmail, this is lower priority. The connector pattern would mirror Gmail closely but with Microsoft Graph instead of Google APIs.

### 4.4 Notion

- **Butler(s):** General, Education
- **Data type:** Structured data (pages, databases, blocks, comments)
- **Integration method:** Notion API v1 polling. Search endpoint for broad change detection, then page/database-specific queries for details. No webhook/push support.
- **Priority:** P2
- **Complexity:** Medium
- **Key challenges:** Notion API is read-heavy but lacks change detection (no sync tokens or delta queries). Must poll and diff. Rich block types (toggles, callouts, databases, embeds) require careful normalization. Rate limit: 3 requests/second. Nested page hierarchies can be deep. Internal integration token (simpler) vs. OAuth (multi-workspace).
- **Notes:** Bidirectional sync is the dream (butler writes notes back to Notion) but read-only ingestion is the starting point. Most valuable for users who use Notion as their primary knowledge base. The General Butler or Education Butler could use Notion content for context.

### 4.5 Obsidian Vault

- **Butler(s):** General, Education, Health (health journal)
- **Data type:** Files (Markdown), structured data (frontmatter YAML, tags, links)
- **Integration method:** File system watcher (inotify/fswatch) on the vault directory. Parse Markdown + YAML frontmatter. Track changes via file modification timestamps or git history if vault is git-backed.
- **Priority:** P2
- **Complexity:** Low
- **Key challenges:** Requires vault directory to be accessible from the Butlers host (local path, Syncthing, or mounted volume). No API — pure filesystem. Wikilink resolution (`[[page]]`) requires building a link graph. Plugin-generated files (daily notes, templates) should be filtered. Large vaults (10k+ notes) need efficient change detection.
- **Notes:** Extremely low complexity if the vault is accessible as a mounted volume. Obsidian Sync makes the vault available across devices but does not expose an API — the connector watches the synced local copy. This is one of the simplest possible connectors: watch directory, parse Markdown, submit text content as ingestion events.

### 4.6 Google Drive / Dropbox / Cloud Storage

- **Butler(s):** General, Finance (tax documents), Education
- **Data type:** Files (documents, spreadsheets, PDFs), events (file changes, shares, comments)
- **Integration method:** Google Drive API (Changes API with page tokens), Dropbox API (list_folder/continue with cursor). OAuth2 auth (Google already available).
- **Priority:** P2
- **Complexity:** Medium
- **Key challenges:** File content extraction (PDF parsing, document format conversion). Large files and binary content (images, video) are not useful for text-based butler processing. Change volume can be high in active workspaces. Storage and indexing of file metadata vs. content. Google Drive API shares OAuth infrastructure with Gmail/Calendar.
- **Notes:** File change notifications are useful ("your accountant shared a new tax document") but full-text indexing of cloud storage is a different problem than message ingestion. Start with metadata-only (file names, sharing events, comments) and add content extraction for specific file types later.

### 4.7 Browser Bookmarks / Reading List

- **Butler(s):** General, Education
- **Data type:** Structured data (URLs, titles, tags, timestamps)
- **Integration method:** (a) Browser extension that pushes bookmarks to a webhook, (b) Parse Chrome/Firefox bookmark JSON/SQLite export files, (c) Raindrop.io or Pocket API polling
- **Priority:** P3
- **Complexity:** Low
- **Key challenges:** Browser bookmark files are not designed for real-time sync. Extension-based approach requires building and maintaining a browser extension. Raindrop.io/Pocket APIs are simpler but require the user to adopt those tools. URL content extraction (fetching and summarizing bookmarked pages) adds value but is a separate concern.
- **Notes:** Low standalone value. Bookmarks are useful context for the General Butler's memory but are infrequently accessed data. A Pocket/Raindrop.io connector via their REST APIs is the lowest-effort path.

---

## 5. Social Media

### 5.1 Twitter / X

- **Butler(s):** General, Relationship
- **Data type:** Messages (DMs, mentions, replies), events (likes, retweets, follows)
- **Integration method:** X API v2 (OAuth2 PKCE). Filtered stream for real-time mentions. Polling for DMs and timeline.
- **Priority:** P3
- **Complexity:** Medium
- **Key challenges:** X API access tiers are expensive (Basic: $100/month for 10k tweets read, Pro: $5000/month). Free tier only allows posting, not reading. API instability and policy changes. Rate limits are strict. DM access requires elevated permissions. The API pricing alone makes this impractical for a personal assistant.
- **Notes:** Unless the user is a heavy Twitter/X user who needs DM management or mention monitoring, the cost-to-value ratio is poor. Email notifications from X (already captured by Gmail connector) provide a subset of this functionality at zero cost.

### 5.2 LinkedIn

- **Butler(s):** Relationship (professional network), General
- **Data type:** Messages, connection requests, post notifications, job alerts
- **Integration method:** No viable official API for personal use. LinkedIn API requires partner-level approval for messaging access. Options: (a) Email notification parsing via Gmail connector (practical), (b) Browser automation (fragile, ToS violation), (c) LinkedIn messaging via IMAP bridge (nonexistent)
- **Priority:** P3
- **Complexity:** High (for direct integration), Already covered (via email)
- **Key challenges:** LinkedIn aggressively blocks automation and scraping. API access for messaging is restricted to approved partners. No personal-use developer API. Browser automation breaks on LinkedIn's anti-bot measures.
- **Notes:** Email notification parsing is the only practical approach. LinkedIn sends email notifications for messages, connection requests, and post engagement — the Gmail connector already captures these. Direct API integration is not feasible for personal use.

### 5.3 Instagram

- **Butler(s):** Relationship
- **Data type:** Messages (DMs), stories, post notifications
- **Integration method:** Instagram Graph API (business/creator accounts only) or Instagram Basic Display API (deprecated 2024). No API for personal account DMs.
- **Priority:** P3
- **Complexity:** High
- **Key challenges:** No DM access via API for personal accounts. Graph API requires business or creator account. Meta's API policies are restrictive and frequently change. Scraping violates ToS. Similar to LinkedIn — email notifications are the practical path.
- **Notes:** Not feasible for personal accounts. Instagram DM notifications via email (captured by Gmail connector) are the only viable approach.

### 5.4 Reddit

- **Butler(s):** General, Education
- **Data type:** Messages (DMs, comment replies), events (post notifications, subreddit activity)
- **Integration method:** Reddit API (OAuth2, free tier available). PRAW library. Polling inbox endpoint for DMs and replies.
- **Priority:** P3
- **Complexity:** Low
- **Key challenges:** Reddit API rate limit: 100 requests/minute (generous). OAuth2 app registration is free. Content volume can be very high in active subreddits. Primarily a content consumption platform — ingesting all subreddit activity is noise. Best scoped to inbox (DMs + replies to user's posts/comments).
- **Notes:** Low priority unless the user is an active Reddit participant who wants reply/DM management. The API is well-documented and free, making technical integration easy, but the personal-assistant value is limited.

---

## 6. Smart Home

### 6.1 Home Assistant

- **Butler(s):** Home
- **Data type:** Events (state changes, automations, device status), structured data (entity states, sensor readings)
- **Integration method:** Home Assistant REST API + WebSocket API for real-time event streaming. Long-lived access token auth (no OAuth needed).
- **Priority:** P0
- **Complexity:** Low
- **Key challenges:** Home Assistant must be network-accessible from the Butlers host (typically same LAN or Tailnet). WebSocket API provides real-time event streams — ideal for push-based connector. High event volume (sensor updates every few seconds) requires aggressive filtering to avoid flooding the Switchboard. Entity naming conventions vary by user's HA config.
- **Notes:** This is THE smart home connector. Home Assistant is the de facto hub for smart home automation, and its API is excellent. The WebSocket event stream maps directly to the connector push model. The Home Butler's value proposition depends entirely on this connector existing. Start with a curated entity allowlist (doors, lights, climate, presence) rather than ingesting all events.

### 6.2 MQTT Broker

- **Butler(s):** Home
- **Data type:** Events (sensor data, device commands, status messages)
- **Integration method:** MQTT client subscribing to configured topic patterns. Libraries: `aiomqtt` (asyncio-native).
- **Priority:** P2 (P1 if no Home Assistant)
- **Complexity:** Low
- **Key challenges:** MQTT is a transport layer, not a device layer — messages have no standard schema. Topic structure and payload format vary by device/firmware. Requires user to configure which topics to subscribe to. High message volume for frequent sensors. TLS and authentication configuration.
- **Notes:** If the user runs Home Assistant, MQTT is redundant (HA already aggregates MQTT devices). Direct MQTT is valuable for users with custom IoT setups that bypass HA, or for capturing raw sensor data that HA does not expose. Very simple connector technically — just an MQTT subscriber that wraps payloads in ingest.v1 envelopes.

### 6.3 Zigbee / Z-Wave Hub Direct

- **Butler(s):** Home
- **Data type:** Events (device state changes, sensor readings)
- **Integration method:** Hub-specific APIs (Hubitat Maker API, SmartThings API, Hue Bridge API)
- **Priority:** P3
- **Complexity:** Medium (per hub)
- **Key challenges:** Each hub has its own API. Hubitat Maker API is HTTP-based with webhooks. SmartThings API is cloud-based REST. Hue Bridge is local REST. No unified standard. If the user has Home Assistant, these devices are already accessible via HA integrations.
- **Notes:** Only relevant if the user does not use Home Assistant and has a standalone hub. Home Assistant connector (6.1) is strictly superior for users who run HA.

---

## 7. Travel & Location

### 7.1 Flight / Airline Tracking

- **Butler(s):** Travel
- **Data type:** Structured data (flight status, delays, gate changes, check-in windows)
- **Integration method:** (a) AeroAPI / FlightAware API (paid, ~$1/query), (b) AviationStack (free tier: 100 requests/month), (c) Parse airline confirmation emails via Gmail connector (already works), (d) Flighty app webhook (if supported)
- **Priority:** P2
- **Complexity:** Medium
- **Key challenges:** Flight tracking APIs are paid. AeroAPI pricing makes continuous polling expensive. Email parsing already captures booking confirmations. Real-time flight status (delays, gate changes) is the incremental value over email, but requires active polling during travel days only. Flight number extraction from emails is needed to seed the API queries.
- **Notes:** The Travel Butler already gets booking confirmations via Gmail. The gap is real-time status updates during travel. A hybrid approach works: extract flight numbers from email-ingested bookings, then activate API polling only on travel days. AviationStack free tier (100 requests/month) is sufficient for occasional travel.

### 7.2 Hotel / Booking Platforms

- **Butler(s):** Travel
- **Data type:** Structured data (reservations, check-in/out times, loyalty points)
- **Integration method:** No unified API. Options: (a) Parse booking confirmation emails (already works via Gmail), (b) Individual hotel chain APIs (Marriott, Hilton — mostly limited to loyalty program members), (c) Booking.com API (affiliate-only), (d) TripIt API (aggregates travel emails)
- **Priority:** P3
- **Complexity:** Medium (TripIt) or High (per-chain)
- **Key challenges:** No universal hotel API. Major booking platforms do not offer personal-use APIs. Email parsing is the dominant approach and is already functional. TripIt automatically parses forwarded confirmation emails — could act as an intermediary but adds a dependency.
- **Notes:** Email parsing covers 90% of the use case. Direct hotel API integration is not practical for a personal assistant. TripIt API could serve as a normalizer if the user already uses TripIt.

### 7.3 Google Maps / Navigation History

- **Butler(s):** Travel, General
- **Data type:** Structured data (location history, saved places, commute times, frequent routes)
- **Integration method:** Google Maps Timeline export (JSON via Google Takeout), or Google Maps Platform APIs (Directions, Places — paid per request)
- **Priority:** P3
- **Complexity:** Medium
- **Key challenges:** Google Timeline is being moved to on-device storage (2024+), making cloud API access deprecated. Google Takeout exports are manual and batch-oriented. Maps Platform APIs are pay-per-use and designed for app developers, not personal data access. Location history is extremely privacy-sensitive.
- **Notes:** Google is actively reducing cloud access to location history. The practical approach is periodic Google Takeout export processing, but this is batch-oriented and low-frequency. Real-time location tracking is better served by OwnTracks (7.4).

### 7.4 OwnTracks / GPS Location

- **Butler(s):** Home (presence detection), Travel (trip detection), General (location context)
- **Data type:** Events (location updates, geofence enter/exit, waypoints)
- **Integration method:** OwnTracks HTTP mode (phone pushes location to configurable endpoint) or MQTT mode (phone publishes to MQTT broker). Webhook receiver or MQTT subscriber.
- **Priority:** P1
- **Complexity:** Low
- **Key challenges:** Requires OwnTracks app on phone. HTTP mode needs a reachable endpoint (can use Tailnet). MQTT mode can piggyback on existing MQTT broker. Battery impact on phone (mitigated by significant-change mode). Privacy sensitivity of continuous location tracking. Geofence configuration needed for meaningful events (home, office, gym).
- **Notes:** High-value, low-complexity connector. OwnTracks is privacy-respecting (self-hosted), works on iOS and Android, and provides geofence enter/exit events that are immediately actionable: "user arrived home" triggers Home Butler, "user left for airport" triggers Travel Butler. The MQTT integration path is trivial if an MQTT broker is already running. HTTP mode fits the existing connector webhook pattern.

---

## 8. Media & Entertainment

### 8.1 Spotify / Music Streaming

- **Butler(s):** General, Relationship (shared playlists, concert attendance)
- **Data type:** Events (now playing, recently played), structured data (playlists, saved tracks, listening history)
- **Integration method:** Spotify Web API (OAuth2 PKCE). Polling `currently-playing` and `recently-played` endpoints.
- **Priority:** P3
- **Complexity:** Low
- **Key challenges:** OAuth2 flow with Spotify accounts. Rate limits are reasonable. "Currently playing" requires frequent polling for real-time awareness. Listening history is useful for mood detection and daily summaries but is niche. 50-track limit on recently-played endpoint.
- **Notes:** Fun but low practical value for a personal assistant. Most useful as ambient context ("user is listening to focus music — don't interrupt with low-priority notifications"). Could feed into the Health Butler's wellness tracking (sleep music patterns, stress indicators).

### 8.2 YouTube / Video

- **Butler(s):** Education, General
- **Data type:** Structured data (watch history, subscriptions, liked videos, playlists)
- **Integration method:** YouTube Data API v3 (Google OAuth — already have infrastructure). Polling activities feed and watch history.
- **Priority:** P3
- **Complexity:** Low
- **Key challenges:** Shares Google OAuth infrastructure with Gmail/Calendar. Watch history access may require additional OAuth scope. API quota is 10,000 units/day (generous for personal use). Content extraction from videos (transcripts) requires separate YouTube transcript API or yt-dlp.
- **Notes:** Low standalone value. YouTube watch history could feed Education Butler context ("user watched a 3-hour lecture on quantum computing") but this is marginal. Video transcript extraction is more interesting but is a content processing pipeline, not a connector.

### 8.3 Podcast Apps

- **Butler(s):** Education, General
- **Data type:** Structured data (subscriptions, listened episodes, progress, bookmarks)
- **Integration method:** Varies by app. Apple Podcasts has no API. Pocket Casts has an unofficial API. Overcast has no API. OPML export for subscriptions is universal but one-time. Podcast Index API for episode metadata.
- **Priority:** P3
- **Complexity:** Medium (fragmented ecosystem)
- **Key challenges:** No dominant podcast app has a proper API. OPML export captures subscriptions but not listening history. RSS feed parsing can track new episodes but not user engagement. Custom podcast apps with webhook support are the exception, not the rule.
- **Notes:** Not practical given the lack of APIs. If the user uses a podcast app with export/API capabilities, a narrow connector could be built, but this is too fragmented for general investment.

### 8.4 Reading Apps (Kindle, Pocket, Readwise)

- **Butler(s):** Education, General
- **Data type:** Structured data (highlights, annotations, reading progress, book metadata)
- **Integration method:** Readwise API (OAuth2, exports highlights from Kindle/Apple Books/Pocket/Instapaper). Kindle highlights via Readwise or direct `My Clippings.txt` file parsing. Pocket API (OAuth, articles and highlights).
- **Priority:** P2
- **Complexity:** Low (Readwise), Medium (direct Kindle)
- **Key challenges:** Kindle has no API — highlights are accessible via Readwise integration or parsing the `My Clippings.txt` file from the device. Readwise is the best aggregator but requires a paid subscription ($8/month). Pocket API is free and well-documented. Reading progress tracking is limited to what each platform exposes.
- **Notes:** Readwise is the ideal single connector for reading highlights across all platforms. If the user has Readwise, this is a low-complexity, high-value connector for the Education Butler. Without Readwise, Pocket API is the next best option.

---

## 9. Commerce & Receipts

### 9.1 Amazon Order History

- **Butler(s):** Finance, General
- **Data type:** Structured data (orders, delivery tracking, spending, returns)
- **Integration method:** (a) Parse Amazon order confirmation/shipping emails via Gmail (already works), (b) Amazon Order History Report (CSV export from account settings), (c) Amazon SP-API (seller API — not applicable for personal purchases)
- **Priority:** P3
- **Complexity:** Low (email parsing), Medium (CSV export automation)
- **Key challenges:** Amazon has no consumer-facing API for order history. Email parsing already captures order confirmations and shipping notifications. CSV export from Amazon account is comprehensive but manual. Browser automation to download CSV is fragile and ToS-violating.
- **Notes:** Email parsing covers this adequately. The Gmail connector already ingests Amazon order confirmations, and the Finance Butler routes and processes them. No additional connector needed in practice.

### 9.2 Grocery Delivery (Instacart, Amazon Fresh, etc.)

- **Butler(s):** Finance, Health (nutrition tracking), Home
- **Data type:** Structured data (orders, items, prices, delivery times)
- **Integration method:** Email notification parsing via Gmail connector. No consumer APIs available.
- **Priority:** P3
- **Complexity:** N/A (already covered by email)
- **Key challenges:** No APIs. Email parsing is the only path and is already functional.
- **Notes:** Already covered by Gmail connector. Grocery order emails are parsed by the Finance Butler for spending tracking and potentially by the Health Butler for nutrition awareness.

### 9.3 Receipt Email Parsing (Enhanced)

- **Butler(s):** Finance
- **Data type:** Structured data (merchant, amount, items, date, category)
- **Integration method:** Not a separate connector — this is an enhancement to the Gmail connector's ingestion policy and the Finance Butler's processing logic. Use structured email parsing (HTML tables, JSON-LD in email headers, common receipt templates).
- **Priority:** P1 (as a Finance Butler feature, not a new connector)
- **Complexity:** Medium
- **Key challenges:** Receipt email formats are wildly inconsistent. Major merchants (Amazon, Apple, Uber, airlines) have parseable templates. LLM-based extraction handles the long tail. JSON-LD schema.org/Order annotations in email HTML are underutilized.
- **Notes:** This is not a new connector but an enhancement to existing Gmail ingestion. Listed here for completeness because it fills a "connector gap" in terms of structured financial data extraction.

---

## 10. Government & Official

### 10.1 Tax Portal Integration

- **Butler(s):** Finance
- **Data type:** Structured data (tax filings, refund status, payment confirmations)
- **Integration method:** No APIs. IRS (US) has no consumer API. HMRC (UK) has a limited personal tax API. Most countries provide no programmatic access. Email notifications are the only viable path.
- **Priority:** P3
- **Complexity:** High (where APIs exist), N/A (where they don't)
- **Key challenges:** Government APIs are rare, bureaucratic to access, and jurisdiction-specific. Most tax information arrives via email or postal mail.
- **Notes:** Not practical as a dedicated connector. Email notifications from tax authorities are captured by Gmail. The Finance Butler can be taught to recognize and flag tax-related emails with higher priority.

### 10.2 Immigration / Visa Status

- **Butler(s):** Travel, General
- **Data type:** Structured data (application status, expiry dates, appointment schedules)
- **Integration method:** No APIs in most jurisdictions. USCIS (US) has a case status check page but no API. Email/SMS notifications are the primary notification channel.
- **Priority:** P3
- **Complexity:** N/A
- **Key challenges:** No APIs. Government portals are hostile to automation. Status checks are infrequent (weekly at most).
- **Notes:** Email notifications are sufficient. The Travel Butler can flag visa expiry dates from email-parsed documents.

### 10.3 Vehicle Registration / Insurance

- **Butler(s):** Finance, General
- **Data type:** Structured data (registration renewal dates, insurance policy details, payment due dates)
- **Integration method:** Email notification parsing. No consumer APIs.
- **Priority:** P3
- **Complexity:** N/A
- **Key challenges:** Jurisdiction-specific. No APIs. All relevant notifications arrive via email or postal mail.
- **Notes:** Already covered by email. The Finance Butler can track renewal dates from parsed emails.

---

## 11. IoT & Environmental Sensors

### 11.1 Weather Station (Personal)

- **Butler(s):** Home, Health (air quality impact on health)
- **Data type:** Structured data (temperature, humidity, barometric pressure, rain, wind, UV index)
- **Integration method:** (a) Weather station API if cloud-connected (Ambient Weather, Davis, Netatmo — all have REST APIs), (b) MQTT if station publishes locally, (c) Home Assistant integration (if HA connector exists, this is automatic)
- **Priority:** P3 (P2 if no Home Assistant)
- **Complexity:** Low
- **Key challenges:** Station-specific APIs. Most personal weather stations sync to a cloud service with a REST API. If Home Assistant is present, weather station data is already available via HA entities — no separate connector needed.
- **Notes:** If the Home Assistant connector (6.1) is built, personal weather station data flows through it automatically. A standalone connector only makes sense for users without HA who have a cloud-connected station.

### 11.2 Air Quality Monitor

- **Butler(s):** Health, Home
- **Data type:** Structured data (PM2.5, PM10, CO2, VOC, temperature, humidity)
- **Integration method:** (a) PurpleAir API (public sensors, free), (b) Awair API (personal device, OAuth), (c) IQAir API, (d) Home Assistant integration
- **Priority:** P3
- **Complexity:** Low
- **Key challenges:** Device-specific APIs. Most air quality monitors sync to cloud with REST API. Same story as weather stations — HA connector subsumes this.
- **Notes:** Same rationale as weather stations. Home Assistant connector handles this. Standalone only for users without HA.

### 11.3 Security Cameras / NVR

- **Butler(s):** Home
- **Data type:** Events (motion detection, person detection, doorbell press, package delivery)
- **Integration method:** (a) ONVIF protocol (standard for IP cameras), (b) Camera-specific APIs (Unifi Protect, Reolink, Hikvision), (c) NVR APIs (Frigate, Blue Iris), (d) Home Assistant integration
- **Priority:** P2
- **Complexity:** Medium
- **Key challenges:** ONVIF is a standard but implementation quality varies. Frigate (popular open-source NVR) has an MQTT event interface that pairs well with MQTT connector (6.2) or HA connector (6.1). Video content is not useful for text-based butlers — only event metadata (motion detected, person identified, doorbell pressed) is ingested. Privacy implications of camera event logging.
- **Notes:** Event-based ingestion only (no video streaming). Frigate events via MQTT are the cleanest path. HA connector captures Frigate events if HA is the central hub. Useful for Home Butler: "front door motion detected," "package delivered," "unfamiliar person at door."

---

## 12. Summary: Recommended Build Order

Based on practical value, existing infrastructure leverage, and complexity:

### Phase 1 — Immediate (P0)

| Connector | Butler | Rationale |
|---|---|---|
| WhatsApp User Client | All (via Switchboard) | Already in progress. Completes the messaging coverage alongside Telegram. |
| Google Calendar | All | Trivial to add — reuses existing Google OAuth infrastructure. Calendar awareness is cross-cutting. |
| Home Assistant | Home | Unlocks the entire Home Butler value proposition. Excellent API. Low complexity. |

### Phase 2 — Near-term (P1)

| Connector | Butler | Rationale |
|---|---|---|
| Bank Transactions (Plaid) | Finance | Highest-value data source for Finance Butler beyond email. |
| Apple Health Export | Health | Richest personal health dataset. Low parsing complexity. |
| OwnTracks / GPS | Home, Travel | Privacy-respecting location awareness. Enables geofence-based triggers. |
| Receipt Parsing (Gmail enhancement) | Finance | Not a new connector but fills a critical data extraction gap. |

### Phase 3 — Medium-term (P2)

| Connector | Butler | Rationale |
|---|---|---|
| Obsidian Vault | General, Education | Simple file watcher. High value for knowledge-base users. |
| Readwise / Pocket | Education | Reading highlights aggregation. Low complexity via Readwise API. |
| CalDAV | General | Only if user is not on Google Calendar. |
| Microsoft Outlook | General | Only if user is on Microsoft ecosystem. |
| Notion | General, Education | Useful but lacks change detection — must poll and diff. |
| Fitbit / Garmin / Wearable | Health | Incremental over Apple Health for real-time intraday data. |
| FHIR Health Records | Health | High value but high complexity and fragmented ecosystem. |
| MQTT Broker | Home | If user has custom IoT without Home Assistant. |
| Discord User Client | General | Draft exists. ToS risk assessment needed. |
| Slack (Socket Mode) | General | Only for Slack-using individuals. Socket Mode fits deployment model. |
| Security Cameras / Frigate | Home | Event metadata only. HA connector may subsume. |
| Flight Tracking API | Travel | Incremental over email for real-time status during travel. |

### Phase 4 — Backlog (P3)

| Connector | Butler | Rationale |
|---|---|---|
| Signal | Relationship | Only after WhatsApp proves the sidecar model. Fragile linked-device protocol. |
| iMessage | Relationship | macOS-only. Platform-locked. |
| SMS / RCS | General | Fragmented. Twilio adds cost. |
| Crypto Exchanges | Finance | Niche. CCXT makes it easy technically. |
| Stripe / PayPal | Finance | Email notifications are sufficient for most users. |
| Twitter / X | General | Prohibitive API pricing. |
| LinkedIn | Relationship | No personal-use API. Email notifications suffice. |
| Instagram | Relationship | No personal account DM API. |
| Reddit | General | Low personal-assistant value. |
| Spotify | General | Fun but marginal utility. |
| YouTube | Education | Low standalone value. |
| Podcast Apps | Education | Fragmented, no APIs. |
| Amazon Orders | Finance | Already covered by email parsing. |
| Grocery Delivery | Finance, Health | Already covered by email parsing. |
| Weather Station | Home | Subsumed by Home Assistant connector. |
| Air Quality | Health, Home | Subsumed by Home Assistant connector. |
| Tax / Government | Finance | No APIs. Email is the only path. |
| Google Maps | Travel | Google deprecated cloud Timeline access. |
| Browser Bookmarks | General | Low value. Raindrop/Pocket APIs are the easy path. |

---

## 13. Architectural Notes

### Common Patterns to Extract

Several connector categories share implementation patterns that should be factored into shared infrastructure:

1. **OAuth2 PKCE flow** — Google (Calendar, Drive, YouTube), Microsoft Graph, Spotify, Readwise, Notion. Build a reusable OAuth2 connector base class with token refresh, scope management, and dashboard pairing UI.

2. **File/directory watcher** — Obsidian vault, Apple Health export, Kindle clippings. Build a generic `FileWatchConnector` base that handles inotify, debouncing, and checkpoint-by-mtime.

3. **WebSocket event stream** — Home Assistant, Discord Gateway, Slack Socket Mode. Build a generic `WebSocketConnector` base with reconnection, heartbeat, and backpressure.

4. **MQTT subscriber** — OwnTracks, direct IoT sensors, Frigate NVR. Build a generic `MQTTConnector` base with topic filtering, QoS handling, and payload normalization.

5. **Go/external sidecar** — WhatsApp (whatsmeow), potentially Signal (signal-cli). Standardize the sidecar lifecycle pattern (health checks, graceful shutdown, IPC protocol) established by the WhatsApp connector.

### Privacy Tiers

Connectors should be tagged with a privacy sensitivity level that influences default ingestion policy:

- **Tier 1 (High sensitivity):** Health records (FHIR), bank transactions, SMS (2FA codes), location tracking, security cameras. Default: opt-in per data category, encrypted at rest.
- **Tier 2 (Medium sensitivity):** Personal messages (WhatsApp, Signal, iMessage, Discord DMs), email, calendar. Default: discretion filtering enabled, contact-aware routing.
- **Tier 3 (Low sensitivity):** Smart home events, weather data, media consumption, bookmarks. Default: ingest all, filter by volume only.

### Connector Template

When building new connectors, use the following checklist (derived from the existing connector architecture in `docs/connectors/overview.md`):

- [ ] Standalone process with `SWITCHBOARD_MCP_URL` connection
- [ ] ingest.v1 envelope normalization with correct `channel` and `provider`
- [ ] Endpoint identity auto-resolution at startup
- [ ] Cursor-based checkpointing via `cursor_store`
- [ ] Heartbeat liveness reporting (2-minute default)
- [ ] Source-side rate limiting with jittered backoff
- [ ] Ingest-side backpressure (bounded in-flight requests)
- [ ] Idempotency key generation for deduplication
- [ ] Health endpoint (FastAPI, configurable port)
- [ ] Prometheus metrics endpoint
- [ ] Docker Compose service definition
- [ ] Credential storage via `CredentialStore` (not environment variables)
- [ ] Documentation in `docs/connectors/`
- [ ] Channel/provider registration in Switchboard routing contracts
