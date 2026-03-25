# Egress Communications Enhancements

> Planning document for all output/action capabilities beyond messaging.

**Status:** Draft
**Date:** 2026-03-25
**Scope:** Proposals for document generation, automated workflows, external service integration, file management, and communication drafting across the Butlers system.

---

## Current Egress Surface

The system today can perform these outbound actions:

| Capability | Owner | Infrastructure |
|---|---|---|
| Send Telegram messages | Messenger | `telegram_send_message` via notify contract |
| Send emails | Messenger | `email_send_message` via notify contract |
| React to Telegram messages | Messenger | `telegram_react_to_message` |
| Reply to Telegram threads | Messenger | `telegram_reply_to_message` |
| Reply to email threads | Messenger | `email_reply_to_thread` |
| Create/update calendar events | Any butler (Calendar module) | Google Calendar API |
| Control smart home devices | Home | Home Assistant `ha_call_service` |
| Activate HA scenes | Home | Home Assistant `ha_activate_scene` |

All user-facing egress routes through the Messenger butler via the `notify.v1` contract. Approval gating is available via the Approvals module for risk-tiered tool interception.

---

## Design Principles

These principles apply across all proposals below:

1. **Reversibility first.** Prefer actions that can be undone or that produce drafts for human review over fire-and-forget mutations in external systems.
2. **Approval gates by default.** Any action with real-world consequences (spending money, sending to external parties, modifying external state) must route through the Approvals module. Risk tiers: `low` (informational), `medium` (reversible side effects), `high` (irreversible or financial), `critical` (security-sensitive).
3. **Audit trail.** Every egress action must produce a durable record: what was done, by which butler, in which session, with what approval (if any).
4. **User-federated scope.** This is a personal assistant for a single user. Actions target the user's own accounts, services, and devices. Multi-tenant and multi-user scenarios are out of scope.
5. **Module boundaries.** New egress capabilities are implemented as modules on the owning butler. Modules add tools; they never touch core infrastructure.
6. **Messenger as delivery plane.** All channel-specific delivery (Telegram, email, WhatsApp, future channels) continues to route through Messenger. Domain butlers request delivery via `notify()`, never by calling channel tools directly.

---

## Feasibility Tiers

| Tier | Definition | Typical effort | Examples |
|---|---|---|---|
| **Tier 1** | Low-hanging fruit. Uses existing infrastructure (modules, tools, approval gates). Minimal new code. | 1-3 days | Scheduled report formatting, email drafts, multi-channel broadcast |
| **Tier 2** | Moderate effort. Needs a new module, new tools on an existing butler, or a new spec. | 1-2 weeks | Document generation pipeline, event-driven automation rules, file organization |
| **Tier 3** | Ambitious. Significant new infrastructure, new butler capabilities, or complex integration. | 2-4 weeks | Payment initiation with approval chains, booking modification workflows |
| **Tier 4** | Future vision. Requires external service integration, complex OAuth flows, or capabilities that do not exist yet in the ecosystem. | 1+ months | Grocery ordering, smart home voice responses, full tax preparation |

---

## 1. Document Generation

### 1.1 Travel Summaries and Itineraries

**Description:** Generate a formatted travel itinerary document (structured text or PDF) from the Travel butler's trip container model. Includes flights, hotels, ground transport, documents, and pre-trip actions in a printable timeline format.

**Owner:** Travel butler
**Complexity:** Tier 1 (text) / Tier 2 (PDF)
**Risk level:** Low. Read-only data aggregation. No external side effects.
**Dependencies:**
- Existing `trip_summary` tool provides all data
- Text/Markdown output: zero new infrastructure
- PDF output: needs a document rendering module (see 1.7)
**Approval requirements:** None. Informational output only.

### 1.2 Health Reports

**Description:** Periodic health summaries with trend data, medication adherence rates, symptom patterns, and environmental correlation from Home Assistant sensors. Extends the existing `weekly-health-summary` scheduled task with richer formatting and optional chart data.

**Owner:** Health butler
**Complexity:** Tier 1 (enhanced text) / Tier 2 (with chart images)
**Risk level:** Low. Read-only aggregation of user's own health data.
**Dependencies:**
- Existing `health_summary`, `trend_report`, `ha_get_statistics` tools
- Chart generation: needs a charting utility (matplotlib or lightweight SVG generator) accessible to the butler runtime
- Image delivery: Telegram bot API supports sending images; needs a `notify` extension for media attachments
**Approval requirements:** None. Informational output only.

### 1.3 Financial Statements and Spending Reports

**Description:** Monthly and on-demand spending reports with category breakdowns, subscription renewals, bill payment status, and month-over-month comparisons. Extends the existing `monthly-spending-summary` scheduled task.

**Owner:** Finance butler
**Complexity:** Tier 1 (enhanced text) / Tier 2 (with charts or PDF)
**Risk level:** Low. Read-only aggregation of user's own financial data.
**Dependencies:**
- Existing `spending_summary`, `upcoming_bills`, `list_transactions` tools
- Chart/PDF: same as 1.2
**Approval requirements:** None. Informational output only.

### 1.4 Tax Preparation Document Packages

**Description:** Aggregate a year's financial records into a tax-preparation package: categorized deductions, income summary, receipt references, and a checklist of missing documents. Output as a structured document (Markdown, PDF, or CSV export).

**Owner:** Finance butler
**Complexity:** Tier 3
**Risk level:** Medium. The data is sensitive (financial records). The output itself is informational, but incorrect categorization could mislead tax preparation.
**Dependencies:**
- Existing transaction and subscription data in Finance schema
- New tools: `tax_year_summary(year, jurisdiction)`, `export_transactions_csv(date_range, categories)`
- Document generation module (see 1.7)
- Receipt attachment retrieval (currently limited; see 4.1)
**Approval requirements:** None for generation. The document is a draft for human review, not a filing.
**What could go wrong:** Miscategorized deductions. Mitigation: clearly label output as "draft for review," include raw data alongside categorized summaries.

### 1.5 Relationship Digests

**Description:** Periodic digest of relationship maintenance: who to reach out to (30+ days since last interaction), upcoming important dates (birthdays, anniversaries), gift ideas pending, and interaction frequency trends.

**Owner:** Relationship butler
**Complexity:** Tier 1
**Risk level:** Low. Read-only aggregation. The existing `relationship-maintenance` and `upcoming-dates-check` scheduled tasks already produce this data.
**Dependencies:**
- Existing tools: `interaction_list`, `date_list`, `gift_list`, `reminder_list`
- Enhancement: combine these into a single formatted digest instead of separate notifications
**Approval requirements:** None. Informational output only.

### 1.6 Meeting Preparation Briefs

**Description:** Before a calendar event, generate a preparation brief: attendee context (from Relationship butler's contact data and interaction history), relevant documents or notes (from General butler's collections), and agenda items. Delivered via Telegram a configurable time before the meeting.

**Owner:** General butler (orchestrates), with cross-butler data from Relationship and domain butlers via Switchboard
**Complexity:** Tier 2
**Risk level:** Low. Read-only data aggregation across butlers.
**Dependencies:**
- Calendar module (event details, attendee names)
- Cross-butler data resolution: General butler requests contact context from Relationship butler via Switchboard MCP routing
- New scheduled task or enhancement to `eod-tomorrow-prep`
**Approval requirements:** None. Informational output only.

### 1.7 Document Rendering Module (Shared Infrastructure)

**Description:** A shared module providing document rendering capabilities: Markdown-to-PDF, HTML-to-PDF, chart/graph generation (SVG or PNG), and templated document assembly. Available to any butler that enables it.

**Owner:** New shared module (`modules.document_renderer`)
**Complexity:** Tier 2
**Risk level:** Low. Pure computation, no external side effects.
**Dependencies:**
- Python PDF library (weasyprint, reportlab, or fpdf2)
- Chart library (matplotlib or a lighter alternative like plotly for static export)
- Media delivery extension for `notify.v1` (file attachment support)
**Approval requirements:** None. Internal infrastructure.

---

## 2. Automated Workflows

### 2.1 Event-Driven Automation Rules

**Description:** A rule engine that allows the user to define trigger-condition-action chains. Rules are stored durably and evaluated when matching events arrive. Examples:
- "When a bill email arrives, extract amount + due date, create calendar reminder, file email"
- "When a health measurement is abnormal, alert + schedule follow-up reminder"
- "When a new contact is added, check for social profiles"

Two flavors:
- **Template rules:** Predefined trigger-action templates (e.g., "bill email -> track bill + calendar reminder"). Deterministic, fast, no LLM invocation.
- **LLM-planned rules:** User describes intent in natural language, LLM decomposes it into a multi-step plan at rule creation time, plan is stored and executed deterministically on trigger.

**Owner:** Switchboard (rule evaluation and dispatch) + domain butlers (action execution)
**Complexity:** Tier 3
**Risk level:** Medium. Automated actions without human review at execution time. Mitigation: approval gates on high-risk actions, dry-run mode for new rules, execution audit log.
**Dependencies:**
- New `automation_rules` table in Switchboard schema (trigger type, conditions JSONB, actions JSONB, enabled flag, approval_policy)
- Rule evaluation hook in the Switchboard triage pipeline (after connector ingestion, before/alongside pipeline LLM classification)
- Action dispatch via existing butler MCP routing
- New Switchboard tools: `automation_rule_create`, `automation_rule_list`, `automation_rule_update`, `automation_rule_delete`, `automation_rule_test` (dry run)
**Approval requirements:**
- Rule creation/modification: `medium` risk tier (user reviews rule definition)
- Rule execution: configurable per-rule. Default `low` for informational actions (create reminder), `medium` for mutations (file email, update records), `high` for anything involving external communication

### 2.2 Bill Email Processing Pipeline

**Description:** Specific implementation of the "bill email arrives -> extract + track + remind" workflow. When the Gmail connector ingests a bill/invoice email, Finance butler automatically extracts amount, due date, payee, and creates a tracked bill with a calendar reminder.

**Owner:** Finance butler (extraction and tracking), Switchboard (routing bill emails to Finance)
**Complexity:** Tier 1-2 (this already partially works via pipeline routing + Finance butler behavior)
**Risk level:** Low. The Finance butler already does this when manually triggered. Automation makes it consistent.
**Dependencies:**
- Pipeline classification already routes financial emails to Finance butler
- Finance butler's `track_bill` and calendar tools already exist
- Enhancement: structured extraction prompts and deduplication via `source_message_id`
**Approval requirements:** None for extraction and tracking. Calendar reminders are internal.

### 2.3 Health Anomaly Alerting

**Description:** When a health measurement is logged outside normal ranges (based on historical baseline or explicit thresholds), immediately alert the user and optionally create a follow-up reminder or calendar event for a doctor visit.

**Owner:** Health butler
**Complexity:** Tier 1-2
**Risk level:** Medium. False positives could cause unnecessary alarm. False negatives could miss real issues. Mitigation: conservative thresholds, "informational only" framing, never diagnose.
**Dependencies:**
- Existing `measurement_log` and `trend_report` tools
- New: threshold configuration (per measurement type, stored in Health butler state or memory)
- Existing `notify()` for alerts, `calendar_create_event` for follow-ups
**Approval requirements:** None for alerts (informational). Follow-up calendar events: `low`.

### 2.4 Contact Enrichment on Add

**Description:** When a new contact is created in the Relationship butler, automatically check for additional information: Google Contacts data (already synced), Telegram profile match, and cross-reference with existing memory facts.

**Owner:** Relationship butler
**Complexity:** Tier 1 (with existing data sources) / Tier 3 (with external social profile lookup)
**Risk level:** Low with existing data. Medium with external lookups (privacy considerations, rate limiting).
**Dependencies:**
- Existing Contacts module sync (Google Contacts, Telegram)
- Entity resolution via memory module
- External social profile lookup: Tier 4 territory (requires API integrations with LinkedIn, etc.)
**Approval requirements:** None for internal data cross-referencing. `high` for any external API lookups.

---

## 3. External Service Integration

### 3.1 Payment and Transfer Initiation

**Description:** Initiate payments or bank transfers through banking APIs or payment services (e.g., "pay the electric bill"). The butler prepares the payment details and submits after explicit human approval.

**Owner:** Finance butler
**Complexity:** Tier 4
**Risk level:** Critical. Irreversible financial transactions. Money leaves the account.
**Dependencies:**
- Bank API integration (Plaid, bank-specific APIs, or payment service APIs)
- OAuth flows for bank account linking
- New module: `modules.payments`
- Multi-factor approval: `critical` risk tier with mandatory human confirmation + potential 2FA passthrough
**Approval requirements:** `critical`. Requires explicit human approval for every transaction. No standing rules. Double-confirmation UI (approve + confirm amount).
**What could go wrong:** Wrong amount, wrong recipient, duplicate payment. Mitigation: amount confirmation, recipient verification against contact records, idempotency keys, daily spending limits.
**Reversibility:** Generally irreversible. Some transfers can be recalled within a window. The butler should surface recall options when available.

### 3.2 Booking Confirmations and Modifications

**Description:** Modify existing bookings (flights, hotels, restaurants) through provider APIs or email-based modification workflows. Examples: change hotel dates, select airline seats, cancel a reservation.

**Owner:** Travel butler
**Complexity:** Tier 4
**Risk level:** High. Booking modifications can incur fees, and cancellations may be irreversible.
**Dependencies:**
- Provider API integrations (airline APIs, hotel booking platforms, restaurant reservation APIs)
- Or: email-based modification (draft a modification request email for human review)
- OAuth flows per provider
**Approval requirements:** `high` for modifications with fees. `medium` for free cancellations. Always show cost/penalty before approval.
**What could go wrong:** Accidental cancellation, modification fees, loss of original booking. Mitigation: show full cost breakdown before approval, store original booking details for recovery.

### 3.3 Grocery and Delivery Ordering

**Description:** Create and submit grocery or delivery orders through service APIs (e.g., Instacart, Amazon Fresh, local delivery services). Butler maintains a shopping list, suggests items based on patterns, and submits orders with approval.

**Owner:** New butler or General butler with a `shopping` module
**Complexity:** Tier 4
**Risk level:** High. Financial transaction + delivery logistics.
**Dependencies:**
- Delivery service API integration (varies wildly by provider and region)
- Shopping list management (could be a General butler collection initially)
- Payment method on file with the service (not managed by butler)
**Approval requirements:** `high`. Order review + amount confirmation before submission.
**What could go wrong:** Wrong items, wrong quantities, wrong address, duplicate orders. Mitigation: order preview with line items and total, address confirmation, deduplication within time window.

### 3.4 Smart Home Commands as Egress

**Description:** Expand Home butler's write capabilities for proactive automation: scheduled scene activations, comfort-based HVAC adjustments, security mode changes, and device group commands. Currently exists but is reactive (user-triggered). Enhancement is proactive (butler-initiated based on schedule, presence, or sensor data).

**Owner:** Home butler
**Complexity:** Tier 2
**Risk level:** Medium. Physical world effects (temperature changes, door locks). Most are reversible.
**Dependencies:**
- Existing `ha_call_service` and `ha_activate_scene` tools
- New: proactive triggers from sensor data or schedule (extend Home butler's scheduled tasks)
- Approval policy: configurable per action category (lighting: auto-approve, locks: require approval, HVAC: auto-approve within comfort range)
**Approval requirements:**
- `low`: Lighting, media, comfort within stated preferences
- `medium`: HVAC outside normal range, appliance control
- `high`: Door locks, security system, garage doors
**What could go wrong:** Locking someone out, uncomfortable temperature, false security alarm. Mitigation: always allow manual override, time-bound automations with auto-revert, security actions require approval.

### 3.5 Calendar-Based Auto-Responses

**Description:** When the user is in a meeting or marked as busy, auto-respond to Telegram messages with a brief status ("In a meeting until 3pm, will respond after"). Optionally, escalate urgent messages that match certain patterns.

**Owner:** General butler (or a new `presence` module on Switchboard)
**Complexity:** Tier 2
**Risk level:** Medium. Sends messages on behalf of the user without per-message approval.
**Dependencies:**
- Calendar module (check current busy/free status)
- Switchboard integration (intercept inbound messages during busy periods)
- Auto-response templates (stored in state or memory)
- Urgency classification (reuse pipeline LLM classification)
**Approval requirements:**
- Rule setup: `medium` (user defines when and how to auto-respond)
- Individual responses: `low` (template-based, non-personalized). Or: opt-in auto-approve via standing rule.
**What could go wrong:** Inappropriate auto-response to wrong person, auto-responding to bots or group chats, over-sharing schedule details. Mitigation: allowlist/blocklist for contacts, generic responses ("busy, will respond later"), never share meeting details.

---

## 4. File Management

### 4.1 Receipt Organization and Categorization

**Description:** When a receipt image or PDF arrives (via Telegram photo, email attachment, or manual upload), extract merchant, amount, date, and category, then file it in organized storage with consistent naming.

**Owner:** Finance butler
**Complexity:** Tier 2-3
**Risk level:** Low. File organization is reversible.
**Dependencies:**
- Image/PDF text extraction (OCR or multimodal LLM vision)
- File storage backend (MinIO is already in the stack)
- Attachment handling in connectors (currently limited; email module does not expose attachments)
- New tools: `receipt_upload`, `receipt_search`, `receipt_export`
- Naming convention: `YYYY-MM-DD_<merchant>_<amount>.<ext>`
**Approval requirements:** None. Internal organization.
**What could go wrong:** Misclassification, duplicate filing. Mitigation: deduplication via content hash, human-reviewable categorization.

### 4.2 Document Filing and Naming Conventions

**Description:** Organize documents by type (medical, financial, travel, personal) with consistent naming, tagging, and searchability. Butler auto-suggests category based on content.

**Owner:** General butler (cross-domain) or domain butlers for their specific documents
**Complexity:** Tier 2
**Risk level:** Low. Organizational task, reversible.
**Dependencies:**
- File storage backend (MinIO)
- Content classification (LLM-based or rule-based)
- New shared module: `modules.file_store` with tools for upload, tag, search, list, download
**Approval requirements:** None. Internal organization.

### 4.3 Backup and Export Workflows

**Description:** Scheduled or on-demand export of butler data: transactions CSV, health measurements, contact list, trip history, calendar events. For data portability and backup.

**Owner:** Each domain butler exports its own data
**Complexity:** Tier 2
**Risk level:** Low. Read-only export.
**Dependencies:**
- Export tools per butler: `export_transactions_csv`, `export_health_data`, `export_contacts_vcf`, etc.
- File delivery: attach to email or store in MinIO with download link
- Scheduled export: extend butler schedule with export tasks
**Approval requirements:** None. User's own data.

### 4.4 Photo and Media Organization

**Description:** Organize photos and media files received via Telegram or email: tag by date, event, people (using contact recognition), and location. Create albums or folders.

**Owner:** General butler with a `media` module
**Complexity:** Tier 3-4
**Risk level:** Low. Organizational task.
**Dependencies:**
- Media storage backend (MinIO)
- Image metadata extraction (EXIF for date/location)
- Face recognition for people tagging (significant infrastructure)
- Album/folder structure in file store
**Approval requirements:** None. Internal organization.

---

## 5. Communication Drafting

### 5.1 Email Draft Preparation

**Description:** Butler prepares email drafts that the user reviews and sends manually. The draft is created in the user's email drafts folder (Gmail Drafts) or presented via Telegram for approval before sending.

**Owner:** Messenger butler (delivery) + domain butlers (content generation)
**Complexity:** Tier 1 (Telegram preview + approval) / Tier 2 (Gmail Drafts API)
**Risk level:** Low (draft only). Medium (if auto-send after approval).
**Dependencies:**
- Tier 1: existing `notify()` to present draft via Telegram + Approvals module for send confirmation
- Tier 2: Gmail Drafts API integration (create draft in user's Gmail)
- Content generation: domain butler produces the text, Messenger handles draft creation
**Approval requirements:**
- Draft creation: None (it is just a draft)
- Sending the draft: `medium` (standard email send risk tier)
**What could go wrong:** Draft sent prematurely, wrong recipient, embarrassing content. Mitigation: draft-then-review is the default flow; never auto-send without explicit approval.

### 5.2 Message Templates for Common Scenarios

**Description:** Pre-defined and user-customizable message templates for recurring communications: birthday wishes, meeting follow-ups, thank-you notes, appointment confirmations. Butler selects and personalizes the template, user approves.

**Owner:** Relationship butler (templates) + Messenger butler (delivery)
**Complexity:** Tier 1
**Risk level:** Low. Templates are reviewed by the user before use.
**Dependencies:**
- Template storage: Relationship butler state store or General butler collections
- Template personalization: LLM fills in context-specific details
- Delivery: standard `notify()` flow with approval
**Approval requirements:** `medium` for sends. Template creation: None.

### 5.3 Multi-Channel Announcement

**Description:** Send the same message to multiple channels simultaneously (e.g., Telegram + email). Butler adapts formatting per channel (plain text for Telegram, HTML for email).

**Owner:** Messenger butler
**Complexity:** Tier 1
**Risk level:** Medium. Same message goes to multiple destinations. Higher blast radius for mistakes.
**Dependencies:**
- Existing channel delivery infrastructure
- New `notify()` parameter: `channels=["telegram", "email"]` (plural) or sequential `notify()` calls
- Channel-specific formatting adapter
**Approval requirements:** `medium`. Single approval covers all channels (user sees the message and the target channels).
**What could go wrong:** Formatting breaks on one channel, partial delivery (sent to email but Telegram fails). Mitigation: preview per channel before approval, atomic status reporting (show which channels succeeded/failed).

---

## 6. Notify Contract Extensions

Several proposals above require extensions to the existing `notify.v1` contract. These are collected here as a shared dependency.

### 6.1 Media Attachments

**Description:** Extend `notify.v1` to support file and image attachments alongside or instead of text messages.

**New fields:**
```
delivery.attachments: [
  { type: "image"|"document"|"file", storage_ref: "<minio_key>", filename: "report.pdf", mime_type: "application/pdf" }
]
```

**Owner:** Core notify infrastructure + Messenger butler
**Complexity:** Tier 2
**Dependencies:** MinIO file storage, Telegram Bot API file upload, email attachment MIME encoding
**Risk level:** Low. Extends existing contract.

### 6.2 Draft Intent

**Description:** Add `intent="draft"` to the notify contract. Instead of delivering, creates a draft in the target channel (Gmail Drafts) or presents for review.

**Owner:** Core notify infrastructure + Messenger butler
**Complexity:** Tier 2
**Dependencies:** Gmail Drafts API, Telegram preview message with inline approval buttons
**Risk level:** Low. Non-destructive by design.

### 6.3 Multi-Channel Delivery

**Description:** Allow `notify()` to target multiple channels in a single call with per-channel formatting.

**Owner:** Core notify infrastructure
**Complexity:** Tier 1-2
**Dependencies:** Channel-specific formatters (Markdown for Telegram, HTML for email)
**Risk level:** Low. Convenience extension.

---

## 7. Cross-Cutting Concerns

### 7.1 Approval UX for New Action Types

The Approvals module currently supports approve/reject via the dashboard. For higher-volume egress actions, the approval UX needs to be faster:
- **Telegram inline buttons:** Approve/reject directly in the notification message
- **Batch approvals:** "Approve all 5 pending bill reminders"
- **Standing rules with scoping:** "Auto-approve all Telegram sends to [contact_id] for the next 24 hours"

**Complexity:** Tier 2
**Owner:** Approvals module + Messenger butler (for Telegram inline buttons)

### 7.2 Egress Rate Limiting

As automated workflows increase egress volume, rate limiting becomes important:
- Per-channel rate limits (Telegram: max N messages per minute)
- Per-contact rate limits (do not spam a person with 10 messages in an hour)
- Daily egress budget (total outbound messages per day)

**Complexity:** Tier 2
**Owner:** Messenger butler + Switchboard

### 7.3 Egress Audit Log

A unified log of all outbound actions across all butlers:
- What was sent/done
- To whom/where
- By which butler and session
- Approval status and approver
- Outcome (delivered, failed, pending)

**Complexity:** Tier 2
**Owner:** Shared schema (`shared.egress_log`)

---

## Implementation Priority

Recommended sequencing based on value, feasibility, and dependency ordering:

### Phase 1: Enhanced Reports (Tier 1)

1. **1.5 Relationship digests** -- combine existing scheduled tasks into a single rich digest
2. **1.1 Travel summaries (text)** -- formatted itinerary from existing `trip_summary`
3. **1.3 Financial reports (text)** -- enhanced `monthly-spending-summary`
4. **5.2 Message templates** -- template storage + personalization
5. **5.1 Email drafts (Telegram preview)** -- present draft via Telegram with approval

These require no new infrastructure. Estimated total: 1-2 weeks.

### Phase 2: Workflow Automation + Document Infrastructure (Tier 2)

6. **2.2 Bill email processing** -- make the existing ad-hoc behavior consistent
7. **2.3 Health anomaly alerting** -- threshold-based alerts
8. **6.1 Media attachments** -- extend notify for file delivery
9. **1.7 Document rendering module** -- PDF generation infrastructure
10. **5.3 Multi-channel announcement** -- multi-target notify
11. **3.5 Calendar auto-responses** -- busy status + template responses
12. **4.2 Document filing** -- shared file store module
13. **7.3 Egress audit log** -- shared egress tracking

Estimated total: 4-6 weeks.

### Phase 3: Ambitious Features (Tier 3)

14. **2.1 Event-driven automation rules** -- rule engine on Switchboard
15. **4.1 Receipt organization** -- OCR + file storage + categorization
16. **1.4 Tax preparation packages** -- year-end financial aggregation
17. **1.6 Meeting preparation briefs** -- cross-butler data aggregation
18. **1.2 Health reports (with charts)** -- chart generation + media delivery
19. **3.4 Smart home proactive automation** -- sensor-driven triggers
20. **7.1 Approval UX enhancements** -- inline buttons, batch approvals

Estimated total: 6-10 weeks.

### Phase 4: External Service Integration (Tier 4)

21. **3.2 Booking modifications** -- provider API integrations
22. **3.1 Payment initiation** -- bank API + critical approval gates
23. **3.3 Grocery/delivery ordering** -- delivery service APIs
24. **4.4 Photo/media organization** -- media pipeline with metadata extraction
25. **2.4 Contact enrichment (external)** -- social profile API lookups

These depend on external service availability, API access, and regional considerations. No timeline estimate; implement opportunistically as APIs become available.

---

## Risk Summary

| Risk | Severity | Proposals affected | Mitigation |
|---|---|---|---|
| Accidental send to wrong recipient | High | 3.1, 3.2, 5.1, 5.3 | Draft-then-review default, approval gates, recipient confirmation |
| Financial loss from wrong amount | Critical | 3.1, 3.3 | `critical` risk tier, double-confirmation, daily limits, idempotency |
| Over-automation fatigue (too many notifications) | Medium | 2.1, 2.2, 2.3, 3.5 | Rate limiting (7.2), configurable quiet hours, digest mode |
| Privacy leak via external API | Medium | 2.4, 3.2, 3.3 | Minimal data sharing, user consent per integration, audit log |
| False health alarms | Medium | 2.3 | Conservative thresholds, "informational only" framing, trend context |
| Smart home safety | Medium | 3.4 | Approval tiers per device category, auto-revert timers, manual override |
| Template/auto-response tone mismatch | Low | 3.5, 5.2 | User reviews templates, generic defaults, personalization review |

---

## Open Questions

1. **PDF vs. rich text:** Should document generation target PDF (portable, printable) or rich HTML (interactive, linkable)? Or both with a format parameter?
2. **File storage scope:** Is MinIO the right long-term file store, or should we integrate with the user's cloud storage (Google Drive, Dropbox)?
3. **Automation rule language:** Should rules be expressed as structured JSONB (deterministic, fast) or natural language (flexible, requires LLM at evaluation time)?
4. **Cross-butler orchestration:** Meeting prep briefs (1.6) and tax packages (1.4) require data from multiple butlers. Should this use the existing Switchboard routing, or do we need a dedicated orchestration layer?
5. **Channel expansion:** WhatsApp and Discord connectors are specified but not yet built. Should egress enhancements target only Telegram + email for now, or design for N channels from the start?
6. **Standing approval rules scope:** How broad should standing rules be allowed to get? "Auto-approve all Telegram sends" is convenient but dangerous. What guardrails prevent overly permissive rules?
