## Context

RFC 0038 makes domains deterministic capability services (D2) classified by catalog metadata (D4).
The Drive native tools (#5843, #5949) set the pattern: a module registers tools only while
`enforcement_enabled()` is true, so main stays dark until the switch.

## Decisions

### D1. Messenger owns the module; one client, per-call account

`[modules.gmail]` lives in `roster/messenger/butler.toml` (Messenger owns the email channel
surface). The module has no config; the Google account is a per-call argument resolved against
`list_google_accounts` (active accounts only), by email or account id. Tokens come from the shared
`resolve_google_credentials` and `GoogleTokenCache` path; an `invalid_grant` marks the account
revoked through the shared rule and returns `account_revoked`.

### D2. D4 descriptor table

| Tool | effect_class | output_trust | resource params | idempotency |
| --- | --- | --- | --- | --- |
| `gmail_list_mailboxes` | read | untrusted | none | none |
| `gmail_search` | read | untrusted | `account` | none |
| `gmail_get_thread` | read | untrusted | `account`, `thread_id` | none |
| `gmail_modify_threads` | record | untrusted | `account`, `thread_ids` | none |
| `gmail_list_drafts`, `gmail_get_draft` | read | untrusted | `account` | none |
| `gmail_save_draft` | record | trusted receipt | `account` | none |
| `gmail_send_reply` | communicate | trusted receipt | `account` | key_required |

All four are `owner_authority=False` and carry every S1 category. `gmail_modify_threads` is
`record` because archive, read state, star, important and existing user labels are reversible
changes to the owner's own mailbox state (security.md, "Approval Gates"). It is a label set, so it
is naturally idempotent and keeps no effect receipts; the broker still records the effect in the
effect ledger (D12). Store writers: `external:gmail` lists `gmail_modify_threads` as its record
capability and `GmailModule._modify_threads` as its native writer.

The draft/reply additions also declare `owner_authority=False`. Draft reads carry all S1
categories; save/send return ordinary metadata receipts with no content category. Save has no
recipient parameters because a draft delivers nothing, with an explicit non-delivery catalog
validation exemption. The broker parks exposed/third-party saves as per-event-only. Send names
both `to` and `cc` and pins all safety arguments, including subject/body. A custom renderer in
the existing preview registry shows account, target context and replacement draft in addition
to delivered content; it does not use the narrower generic content-only renderer.

Sends require `in_reply_to_sender` and `in_reply_to_date`, copied from the target message read,
so the deterministic preview can show that exact context without another Gmail read while
rendering. Dispatch verifies both against the selected message before writing anything. The
domain commits a metadata-only `msg_005` receipt before its single messages.send attempt. A
Gmail-specific HTTP-forwarder mapping mirrors sent/failed/unknown into the effect ledger.
Unknown reconciliation requires an exact SENT Message-ID match and never repeats the send.
Cleanup is digest-guarded and the migration refuses to drop retained receipts.

The canonical shared owner-edit/audit-snapshot integration belongs to prerequisite `bu-mmdsl7`.
This source package preserves the existing goal-effect edit refusal and does not claim that
edited-send acceptance is complete.

### D3. Exposed and third-party modify: the owner rule in the broker

The recorded owner rule (2026-10-15 correction, unchanged by the 2026-10-20 owner decision): under a
third-party-origin or exposed invocation `gmail_modify_threads` may touch only the thread that
triggered the goal; removing INBOX or UNREAD parks for owner approval; any other target parks; and
a per-goal cap of 5 modify calls applies besides the 50-thread per-call cap.

bu-kqme7f.10 builds it in the broker, with the RFC 0038 amendment (D5, D7, D10, D13 "Gmail
exposure rule") in the same change:

- Binding (D5). `Evidence.trigger_thread` carries `(account, thread_id)`; the accepted-source
  reader sets it for a Gmail envelope from the connector endpoint (`gmail:user:<address>`) and the
  envelope's `external_conversation_id`. Admission writes it to `switchboard.goals`
  (`trigger_gmail_account`, `trigger_gmail_thread`, sw_083), immutable under sw_063's guard.
- Park (D13). `policy.gmail_rule_applies` binds a call when the invocation is exposed or the goal
  is third-party. `policy.gmail_parks` lets it dispatch only for the bound account and thread
  with no INBOX or UNREAD removal; anything else becomes `PER_EVENT`. Applied in
  `Broker._authorize` right after `decide()`, before owner law, so it only narrows.
- Cap (D10). `BrokerLedger.counted_effects` counts the goal's live effects of the capability under
  the goal lock the call already holds; the sixth call is refused `policy_refused` before any
  reservation. Failed effects and ended parks do not count; the D12 duplicate check refuses a
  replay before it writes a row. A call the domain answers with a typed error result (for example
  an unknown label) is a committed effect and counts: the broker never reads tool content.
- Domain. The module refuses missing lineage or an unknown origin, and an exposed or third-party
  INBOX/UNREAD removal unless the lineage carries the approval of this exact call.

Obligation lineage: exposure decides, not origin. An unexposed obligation keeps the record
behavior (its authority is owner-declared, RFC 0038 D5; its prompt is trusted unless created under
exposure, D6); an exposed one is bound and, with no triggering thread, parks every change.

### D4. Reads expose the invocation and scrub bearer material

Search and thread reads return third-party text. Every subject, snippet and body passes
`scrub_text(..., provider_domain="gmail")`; a scrubber exception withholds content and returns
metadata only. Bodies decode through the shared helper and truncate per message with a
`truncated` flag. Results go only to the invoking goal and are never submitted as `ingest.v1`
envelopes.

### D5. Label rules

Changeable labels: `INBOX`, `UNREAD`, `STARRED`, `IMPORTANT` and existing user labels (by id or
exact name). `TRASH`, `SPAM`, `SENT`, `DRAFT`, `CHAT` and `CATEGORY_*` are refused; unknown labels,
empty changes, a label both added and removed, and more than 50 thread ids are refused before any
Gmail call. A partial failure returns per-thread outcomes.

## Open items

- Owner rollout: confirm `gmail.modify` per account on the dashboard and re-consent through
  `/api/oauth/google/start?force_consent=true` where it is missing (docs/modules/gmail.md).
