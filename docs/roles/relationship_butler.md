# Relationship Butler: Permanent Definition

Status: Normative (Target State)
Last updated: 2026-02-16
Primary owner: Product/Domain

## 1. Role
The Relationship Butler is a personal CRM that automates the cognitive overhead of maintaining meaningful human relationships.

It ingests, classifies, and cross-references social context from conversations, scheduled checks, and manual input to surface timely reminders, contextual facts, and proactive relationship-maintenance suggestions — so the user can focus on connection rather than recall.

## 2. Design Goals
- Comprehensive personal CRM covering contacts, relationships, interactions, dates, gifts, loans, groups, and reminders.
- Automated ingestion and classification of relationship-relevant facts from conversational messages.
- LLM-driven contextual inference across facts (e.g. dietary restrictions imply restaurant constraints, job changes imply location changes).
- Proactive relationship maintenance through scheduled checks and stay-in-touch cadence tracking.
- Low-friction data entry: most information should be captured from natural conversation, not forms.
- Rich recall: any question about a contact should be answerable from combined domain data and memory facts.
- Privacy-first: all data is user-owned, butler-local, and never shared across butlers except via explicit MCP routing.

## 2.1 Base Contract Overrides
Inherits unchanged:
- All clauses in `docs/roles/base_butler.md` apply unless explicitly listed in `Overrides`.

Overrides: none.

Additions:
- This role defines a domain-specific persistence schema (section 6) beyond base core tables.
- This role defines domain-specific MCP tools (section 5) beyond base core tools.
- This role defines domain-specific scheduled tasks (section 8) beyond base scheduler semantics.
- This role requires the `memory` module for fact extraction and contextual recall (section 9).
- This role requires the `calendar` module for date-driven event management (section 9).

## 3. Scope and Boundaries

### In scope
- Contact lifecycle management (create, update, search, archive, merge).
- Bidirectional typed relationship tracking between contacts.
- Important date tracking with proactive reminder generation.
- Interaction logging with type, direction, duration, emotion, and context.
- Note-taking with emotion tags and full-text search.
- Gift lifecycle tracking (idea through delivery and acknowledgment).
- Loan/debt tracking between user and contacts.
- Group and label organization with flexible taxonomies.
- Quick facts (key-value pairs per contact for fast structured recall).
- Activity feed aggregation across all entity mutations.
- Stay-in-touch cadence tracking and staleness detection.
- Automated fact extraction from conversational messages via memory module.
- Contextual inference across facts (dietary restrictions, life events, preferences).
- Contact information management (email, phone, social handles, addresses).
- vCard export for interoperability.
- Proactive relationship maintenance suggestions.
- Calendar integration for social events and follow-ups.

### Out of scope
- Direct channel delivery (owned by Messenger Butler via `notify`).
- Ingress routing and classification (owned by Switchboard).
- Financial management beyond simple loan/debt tracking.
- Professional CRM features (sales pipelines, lead scoring, deal tracking).
- Social media automation or posting on behalf of the user.
- Contact sync with external providers (Google Contacts, iCloud) — future consideration.

## 4. Manifesto Alignment
The Relationship Butler is manifesto-driven. All features, tools, and UX decisions must align with three core principles from `roster/relationship/MANIFESTO.md`:

- **Thoughtfulness:** Never miss what matters. Proactive reminders, gift tracking, and date awareness.
- **Richness:** Capture the full texture of relationships — emotion, context, history, not just facts.
- **Connection:** Reduce cognitive overhead so the user can be more present and intentional.

Features that increase data-entry burden without proportional relationship value should be rejected. Features that automate recall and surface context at the right moment should be prioritized.

## 5. Tool Surface Contract

### 5.1 Contact Management
- `contact_create(first_name, last_name?, nickname?, company?, job_title?, gender?, pronouns?, metadata?)` — Create a new contact. Returns contact ID.
- `contact_update(contact_id, **fields)` — Update any contact field.
- `contact_get(contact_id | name)` — Retrieve full contact record with related data.
- `contact_search(query, limit?)` — Full-text search across name, company, job title, notes, and contact info fields.
- `contact_resolve(name, context?)` — Resolve a name string to a contact_id with confidence levels (high/medium/none). Uses salience-based disambiguation when multiple contacts share a first name (see §10.4). Returns `inferred` flag and reason when salience was used to auto-resolve.
- `contact_archive(contact_id)` — Soft-delete via `listed=false`. Archived contacts are excluded from default queries but recoverable.
- `contact_merge(source_id, target_id)` — Merge two contact records, combining all related entities. Target survives, source is archived. *(Target state — not yet implemented.)*
- `contact_export_vcard(contact_id?)` — Export one or all contacts as vCard 3.0 format.
- `contact_import_vcard(vcard_data)` — Import vCard data and create contacts.

### 5.2 Contact Information
- `contact_info_add(contact_id, type, value, label?, is_primary?)` — Add email, phone, telegram, linkedin, twitter, website, or other contact method.
- `contact_info_list(contact_id, type?)` — List contact info, optionally filtered by type.
- `contact_info_remove(info_id)` — Remove a contact info record.
- `contact_search_by_info(value)` — Reverse-lookup contacts by info value (e.g. find who owns a phone number).
- `address_add(contact_id, line_1?, line_2?, city?, province?, postal_code?, country?, label?, is_current?)` — Add a physical address.
- `address_list(contact_id)` — List addresses for a contact.
- `address_update(address_id, **fields)` — Update an address.
- `address_remove(address_id)` — Remove an address.

### 5.3 Relationships
- `relationship_add(contact_id, related_contact_id, type_id | type, notes?)` — Create a bidirectional typed relationship. Can reference a seeded type by ID or use freetext.
- `relationship_list(contact_id)` — List all relationships for a contact.
- `relationship_remove(relationship_id)` — Remove a relationship and its inverse.
- `relationship_type_get(type_id)` — Get a relationship type by ID.
- `relationship_types_list()` — List all relationship types grouped by category (Love, Family, Friend, Work, Custom). Seeded with 15 types (spouse, partner, parent/child, sibling, friend, colleague, boss/report, mentor/mentee, etc.).

### 5.4 Important Dates
- `date_add(contact_id, date_type, month, day, year?, label?)` — Track birthdays, anniversaries, and custom milestones. Year is optional (some people don't share birth year).
- `date_list(contact_id?)` — List important dates, optionally filtered by contact.
- `upcoming_dates(days_ahead?)` — Return dates occurring within the specified window (default 7 days).

### 5.5 Notes
- `note_create(contact_id, title?, body, emotion?)` — Create a note with optional emotion tag (positive/neutral/negative). Idempotent within a timestamp window to prevent duplicates.
- `note_list(contact_id, limit?, offset?)` — List notes for a contact.
- `note_search(query, contact_id?)` — Full-text search across note titles and bodies.

### 5.6 Interactions
- `interaction_log(contact_id, interaction_type, direction?, summary?, duration_minutes?, metadata?)` — Log a call, meeting, meal, message, or other interaction. Idempotent within a timestamp window.
- `interaction_list(contact_id, type?, limit?, offset?)` — List interactions, optionally filtered by type.

Supported interaction types: `call`, `meeting`, `meal`, `coffee`, `message`, `email`, `video_call`, `event`, `other`.

### 5.7 Reminders
- `reminder_create(contact_id?, label, reminder_type, next_trigger_at, recurrence_rule?)` — Create a one-time or recurring reminder. May be contact-scoped or general.
- `reminder_list(contact_id?, active_only?)` — List reminders.
- `reminder_dismiss(reminder_id)` — Dismiss/snooze a reminder.

Supported reminder types: `one_time`, `recurring`, `stay_in_touch`.

### 5.8 Gifts
- `gift_add(contact_id, description, status?, url?, price_estimate?, occasion?)` — Add a gift idea or record.
- `gift_update_status(gift_id, new_status)` — Advance gift through the pipeline. Forward-only transitions enforced.
- `gift_list(contact_id?, status?)` — List gifts, optionally filtered.

Gift pipeline: `idea` -> `purchased` -> `wrapped` -> `given` -> `thanked`.

### 5.9 Loans
- `loan_create(lender_contact_id, borrower_contact_id, amount_cents, currency, name?, loaned_at?)` — Record a loan between user and contact (either direction).
- `loan_settle(loan_id)` — Mark a loan as settled.
- `loan_list(contact_id?, settled?)` — List loans, optionally filtered.

### 5.10 Groups and Labels
- `group_create(name, group_type?)` — Create a contact group (family, couple, friends, team, custom).
- `group_add_member(group_id, contact_id, role?)` — Add a contact to a group with optional role.
- `group_list()` — List all groups with member counts.
- `group_members(group_id)` — List members of a group.
- `label_create(name, color?)` — Create a label/tag.
- `label_assign(label_id, contact_id)` — Apply a label to a contact.
- `contact_search_by_label(label_name)` — Find contacts with a given label.

### 5.11 Quick Facts
- `fact_set(contact_id, category, content)` — Store a structured key-value fact (e.g. category="food_allergy", content="shellfish").
- `fact_list(contact_id)` — List all quick facts for a contact.

### 5.12 Activity Feed
- `feed_get(contact_id?, entity_type?, limit?, offset?)` — Retrieve the chronological activity feed. Auto-populated from all entity mutations.

### 5.13 Stay-in-Touch
- `stay_in_touch_set(contact_id, frequency_days | null)` — Set desired contact cadence for a person, or pass null to clear.
- `contacts_overdue()` — List contacts exceeding their configured stay-in-touch cadence.
- `stay_in_touch_status(contact_id?)` — Return detailed staleness status: days since last interaction vs. desired frequency, optionally for one contact or all tracked contacts sorted by overdue-ness. *(Target state — not yet implemented.)*

### 5.14 Life Events
- `life_event_log(contact_id, type_name, summary, description?, happened_at?)` — Log a life event (new job, promotion, marriage, move, etc.) for a contact.
- `life_event_list(contact_id?, type?, limit?)` — List life events, optionally filtered by contact or type.
- `life_event_types_list()` — List available life event types grouped by category (Career, Personal, Social). Seeded with 12 types.

### 5.15 Tasks
- `task_create(contact_id?, title, description?)` — Create a task, optionally associated with a contact.
- `task_list(contact_id?, include_completed?)` — List tasks, optionally filtered by contact.
- `task_complete(task_id)` — Mark a task as completed.
- `task_delete(task_id)` — Delete a task.

## 6. Persistence Contract

### 6.1 Core Tables (Base Contract)
Inherited: `state`, `scheduled_tasks`, `sessions`.

### 6.2 Domain Tables
All domain tables live in the `butler_relationship` database and are managed through the Relationship Butler's Alembic migration chain.

| Table | Purpose |
|---|---|
| `contacts` | Primary contact records (name, company, job, gender, pronouns, avatar, metadata) |
| `contact_info` | Multi-valued contact methods (type/value/label with primary flag) |
| `addresses` | Physical addresses with `is_current` flag |
| `relationships` | Bidirectional typed relationships between contacts |
| `important_dates` | Birthdays, anniversaries, milestones (month/day/optional year) |
| `notes` | Titled notes with body and emotion tag |
| `interactions` | Logged interactions with type, direction, summary, duration |
| `reminders` | One-time and recurring reminders with trigger scheduling |
| `gifts` | Gift pipeline records with status, URL, price, occasion |
| `loans` | Two-party loan records (lender/borrower/amount/currency/settled) |
| `groups` | Named contact groups with type taxonomy |
| `group_members` | Group membership with optional role |
| `labels` | User-defined tags with optional color |
| `contact_labels` | Many-to-many label assignments |
| `quick_facts` | Structured key-value facts per contact |
| `activity_feed` | Polymorphic event log (entity_type/entity_id linking) |
| `life_event_categories` | Life event category taxonomy (Career, Personal, Social) |
| `life_event_types` | Life event type taxonomy (12 seeded types) |
| `life_events` | Logged life events per contact with type, summary, happened_at |
| `tasks` | Contact-scoped or general tasks with completion tracking |

Note: Stay-in-touch cadence is stored as `stay_in_touch_days` column on the `contacts` table, not a separate table.

### 6.3 Data Integrity Rules
- All entity mutations auto-populate the `activity_feed` table.
- Gift status transitions are forward-only and enforced at the tool layer.
- Loan records use a two-party model (lender_contact_id, borrower_contact_id) — not a direction enum.
- Note creation and interaction logging enforce idempotency guards (timestamp window + dedupe key).
- Contact archival is soft-delete via `listed=false`; archived contacts retain all related data.
- Important dates support year-optional storage (some people share only month/day).

## 7. Dashboard API Contract
The Relationship Butler exposes read-only dashboard API routes for the frontend. Write operations go through MCP tools only (dual data-access pattern).

| Endpoint | Purpose |
|---|---|
| `GET /api/relationship/contacts` | List/search contacts with labels and last_interaction_at |
| `GET /api/relationship/contacts/:id` | Full contact detail with all related data |
| `GET /api/relationship/contacts/:id/notes` | Paginated notes for a contact |
| `GET /api/relationship/contacts/:id/interactions` | Paginated interactions for a contact |
| `GET /api/relationship/contacts/:id/gifts` | Gifts for a contact |
| `GET /api/relationship/contacts/:id/loans` | Loans involving a contact |
| `GET /api/relationship/contacts/:id/feed` | Activity feed for a contact |
| `GET /api/relationship/groups` | List groups with member counts |
| `GET /api/relationship/groups/:id` | Group detail with members |
| `GET /api/relationship/labels` | List all labels |
| `GET /api/relationship/upcoming-dates` | Important dates in the upcoming window |

Target-state additions:
| Endpoint | Purpose |
|---|---|
| `GET /api/relationship/contacts/:id/facts` | Quick facts for a contact |
| `GET /api/relationship/contacts/:id/life-events` | Life events for a contact |
| `GET /api/relationship/contacts/:id/tasks` | Tasks for a contact |
| `GET /api/relationship/contacts/:id/stay-in-touch` | Cadence status for a contact |
| `GET /api/relationship/contacts/:id/timeline` | Unified chronological timeline combining notes, interactions, gifts, life events, dates, and feed events |
| `GET /api/relationship/stay-in-touch/overdue` | All overdue stay-in-touch contacts |
| `GET /api/relationship/tasks` | All tasks (optionally filtered by completion status) |
| `GET /api/relationship/life-events` | All life events (optionally filtered by type/contact) |
| `GET /api/relationship/stats` | Relationship health dashboard (interaction frequency, staleness distribution, upcoming dates count) |

## 8. Scheduled Tasks

### 8.1 Upcoming Dates Check
- **Schedule:** Daily at 08:00
- **Behavior:** Query `upcoming_dates(days_ahead=7)`. For each upcoming date, generate a contextual reminder message incorporating recent notes, interaction history, and known preferences for the contact. Deliver via `notify` through Switchboard.
- **Target-state enhancement:** Include gift pipeline status (if a gift is tracked for this person/occasion, include its current status in the reminder). Include contextual suggestions (e.g. "Sarah's birthday is in 3 days. She mentioned wanting the new Haruki Murakami novel. Gift status: idea — not yet purchased.").

### 8.2 Relationship Maintenance
- **Schedule:** Weekly, Monday at 09:00
- **Behavior:** Identify contacts with no interaction in 30+ days. Cross-reference with stay-in-touch cadence settings where available. Suggest 3 people to reach out to, ranked by overdue-ness and relationship importance. Include context: last interaction summary, any upcoming dates, recent life changes from memory facts.
- **Target-state enhancement:** Factor in relationship closeness tier and stay-in-touch priority. Generate draft outreach messages personalized with recent context (e.g. "You haven't talked to Alex in 45 days. Last time, he mentioned starting a new startup. Maybe check in on how it's going?").

### 8.3 Stay-in-Touch Digest (Target State)
- **Schedule:** Daily at 09:00
- **Behavior:** Evaluate all contacts with `stay_in_touch` cadence settings. Identify contacts overdue by more than 20% of their configured frequency. Group by urgency tier. Deliver a prioritized digest via `notify`.

### 8.4 Life Event Anniversary Check (Target State)
- **Schedule:** Daily at 08:30
- **Behavior:** Scan memory facts and important dates for anniversary-type events (not just birthdays — job start dates, moves, relationship milestones). Surface relevant anniversaries approaching within 7 days with contextual suggestions.

### 8.5 Periodic Contact Health Audit (Target State)
- **Schedule:** Monthly, 1st at 10:00
- **Behavior:** Identify contacts with incomplete profiles (no phone/email, no important dates, no interactions logged). Suggest enrichment actions. Identify potential duplicate contacts for merge review.

## 9. Module Dependencies

### 9.1 Memory Module (Required)
The memory module enables:
- **Fact extraction:** Conversational messages are parsed for relationship-relevant facts (preferences, life events, opinions, plans) and stored with structured metadata (subject, predicate, permanence, importance, tags).
- **Contextual recall:** Before answering questions about a contact, the butler queries both domain tools and memory facts, synthesizing a complete picture.
- **Cross-referencing:** Memory facts enable contextual inference that domain tables alone cannot provide (e.g. "Sarah is allergic to shellfish" stored as a fact enables the butler to flag seafood restaurant suggestions).

Memory fact taxonomy for relationship domain:
- **Permanent facts:** Birthday, family relationships, identity-defining attributes.
- **Stable facts:** Workplace, location, relationship status, dietary restrictions, allergies.
- **Standard facts:** Current interests, hobbies, ongoing projects, preferences.
- **Volatile facts:** Temporary states, travel plans, mood, passing interests.

### 9.2 Calendar Module (Required)
The calendar module enables:
- Social event scheduling (dinners, catchups, celebrations).
- Follow-up reminders tied to calendar events.
- Birthday/anniversary event creation.
- Conflict detection with `suggest` policy (propose alternatives, don't silently overbook).

Calendar rules:
- Events are written to the dedicated butler subcalendar, not the user's primary calendar.
- Attendee invites are out of scope for v1.

## 10. Automated Ingestion and Classification

### 10.1 Conversational Fact Extraction
When processing messages routed from Switchboard (indicated by `request_context` presence), the butler must:

1. **Identify mentions of people** in the message text.
2. **Extract facts** about those people using the memory module's fact taxonomy.
3. **Store facts** with appropriate permanence, importance, and tags.
4. **Log interactions** when the message implies the user interacted with someone.
5. **Update domain records** when facts map to structured fields (e.g. a birthday mentioned in conversation should create both a memory fact and an `important_dates` record).

### 10.2 Contextual Inference
The butler should perform cross-fact inference when answering questions or generating suggestions:

- **Dietary constraints:** "Allergic to shellfish" implies cannot eat at seafood restaurants, should not receive shellfish-related gifts.
- **Location changes:** "Moving to Seattle" implies address update, potential impact on in-person meetup feasibility, and a follow-up check-in opportunity after the move.
- **Life events:** "Just had a baby" implies congratulations are appropriate, sleep deprivation context for scheduling, and a potential gift occasion.
- **Relationship graph:** "Sarah is John's sister" implies shared family events, and a note about John may be relevant when interacting with Sarah.
- **Temporal reasoning:** "Started a new job 3 months ago" implies it may be appropriate to ask how it's going; "Getting married in June" implies an upcoming gift/card occasion.

### 10.3 Duplicate and Conflict Resolution
When ingesting facts that conflict with existing data:
- **Memory facts:** Use the memory module's supersession mechanism (new fact supersedes old with linking).
- **Domain records:** Flag conflicts for user confirmation rather than silently overwriting (e.g. "I have Sarah's birthday as March 15, but you just said March 16 — which is correct?").

### 10.4 Contact Salience and First-Name Disambiguation

#### Problem

When a user says "I met Chloe on Saturday", the system must resolve "Chloe" to a contact record. If the address book contains both Chloe Wong (partner) and Chloe Tan (former colleague), a naive first-name match returns an ambiguous result. In practice, the vast majority of bare first-name mentions refer to the *most important* person with that name — but the current resolver has no model of importance.

#### Design Principle

Contacts have implicit **salience** — a composite signal of relational closeness, interaction density, and user-declared importance. When multiple contacts match a first-name query, salience scoring breaks ties transparently: the system picks the most salient candidate and confirms the inference to the user rather than asking every time.

#### Salience Score Computation

When `contact_resolve` encounters multiple candidates for a name query, it computes a **salience score** for each candidate by summing weighted signals from existing domain data:

| Signal | Data Source | Scoring | Rationale |
|--------|------------|---------|-----------|
| Relationship type | `relationships` table (type to user) | spouse/partner: +50, parent/child/sibling: +30, close friend: +20, friend: +10, colleague: +5, acquaintance: +2 | Closest relationships dominate casual mentions |
| Interaction frequency | `interactions` count (last 90 days) | +2 per interaction, capped at +20 | Frequent contact correlates with conversational relevance |
| Interaction recency | `interactions` most recent timestamp | <7 days: +15, <30 days: +10, <90 days: +5, else: +0 | Recent interactions boost contextual relevance |
| Fact & note density | `quick_facts` + `notes` row count | +1 per record, capped at +10 | More recorded detail implies deeper engagement |
| Stay-in-touch cadence | `contacts.stay_in_touch_days` | weekly (≤7): +10, biweekly (≤14): +7, monthly (≤30): +5 | User explicitly declared contact importance |
| Group membership | `group_members` → `groups.group_type` | family: +10, couple: +15, friends: +5, team: +3 | Membership in close-tie groups implies higher salience |

The salience score is added to the existing candidate `score` (which includes name-match quality and context-boost points). This means salience acts as a tiebreaker when name-match quality is equal, and strong name matches can still override salience (e.g., "Chloe Tan" as an exact full-name match still resolves to Chloe Tan regardless of salience).

Salience is computed lazily — only when disambiguation is needed (multiple candidates), not on every resolve call.

#### Resolution Thresholds

After salience scoring, the resolver applies these decision rules:

| Condition | Behavior |
|-----------|----------|
| Single candidate after name match | Return as HIGH confidence, no salience needed |
| Multiple candidates, top scorer leads by ≥30 points | Return top candidate as HIGH confidence with `inferred: true` |
| Multiple candidates, gap <30 points | Return MEDIUM confidence with `inferred: false`, present all candidates |
| No candidates | Return NONE confidence |

The 30-point threshold is chosen because it requires at least a meaningful relationship-type difference (spouse vs. colleague = 45 points) or a strong combination of frequency + recency signals. Trivial differences (e.g., one extra note) don't trigger auto-resolution.

#### Resolver Response Shape

When salience-based inference is applied, the resolver response includes two additional fields:

```python
{
    "contact_id": "<resolved-uuid>",
    "confidence": "high",
    "inferred": True,                                      # salience was used to pick winner
    "inferred_reason": "partner, most frequent contact",   # human-readable explanation
    "candidates": [
        {"contact_id": "...", "name": "Chloe Wong", "score": 145, "salience": 85},
        {"contact_id": "...", "name": "Chloe Tan",  "score": 62,  "salience": 12}
    ]
}
```

When salience is not needed (single match) or doesn't produce a clear winner, `inferred` is `false` and `inferred_reason` is `null`.

#### LLM Confirmation Behavior

The resolver provides the data; the LLM provides the UX. The butler's response behavior depends on the `inferred` flag:

| `inferred` | LLM Behavior |
|------------|--------------|
| `true` | Proceed with the resolved contact and confirm: *"Assuming you're referring to Chloe Wong (your partner) — noted that you met her on Saturday."* |
| `false`, multiple candidates | Ask the user: *"Did you mean Chloe Wong or Chloe Tan?"* |
| N/A (single match) | Proceed silently (no disambiguation needed) |

The confirmation phrasing should include the `inferred_reason` in parentheses to make the inference transparent and correctable. If the user corrects the inference (e.g., "No, I meant Chloe Tan"), the butler should:
1. Redo the action with the correct contact.
2. Optionally note the correction for future context boosting (though salience scores are computed live, not cached, so this is supplementary).

#### Switchboard Integration

The `relationship-extractor` skill on the Switchboard already produces `contact_hint` fields and handles ambiguous matches at the extraction level. Salience scoring is applied *after* extraction, during the `contact_resolve` call that the Switchboard makes before routing. The extractor's existing `candidates` array in ambiguous-match scenarios will be enriched with salience scores.

#### Salience Score Properties

- **Not cached.** Computed on-demand from live data. As interaction patterns change, salience shifts automatically.
- **Not user-editable.** The user influences salience indirectly through relationship types, interaction frequency, and stay-in-touch settings. No "pin this contact" override — the system should reflect actual relationship patterns.
- **Composable with context boost.** Salience and textual context boosting stack. If "Chloe from work" appears in context and Chloe Tan's metadata includes "work", the context boost can lift Chloe Tan past Chloe Wong's salience advantage — which is the correct behavior.
- **Zero-cost for unambiguous names.** The salience query only runs when `contact_resolve` finds ≥2 candidates. Single-match names skip it entirely.

#### Edge Cases

| Scenario | Expected Behavior |
|----------|-------------------|
| New contact "Chloe Tan" with 0 interactions | Salience near zero; Chloe Wong (partner) auto-resolves. Correct default. |
| User starts interacting with Chloe Tan daily | Interaction frequency/recency gradually raises Chloe Tan's salience. After sustained contact, system may start asking rather than assuming. |
| "Chloe from work mentioned..." | Context boost for "work" stacks with salience. If Chloe Tan is the work colleague, context can override salience. |
| Three contacts named "Alex" | Same logic applies. If one Alex is a close friend (salience 60) and the other two are acquaintances (salience 5-10), the close friend auto-resolves. If two are close, the system asks. |
| Nickname match (e.g., "Chlo" → Chloe) | Resolver already handles nickname/diminutive matching. Salience applies after candidate identification, regardless of how candidates were found. |

## 11. Interactive Response Contract
When `request_context` is present with a user-facing `source_channel`, the butler engages interactive response mode per the behavioral contract in `roster/relationship/CLAUDE.md`.

Response mode selection:
- **React** (emoji only): Simple, self-explanatory actions (e.g. date added).
- **Affirm** (brief text): Actions needing short confirmation (e.g. interaction logged).
- **Follow-up** (question/suggestion): When more info is needed or helpful next steps exist.
- **Answer** (substantive): Direct questions about contacts or relationships.
- **React + Reply** (combined): Immediate visual feedback plus substantive response.

Guideline: Always respond when `request_context` is present. Silence feels like failure. Be concise — users are on mobile.

## 12. Target-State Feature Roadmap

### Phase 1: Foundation (Implemented)
- Contact CRUD with full field support, including `contact_resolve` for fuzzy name matching
- Bidirectional relationships with typed taxonomy (15 seeded relationship types)
- Important dates with upcoming window queries
- Notes with emotion tags and full-text search
- Interaction logging with type taxonomy and direction/duration/metadata
- Reminders (one-time, recurring yearly, recurring monthly)
- Gift pipeline (idea -> purchased -> wrapped -> given -> thanked)
- Two-party loan tracking with currency support
- Groups with type taxonomy and member roles
- Labels with color support
- Quick facts (key-value per contact)
- Activity feed auto-population
- Contact info management (email, phone, social, addresses) with reverse-lookup
- Life events with typed taxonomy (12 seeded types across 3 categories)
- Tasks (contact-scoped or general, with completion tracking)
- Stay-in-touch basics (set cadence, query overdue contacts)
- vCard import and export
- Dashboard API (read-only, dual data-access pattern)
- Gift brainstorm skill
- Reconnect planner skill
- Calendar integration (social events, follow-ups)
- Memory integration (fact extraction, contextual recall)
- Interactive Telegram response mode

### Phase 2: Relationship Intelligence
- **Contact salience and first-name disambiguation:** Salience-based scoring for `contact_resolve` when multiple contacts share a first name, with transparent LLM confirmation (see §10.4).
- **Stay-in-touch digest and detailed status:** Proactive daily digest generation and per-contact staleness detail view (basic set/overdue is Phase 1).
- **Contact merge:** Deduplicate contacts, combining all related entities into a single record.
- **Contact timeline API:** Unified chronological view combining notes, interactions, gifts, dates, and feed events.
- **Relationship health score:** Computed metric per contact based on interaction frequency, recency, reciprocity, and configured importance.
- **Contextual inference engine:** Cross-fact reasoning for dietary constraints, location impacts, life event implications, and relationship graph effects.

### Phase 3: Proactive Intelligence
- **Smart birthday/occasion reminders:** Include gift pipeline status, preference-based gift suggestions, and contextual details (e.g. "Sarah turns 30 — milestone birthday").
- **Reconnection drafts:** AI-generated outreach messages personalized with recent context, shared interests, and last interaction summary.
- **Life event detection:** Automated detection of major life events from conversation patterns (new job, move, baby, breakup) with appropriate response suggestions.
- **Seasonal and cultural awareness:** Surface relevant cultural/religious dates for contacts where known (e.g. Lunar New Year for contacts who celebrate it).

### Phase 4: Organization and Enrichment
- **Smart groups:** Auto-suggested groupings based on interaction patterns and relationship graph (e.g. "You frequently mention John, Sarah, and Alex together — create a group?").
- **Contact enrichment prompts:** Periodic suggestions to fill in missing profile data based on conversational mentions.
- **Duplicate detection:** Automated identification of potential duplicate contacts based on name similarity, shared contact info, or overlapping relationship data.
- **Conversation topics tracker:** Track topics discussed with each contact to avoid repetition and enable richer follow-ups.
- **Food and preference profiles:** Structured preference tracking (favorite foods, restaurants, activities, allergies, dislikes) with inference for planning suggestions.
- **Pet tracking:** Record pets' names, types, and relevant details for contacts who are pet owners.

### Phase 5: Advanced CRM
- **CSV import:** CSV import for bootstrapping from other CRM tools (vCard import is Phase 1).
- **Journal/diary integration:** Personal journal entries that can reference contacts and be cross-linked to interaction history.
- **Company/organization tracking:** Structured company records linked to contacts via employment relationships.
- **Multi-address lifecycle:** Track address history with move dates, enabling "where were they living when..." queries.
- **Custom fields:** User-defined structured fields beyond the built-in schema, for domain-specific tracking needs.
- **Relationship strength visualization data:** API surface for frontend to render relationship graph, interaction heatmaps, and staleness dashboards.

## 13. Observability Contract
Inherits base butler observability requirements.

Domain-specific metric recommendations:
- `butlers.relationship.contacts_total` (gauge): Total active (listed) contacts.
- `butlers.relationship.interactions_logged` (counter): Interactions logged, by type.
- `butlers.relationship.facts_extracted` (counter): Memory facts extracted from conversations.
- `butlers.relationship.reminders_triggered` (counter): Reminders that fired.
- `butlers.relationship.stay_in_touch_overdue` (gauge): Contacts currently overdue for contact.
- `butlers.relationship.gifts_in_pipeline` (gauge): Gifts by pipeline stage.

Required low-cardinality attributes: `butler=relationship`, `tool_name`, `outcome`, `trigger_source`.

## 14. Security and Safety Invariants
- Relationship data is highly personal. All persistence is butler-local per base contract.
- No cross-butler DB access. Inter-butler communication only via MCP/Switchboard.
- Outbound notifications (reminders, digests) go through `notify` -> Switchboard -> Messenger. No direct channel access.
- Contact data must never be included in metrics or low-cardinality log attributes.
- Memory facts with `sensitive` tag should be excluded from broad recall queries and only surfaced when specifically relevant.

## 15. Comparison with Monica CRM Feature Coverage

The following maps Monica CRM's feature surface to the Relationship Butler's target-state coverage, highlighting where the butler's automated ingestion and LLM-driven intelligence provide advantages.

| Monica CRM Feature | Relationship Butler Coverage | Notes |
|---|---|---|
| Contact management | Phase 1 (implemented) | Full CRUD, search, archive |
| Contact photos/avatars | Phase 1 (schema supports `avatar_url`) | URL-based, no upload hosting |
| Custom contact fields | Phase 5 (target state) | Quick facts cover most use cases now |
| Relationship types | Phase 1 (implemented) | Bidirectional, typed |
| How we met | Phase 1 (notes + memory facts) | No dedicated field; captured as note or memory fact |
| Activities/interactions | Phase 1 (implemented) | Richer: includes emotion, duration, metadata |
| Phone calls logging | Phase 1 (interaction_type=call) | Unified interaction model |
| Conversations | Phase 1 (interaction + notes) | No per-message conversation threading yet |
| Reminders | Phase 1 (implemented) | One-time + recurring + stay-in-touch |
| Important dates | Phase 1 (implemented) | Year-optional, proactive reminders |
| Gift tracking | Phase 1 (implemented) | 5-stage pipeline (Monica has 3) |
| Debt tracking | Phase 1 (implemented) | Two-party model with currency |
| Notes | Phase 1 (implemented) | With emotion tags (Monica lacks this) |
| Journal/diary | Phase 5 (target state) | Not yet implemented |
| Life events | Phase 1 (implemented) | Typed taxonomy with 12 event types; auto-detection from conversations is Phase 3 |
| Tags/labels | Phase 1 (implemented) | With color support |
| Groups | Phase 1 (implemented) | Typed (family, friends, team, couple) with roles |
| Search | Phase 1 (implemented) | Full-text across multiple fields |
| Companies | Phase 5 (target state) | Contact `company` field exists; structured company records planned |
| Tasks/to-dos | Phase 1 (implemented) | Contact-scoped tasks with completion tracking |
| vCard import | Phase 1 (implemented) | Both import and export implemented |
| vCard export | Phase 1 (implemented) | |
| API access | Phase 1 (MCP tools + dashboard API) | MCP-native, not REST-first |
| Multi-user | Out of scope | Single-user personal CRM by design |
| Food preferences | Phase 1 (quick facts + memory) | Captured as facts with inference (e.g. allergy -> restaurant constraint) |
| Pet tracking | Phase 4 (target state) | Currently capturable via quick facts or memory |
| Dashboard statistics | Phase 2 (target state) | Relationship health scores, interaction heatmaps |
| Localization/i18n | Out of scope for v1 | English-first; future consideration |
| 2FA/security | Platform-level (base contract) | Not butler-specific |

**Key advantages over Monica CRM:**
- **Automated fact ingestion:** Most data enters through natural conversation, not manual forms.
- **LLM-driven inference:** Cross-fact reasoning (dietary restrictions, life event implications, relationship graph effects).
- **Proactive intelligence:** AI-generated reconnection suggestions, contextual gift ideas, and timely follow-up prompts.
- **Emotion tracking:** Notes and interactions capture emotional context for richer recall.
- **Memory integration:** Facts are stored with permanence and importance metadata, enabling intelligent recall prioritization.
- **Conversational interface:** Primary interaction is natural language via Telegram/email, not a web form.

## 16. Skills

### 16.1 Gift Brainstorm (Implemented)
Generate personalized gift ideas based on contact preferences, interests, recent life events, and budget constraints. Integrates with the gift pipeline to add selected ideas directly.

### 16.2 Reconnect Planner (Implemented)
Identify contacts at risk of relationship decay based on interaction frequency and importance. Generate prioritized reconnection plans with personalized outreach suggestions.

### 16.3 Event Planner (Target State)
Plan social gatherings by cross-referencing attendee preferences, dietary restrictions, location constraints, and calendar availability. Suggest venues, dates, and activities that work for the group.

### 16.4 Relationship Review (Target State)
Monthly or quarterly relationship health review. Summarize interaction patterns, highlight neglected important relationships, celebrate maintained connections, and suggest adjustments to stay-in-touch cadences.

## 17. Change Control Rules
Any change to Relationship Butler domain contracts must update, in the same change:
- this document,
- migration/schema artifacts if relevant,
- domain tool implementations if tool signatures change,
- dashboard API routes if API surface changes,
- conformance tests for affected contracts.

## 18. Non-Normative Note
Implementation mapping between this target-state document and current codebase is maintained through the project's beads issue tracker and OpenSpec change management workflow. Consult `roster/relationship/` for current implementation state.
