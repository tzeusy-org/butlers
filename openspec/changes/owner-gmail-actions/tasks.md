## 1. Module (bu-kqme7f.5)

- [x] 1.1 Move payload and body decoding to `butlers.gmail_payload`; connector tests pass unchanged.
- [x] 1.2 Add `GmailModule` with the four tools behind `enforcement_enabled()`.
- [x] 1.3 Declare D4 descriptors and the `external:gmail` store writers; add `[modules.gmail]` to Messenger.
- [x] 1.4 Document the module and the owner rollout step in `docs/modules/gmail.md`.

## 2. Exposure rule (bu-kqme7f.10)

- [x] 2.1 Trigger-thread binding, conditional park and per-goal cap of 5; replace the interim
  `exposed_modify_refused` refusal.

## 3. Archive

- [ ] 3.1 On archive, move the delta to `openspec/specs/module-gmail/spec.md` and repoint
  the `"gmail/mailbox"` key of `CATEGORY_SPECS` in `tool_presentation_inventory.py` to it
  (keep the `module/group` key; a bare `"gmail"` key trips the connector-identity guard).

## 4. Drafts and replies (bu-kqme7f.6)

- [x] 4.1 Add bounded draft list/get/save, plaintext threaded replies and D4 declarations.
- [x] 4.2 Park exposed/third-party draft writes without standing-rule bypass.
- [x] 4.3 Fence Gmail sends with committed metadata receipts and exact reconciliation.
- [x] 4.4 Show complete inert reply previews and guard replacement-draft cleanup.
- [ ] 4.5 Integrate canonical owner edits and audit snapshot through bu-mmdsl7; preserve the
  existing refusal until that shared prerequisite lands.
- [ ] 4.6 Independent source review and hosted merge-group verification of the final head.
