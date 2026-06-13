# Design — Entity-Keyed Preferred Channel

## D1. Predicate shape

`prefers-channel` is a contact-class predicate on `relationship.entity_facts`:

| field | value |
|---|---|
| `subject` | entity UUID (the contact) |
| `predicate` | `prefers-channel` |
| `object` | channel name literal (`'telegram'`, `'email'`, `'discord'`, …) |
| `object_kind` | `'literal'` |
| `src` | authoring butler slug (`'relationship'` for dashboard edits) |
| `verified` | `true` when owner-set via dashboard |

Registry seed: `cardinality = single` (mirrors the existing `has-birthday` /
`dunbar_tier_override` single-valued treatment). Single-valued is enforced by
supersession, not a new unique index: asserting `prefers-channel` marks any prior
active `prefers-channel` triple for the same subject `validity='superseded'`.
Clearing the preference retracts the active triple (`validity='retracted'`).

**Why a literal, not an `object_kind='entity'`:** the object is a channel name,
not another entity. This matches how `has-handle` stores `telegram:<id>` literals.

## D2. Write-time validation

A `prefers-channel` assertion is rejected unless the subject already has a
contact fact for that channel family:

- `telegram` / `discord` / other handle channels → an active `has-handle` fact
  whose value is namespaced to that channel (`telegram:…`, `discord:…`).
- `email` → an active `has-email` fact.
- `phone` / `sms` → an active `has-phone` fact.

This makes "prefer a channel you can't be reached on" unrepresentable
([Inferred] best-effort; the validator is advisory if the handle taxonomy lacks
a clean channel prefix — record as a follow-up rather than blocking the write).

## D3. Outbound resolution (the load-bearing part)

New helper, e.g. `resolve_outbound_channel(contact_id) -> channel | None`,
consulted by the `notify()` path **only when the caller did not force a
`channel`** (today `channel` is a required arg — see Open Question OQ1):

```
1. entity_id ← public.contacts.entity_id for contact_id   (None → fall through)
2. pref ← active prefers-channel fact for entity_id
3. if pref AND pref ∈ DELIVERABLE_CHANNELS (today {telegram, email}):
        return pref
4. else fall back to today's precedence
        (has-handle:telegram → has-email), unchanged
```

A stored preference for a non-deliverable channel (`discord`) is skipped at
step 3 — no error, no delivery on that channel — until `core-notify`'s Channel
Validation list grows. This keeps "preference can name it / delivery can't reach
it yet" honest.

## D4. Migration

1. Additive: seed `prefers-channel` into the predicate registry.
2. Data backfill: for each `public.contacts` row with non-null
   `preferred_channel` and a resolvable `entity_id`, assert a `prefers-channel`
   fact (`src='migration'`, `verified=true`). Rows without `entity_id` are
   logged and skipped (the value was already unreachable by any entity-keyed
   reader).
3. Cut the dashboard write path over to fact-assert.
4. Remove `patch_contact` preferred-channel handling, then the endpoint + hook.
5. `DROP COLUMN public.contacts.preferred_channel`.

Single-user deployment → no transition window; steps 1–5 may land together,
gated on backfill parity (count of migrated facts == count of non-null,
entity-resolvable column values).

## Open questions

- **OQ1 — surfacing to the agent vs. resolving in the tool.** `notify(channel=…)`
  is currently a *required* param chosen by the calling LLM instance. Two ways to
  make the preference load-bearing: (a) make `channel` optional and resolve it in
  the tool when omitted (D3 as written); or (b) keep `channel` required but inject
  the resolved preference into the contact context the agent sees, so the agent
  picks it. (a) is deterministic and testable; (b) is softer but avoids changing
  the tool contract. Recommend (a). Decide at Gate 5 sign-off.
- **OQ2 — channel-prefix taxonomy for validation (D2).** Confirm `has-handle`
  values are reliably channel-prefixed (`telegram:`, `discord:`) so validation can
  key off them; if not, validation degrades to "entity has *any* handle." Verify
  against the handle-writing connectors before implementation.
