## ADDED Requirements

### Requirement: Mailbox listing

The Gmail module SHALL expose `gmail_list_mailboxes`, returning each active connected Google account
with its `account` label (email), `is_primary`, `modify_granted`, and the labels that can be
changed (`INBOX`, `UNREAD`, `STARRED`, `IMPORTANT` and the account's user labels). An account that
lacks `gmail.modify` SHALL be listed with `modify_granted=false` and no labels.

ID: REQ-module-gmail-001
Source: bu-kqme7f.5; RFC 0038 D2, D4
Scope: v1-mandatory

#### Scenario: Accounts and labels
- **WHEN** two active accounts are connected and one lacks `gmail.modify`
- **THEN** both are listed and only the granted account carries labels

### Requirement: Bounded search

`gmail_search` SHALL run Gmail query syntax over one account and return at most 50 thread
summaries per call with a `next_page_token`. It SHALL refuse `max_results` outside 1 to 50 before
any Gmail call. Its output is untrusted third-party text and reading SHALL expose the invocation
(RFC 0038 D6). Results SHALL NOT be submitted as `ingest.v1` envelopes and SHALL NOT admit a goal.

ID: REQ-module-gmail-002
Source: bu-kqme7f.5; connector-gmail "[TARGET-STATE] On-demand backfill"; RFC 0038 D4, D6
Scope: v1-mandatory

#### Scenario: Page size is enforced
- **WHEN** `max_results` is 51
- **THEN** the call returns `invalid_argument` and no Gmail call is made

#### Scenario: No ingest
- **WHEN** a search or thread read succeeds
- **THEN** no `ingest.v1` envelope is submitted and no goal is admitted

### Requirement: Thread reads return decoded text and attachment metadata only

`gmail_get_thread` SHALL return the thread's messages oldest first with headers, a plain-text body
decoded through the shared body helper, a `truncated` flag, and attachment metadata. It SHALL NOT
return attachment content.

ID: REQ-module-gmail-003
Source: bu-kqme7f.5
Scope: v1-mandatory

#### Scenario: Attachments are metadata
- **WHEN** a message carries a PDF attachment
- **THEN** its filename, type and size are returned and its bytes are not

### Requirement: Bearer scrubbing fails closed

Every returned subject, snippet and body SHALL be bearer-scrubbed. If the scrubber raises, the
message SHALL be returned with metadata only and `content_withheld=true`.

ID: REQ-module-gmail-004
Source: bu-kqme7f.5; connector-gmail "Bearer material never persists"
Scope: v1-mandatory

#### Scenario: One-time code
- **WHEN** a body contains a one-time code
- **THEN** the code is replaced by its placeholder in the result

#### Scenario: Scrubber failure
- **WHEN** the scrubber raises
- **THEN** no subject, snippet or body is returned

### Requirement: Thread label changes

`gmail_modify_threads` SHALL change `INBOX`, `UNREAD`, `STARRED`, `IMPORTANT` and existing user
labels on at most 50 threads per call. It SHALL refuse `TRASH`, `SPAM`, `SENT`, `DRAFT`, `CHAT`,
`CATEGORY_*`, unknown labels, empty changes, a label both added and removed, and more than 50
thread ids before any Gmail call. A partial failure SHALL return per-thread outcomes. The module
SHALL NOT send, draft, trash or delete mail.

ID: REQ-module-gmail-005
Source: bu-kqme7f.5; security.md "Approval Gates"; RFC 0038 D4
Scope: v1-mandatory

#### Scenario: Archive
- **WHEN** `INBOX` is removed from a thread
- **THEN** the thread is archived and the outcome is `ok`

#### Scenario: Disallowed label
- **WHEN** the change adds `TRASH`
- **THEN** the call returns `invalid_argument` and no Gmail call is made

### Requirement: Exposed and third-party modify follows the owner rule

Under an exposed or third-party-origin invocation, `gmail_modify_threads` SHALL touch only the
thread that triggered the goal without approval; removing `INBOX` or `UNREAD` SHALL park for
per-event owner approval; any other target SHALL park for per-event owner approval; and a per-goal
cap of 5 modify calls SHALL apply besides the 50-thread per-call cap, refusing the sixth.

The broker enforces the rule (RFC 0038 D5, D10, D13). Admission SHALL record the admitting Gmail
message's account and thread on the goal, once, from the trusted source reader; a goal no Gmail
message admitted SHALL park every bound call. The cap SHALL count the goal's parked, approved,
dispatched, committed and unknown effects of the capability, SHALL NOT count failed effects or
parks that ended rejected, expired or cancelled, and a replay of a live call, even at the cap, SHALL be refused as a
duplicate without being counted. The domain SHALL refuse with `exposed_modify_refused` a call with
no lineage or an unknown origin, and an exposed or third-party `INBOX` or `UNREAD` removal that is
not the owner-approved call itself.

Exposure decides, not origin. An unexposed `obligation` or `staffer` goal SHALL keep the ordinary
`record` behavior, like an unexposed owner goal: an obligation's authority is owner-declared (RFC
0038 D5) and its prompt is trusted unless created under exposure (D6), and the rule guards against
injected mail, which only reaches a goal by exposing it. An exposed obligation is bound like any
other exposed goal and, having no triggering thread, parks every change.

ID: REQ-module-gmail-006
Source: bu-kqme7f.5 owner rule (2026-10-15 correction, reaffirmed 2026-10-20); bu-kqme7f.10;
RFC 0038 D4, D5, D6, D7, D10, D13
Scope: v1-mandatory

#### Scenario: Label on the triggering thread
- **WHEN** a third-party-origin goal admitted by a Gmail message adds a label to that message's
  thread
- **THEN** the call dispatches without parking and is recorded in the effect ledger

#### Scenario: Injected archive of another thread
- **WHEN** an instruction in inbound mail makes the goal remove `INBOX` from another thread
- **THEN** the call parks for per-event owner approval and no Gmail call is made

#### Scenario: Archive or mark read of the triggering thread
- **WHEN** an exposed goal removes `INBOX` or `UNREAD` from its triggering thread
- **THEN** the call parks for per-event owner approval

#### Scenario: Per-goal cap
- **WHEN** a bound goal makes its sixth `gmail_modify_threads` call
- **THEN** the broker refuses it

#### Scenario: Failed call does not count
- **WHEN** a bound call fails and is retried
- **THEN** the failed effect does not count toward the cap

#### Scenario: Unexposed obligation
- **WHEN** an unexposed obligation goal archives a thread
- **THEN** the call dispatches as an ordinary `record` call

### Requirement: Account and scope errors are typed

An unknown or inactive account SHALL return `unknown_account`; an account without `gmail.modify`
SHALL return `scope_missing` naming the re-consent route; a revoked grant SHALL return
`account_revoked` and leave the account marked revoked by the shared mint rule. No Gmail call is
made for the first two. No error SHALL carry token material.

ID: REQ-module-gmail-007
Source: bu-kqme7f.5
Scope: v1-mandatory

#### Scenario: Missing scope
- **WHEN** the account has not granted `gmail.modify`
- **THEN** the call returns `scope_missing` and no Gmail call is made

#### Scenario: Revoked grant
- **WHEN** the token refresh answers `invalid_grant`
- **THEN** the call returns `account_revoked` and the account is marked revoked

### Requirement: Registration is flag-gated on Messenger

The module SHALL register its mailbox, draft and reply tools only while the RFC 0038 switch is on
(`enforcement_enabled()`), and SHALL register none while it is off. Each tool SHALL carry the D4
descriptor in the change design (RFC 0038 D4 to D7).

ID: REQ-module-gmail-008
Source: bu-kqme7f.5; RFC 0038 D4 to D7
Scope: v1-mandatory

#### Scenario: Switch off
- **WHEN** enforcement is off
- **THEN** no `gmail_*` tool is registered

### Requirement: Private Gmail drafts

`gmail_list_drafts` and `gmail_get_draft` SHALL read drafts in one active account with
`gmail.modify`, returning untrusted sensitive data. Lists SHALL be paginated with at most 50
entries; read content SHALL be bearer-scrubbed, failing closed when scrubbing fails.
`gmail_save_draft` SHALL create or replace a plaintext draft with explicit To/Cc, subject and
body, optionally replying to a message in a named thread. A draft SHALL deliver nothing.
Exposed or third-party-origin saves SHALL park with exact arguments and goal lineage for
per-event approval; no standing rule SHALL approve them. An unexposed owner-origin save SHALL
be an ordinary record effect. Drafts SHALL carry no effect-derived Message-ID.

ID: REQ-module-gmail-009
Source: bu-kqme7f.6; RFC 0038 D4, D6, D13
Scope: v1-mandatory

#### Scenario: Save a private owner draft
- **WHEN** an unexposed owner goal saves a draft
- **THEN** Gmail receives the exact To, Cc, subject and body and no messages.send call

#### Scenario: Exposed or third-party draft
- **WHEN** an exposed or third-party-origin goal saves a draft, even with an exact standing rule
- **THEN** the effect parks with lineage and Gmail receives no draft write before owner approval

### Requirement: Bounded plaintext in-thread replies

`gmail_send_reply` SHALL send only in an existing thread, using explicit To/Cc and a pinned
plaintext subject/body. Before any Gmail write it SHALL validate an active account with
`gmail.modify`, 1-20 bare recipient addresses with nonempty To, a single-line nonblank subject
of at most 998 characters, and a body of at most 50,000 characters. The target message SHALL
belong to the selected thread/account; its Message-ID, References (or In-Reply-To fallback),
subject, sender and date SHALL match the pinned reply context and reject unsafe headers.
Subject comparison SHALL strip repeated Re/Fw/Fwd prefixes and surrounding whitespace.
The MIME SHALL have From, To, optional Cc, Subject, Date, effect-derived Message-ID,
In-Reply-To and References. The decoded body SHALL equal the pinned body, with no added quote
or newline. Gmail messages.send SHALL receive the MIME raw data and threadId.
Bcc, HTML, attachments, forwarding, send-as aliases and new-message sends SHALL be unavailable.
Non-owner communication SHALL retain the broker and Messenger approval gates; every named To
and Cc party SHALL resolve uniquely to the live owner to receive the owner classification.
An exposed standing rule SHALL pin subject and body as well as all supplied safety arguments.

ID: REQ-module-gmail-010
Source: bu-kqme7f.6; REQ-capability-authorization-006; REQ-capability-authorization-016; REQ-butler-messenger-007
Scope: v1-mandatory

#### Scenario: Correct threading
- **WHEN** the owner approves a stored reply
- **THEN** its exact plaintext content is sent with the target threadId, In-Reply-To and References

#### Scenario: Invalid or changed target
- **WHEN** the target message is off-thread, its pinned sender/date changed, or the subject differs
- **THEN** no Gmail write occurs

#### Scenario: Non-owner Cc
- **WHEN** To resolves to the owner but Cc includes another or unresolved party
- **THEN** the reply parks unless an eligible owner-created standing rule approves it

### Requirement: Durable at-most-once reply handoff

`gmail_send_reply` SHALL use the verified effect_id as its send identity. It SHALL commit a
unique metadata-only receipt in Messenger before calling messages.send once, with no automatic
send retry. Reservation failure SHALL send nothing. A repeated key SHALL return its stored
receipt; changed arguments under that key SHALL refuse. Success SHALL record the Gmail id as
sent; definite 4xx rejection SHALL record failed; timeouts, connection loss, 5xx, malformed
acknowledgements or a crash after reservation SHALL remain unknown and SHALL never resend.
The broker SHALL mirror sent/failed/unknown truth rather than treating an MCP response as proof
of delivery. Reconciliation SHALL mark sent only for exactly one SENT message in the pinned
thread with the exact generated Message-ID. Missing or mismatched evidence SHALL preserve
unknown for owner reconciliation. Receipts SHALL contain no recipients, subject or body.
Downgrade SHALL lock the table and refuse while any receipt exists.

ID: REQ-module-gmail-011
Source: bu-kqme7f.6; REQ-capability-authorization-009; RFC 0038 D11, D12
Scope: v1-mandatory

#### Scenario: Repeated or interrupted send
- **WHEN** a committed send reservation is encountered again after success or interruption
- **THEN** messages.send is not called again

#### Scenario: Exact reconciliation
- **WHEN** exactly one SENT message in the selected thread carries the effect-derived Message-ID
- **THEN** an unknown receipt may settle to sent; any other evidence leaves it unknown

### Requirement: Guarded replacement draft cleanup

After a confirmed send naming replaces_draft_id, the module SHALL read that draft and delete it
only when its thread and the digest of all current To/Cc, subject and plaintext body equal the
sent content. Changed drafts SHALL remain with draft_kept_changed; unavailable or uncertain
cleanup SHALL report draft_kept_unavailable without changing the send outcome. Cleanup SHALL
never blindly delete a draft edited in Gmail after approval.

ID: REQ-module-gmail-012
Source: bu-kqme7f.6
Scope: v1-mandatory

#### Scenario: Owner changed the replacement draft
- **WHEN** Gmail's draft body differs from the sent body
- **THEN** the draft is retained and the sent receipt reports draft_kept_changed

### Requirement: Exact inert approval preview

The existing approval preview registry SHALL show the stored account, every To/Cc recipient,
subject, thread/message identifiers, replied-to sender/date, any replacement draft and the full
body as plaintext. Telegram SHALL offer only Open when the complete call exceeds its fit
budget, never approval of truncated content. Its envelope SHALL set render_links_inert=true;
URLs in the card SHALL be defanged. The dashboard SHALL show the full bounded body and every
recipient unredacted as inert plaintext, bound to the exact argument digest. Approval SHALL
execute the stored call once, or the owner's edit through the canonical shared goal-effect edit
path, never a call reissued by the model. Stop SHALL cancel parked replies. The canonical owner
edit and audit snapshot integration remains required by bu-mmdsl7; until it lands, the existing
shared API SHALL continue refusing goal-effect edits rather than bypassing the effect digest.

ID: REQ-module-gmail-013
Source: bu-kqme7f.6; bu-mmdsl7; REQ-capability-authorization-010; REQ-butler-messenger-003; RFC 0038 D13
Scope: v1-mandatory

#### Scenario: Oversized Telegram preview
- **WHEN** the whole stored reply does not fit the Telegram card
- **THEN** only Open is offered and the dashboard shows the complete body before approval

#### Scenario: Stop before approval
- **WHEN** a parked reply's goal stops before its approval dispatches
- **THEN** the effect is cancelled and Gmail receives no send
