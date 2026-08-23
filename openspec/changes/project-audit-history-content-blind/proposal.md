# Project the audit-history endpoint content-blind

## Why

Two requirements in `dashboard-api` gave opposite rules for the same free text.
`Secrets Inventory and Per-Credential Read Endpoints` says the payload SHALL NOT
contain an audit note — explicitly including audit rows written by producers
outside the secrets router, because the projection is enforced on read. But
`Secrets Audit-History and Breaks-Catalogue Endpoints` says each `AuditEvent`
includes `note` as a "verbatim stored note". `GET /api/secrets/audit/<scope>/<key>`
implemented the second one, so it published the note straight off the column.

That is not a theoretical gap. A failed probe writes the provider's own text into
that column: `_write_credential_audit` persists `"Probe failed: <provider text>;
probe_status=<token>"`, and the system probe does the same. bu-nz4sn made the
probe endpoints content-blind by publishing a `PROBE_FAILURE_VOCABULARY` category
instead of the failure tail — but the identical text stayed reachable one endpoint
over, so that fix narrowed the leak rather than closing it. The contradiction is
what hid it: the code matched a requirement, and the model docstring said the
passthrough was intentional.

## What Changes

- **The contradiction is reconciled toward content-blindness.** The audit-history
  endpoint is brought under the same rule the inventory and per-credential read
  endpoints already carry: no audit note on the wire. The other direction —
  keeping the note published and relaxing the inventory rule — was rejected
  because it would require an allowlist over text written by producers outside
  this router, which is exactly the enforcement point the sibling requirement
  says cannot hold.
- `AuditEvent` loses its `note` field, and `get_audit_history` stops selecting
  the `note` column at all, so there is no in-process value to reintroduce by
  accident. `ts`, `actor`, and `action` remain; `action` is the machine-readable
  verb and already carries what the note was being read for.
- Following the sibling change's precedent, the field is **dropped rather than
  published as an always-null placeholder**: a field with no authoritative source
  should be absent, not present-and-empty.

Server-side evidence is untouched. The writers still persist the note, and the
free text still reaches `public.audit_log`, `public.secret_probe_log`, and the
`last_test_message` cache. Content blindness is about the wire, not about
destroying operator forensics — an operator reads the diagnostic at the database,
which is where a raw provider string belongs.

No frontend behaviour changes: `getCredentialAudit` has no component caller, and
the passport already pins its audit `note` to `""` because the detail endpoints
dropped it (bu-iph56).

## Baseline overwrite

This change's MODIFIED block deletes one live baseline clause, and that deletion
is the point of the change rather than a side effect of rebuilding the block on
a stale ancestor. The clause is:

> - **AND** each `AuditEvent` includes `ts` (server pre-formatted relative
>   timestamp), `actor`, `action`, `note` (serif-italic; verbatim stored note,
>   never LLM-generated)

It is the exact sentence that authorised the leak, so carrying it forward would
contradict the block that replaces it. Every other clause and scenario heading
in the requirement is reproduced verbatim.

`scripts/check_spec_overwrites.py` flagged it (exit 1) and it is now frozen in
`scripts/spec-overwrite-baseline.json` under
`project-audit-history-content-blind/dashboard-api/Secrets Audit-History and
Breaks-Catalogue Endpoints`, digest `95aaf609a86d`. The freeze diff added
exactly one entry — no unrelated in-flight loss was swept in with it.

No other unarchived change holds a block on this requirement:

```
rg -l '^### Requirement: Secrets Audit-History and Breaks-Catalogue Endpoints$' \
  openspec/changes/*/specs/*/spec.md
```

matches only this change. The neighbouring
`project-secret-read-endpoints-content-blind` targets a *different* requirement
(`Secrets Inventory and Per-Credential Read Endpoints`), so the two cannot
clobber each other at archive time.

## Impact

- Affected specs: `dashboard-api`
- Affected code: `src/butlers/api/routers/secrets_v2.py`,
  `tests/api/test_secrets_v2_audit_history.py`, `frontend/src/api/types.ts`
- Breaking for any client reading `note` off
  `GET /api/secrets/audit/<scope>/<key>`. The frontend type is updated in the
  same change; no other consumer exists in-repo.
- `POST /api/secrets/system/<key>` still returns the unprojected record
  (bu-m9s61) and remains out of scope here.
- The same audit row's `note` and `error` are also published verbatim by the
  general operator audit log (`GET /api/audit`, `GET /api/audit/<id>` in
  `src/butlers/api/routers/audit.py`), which this endpoint's own
  `meta.deep_link` points at. That is a different router under a different
  requirement, and a general audit log has a much stronger forensic claim on
  its free text than a credential passport does — so it is deliberately not
  reconciled here. Filed separately rather than resolved by reflex.
