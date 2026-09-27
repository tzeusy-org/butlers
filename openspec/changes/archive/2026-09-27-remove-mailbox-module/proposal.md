## Why

The mailbox module (a per-butler `mailbox` message queue with five `mailbox_*`
MCP tools) was never enabled by any roster butler. Its only caller, the
Switchboard `post_mail` tool, therefore always failed with "mailbox module not
enabled", including the Chronicler's recurring-companion enrichment proposals to
the relationship butler. Keeping it costs maintenance and misleads readers.

## What Changes

- Remove the `module-mailbox` capability, the module code, its migration chain,
  and its tests.
- Remove the Switchboard `post_mail` tool, which existed only to call
  `mailbox_post`.
- Stop wiring the Chronicler's `post_mail`-based enrichment proposer; the
  injectable `propose_enrichment_fn` seam stays (the Chronicler spec's enrichment
  proposal is a MAY).
- Add core migration `core_249`, which drops any leftover per-butler `mailbox`
  table with `IF EXISTS`.
- Drop mailbox mentions from docs and from the docs tree and routing specs.

## Capabilities

### Modified Capabilities

- `docs-information-architecture`: the suggested docs tree no longer lists `mailbox.md`.
- `cross-butler-delegation`: the routing requirement no longer names `post_mail`.

### Removed Capabilities

- `module-mailbox`

## Impact

Removes five module tools and one Switchboard tool that could never succeed.
One forward, idempotent core migration. No butler config changes.
