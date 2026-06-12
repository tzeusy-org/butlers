# Contact-Info Legacy Backfill Report

**Generated:** 2026-06-03T14:44:35Z
**Mode:** DRY-RUN
**Script:** `src/butlers/scripts/backfill_contact_info_triples.py`
**Beads:** bu-q8rro (non-secured path), bu-krbqx (secured path)

---

## Summary — non-secured path (→ relationship.entity_facts)

| Outcome | Count |
|---------|-------|
| Gap rows asserted (would-assert) | 4 |
| Already present in entity_facts (idempotent) | 866 |
| Skipped — null entity_id (orphan contact) | 0 |
| Skipped — unmapped type | 2 |
| Skipped — empty/whitespace value | 0 |
| Errors | 0 |

---

## Summary — secured path (→ public.entity_info)

| Outcome | Count |
|---------|-------|
| Inserted (would-assert) | 0 |
| Already present in entity_info (conflict no-op) | 0 |
| Skipped — null entity_id (orphan contact) | 0 |
| Errors | 0 |

| **Total contact_info rows examined** | **872** |

---

## Asserted by predicate (would-assert)

| Predicate | Count |
| --- | --- |
| `has-email` | 2 |
| `has-handle` | 1 |
| `has-phone` | 1 |

---

## Secured inserts by type (would-assert)

_No secured rows inserted._

---

## Skipped unmapped types

| Type | Count |
| --- | --- |
| `google_health` | 2 |

---

## Notes

- **Secured rows** (`secured=true`): backfilled into `public.entity_info` per
  RFC 0004 Amendment 2 (bu-krbqx).  Rows with `entity_id IS NULL` are skipped
  (counted as `secured_skipped_null_entity`) — run `contact_orphan_resolver.py`
  first to mint entities for those contacts, then re-run this script.
- **Null entity_id rows** (non-secured): 0 — contact has no
  linked entity; run `contact_orphan_resolver.py` first, then re-run.
- **Unmapped types**: types such as `telegram_chat_id`, `google_health`,
  `home_assistant_url` have no registered has-* predicate and are intentionally
  not backfilled.  `telegram_user_id` and `telegram_username` ARE mapped
  (both → `has-handle`) since bead bu-55ggu.
- **Idempotency**: re-running in --apply mode is safe.  Non-secured triples
  already in `relationship.entity_facts` are counted as "already present".
  Secured rows already in `public.entity_info` trigger ON CONFLICT DO UPDATE
  (value preserved) and are counted as "already present in entity_info".
