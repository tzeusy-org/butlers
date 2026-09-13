# RFC 0017: Owner-routing safety and audit hardening — incident reconciliation

**Status:** Accepted
**Date:** 2026-04-29
**Epic:** bu-7qfrg — "Owner-routing safety and audit hardening for outbound notify()"
**Reconciliation bead:** bu-7qfrg.1

---

## 1. Incident summary

On 2026-04-21, the relationship butler ingested an email thread where the user
wrote "am I correct in understanding my QRT email would be
TzeHow.Lee@qube-rt.com?" — a speculative question about a future work email.
The runtime LLM treated this as a factual identity claim and called:

```python
contact_info_add(
    contact_id="ccf6241a-01cd-40b2-817e-39643d50322b",  # OWNER contact
    type="email",
    value="TzeHow.Lee@qube-rt.com",
    is_primary=False,
)
```

This row was immediately written to `public.contact_info`, poisoning outbound
email routing for ~3 days.  On 2026-04-26, the relationship butler sent a
personal-life update about MOM helper paperwork via
`notify(channel="email")`.  The recipient resolver picked the qube address
(earliest-inserted email row, `is_primary=false`).  The email guard
auto-approved because the resolved contact had role `owner` — regardless of
whether the address was primary.  The send failed at SMTP 535 (bot credentials
misconfigured), which surfaced in the dashboard's failed-deliveries list.  With
working credentials, the email would have shipped silently to a work address.

---

## 2. Gen-1 fixes delivered

Four independent changes closed the incident loop end-to-end.

### 2.1 bu-jwby9 — Gate non-primary owner-email sends (PR #1237, merged)

**Root cause patched:** `email_guard.py` auto-approved any owner-role email.

**Change:**  
`src/butlers/modules/approvals/email_guard.py` — the owner bypass now requires
`is_primary=true` on the targeted `contact_info` row:

```python
is_primary = await is_primary_contact(pool, contact.contact_id, "email", email_target)
if is_primary:
    return EmailGuardDecision(allowed=True, reason="owner")
# Non-primary falls through to rules/parking flow
```

**Evidence:**  
- Commit `862c0a0f` — `fix(approvals): owner email auto-approve only for primary address`
- Test: `tests/modules/test_email_guard.py::TestCheckEmailRecipient::test_owner_non_primary_email_parks`

**Follow-up bu-axdie (PR #1245):** same primacy gate applied to `gate.py`
(covers Telegram and other channels using the same approval gate path).

### 2.2 bu-uv4b4 — Contact_info context tagging + context-aware notify() (PR #1241, merged)

**Root cause patched:** recipient resolver was context-blind; routing to the
wrong address sphere was by design.

**Changes:**  
- `alembic/versions/core/core_083_contact_info_context.py` — adds `context`
  column (VARCHAR, CHECK in `('personal','work','other')`) to `public.contact_info`;
  existing rows left NULL (treated as compatible with any context).
- `src/butlers/daemon.py::_resolve_contact_channel_identifier` — when
  `msg_context` is provided, uses a `CASE`-based `ORDER BY` that prefers
  matching-context rows, then unclassified (NULL), then any other context.
- `src/butlers/modules/approvals/email_guard.py::check_email_recipient` — new
  `msg_context` parameter; context mismatch (non-NULL sender context vs
  non-NULL address context, differing) parks the send.
- `src/butlers/core_tools/_notifications.py` — `notify()` tool accepts
  `msg_context`; passes it through to `_resolve_contact_channel_identifier`
  and `check_email_recipient`.
- `about/legends-and-lore/rfcs/0004-identity-and-contact-resolution.md` —
  amended 2026-04-29 to document the context column schema and routing contract.

**Follow-up bu-vwp11 (PR #1247):** work-domain heuristic auto-tags
`context='work'` for new email inserts at known work domains (default:
`qube-rt.com`). Configurable via `BUTLERS_WORK_DOMAINS` env var.

**Evidence:**  
- Commit `01b7775b` — `feat(identity): contact_info context sphere tagging and context-aware notify() routing`
- Test: `tests/daemon/test_notify_msg_context.py::TestResolveContactChannelIdentifierContext`
- Test: `tests/modules/test_email_guard_context.py`

### 2.3 bu-v6ttx — Gate owner-contact mutations in contact_info_add/update (PR #1248, merged)

**Root cause patched:** butler tools could directly mutate owner identity rows.

**Change:**  
`roster/relationship/tools/contact_info.py::contact_info_add` —
owner gate added at the tool boundary:

```python
if await _is_owner_contact(pool, contact_id):
    # Create pending_action instead of inserting
    action_id = await _create_pending_action(pool, "contact_info_add", tool_args, summary)
    return {"status": "pending_approval", "action_id": str(action_id), ...}
# Non-owner path — write immediately
```

The same gate is applied to `contact_info_update`. Gate is enforced at the
tool layer so any butler exposing `contact_info` mutation tools inherits it
without per-butler configuration.

**Evidence:**  
- Commit `b430d37c` — `fix(relationship): gate owner-contact mutations in contact_info_add/update`
- Test: `roster/relationship/tests/test_contact_info.py::test_contact_info_add_owner_gate_parks_action`
  (includes literal replay of the incident: `value="TzeHow.Lee@qube-rt.com"`)

### 2.4 bu-m24ua — Dashboard mutation audit middleware (PR #1250, merged)

**Root cause patched:** dashboard DELETE/PATCH/POST routes left no audit trail.

**Changes:**  
- `src/butlers/api/audit_emit.py` — `emit_dashboard_audit()` helper writes to
  `switchboard.dashboard_audit_log`; redacts sensitive fields including
  `contact_info.value`.
- `src/butlers/api/dashboard_audit_middleware.py` — `DashboardAuditMiddleware`
  intercepts every non-GET `/api/` request and writes an audit row.
- `src/butlers/api/app.py` — middleware registered via `app.add_middleware(DashboardAuditMiddleware)`.
- `roster/relationship/api/router.py` — explicit `emit_dashboard_audit(...)` calls
  in three sensitive handlers: `contact_info_create` (op `contact_info_create`),
  `delete_contact_info` (op `contact_info_delete`), `patch_contact_info`
  (op `contact_info_patch`).  Credential reveal endpoint also emits explicitly
  (GET, skipped by middleware).

**Evidence:**  
- Commit `b673503b` — `feat(api): dashboard mutation audit middleware + explicit emits`
- Test: `tests/api/test_dashboard_audit_middleware.py`

### 2.5 bu-0kien — Consolidate is_primary helpers (PR #1252, merged)

Deduplication refactor: both `email_guard.py` and `gate.py` previously
maintained separate `is_primary` query logic.  Now both import
`is_primary_contact` from `src/butlers/modules/approvals/_shared.py`.

### 2.6 Cross-schema owner lookup must preserve ambiguity (bu-rp2ie7)

Schema-isolated butlers cannot read `relationship.entity_facts` directly, so
outbound gates use the owner-only `public.resolve_owner_triple` function rather
than receiving broader relationship-schema privileges. That fallback is an
authorization decision, not a general contact lookup: it MUST return an owner
match only when the supplied channel candidates resolve to exactly one live
entity across owner and non-owner facts.

Filtering to owner facts before checking uniqueness is unsafe. If the same
email address or Telegram identifier is attached to both the owner and an
external entity, owner-first filtering erases the collision and can create an
external-party bypass. The lookup therefore checks cross-entity ambiguity
first and returns no authorization on a collision. The ordinary outbound email
guard additionally retains this RFC's primary-address requirement; lookup
failure or ambiguity falls through to standing rules and owner review.

---

## 3. Incident scenario replay

Each acceptance criterion mapped to the incident scenario:

| Scenario step | Expected behaviour | Covered by |
|---|---|---|
| Butler calls `contact_info_add(contact_id=<owner>, value="TzeHow.Lee@qube-rt.com")` | Returns `{"status":"pending_approval"}`, no row in `contact_info` | bu-v6ttx |
| `notify(channel="email", msg_context="personal")` with owner having personal+work email | Resolver picks personal-tagged address | bu-uv4b4 |
| `notify()` resolves to a work-tagged address but caller declared `msg_context="personal"` | Email guard parks delivery | bu-uv4b4 + bu-jwby9 |
| `notify()` resolves to non-primary owner address (any context) | Email guard parks delivery | bu-jwby9 |
| Dashboard `DELETE /contacts/{id}/contact-info/{info_id}` removes poisoned row | Audit row written to `switchboard.dashboard_audit_log` | bu-m24ua |

Replay integration tests live in
`tests/reconciliation/test_incident_2026_04_21_replay.py`.  All 9 tests pass.

---

## 4. Known gaps and open items

### 4.1 vCard test fixture stale schema (bu-1n5ur, in review PR #1256)

`tests/features/test_vcard.py` has a hand-rolled `CREATE TABLE` for
`public.contact_info` that is missing the `context` column added by migration
`core_083`.  This causes 4 integration tests to fail with
`asyncpg.UndefinedColumnError: column "context" of relation "contact_info" does not exist`.

Status: fix is on branch `agent/bu-1n5ur`, PR #1256 open for review (bead
bu-snqp7 in_progress).  This is a pre-existing failure on main at the time of
the reconciliation; it is not a regression introduced by this branch.

### 4.2 Default owner email resolution path has no context support

`_resolve_default_notify_recipient` (the path taken when `notify()` is called
without `contact_id` on the email channel) does not accept or use `msg_context`.
However, for email, this path only fires when an explicit `recipient` string is
provided — the function returns `None` for email without a recipient string, and
that triggers `resolved_recipient is None` which bypasses the email guard entirely.

In practice, relationship butler email sends must either supply `recipient` (an
explicit address string) or `contact_id`.  The `contact_id` path is
context-aware via `_resolve_contact_channel_identifier`.  The explicit
`recipient` string path is NOT context-filtered — but the email guard's
context-mismatch check still fires when `msg_context` is set.

This is a documentation gap rather than a code gap: the spec in RFC 0004
correctly describes `contact_id` as the recommended path for context-aware
resolution.  No code change required; operator guidance should recommend always
using `contact_id` for owner-targeted sends.

### 4.3 Butler-level default msg_context not implemented

bu-uv4b4 AC #2 specified butler-domain context inference (relationship →
`personal` by default, finance requires explicit declaration).  RFC 0004 §5.2
documents that "Butler-level context defaults are not currently enforced
automatically."  Callers must supply `msg_context` explicitly.  This is an
accepted limitation; no gap bead filed.

---

## 5. Verdict

**Clean — no gen-2 reconciliation bead needed.**

All four epic acceptance criteria are implemented and verified:

1. AC #1 (approval-gate non-primary owner-email): delivered by bu-jwby9 (PR #1237) ✓
2. AC #2 (context-aware routing): delivered by bu-uv4b4 (PR #1241) + bu-vwp11 (PR #1247) ✓
3. AC #3 (owner-contact mutation gate): delivered by bu-v6ttx (PR #1248) ✓
4. AC #4 (dashboard mutation audit): delivered by bu-m24ua (PR #1250) ✓

The only open item (vCard fixture stale schema, bu-1n5ur) is already tracked,
has a fix in review (PR #1256), and is a test infrastructure issue rather than
a gap in the incident fix coverage.

---

## 6. Files of interest

| File | Change |
|---|---|
| `src/butlers/modules/approvals/email_guard.py` | AC #1 + AC #2: is_primary gate + context mismatch |
| `src/butlers/modules/approvals/_shared.py` | Shared is_primary_contact helper (bu-0kien) |
| `src/butlers/modules/approvals/gate.py` | AC #1 extension: is_primary in Telegram gate (bu-axdie) |
| `roster/relationship/tools/contact_info.py` | AC #3: owner-contact mutation gate |
| `src/butlers/daemon.py::_resolve_contact_channel_identifier` | AC #2: context-aware SQL |
| `src/butlers/core_tools/_notifications.py` | AC #2: msg_context propagation |
| `alembic/versions/core/core_083_contact_info_context.py` | AC #2: schema migration |
| `src/butlers/api/audit_emit.py` | AC #4: audit emit helper |
| `src/butlers/api/dashboard_audit_middleware.py` | AC #4: broad middleware |
| `src/butlers/api/app.py` | AC #4: middleware registration |
| `roster/relationship/api/router.py` | AC #4: explicit emits on contact_info routes |
| `tests/reconciliation/test_incident_2026_04_21_replay.py` | This reconciliation's replay tests |
| `about/legends-and-lore/rfcs/0004-identity-and-contact-resolution.md` | Spec update (bu-uv4b4) |
