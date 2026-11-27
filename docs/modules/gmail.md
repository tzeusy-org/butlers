# Gmail

The module lets the generalist lead read and organize the owner's Gmail, prepare private drafts,
and send approval-gated plaintext replies in existing threads. It is
owned by Messenger (`[modules.gmail]` in `roster/messenger/butler.toml`) and is dark: it registers
no tool until the RFC 0038 goal-runtime switch turns on. Spec: `openspec/changes/owner-gmail-actions`.

## Tools

| Tool | What it does |
| --- | --- |
| `gmail_list_mailboxes` | Connected Google accounts the assistant may act in, with labels. |
| `gmail_search` | Gmail query syntax over one account; at most 50 threads per call, paginated. |
| `gmail_get_thread` | One thread: headers, decoded plain-text bodies, attachment metadata. |
| `gmail_modify_threads` | Archive or unarchive, mark read or unread, star, mark important, add or remove existing user labels; at most 50 threads per call. |
| `gmail_list_drafts` | Paginated draft references, at most 50 per call. |
| `gmail_get_draft` | Scrubbed recipients, subject and plaintext draft body. |
| `gmail_save_draft` | Create or replace a private draft; optionally reply in an existing thread. |
| `gmail_send_reply` | Send the exact approved plaintext reply with a durable effect-id fence. |

New-message sending, forwarding, Bcc, HTML, attachments and send-as aliases are unavailable.
The module never trashes mail, creates labels or returns attachment content. It deletes only an
explicit replacement draft whose current content still matches a confirmed send.
`account` is the email from `gmail_list_mailboxes`.

## Trust and scrubbing

Search snippets, subjects and bodies are third-party text. Reading them marks the invocation
exposed (RFC 0038 D6). Every returned subject, snippet and body is bearer-scrubbed (one-time
codes, reset and magic links); if the scrubber fails the message comes back with metadata only and
`content_withheld`. Results go only to the calling goal: they are not `ingest.v1` envelopes and
admit no goal. Bodies decode through `butlers.gmail_payload`, shared with the ingest connector.

## Errors

`invalid_argument`, `unknown_account`, `scope_missing` (re-consent needed), `account_revoked`
(the shared mint rule also marks the account revoked), `credentials_unavailable`, `not_found`,
`rate_limited`, `provider_error`, `exposed_modify_refused`. No error carries token material.
Draft/send validation also returns `unverified_call`, `approval_required`, `receipt_unavailable`
or `effect_conflict`. A send returns `sent`, `failed` or `unknown`; an unknown result never
requests a retry.

## Drafts, replies and approval

Use explicit lists of bare email addresses for `to` and optional `cc`, with nonempty To and
1-20 total recipients. Subject is nonblank single-line text of at most 998 characters; body is
at most 50,000 plaintext characters. A reply pairs `thread_id` and `in_reply_to_message_id` from
the same account. Its subject must match after stripping repeated Re/Fw/Fwd prefixes and
surrounding whitespace. Sends additionally pin `in_reply_to_sender` and `in_reply_to_date` from
the target message's `from` and `date` fields returned by `gmail_get_thread`; dispatch verifies
those values before writing anything.

MIME uses the selected account's From, explicit To/Cc, Subject, Date and exact decoded body,
without adding a quote or newline. In-Reply-To is the target's Message-ID. References is the
target's References (or In-Reply-To fallback), followed by that Message-ID. Gmail receives both
the raw MIME and threadId. Drafts have no effect-derived Message-ID and never call messages.send.
Draft writes do not retry automatically; inspect drafts after an uncertain response.

An unexposed owner, obligation or staffer can save a draft as an ordinary record effect. Every
exposed or third-party-origin save parks with its exact arguments and lineage; no standing rule
approves that park. The existing broker and Messenger gates govern sends. Every To and Cc party
must resolve uniquely to the live owner to receive the owner classification. Any other party,
including an unknown or ambiguous address, requires per-event approval or an eligible
owner-created standing rule. Exposed rules must pin the subject and body, plus every supplied
safety argument. Park-only grants never use standing rules. Stop cancels parked replies.

The existing exact-call preview shows the account, all recipients, subject, target sender/date,
thread/message ids, any replacement draft and full body. Links in Telegram cards are defanged,
and the envelope sets `render_links_inert=true`. A card that cannot fit everything offers only
Open; the dashboard shows the full bounded body as inert plaintext. Approval runs the stored
call through the existing domain decision and broker. The canonical owner-edit and original-args
audit snapshot prerequisite is `bu-mmdsl7`: until it lands, the shared API continues refusing edits
to goal effects. This package does not bypass that refusal.

## Send identity and uncertainty

The verified `effect_id` generates Message-ID and keys a metadata-only row in Messenger's
`gmail_owner_sends` (`msg_005`). Its reservation commits before the one messages.send handoff.
The row contains account/thread/message ids, the argument digest, outcome and timestamps; it
contains no recipients, subject or body. Concurrent or repeated reservations cannot send twice.
Reservation failure sends nothing. A definite Gmail 4xx is failed; timeout, connection loss,
5xx, malformed acknowledgement or interruption after reservation is unknown. An MCP response
alone never marks a Gmail effect delivered in the broker ledger.

Repeated uncertain effects perform read-only reconciliation: exactly one SENT message in the
selected thread must have the generated Message-ID. Any other evidence leaves the send unknown,
without resending. The owner can inspect Gmail and reconcile uncertainty; if Gmail replaces the
client's Message-ID, the module cannot establish that exact proof. No worker verifies this live.

When `replaces_draft_id` is provided, a confirmed send reads the current draft before deleting
it. Thread and the digest of To/Cc, subject and decoded plaintext body must still match; an owner
edit keeps the draft with `draft_kept_changed`. An unavailable or uncertain cleanup reports
`draft_kept_unavailable` and preserves delivery truth. Retained send receipts prevent migration
downgrade; rollback can disable the switch while preserving those receipts.

## Exposed and third-party modify

Recorded owner rule: under a third-party-origin or exposed invocation `gmail_modify_threads` may
touch only the thread that triggered the goal; removing INBOX or UNREAD parks for owner approval;
any other target parks; a per-goal cap of 5 modify calls applies. The capability broker enforces
it (RFC 0038 D13 "Gmail exposure rule"):

- When a Gmail message admits a goal, admission records that message's account and thread on the
  goal (`switchboard.goals.trigger_gmail_account` / `trigger_gmail_thread`, immutable).
- A bound call (exposed invocation, or third-party goal) dispatches only for that account and
  thread with no INBOX or UNREAD removal. Anything else parks for per-event approval; a goal no
  Gmail message admitted parks every change.
- The sixth bound call in a goal is refused. Parked, approved, committed and unknown effects
  count; failed effects and parks the owner rejected (or that expired or were cancelled) do not;
  a replay of a live call is refused as a duplicate and not counted. A call answered with a typed
  error such as `invalid_argument` still counts, since the broker never reads tool results.
- Exposure decides, not origin: an unexposed owner, obligation or staffer goal is not bound.

The module re-checks what the call alone decides: no lineage or an unknown origin, and an exposed
or third-party INBOX/UNREAD removal that is not the owner-approved call, return
`exposed_modify_refused` and change nothing.

## Owner rollout step

Before the switch release enables this in production, the owner confirms in the dashboard's Google
account settings that each account the assistant should act in lists `gmail.modify` among its
granted scopes. For any account that lacks it, re-consent through the dashboard's Google connect
flow (`/api/oauth/google/start` with `force_consent=true`). No other scope is needed. Workers never
run this against a real Google account.
Only the owner can activate the switch and approve a first real parked reply. This source change
does not activate Gmail actions, perform OAuth, mutate a live draft or send real mail.
