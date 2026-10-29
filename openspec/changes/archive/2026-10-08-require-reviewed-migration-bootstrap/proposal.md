# Require reviewed bootstrap before online migrations

## Why
The owner selected reviewed bootstrap before the first supported online migration and when its managed surface changes. The existing protected guards reject late; the two online entrypoints can create extensions or target schemas first.

## What Changes
- Add a shared read-only, actual-connection prerequisite check before online side effects.
- Accept source-derived staged and legitimately finalized catalog states, retaining independent protected migration guards.
- Preserve direct runtime fallback, with the approved online-startup qualification and governing DND exceptions.
- Document separate bootstrap and normal migration identities, historical repair, repeat and managed-surface procedures.

## Impact
Affected capabilities: database-security and deployment-hardening. No new migration, grant, executor enablement, completion marker, live repair or protected guard rewrite. Real PostgreSQL controls remain mandatory; source/software receipts alone do not establish them.
