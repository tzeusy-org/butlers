# Tasks

## 1. Contract
- [x] 1.1 Add the `dashboard-audit-log` requirement **Credential-Target Audit
      Group Identity Includes The Persisted Failure Category**, stating that
      identity is the credential *and* its cause, that the cause is a persisted
      vocabulary member, and that it is never derived at read time.
- [x] 1.2 Place it as an ADDED requirement: the requirement it revises lives
      only in `project-issue-group-identity-credential-blind`'s own unarchived
      ADDED block, so a MODIFIED header has no baseline to match. State the
      supersession in prose, naming the requirement and the scenario.
- [x] 1.3 Record the two demanded decisions — what non-failure producers
      persist, and how pre-existing rows group — in the proposal, with the
      reason backfilling is refused.

## 2. Schema
- [x] 2.1 `core_202`: add `public.audit_log.failure_category TEXT`, nullable, no
      default, not backfilled.
- [x] 2.2 Add the `audit_log_failure_category_vocabulary` CHECK constraint over
      the eight `PROBE_FAILURE_VOCABULARY` members, so a writer that bypasses
      `audit.append()` still cannot store a raw token.
- [x] 2.3 Inline the vocabulary in the migration (a migration is a frozen
      snapshot) and pin it to the live tuple with a test rather than an import.

## 3. Write path
- [x] 3.1 Move `PROBE_FAILURE_VOCABULARY` to `api/models/audit.py` and add
      `clamp_failure_category`; re-export from `routers/secrets_v2` so existing
      readers and `bind-secret-mutation-content-blindness` stay true.
- [x] 3.2 `audit.append()` accepts `failure_category`, clamps it, and INSERTs
      it. The clamp's warning names only the `action`, never the rejected
      value, so the log does not become a new home for free text.
- [x] 3.3 `_write_credential_audit` / `_write_system_audit` / `_write_cli_audit`
      accept and forward it; both probe endpoints pass the category they had
      already derived for `TestResult.message`.
- [x] 3.4 `_emit_oauth_audit` accepts and forwards it; all seven
      `action="failed"` OAuth sites pass a literal vocabulary member.

## 4. Read path
- [x] 4.1 The shared `normalized_errors` CTE selects `failure_category` and
      appends `COALESCE(' [' || failure_category || ']', '')` to the credential
      title, so uncategorised rows keep the byte-identical legacy string.
- [x] 4.2 Leave `_OCCURRENCES_SELECT` and `AuditLogEntry` untouched: the wire
      projection does not widen.

## 5. Tests
- [x] 5.1 Static enumeration over all five writer helpers and every call site:
      any site whose `action` can be `"failed"` passes `failure_category`, and
      no success-only site does.
- [x] 5.2 Static: every OAuth literal is a vocabulary member; every helper
      forwards its parameter through to `append`.
- [x] 5.3 DB-free: the credential title reads `failure_category`, guards it with
      `COALESCE`, reads no withheld column, and does not reach the outer
      occurrences SELECT.
- [x] 5.4 DB: one credential with two causes is two groups with two
      `issue_key`s; one credential with three occurrences of one cause is one
      group of three.
- [x] 5.5 DB: an uncategorised row's title is byte-identical to the pre-change
      string, and a categorised row does not join its credential's legacy group.
- [x] 5.6 DB: the CHECK constraint's allowed set equals the live
      `PROBE_FAILURE_VOCABULARY`; a raw token is refused by the database; a
      non-member handed to `append()` is clamped to `other` and the row lives.
- [x] 5.7 DB: the absence sentinel over `GET /api/issues` covers **both** title
      shapes, categorised and uncategorised.
- [x] 5.8 Mutation-prove the absence and grouping assertions are not vacuous.
