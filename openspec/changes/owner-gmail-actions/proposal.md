## Why

Owner direction 2026-10-15 asks Butlers to act in the owner's own Gmail (search, read, archive,
label), planned around the move to one generalist lead (RFC 0038). Today no tool reads the owner's
Gmail on demand: `email_search_inbox` and `email_read_message` read Butlers' own bot mailbox over
IMAP, and the Gmail ingest connector only reads. The owner's connected Google account already
grants `gmail.modify`. This closes items B4 (on-demand search) and B7 (archive and label) of the
2026-10-15 Dots case study (bu-kqme7f.5).

## What Changes

- ADD capability `module-gmail`: a Messenger-owned Gmail module with four tools
  (`gmail_list_mailboxes`, `gmail_search`, `gmail_get_thread`, `gmail_modify_threads`), typed
  account and scope errors, bearer scrubbing, D4 descriptors and registration only after the
  RFC 0038 switch.
- Move the connector's message-payload and body-decoding helpers to `butlers.gmail_payload` so the
  connector and the module share one decoder.
- Nothing sends, drafts, trashes or deletes mail. Attachment content is never returned.
- Under an exposed or third-party invocation `gmail_modify_threads` follows the recorded owner
  rule, enforced by the capability broker (bu-kqme7f.10, RFC 0038 D13; see design D3): only the
  triggering thread changes directly, other targets and INBOX/UNREAD removals park, and a goal
  makes at most five calls.

## Impact

- Code: `src/butlers/modules/gmail/`, `src/butlers/gmail_payload.py`,
  `src/butlers/connectors/gmail.py` (helper move), `src/butlers/core/tool_presentation_inventory.py`,
  `roster/messenger/butler.toml`. Exposure rule (bu-kqme7f.10): `src/butlers/capability_broker/`
  (policy, service, ledger, contracts), `src/butlers/core/goal_admission.py`,
  `src/butlers/core/goal_ledger.py`, the accepted-source reader and Switchboard migration sw_083.
- Docs: `docs/modules/gmail.md`, `docs/architecture/capability-broker.md`, RFC 0038 (D5, D7, D10,
  D13).
- Dark on main: no tool is registered until the goal-runtime switch (M6, bu-u5euwb).
