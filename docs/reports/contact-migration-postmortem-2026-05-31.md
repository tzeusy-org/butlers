# Contact → Triples Migration — Post-Cut-Over Verification Report

- **Bead:** bu-hpv4u (migration bead 9; parent epic bu-uhjxr)
- **Date:** 2026-05-31
- **Author:** Beads Worker (branch `agent/bu-hpv4u`)
- **Spec anchor:** Brief §6b Amendment 1.1.A.6 + Amendment 1.1.C bead 9
- **Gates:** sign-off for **bu-e2ja9** (`DROP TABLE public.contact_info`, migration bead 10)
- **Data source:** live **dev** DB via `./.claude/skills/butler-dev-debug/scripts/dev-psql.sh`
  (SELECT-only; no data modified, no backfill `--apply` run, nothing dropped)

> **Time-gate waiver (IMPORTANT).** The bead text nominally requires a 30-day
> "triple-store-only" soak before sign-off. The **owner has explicitly waived the
> 30-day calendar gate** in favor of verification-based sign-off. The write-path
> cut-over (bu-k9ylx, PR #2021) merged **2026-05-30**, not 30 days ago. This report
> therefore does **not** block on elapsed time; the GO/NO-GO recommendation rests
> entirely on whether the data in `public.contact_info` is **provably safe to drop**.

## Schema facts used by this audit (verified against the live DB)

- `public.contact_info(id, contact_id, type, value, secured, …)`. `value` holds
  the channel identifier; UNIQUE on `(type, value)`.
- `relationship.entity_facts(subject, predicate, object, validity, …)`. `subject`
  is the **entity UUID** (FK → `relationship.entities`), `object` is the literal
  channel value, `validity ∈ {active, retracted, superseded}`. (This table has no
  `entity_id`/`value`/`status` column — the parity join is
  `subject = contacts.entity_id`, `object = contact_info.value`,
  `validity = 'active'`.)
- Authoritative channel-type → predicate map, taken verbatim from
  `src/butlers/identity.py` `_CHANNEL_TYPE_TO_PREDICATE` (kept in sync with the
  central writer's `_CI_TYPE_TO_PREDICATE` and the backfill script):

  | `contact_info.type` | predicate |
  |---|---|
  | `email` | `has-email` |
  | `phone` | `has-phone` |
  | `website` | `has-website` |
  | `telegram`, `telegram_user_id`, `telegram_user_client`, `telegram_username` | `has-handle` |
  | `linkedin`, `twitter`, `other`, `whatsapp_jid` | `has-handle` |
  | `google_health` | **— (unmapped: routing/credential identifier, no triple)** |

---

## 1. Cumulative triple count

Active `has-*` channel facts now in `relationship.entity_facts`:

| Predicate | validity | count |
|---|---|---|
| `has-email` | active | 41 |
| `has-phone` | active | 321 |
| `has-website` | active | 6 |
| `has-handle` | active | 1 |
| **Total active `has-*`** | | **369** |

> **Read this number in context.** Only **1** `has-handle` triple exists, yet the
> dev DB holds **~495 telegram-family rows** in `contact_info`. The triple store
> has *not* yet absorbed the legacy telegram channels — see §2.

---

## 2. Migration completeness / parity (the critical check)

For every **non-secured** `public.contact_info` row whose contact has a non-null
`entity_id` and a **mapped** type, does a matching **active** triple
(`subject = entity_id` AND `predicate` AND `object = value` AND `validity='active'`)
exist?

```
mapped + entity-linked rows | covered | gap (no active triple)
----------------------------+---------+------------------------
                        867 |     369 |                    498
```

**498 of 867 mapped+linked rows have NO corresponding active triple.** These would
be silently lost by a naïve drop. The gap is dominated by the telegram family
(the backfill that would create their triples has not been run):

| type (gap) | rows |
|---|---|
| `telegram_user_id` | 270 |
| `telegram_username` | 224 |
| `email` | 2 |
| `phone` | 1 |
| `other` | 1 |
| **Total gap** | **498** |

### Full mutually-exclusive categorization (every row counted once)

Total rows in `public.contact_info`: **872** (verified `secured` rows: **0**).

| Category | rows | In triple store? | Lost on naïve drop? |
|---|---|---|---|
| **B. Covered** — mapped, linked, active triple exists | 369 | Yes | No |
| **E. GAP** — mapped, linked, no active triple | 498 | No (recoverable via backfill) | **Yes** |
| **C. Orphan** — null `entity_id` (`phone` ×1, `telegram_user_id` ×1, `telegram_username` ×1) | 3 | No (no entity to attach to) | **Yes** |
| **D. Unmapped type, entity-linked** (`google_health` ×2) | 2 | No (no predicate) | **Yes** |
| **A. Secured** | 0 | n/a | n/a in dev (see §6 prod caveat) |
| **Total** | **872** | | |

Cross-check: 369 + 498 + 3 + 2 + 0 = **872** ✓ (orphan is checked before the
mapped/triple test, since a null-entity row can never carry a triple).

Covered-by-type (the 369): `phone` 321, `email` 41, `website` 6, `other` 1.

---

## 3. Dropped/skipped row count — what `DROP TABLE` would destroy that is NOT represented elsewhere

A naïve `DROP TABLE public.contact_info` today would **destroy 503 rows not
represented anywhere else**:

| Bucket | rows | Detail | Recoverable? |
|---|---|---|---|
| Mapped/linked gap | 498 | mostly telegram (`telegram_user_id` 270, `telegram_username` 224) + `email` 2, `phone` 1, `other` 1 — **backfill not yet applied** | **Yes** — run backfill `--apply` |
| Orphan (null entity) | 3 | `phone` 1, `telegram_user_id` 1, `telegram_username` 1 | No — no entity to attach to |
| Unmapped-type, linked | 2 | `google_health` 2 | No — no triple representation |
| **Total at-risk** | **503** | | |

Only the **369 covered rows** are provably safe to drop. There are **0 secured
rows in the dev DB**, so the credential carve-out has no at-risk rows *here* (see
the production caveat in §6).

> **Headline:** the table is **NOT** in a "triple-store-only" steady state. The
> overwhelming majority of telegram channels still live only in `contact_info`
> because the legacy backfill has not been applied. A drop now would be massively
> destructive.

---

## 4. Incidents during the migration

(From bead history and merged PRs #2021–#2030.)

| PR | Type | Summary |
|---|---|---|
| #2021 | cut-over | Write-path cut-over: removed dual-write shims + `BUTLERS_CONTACT_INFO_DUAL_WRITE` flag; `public.contact_info` made read-only (SELECT retained; INSERT/UPDATE/DELETE revoked via reversible Alembic `core_110`). Merged 2026-05-30. |
| #2022 | fix | Repaired `test_entity_facts_repoint_on_merge.py` — it patched `retract_all_contact_info_facts`, which #2021 deleted; left backend CI red. |
| #2023 | feat | Wired frontend entity-contact triple API + flagged `ContactChannelCard` COMPAT blockers. |
| **#2024** | **prod regression** | After #2021 write-blocked `contact_info`, the dashboard Edit/Delete affordances called `patchContactInfo`/`deleteContactInfo` → **HTTP 409**; every edit/delete failed silently. Stop-the-bleeding hotfix hid the broken buttons. |
| #2025 | feat | `GET /entities/{id}/linked-contacts` now reads from **both** `contact_info` and `entity_facts` has-* triples, de-duped on `(type, value)`. |
| #2026 | feat | Migrated `ContactChannelCard` to entity-keyed mutations; restored edit/delete properly. |
| #2027 | feat | `retract_contact_info_fact()` + wired retraction into `DELETE /contacts/{id}` so triples retract on contact delete. |
| **#2028** | **atomicity fix (caught in review)** | Added `PUT /entities/{id}/contacts/{predicate}/{value_hash}` doing retract-old + assert-new **in a single DB transaction**. The naïve non-atomic retract-then-assert would have left a data-loss window on a crash between the two writes; corrected before merge. |
| #2029 | feat | Migrated `interaction_log()`/`interaction_list()` subjects from `contact:{id}` to `entity:{id}`. |
| #2030 | feat | Added `src/butlers/scripts/backfill_contact_info_triples.py` (dry-run-first) + 31 unit tests. |

Two notable incidents:
1. **Prod regression (#2024):** HTTP 409 on contact channel edit/delete, root-caused
   to dashboard mutation handlers not being repointed off the now-write-blocked
   `contact_info`. Fixed forward (#2024 hotfix → #2025/#2026 proper rewire).
2. **Atomicity data-loss window (#2028):** the edit-in-place path was made
   transactional (retract+assert atomic); caught in review before reaching prod.

---

## 5. Backfill status

- `src/butlers/scripts/backfill_contact_info_triples.py` exists (PR #2030).
- Dry-run-first; `--apply` required to write; idempotent (per-row active-triple
  gap check + `relationship_assert_fact` ON CONFLICT supersession).
- Skip rules (from the script header): `secured=true`, `entity_id IS NULL` (orphan),
  unmapped `type`, already-present.
- **It has NOT been run in `--apply` mode.** The PR's dry-run mentioned "~4 gap
  rows", but the live dev DB shows the true gap is **498 rows** — overwhelmingly
  the legacy telegram channels that were never dual-written. (The "~4" figure in
  the PR likely reflected a much smaller fixture/dataset; the production-shaped dev
  DB tells a very different story.)

**Must the gaps be backfilled before the drop?** **Yes — categorically.** 498
mapped, entity-linked, non-secured rows have a valid triple representation but no
triple. Dropping without backfilling = ~498 rows of silent data loss, including
the bulk of the telegram channel graph. Run
`backfill_contact_info_triples.py --apply`, then re-run the §2 parity query and
confirm `gap = 0` before any drop.

---

## 6. Sign-off — GO / NO-GO for bu-e2ja9 (drop)

**Recommendation: NO-GO — do NOT drop `public.contact_info` (as of 2026-05-31).**

This is an emphatic NO-GO, not a borderline one. A naïve `DROP TABLE
public.contact_info` today would destroy **503 rows** not represented elsewhere —
about 58% of the table — including nearly the entire telegram channel set. The
table is **not** in a triple-store-only steady state.

### Blocking conditions

1. **[HARD BLOCKER] 498 mapped/linked gap rows.**
   The legacy backfill (`backfill_contact_info_triples.py --apply`) has **not been
   run**. Until it is, the triple store does not contain the bulk of the channel
   data (especially telegram). **Run the backfill, then re-verify §2 shows
   `gap = 0`.** This single step moves ~498 rows from "would be lost" to "covered".

2. **[DECISION REQUIRED] 3 orphan rows (null `entity_id`).**
   `phone` ×1, `telegram_user_id` ×1, `telegram_username` ×1. No entity to attach a
   triple to. Either (a) resolve them to an entity and backfill, or (b) the owner
   explicitly **accepts them as dropped** with rationale.

3. **[DECISION REQUIRED] 2 unmapped-type rows (`google_health` ×2).**
   `google_health` has **no predicate** in the triple model (it is a
   routing/credential identifier, per `src/butlers/api/routers/oauth.py`). Either
   (a) migrate it via the connector/credential path, or (b) the owner explicitly
   **accepts these 2 as dropped** with rationale.

4. **[BLOCKER — environment-conditional] Secured credential rows.**
   The **dev** DB has **0** secured rows, so there is no at-risk credential data
   here. But `public.contact_info` cannot be *fully* dropped in any environment
   until the secured-credential carve-out lands: **bu-pl8fy** (secured-row
   migration to `public.entity_info` per RFC 0004 Amendment 2) and **bu-fa5ex**
   (secured reveal endpoint) are **both OPEN**. **Before dropping in any
   environment, re-run the §2 categorization there** and confirm secured = 0, or
   that bu-pl8fy/bu-fa5ex have relocated them. Otherwise the drop must be a
   credential-only carve-out per tasks.md §10.4.

### Path to GO

bu-e2ja9 may proceed **only when ALL** of the following hold:

- backfill `--apply` run → §2 re-verification shows `gap = 0`;
- the 3 orphan rows and 2 `google_health` rows are either migrated/backfilled or
  have a recorded owner **accept-as-dropped** decision;
- in the target environment, secured rows = 0 **or** bu-pl8fy + bu-fa5ex have
  relocated them (else drop only the channel-identity portion);
- pre-migration snapshots (bead 1) retained ≥ 90 days from the drop date
  (Amendment 1.1.A.6);
- a final reconciliation (rows-in vs rows-dropped) recorded at drop time.

Re-run the §2 parity query and §3 at-risk enumeration **immediately before** the
drop in the actual target environment and confirm the at-risk total is 0 (or fully
covered by recorded accept-as-dropped decisions).

### Summary table

| Condition | Status (dev) | Verdict |
|---|---|---|
| Covered rows safe | 369 / 872 | partial |
| Gap rows backfilled | 0 / 498 applied | **HARD BLOCK** |
| Orphan rows handled | 3 undecided | DECISION |
| Unmapped (`google_health`) handled | 2 undecided | DECISION |
| Secured rows migrated (bu-pl8fy/bu-fa5ex) | 0 in dev; beads OPEN | **BLOCK (any env w/ secured rows)** |
| **Overall** | | **NO-GO** |

---

*This report is a recommendation to the coordinator/owner. The worker does not
execute the drop. All numbers were read from the live dev DB on 2026-05-31 via
read-only SELECT queries. Numbers for any other environment (e.g. production) must
be re-verified there before the drop.*
