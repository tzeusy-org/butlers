## 1. Module (bu-kqme7f.5)

- [x] 1.1 Move payload and body decoding to `butlers.gmail_payload`; connector tests pass unchanged.
- [x] 1.2 Add `GmailModule` with the four tools behind `enforcement_enabled()`.
- [x] 1.3 Declare D4 descriptors and the `external:gmail` store writers; add `[modules.gmail]` to Messenger.
- [x] 1.4 Document the module and the owner rollout step in `docs/modules/gmail.md`.

## 2. Exposure rule (bu-kqme7f.10)

- [ ] 2.1 Trigger-thread binding, conditional park and per-goal cap of 5; replace the interim
  `exposed_modify_refused` refusal.

## 3. Archive

- [ ] 3.1 On archive, move the delta to `openspec/specs/module-gmail/spec.md` and repoint
  `CATEGORY_SPECS["gmail"]` in `tool_presentation_inventory.py` to it.
