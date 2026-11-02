## 1. Implementation
- [x] 1.1 `sw_055`: request columns and pending index on `attachment_refs`.
- [x] 1.2 Switchboard `attachment_materialize`: request, bounded wait, typed results, D4 declaration, dark.
- [x] 1.3 Gmail connector: request poll loop, owner-scoped claim, failure recording, qualified SQL, replay-safe upsert, declared-size pre-check and post-download size guard, blob store wiring.
- [x] 1.4 ATTACHMENTS prompt: `status=not_fetched` for lazy refs, one shared builder.
- [x] 1.5 Bind the tool to the calling session's routed request: refuse other refs as `attachment_not_in_request` before the table is touched (bu-jv1moa.59).

## 2. Verification
- [x] 2.1 Real-database test: request, claim, fetch, idempotence, replay, owner scoping, failure and retry, declared-size refusal.
- [x] 2.2 Prompt test over both route paths.
- [x] 2.3 Inventory contract: cataloged with D4 metadata on Switchboard only, not listed.
- [x] 2.4 Unit test: a routed ref proceeds; another message's or attachment's ref, an unbound locator, no session or no routed attachments are refused without a query.
