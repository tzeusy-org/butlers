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

The module SHALL register its four tools only while the RFC 0038 switch is on
(`enforcement_enabled()`), and SHALL register none while it is off. Each tool SHALL carry the D4
descriptor in the change design (RFC 0038 D4 to D7).

ID: REQ-module-gmail-008
Source: bu-kqme7f.5; RFC 0038 D4 to D7
Scope: v1-mandatory

#### Scenario: Switch off
- **WHEN** enforcement is off
- **THEN** no `gmail_*` tool is registered
