# Tasks

## 1. Contract
- [x] 1.1 Add `dashboard-audit-log` requirement **Credential-Target Audit Free
      Text Is Withheld On Read**, stating the decision, its credential-namespace
      scope, and that it narrows the `Audit Log Read API` projection clause.
- [x] 1.2 Place it as an ADDED requirement so it cannot clobber
      `close-audit-failure-spine`'s unarchived MODIFIED block on
      `Audit Log Read API`.

## 2. Enforcement
- [x] 2.1 Add `is_credential_target()` covering `u:`/`s:`/`c:` and the
      long-scope spellings `user:`/`system:`/`cli:`.
- [x] 2.2 Withhold `note`/`error`/`metadata` on the `AuditLogEntry` model
      itself, so all three readers and any future one are covered.
- [x] 2.3 Publish `redacted: bool`, true only when text was actually withheld.

## 3. Tests
- [x] 3.1 Absence-sentinel regression on `GET /api/audit-log`.
- [x] 3.2 Absence-sentinel regression on `GET /api/audit-log/{id}`.
- [x] 3.3 Pin every accepted credential-key spelling.
- [x] 3.4 Pin the model chokepoint against direct construction.
- [x] 3.5 Pin that non-credential rows keep their free text (no blanket gag).
- [x] 3.6 Pin that `redacted` is false when nothing was withheld.

## 4. Frontend
- [x] 4.1 Add `redacted?: boolean` to the `AuditLogEntry` type.
- [x] 4.2 Render a withheld notice in the expanded row, and keep the
      Audit → Issues door mounted for a withheld credential failure.
