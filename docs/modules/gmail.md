# Gmail

The module lets the generalist lead search, read, archive and label the owner's own Gmail. It is
owned by Messenger (`[modules.gmail]` in `roster/messenger/butler.toml`) and is dark: it registers
no tool until the RFC 0038 goal-runtime switch turns on. Spec: `openspec/changes/owner-gmail-actions`.

## Tools

| Tool | What it does |
| --- | --- |
| `gmail_list_mailboxes` | Connected Google accounts the assistant may act in, with labels. |
| `gmail_search` | Gmail query syntax over one account; at most 50 threads per call, paginated. |
| `gmail_get_thread` | One thread: headers, decoded plain-text bodies, attachment metadata. |
| `gmail_modify_threads` | Archive or unarchive, mark read or unread, star, mark important, add or remove existing user labels; at most 50 threads per call. |

Nothing here sends, drafts, trashes or deletes mail, creates labels, or returns attachment content.
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

## Exposed and third-party modify

Recorded owner rule: under a third-party-origin or exposed invocation `gmail_modify_threads` may
touch only the thread that triggered the goal; removing INBOX or UNREAD parks for owner approval;
any other target parks; a per-goal cap of 5 modify calls applies. That path is refused until
bu-kqme7f.10 lands: until then the call returns `exposed_modify_refused` and changes nothing.

## Owner rollout step

Before the switch release enables this in production, the owner confirms in the dashboard's Google
account settings that each account the assistant should act in lists `gmail.modify` among its
granted scopes. For any account that lacks it, re-consent through the dashboard's Google connect
flow (`/api/oauth/google/start` with `force_consent=true`). No other scope is needed. Workers never
run this against a real Google account.
