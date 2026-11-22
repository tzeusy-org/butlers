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

All four are `owner_authority=False` and carry every S1 category. `gmail_modify_threads` is
`record` because archive, read state, star, important and existing user labels are reversible
changes to the owner's own mailbox state (security.md, "Approval Gates"). It is a label set, so it
is naturally idempotent and keeps no effect receipts; the broker still records the effect in the
effect ledger (D12). Store writers: `external:gmail` lists `gmail_modify_threads` as its record
capability and `GmailModule._modify_threads` as its native writer.

### D3. Exposed and third-party modify: interim refusal

The recorded owner rule (2026-10-15 correction, unchanged by the 2026-10-20 owner decision): under a
third-party-origin or exposed invocation `gmail_modify_threads` may touch only the thread that
triggered the goal; removing INBOX or UNREAD parks for owner approval; any other target parks; and
a per-goal cap of 5 modify calls applies besides the 50-thread per-call cap.

That rule needs trigger-thread binding, a conditional park in `capability_broker/policy.py` and a
per-goal counter. Those land in bu-kqme7f.10. Until then the module refuses `gmail_modify_threads`
with the typed error `exposed_modify_refused` whenever the lineage is missing, exposed, or
third-party. This is stricter than the recorded rule and is invisible to the owner because the
module is dark until the switch. The rule's path is marked "refused until bu-kqme7f.10 lands".

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

- bu-kqme7f.10: the substrate for D3.
- Owner rollout: confirm `gmail.modify` per account on the dashboard and re-consent through
  `/api/oauth/google/start?force_consent=true` where it is missing (docs/modules/gmail.md).
