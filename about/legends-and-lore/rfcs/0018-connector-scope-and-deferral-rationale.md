# RFC 0018: Connector Scope and Deferral Rationale

**Status:** Accepted
**Date:** 2026-06-14

## Summary

This RFC records *why* the Butlers connector roster is shaped the way it is: which
external data sources are in v1 scope, which are deferred, and the durable rationale
for each deferral (paid APIs, ToS / ban risk, platform locks, "email already covers
this," and dependency on a proven model). It is a **scope and decision record, not a
commitment to build.** Nothing here obligates a future connector; it preserves the
reasoning so the same debates need not be relitigated.

The implemented connectors each have their normative behavior in
`openspec/specs/connector-*` and design contracts elsewhere in
`about/legends-and-lore/`. Those are not duplicated here. This document is the durable
home for the *deferral catalogue* that previously lived in `docs/plans/connector-gaps.md`
(now deleted). Scope debates end at `about/heart-and-soul/v1.md`; this RFC explains the
connector-specific consequences of that doctrine.

## Motivation

The connector landscape for a personal assistant is effectively unbounded — every
messaging platform, financial service, health device, calendar, knowledge tool, smart-home
hub, travel API, social network, and IoT sensor is a *candidate*. A planning catalogue
enumerated dozens of them with priority ratings and the reasons most were not worth
building. That catalogue was a planning artifact, not a contract, and is being deleted.

The risk on deletion is that the **rationale evaporates** while the temptation to "just add
one more connector" persists. Without a record:

1. A future contributor re-proposes Twitter/X or iMessage without knowing they were already
   assessed and rejected (cost, platform lock).
2. The "email already covers this" decision — which retires whole categories of connectors
   (Amazon orders, grocery, payment processors, tax/government, LinkedIn/Instagram
   notifications) — is forgotten, and parallel API integrations get built for data the
   Gmail connector already ingests.
3. Speculative infrastructure (an MQTT base, a FileWatch base, per-connector privacy tiers)
   gets built "to prepare for" deferred connectors, in direct violation of v1.md:210.

Capability specs describe *what an implemented connector does*; they are the wrong home for
*why other connectors were not built*. This RFC fills that gap.

## The v1 Connector Roster (In Scope)

The v1 connector set is fixed by `about/heart-and-soul/v1.md:82-99`. These are the only
connectors v1 ships:

| Connector | Channel / Purpose | v1.md ref |
|---|---|---|
| Telegram bot | Long-polling adapter for Telegram Bot API | v1.md:83 |
| Telegram user client | Telethon adapter for user-account messages | v1.md:84 |
| Gmail | Push-notification / periodic IMAP adapter for Gmail | v1.md:85 |
| Discord | WebSocket adapter for Discord events | v1.md:86 |
| Heartbeat | Periodic health-check connector | v1.md:87 |
| Live listener | Real-time audio transcription connector (transport, not a voice UI — v1.md:161-162) | v1.md:88 |
| Google Calendar | Sync-token polling adapter for calendar change events | v1.md:89 |
| Google Drive | Changes-list polling adapter for file metadata events | v1.md:90 |
| Home Assistant | WebSocket/REST adapter for smart-home events, with domain/significance filtering | v1.md:91-92 |
| OwnTracks | HTTP webhook receiver for location and waypoint events | v1.md:93 |
| Spotify | Playback-state polling adapter for listening events and session aggregation | v1.md:94-95 |
| Google Health | Polling adapter for wellness data, reusing the Google OAuth pipeline | v1.md:96-98 |
| WhatsApp user client | Passive inbox ingestion via the whatsmeow Go sidecar | v1.md:99 |

The v1 success criteria (v1.md:191-193) require the Switchboard to route correctly from this
specific set — Telegram, Gmail, Discord, Google Calendar, Google Drive, Home Assistant,
OwnTracks, Spotify, and WhatsApp — at >90% classification accuracy. Connector scope is
therefore load-bearing for the v1 acceptance gate, not aspirational.

All connectors share one pattern: a standalone process emitting `ingest.v1` envelopes,
MCP submission to the Switchboard, cursor-based checkpointing, and heartbeat liveness.
Adding a connector that does not fit this pattern is itself a scope question, not a routine
addition.

## Deferred Connectors

The following were assessed and deferred. Priority bands carry over from the planning
catalogue (P1 = strong value, blocked by complexity/limits; P2 = useful but niche or
dependent on a prerequisite; P3 = speculative, limited API availability, or marginal
personal-assistant value). **A priority rating is a relative assessment, not a queue
position — nothing here is scheduled.**

### Messaging

| Connector | Priority | Reason deferred |
|---|---|---|
| Signal | P2 | Requires a dedicated phone number or the fragile linked-device path; Java (signal-cli) runtime; no official bot API; sidecar must hold E2E key material. Reconsider **only after WhatsApp proves the sidecar model** — do not run two unproven sidecars at once. |
| iMessage | P2 | **Platform-locked to macOS** — incompatible with the Linux/Docker deployment target (v1.md:176). Apple actively blocks third-party access; `chat.db` polling is read-only and fragile across OS updates. Needs a dedicated Mac bridge to exist at all. |
| SMS / RCS | P3 | No universal API. Twilio adds a per-message cost and a separate number contacts must text; phone-forwarding needs an always-on device. High privacy sensitivity (2FA, bank alerts). |
| Slack | P2 | Only valuable for Slack-using individuals. Socket Mode fits the tailnet-first / no-public-ingress model, but workspace-scoped OAuth and enterprise barriers limit reach. |
| Reddit | P3 | Free, well-documented API, but low personal-assistant value — mostly content consumption noise; useful only for inbox (DMs/replies). |

(Discord is **in** v1 as a WebSocket connector — v1.md:86 — using the ToS-compliant bot-token
path; the user-token automation path was rejected for the same ban risk that gates WhatsApp.)

### Financial

| Connector | Priority | Reason deferred |
|---|---|---|
| Bank transactions (Plaid / Open Banking) | P1 | Highest-value financial source beyond email, but Plaid requires a **paid plan** for production; Link OAuth needs web UI; high PII sensitivity; regional coverage gaps. Email-based transaction parsing already covers many cases. |
| Crypto exchanges | P3 | Niche — only if the user holds crypto. CCXT makes it technically easy, but the Finance Butler already ingests exchange email notifications. |
| Stripe / PayPal | P3 | Requires a public webhook endpoint (conflicts with tailnet-first architecture); only relevant to merchants/freelancers. **Payment notifications already arrive via email.** |

### Health & Fitness

| Connector | Priority | Reason deferred |
|---|---|---|
| Apple Health export | P1 | Richest iPhone health dataset, but no API — relies on large XML/ZIP exports with no native incremental sync; needs a user-configured iOS Shortcut or third-party app. (Google Health is the v1 wellness path — v1.md:96-98.) |
| Fitbit / Garmin / wearables | P2 | OAuth2 with periodic re-auth; Garmin has no official public API. Largely **subsumed by the wellness path** for users whose devices already sync; direct APIs only add real-time intraday data. |
| FHIR health records | P2 | High value but high complexity: fragmented endpoint discovery, per-system SMART-on-FHIR OAuth, US-centric mandate, highly sensitive PHI. A "build for one provider at a time" effort. |
| Pharmacy / medication APIs | P3 | No universal API; SureScripts is gated to licensed entities; chains hide data behind CAPTCHA portals. Email parsing plus manual entry suffices. |

### Productivity & Knowledge

| Connector | Priority | Reason deferred |
|---|---|---|
| CalDAV (iCloud, Fastmail, Nextcloud) | P2 | Only needed if the primary calendar is not Google. Google Calendar is in v1 (v1.md:89); CalDAV is the fallback for non-Google users. |
| Microsoft Outlook / M365 | P2 | Only relevant if the user's primary email/calendar is Outlook. For Gmail users this is redundant with the Gmail + Google Calendar connectors. |
| Notion | P2 | Useful as a knowledge base, but the API **lacks change detection** (no sync tokens) — must poll and diff; rate-limited. |
| Obsidian vault | P2 | Technically trivial (a file watcher), high value for vault users — but it depends on a generic FileWatch base that v1 does not build (see below) and on the vault being host-accessible. |
| Google Drive / Dropbox cloud storage | P2 | Google Drive *metadata* events are in v1 (v1.md:90). Full-text content extraction (PDF parsing, format conversion) is a different problem and is deferred. |
| Browser bookmarks / reading list | P3 | Low standalone value; infrequently accessed. Raindrop/Pocket APIs are the easy path if ever needed. |
| Readwise / Pocket (reading highlights) | P2 | Low complexity via Readwise, but Readwise is a paid subscription and the value is niche to heavy readers. |

### Social Media

| Connector | Priority | Reason deferred |
|---|---|---|
| Twitter / X | P3 | **Prohibitive API pricing** ($100/mo Basic for read access; $5000/mo Pro; free tier is post-only). Cost-to-value ratio is poor; X email notifications already give a free subset via Gmail. |
| LinkedIn | P3 | **No personal-use API** — messaging access is partner-gated; scraping violates ToS and breaks on anti-bot measures. Email notification parsing (already in Gmail) is the only practical path. |
| Instagram | P3 | **No DM API for personal accounts** (Graph API is business/creator-only; Basic Display deprecated). Email notifications via Gmail are the only viable approach. |

### Travel & Location

| Connector | Priority | Reason deferred |
|---|---|---|
| Flight / airline tracking | P2 | Tracking APIs are paid (AeroAPI ~$1/query); continuous polling is expensive. **Booking confirmations already arrive via Gmail**; only real-time status during travel days is incremental. |
| Hotel / booking platforms | P3 | No unified API; major platforms offer no personal-use API. **Email parsing covers ~90%** of the use case. |
| Google Maps / navigation history | P3 | Google is **deprecating cloud Timeline access** (moving on-device); Platform APIs are pay-per-use developer tools. OwnTracks (in v1, v1.md:93) serves real-time location better. |

### Smart Home / IoT

| Connector | Priority | Reason deferred |
|---|---|---|
| MQTT broker | P2 | **Subsumed by Home Assistant** (in v1, v1.md:91) for users who run HA, which already aggregates MQTT devices. Standalone value only for custom IoT bypassing HA. (See deferred-bases note below.) |
| Zigbee / Z-Wave hub direct | P3 | Each hub has its own API; **strictly inferior to Home Assistant** for HA users, who already see these devices via integrations. |
| Security cameras / NVR (Frigate, ONVIF) | P2 | Event metadata only (no video). Cleanest path is Frigate-over-MQTT, which the Home Assistant connector subsumes if HA is the hub. |
| Weather station (personal) | P3 | **Subsumed by Home Assistant** — station data flows through HA entities. Standalone only for non-HA users with a cloud station. |
| Air-quality monitor | P3 | Same rationale as weather stations — **Home Assistant subsumes it**. |

### Media & Entertainment

| Connector | Priority | Reason deferred |
|---|---|---|
| YouTube | P3 | Low standalone value; shares Google OAuth but watch-history context is marginal. Transcript extraction is a content pipeline, not a connector. |
| Podcast apps | P3 | Fragmented ecosystem with **no dominant API**; OPML export is one-time and lacks listening history. |

(Spotify *is* in v1 — v1.md:94-95 — as the one media-consumption connector; the others above
are the deferred long tail.)

### Commerce, Government & Official

| Connector | Priority | Reason deferred |
|---|---|---|
| Amazon order history | P3 | No consumer API. **Already covered by Gmail** order-confirmation parsing. |
| Grocery delivery (Instacart, etc.) | P3 | No consumer APIs. **Already covered by Gmail.** |
| Tax portal integration | P3 | Governments rarely expose APIs; access is bureaucratic and jurisdiction-specific. **Email is the only path.** |
| Immigration / visa status | P3 | No APIs; portals are hostile to automation. Email/SMS notifications suffice. |
| Vehicle registration / insurance | P3 | No consumer APIs; jurisdiction-specific. **All notifications arrive via email.** |

Receipt email parsing, listed in the planning catalogue, is **not a connector at all** — it is
an enhancement to the existing Gmail ingestion policy and Finance Butler processing. It is
recorded here only to mark that the "structured financial data" gap is closed inside Gmail, not
by a new connector.

## Deferred Infrastructure Bases

The planning catalogue identified shared infrastructure that *would* reduce the cost of several
deferred connectors. **None of it is built in v1, by design.** `about/heart-and-soul/v1.md:210`
forbids building v1 features with hooks for deferred work "that add complexity now," and v1.md:154-155
states no v1 work should be designed to "prepare for" deferred features at the cost of v1
simplicity. These are recorded strictly as **v2 candidates**, contingent on their dependent
connectors actually being approved:

- **MQTT-subscriber base** — a generic `MQTTConnector` (topic filtering, QoS, payload
  normalization) would serve OwnTracks, raw IoT sensors, and Frigate. v1's OwnTracks connector
  uses the HTTP-webhook path (v1.md:93) and does **not** depend on an MQTT base; building the
  base now would be premature abstraction for connectors that do not exist.
- **FileWatch base** — a generic `FileWatchConnector` (inotify, debouncing, mtime checkpoints)
  would serve Obsidian, Apple Health exports, and Kindle clippings. **No v1 connector watches
  the filesystem**, so the base has no current consumer and is not built.
- **Per-connector privacy-sensitivity classification** — the catalogue proposed tagging each
  connector with a privacy tier (high: health/bank/SMS/location/cameras; medium: personal
  messages/email/calendar; low: smart-home/weather/media) to drive default ingestion policy.
  v1 does **not** implement a connector-level privacy-tier abstraction; per-channel discretion
  and routing safety are handled at existing layers (e.g. the owner-routing safety work in
  RFC 0017). A formal privacy-tier taxonomy is a v2 candidate, to be introduced only when a
  high-sensitivity connector (FHIR, bank feeds) is actually approved.

A reusable OAuth2-PKCE base and a WebSocket-stream base were also catalogued. These are **partially
realized** by the in-scope Google connectors (shared OAuth pipeline, reused by Google Health —
v1.md:97-98) and the Home Assistant / Discord WebSocket connectors; they are noted here only so a
future reader does not mistake them for unbuilt deferred work. Any further generalization should
follow the same rule: extract the abstraction only once multiple in-scope connectors demand it,
never to prepare for a deferred one.

## Doctrine

Two anti-patterns from `about/heart-and-soul/v1.md:208-213` govern every future connector
proposal:

1. **"Expanding connector coverage before existing connectors are solid"** (v1.md:212) is an
   explicit anti-pattern. The v1 success bar (v1.md:191-201) is *reliability of the existing
   roster over 7 consecutive days*, not breadth. A new connector that competes for attention with
   hardening the v1 set is, by doctrine, the wrong work.

2. **"Building v1 features with [...] hooks that add complexity now"** (v1.md:210) forbids
   scaffolding for deferred connectors. The deferred bases above stay unbuilt until a concrete,
   approved connector needs them.

The practical decision rule that falls out of this catalogue:

- If **email already covers it** (Amazon, grocery, payment processors, tax/government, LinkedIn,
  Instagram, hotel/flight bookings), a dedicated connector is redundant — improve Gmail
  ingestion instead.
- If **Home Assistant subsumes it** (MQTT, Zigbee/Z-Wave, weather, air quality, cameras for HA
  users), build it standalone only for the non-HA case, and only on demand.
- If it is **paid (Plaid, X, AeroAPI, Readwise) or platform-locked (iMessage)**, the cost or
  lock is the gating factor, not engineering effort — the value must clearly exceed it for the
  *actual* user before it is reconsidered.
- If it is a **new sidecar or transport model (Signal)**, it waits until the existing model
  (WhatsApp) is proven solid.

## References

- `about/heart-and-soul/v1.md` — v1 scope doctrine. Connector roster (lines 82-99), success
  criteria (191-201), deferral doctrine (152-155), anti-patterns (208-213).
- `openspec/specs/connector-*` — normative capability specs for the implemented connectors.
  This RFC deliberately does **not** restate their behavior.
- RFC 0017 (owner-routing safety) — per-channel routing safety / discretion handled at the
  approval and resolver layers, in lieu of a connector-level privacy-tier abstraction.
- `docs/plans/connector-gaps.md` (deleted) — the planning catalogue this RFC supersedes as the
  durable home for connector scope and deferral rationale.
